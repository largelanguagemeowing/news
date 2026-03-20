from __future__ import annotations

import argparse
import json
import logging
import time
import uuid

from app.db import get_connection, init_db, transaction
from app.jobs import pipeline
from app.models import ExtractionMethod
from app.repos import run_repo
from app.settings import get_settings
from app.utils import normalize_text, sha1_hexdigest, simhash64, utc_now_iso


LOG_LEVEL = get_settings().log_level
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("news.backfill")
SETTINGS = get_settings()


def _enrich_with_rate_limit(
    url: str,
    source_id: str,
    title: str,
    body: str,
    max_markdown_new: int,
    markdown_new_used: int,
    only_method: str | None = None,
) -> tuple[str, str, int]:
    """Enrich article using shared pipeline enrichment policy."""
    budget_remaining = max_markdown_new - markdown_new_used
    enriched_body, method, rate_limit_remaining, _rate_limited = pipeline.enrich_with_policy(
        url,
        source_id,
        title,
        body,
        only_method=only_method,
        markdown_new_budget_remaining=budget_remaining,
        stop_on_markdown_rate_limit=True,
    )
    return enriched_body, method, rate_limit_remaining


def backfill_articles(
    limit: int,
    only_missing: bool = False,
    only_dirty: bool = False,
    dry_run: bool = False,
    all_items: bool = False,
    skip_enriched: bool = False,
    max_markdown_new: int = SETTINGS.backfill_default_markdown_new_limit,
    only_method: str | None = None,  # Force specific method only
    source_id: str | None = None,  # Filter to specific source
    exclude_source: str | None = None,  # Exclude source(s), comma-separated
) -> dict[str, int | bool]:
    started_at = time.time()
    conn = get_connection()
    init_db(conn)

    run_id = uuid.uuid4().hex[:12]
    run_repo.create_pipeline_run(conn, run_id, utc_now_iso(), run_type="backfill")
    conn.commit()

    where_missing = "AND (a.body IS NULL OR TRIM(a.body) = '' OR LENGTH(a.body) < 120)" if only_missing else ""
    where_skip_enriched = "AND (a.extraction_method IS NULL OR a.extraction_method = 'rss')" if skip_enriched else ""
    where_source = "AND a.source_id = ?" if source_id else ""
    exclude_ids = [s.strip() for s in (exclude_source or "").split(",") if s.strip()]
    where_exclude = ""
    if exclude_ids:
        placeholders = ",".join("?" for _ in exclude_ids)
        where_exclude = f"AND a.source_id NOT IN ({placeholders})"
    limit_clause = "" if all_items else "LIMIT ?"
    query = f"""
        SELECT a.article_id, a.url, a.title, a.body, a.source_id, a.extraction_method
        FROM articles a
        WHERE TRIM(a.url) != ''
        {where_missing}
        {where_skip_enriched}
        {where_source}
        {where_exclude}
        ORDER BY a.published_at DESC
        {limit_clause}
    """
    params_list: list[str | int] = []
    if source_id:
        params_list.append(source_id)
    params_list.extend(exclude_ids)
    if not all_items:
        params_list.append(max(1, limit))
    params = tuple(params_list) if params_list else ()
    rows = conn.execute(query, params).fetchall()

    if only_dirty:
        rows = [r for r in rows if pipeline.is_probably_dirty_body(str(r["body"] or ""))]

    total_rows = len(rows)
    logger.info(
        "Backfill started candidates=%d only_missing=%s only_dirty=%s all_items=%s dry_run=%s skip_enriched=%s max_markdown_new=%d only_method=%s source_id=%s exclude_source=%s",
        total_rows,
        only_missing,
        only_dirty,
        all_items,
        dry_run,
        skip_enriched,
        max_markdown_new,
        only_method,
        source_id,
        exclude_source,
    )

    attempted = 0
    enriched = 0
    updated = 0
    unchanged = 0
    misses = 0
    markdown_new_used = 0
    markdown_new_rate_limited = False  # Set to True when 429 received
    method_counts: dict[str, int] = {}
    updates: list[tuple[str, str, str, str, str, int]] = []

    progress_interval = max(1, total_rows // 10) if total_rows else 1
    for row in rows:
        attempted += 1
        url = str(row["url"] or "")
        source_id = str(row["source_id"] or "")
        title = str(row["title"] or "")
        body = str(row["body"] or "")
        
        # Check if already enriched and skip_enriched is enabled
        current_method = str(row["extraction_method"] or "rss")
        if skip_enriched and current_method != ExtractionMethod.RSS.value:
            logger.debug("Skipping already enriched article_id=%s method=%s", row["article_id"], current_method)
            unchanged += 1
            continue
        
        # Skip if markdown.new rate limited and only_method is markdown_new
        if markdown_new_rate_limited and only_method == "markdown_new":
            logger.debug("Skipping article_id=%s due to markdown.new rate limit", row["article_id"])
            unchanged += 1
            continue
        
        # Try enrichment with rate limit awareness
        new_body, method, rate_limit_remaining = _enrich_with_rate_limit(
            url, source_id, title, body, max_markdown_new, markdown_new_used, only_method
        )
        
        # Check if rate limited
        if rate_limit_remaining == 0:
            markdown_new_rate_limited = True
            logger.warning("markdown.new rate limit hit, stopping further markdown.new attempts")
        
        if method == ExtractionMethod.MARKDOWN_NEW.value:
            markdown_new_used += 1
        
        method_counts[method] = method_counts.get(method, 0) + 1
        
        if method != ExtractionMethod.RSS.value and new_body:
            enriched += 1
        elif not new_body:
            misses += 1

        new_body = pipeline.truncate_for_storage(str(new_body or "").strip())
        old_body = str(row["body"] or "").strip()
        if not new_body or new_body == old_body:
            unchanged += 1
        else:
            body_norm = normalize_text(new_body)
            body_hash = sha1_hexdigest(body_norm)
            title_norm = normalize_text(str(row["title"] or ""))
            sh = str(simhash64(body_norm or title_norm))
            updates.append((new_body, body_norm, body_hash, sh, method, int(row["article_id"])))

        if attempted % progress_interval == 0 or attempted == total_rows:
            method_summary = ", ".join(f"{k}={v}" for k, v in sorted(method_counts.items()))
            rate_limit_status = "RATE_LIMITED" if markdown_new_rate_limited else f"{markdown_new_used}/{max_markdown_new}"
            logger.info(
                "Backfill progress attempted=%d/%d enriched=%d queued_updates=%d unchanged=%d misses=%d markdown_new=%s methods=%s",
                attempted,
                total_rows,
                enriched,
                len(updates),
                unchanged,
                misses,
                rate_limit_status,
                method_summary,
            )

    if not dry_run and updates:
        with transaction(conn):
            conn.executemany(
                """
                UPDATE articles
                SET body = ?, body_norm = ?, body_hash = ?, simhash = ?, extraction_method = ?
                WHERE article_id = ?
                """,
                updates,
            )
        updated = len(updates)

    duration_ms = round((time.time() - started_at) * 1000, 2)
    method_summary = ", ".join(f"{k}={v}" for k, v in sorted(method_counts.items()))
    rate_limit_status = "RATE_LIMITED" if markdown_new_rate_limited else f"{markdown_new_used}/{max_markdown_new}"
    logger.info(
        "Backfill finished attempted=%d enriched=%d updated=%d unchanged=%d misses=%d markdown_new=%s duration_ms=%.2f methods=%s",
        attempted,
        enriched,
        updated,
        unchanged,
        misses,
        rate_limit_status,
        duration_ms,
        method_summary,
    )

    metrics = {
        "defuddle_enabled": pipeline.DEFUDDLE_ENABLED,
        "dry_run": dry_run,
        "only_missing": only_missing,
        "only_dirty": only_dirty,
        "all_items": all_items,
        "skip_enriched": skip_enriched,
        "only_method": only_method,
        "source_id": source_id,
        "exclude_source": exclude_source,
        "attempted": attempted,
        "enriched": enriched,
        "updated": updated,
        "unchanged": unchanged,
        "misses": misses,
        "markdown_new_used": markdown_new_used,
        "markdown_new_limit": max_markdown_new,
        "markdown_new_rate_limited": markdown_new_rate_limited,
        "method_counts": method_counts,
        "duration_ms": duration_ms,
        "run_id": run_id,
    }

    metrics_to_record = {"run_id": run_id, "backfill": metrics}
    run_repo.complete_pipeline_run(conn, run_id, utc_now_iso(), metrics_to_record)
    conn.commit()

    conn.close()
    return metrics


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill existing articles with enriched extraction")
    parser.add_argument(
        "--limit",
        type=int,
        default=SETTINGS.backfill_default_limit,
        help="Maximum number of articles to process",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Process all matching articles (ignores --limit)",
    )
    parser.add_argument(
        "--only-missing",
        action="store_true",
        help="Only process articles with empty/very short bodies",
    )
    parser.add_argument(
        "--only-dirty",
        action="store_true",
        help="Only process articles with likely polluted/HTML-heavy bodies",
    )
    parser.add_argument("--dry-run", action="store_true", help="Compute metrics without writing updates")
    parser.add_argument(
        "--enable-defuddle",
        action="store_true",
        help="Enable defuddle without setting DEFUDDLE_ENABLED env var",
    )
    parser.add_argument(
        "--skip-enriched",
        action="store_true",
        help="Skip articles already enriched (extraction_method != rss)",
    )
    parser.add_argument(
        "--max-markdown-new",
        type=int,
        default=SETTINGS.backfill_default_markdown_new_limit,
        help="Maximum markdown.new requests (default from BACKFILL_DEFAULT_MARKDOWN_NEW_LIMIT)",
    )
    parser.add_argument(
        "--only-method",
        type=str,
        choices=["youtube", "trafilatura", "markdown_new", "compress_new", "jina", "defuddle"],
        default=None,
        help="Only use specified extraction method, skip if it fails (no fallback)",
    )
    parser.add_argument(
        "--source-id",
        type=str,
        default=None,
        help="Filter to specific source (e.g., openai-blog, cursor-blog)",
    )
    parser.add_argument(
        "--exclude-source",
        type=str,
        default=None,
        help="Exclude source(s), comma-separated (e.g., openai-blog or openai-blog,cursor-blog)",
    )
    args = parser.parse_args()

    if args.enable_defuddle:
        pipeline.DEFUDDLE_ENABLED = True

    logger.info(
        "Backfill CLI invoked limit=%d all=%s only_missing=%s only_dirty=%s dry_run=%s defuddle_enabled=%s skip_enriched=%s max_markdown_new=%d only_method=%s source_id=%s exclude_source=%s",
        args.limit,
        args.all,
        args.only_missing,
        args.only_dirty,
        args.dry_run,
        pipeline.DEFUDDLE_ENABLED,
        args.skip_enriched,
        args.max_markdown_new,
        args.only_method,
        args.source_id,
        args.exclude_source,
    )
    metrics = backfill_articles(
        limit=args.limit,
        only_missing=args.only_missing,
        only_dirty=args.only_dirty,
        dry_run=args.dry_run,
        all_items=args.all,
        skip_enriched=args.skip_enriched,
        max_markdown_new=args.max_markdown_new,
        only_method=args.only_method,
        source_id=args.source_id,
        exclude_source=args.exclude_source,
    )
    print(json.dumps(metrics))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
