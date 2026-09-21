"""Enrichment pipeline: enrich articles that only have RSS body content.

This pipeline queries articles that have not yet been enriched
(extraction_method = 'rss' or body is short/empty) and applies the
priority chain of content extraction methods (trafilatura, jina.ai,
defuddle, markdown.new, etc.).

Triggered separately from fetch and classify pipelines so that:
- Enrichment can be tuned independently (timeouts, rate limits, budgets).
- Enrichment failures do not block feed ingestion.
- The pipeline can be re-run selectively on articles that need it.

Run orchestration (run records, stage records, incident escalation) lives in
app.jobs.runner — this module supplies the enrich stage and its set-up.
"""

from __future__ import annotations

import argparse
import logging
import os
from typing import Any

from app.config import load_sources
from app.jobs import runner
from app.jobs.pipeline import (
    DEFUDDLE_ENABLED,
    STATUS_DIR,
    build_articles,
    build_events,
    build_incidents,
    build_runs,
    build_sources,
    build_summary,
    enrich_with_rate_limit,
    is_probably_dirty_body,
    reset_markdown_new_circuit_breaker,
    truncate_for_storage,
    utc_now_iso,
)
from app.models import ExtractionMethod
from app.repos import article_repo
from app.utils import normalize_text, sha1_hexdigest, simhash64

logger = logging.getLogger("news.pipeline")


def _write_job_summary(message: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        try:
            with open(path, "a") as f:
                f.write("## Enrich Pipeline Failed\n\n")
                f.write(f"{message}\n\n")
        except OSError:
            pass


def _fail(message: str) -> int:
    logger.error("Enrich pipeline aborted: %s", message)
    _write_job_summary(message)
    return 1


def _export_status(conn) -> dict:
    """Export stage for the feed dashboard (matches fetch/classify wiring)."""
    return runner.export_stage(
        conn,
        status_dir=STATUS_DIR,
        summary=lambda c: build_summary(c),
        sources=lambda c: build_sources(c),
        runs=lambda c: build_runs(c),
        incidents=lambda c: build_incidents(c),
        events=lambda c: build_events(c),
        articles=lambda c: build_articles(c),
    )


# Incremental persistence: flush accumulated article updates every N writes so
# a killed/timed-out run keeps at most N-1 articles of work instead of
# discarding the whole batch in the final rollback.
ENRICH_FLUSH_INTERVAL = max(1, int(os.getenv("ENRICH_FLUSH_INTERVAL", "25")))

_FLUSH_UPDATES_SQL = """
    UPDATE articles
    SET body = ?, body_norm = ?, body_hash = ?, simhash = ?, extraction_method = ?
    WHERE article_id = ?
"""


def _flush_updates(conn, updates: list[tuple[str, str, str, str, str, int]]) -> int:
    """Write accumulated updates in one transaction and clear the batch.

    Enrichment attempts recorded since the last flush ride along in the same
    transaction, so bodies and their attempt trail land atomically.
    """
    if not updates:
        return 0
    conn.executemany(_FLUSH_UPDATES_SQL, updates)
    conn.commit()
    flushed = len(updates)
    updates.clear()
    return flushed


def run_enrich_pipeline(
    limit: int | None = None,
    only_missing: bool = True,
    only_dirty: bool = False,
    skip_enriched: bool = True,
    max_markdown_new: int = 100,
    only_method: str | None = None,
    source_id: str | None = None,
    exclude_source: str | None = None,
    flush_interval: int | None = None,
    export: bool = False,
) -> int:
    if source_id:
        source_ids = [s.strip() for s in source_id.split(",") if s.strip()]
        configured_ids = {s.source_id for s in load_sources()}
        unknown = [s for s in source_ids if s not in configured_ids]
        if unknown:
            return _fail(
                f"Unknown source_id(s): {', '.join(unknown)}. Must be one of: {', '.join(sorted(configured_ids))}"
            )
        source_id = source_ids

    reset_markdown_new_circuit_breaker()

    def enrich(ctx) -> dict:
        return _enrich_articles(
            ctx.conn,
            ctx.run_id,
            limit=limit,
            only_missing=only_missing,
            only_dirty=only_dirty,
            skip_enriched=skip_enriched,
            max_markdown_new=max_markdown_new,
            only_method=only_method,
            source_id=source_id,
            exclude_source=exclude_source,
            flush_interval=flush_interval,
        )

    stages = [("enrich", enrich)]
    if export:
        stages.append(("export", _export_status))

    return runner.run_pipeline(run_type="enrich", stages=stages)


def _enrich_articles(
    conn,
    run_id: str,
    limit: int | None = 500,
    only_missing: bool = True,
    only_dirty: bool = False,
    skip_enriched: bool = True,
    max_markdown_new: int = 100,
    only_method: str | None = None,
    source_id: str | None = None,
    exclude_source: str | None = None,
    flush_interval: int | None = None,
) -> dict[str, Any]:
    flush_interval = flush_interval or ENRICH_FLUSH_INTERVAL
    where_missing = "AND (a.body IS NULL OR TRIM(a.body) = '' OR LENGTH(a.body) < 120)" if only_missing else ""
    where_skip_enriched = "AND (a.extraction_method IS NULL OR a.extraction_method = 'rss')" if skip_enriched else ""
    source_ids = list(source_id) if source_id else []
    where_source = ""
    if source_ids:
        placeholders = ",".join("?" for _ in source_ids)
        where_source = f"AND a.source_id IN ({placeholders})"
    exclude_ids = [s.strip() for s in (exclude_source or "").split(",") if s.strip()]
    where_exclude = ""
    if exclude_ids:
        placeholders = ",".join("?" for _ in exclude_ids)
        where_exclude = f"AND a.source_id NOT IN ({placeholders})"
    limit_clause = "" if limit is None else "LIMIT ?"
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
    params_list.extend(source_ids)
    params_list.extend(exclude_ids)
    if limit is not None:
        params_list.append(max(1, limit))
    params = tuple(params_list) if params_list else ()
    rows = conn.execute(query, params).fetchall()

    if only_dirty:
        rows = [r for r in rows if is_probably_dirty_body(str(r["body"] or ""))]

    total_rows = len(rows)
    logger.info(
        "Enrich pipeline candidates=%d only_missing=%s only_dirty=%s skip_enriched=%s max_markdown_new=%d only_method=%s source_id=%s",
        total_rows, only_missing, only_dirty, skip_enriched, max_markdown_new, only_method, source_id,
    )

    attempted = 0
    enriched = 0
    updated = 0
    unchanged = 0
    misses = 0
    markdown_new_used = 0
    markdown_new_rate_limited = False
    stopped_early = False
    method_counts: dict[str, int] = {}
    per_source: dict[str, dict[str, int]] = {}
    updates: list[tuple[str, str, str, str, str, int]] = []
    flushed_total = 0
    flushed_batches = 0

    progress_interval = max(1, total_rows // 10) if total_rows else 1
    for row in rows:
        attempted += 1
        url = str(row["url"] or "")
        sid = str(row["source_id"] or "")
        title = str(row["title"] or "")
        body = str(row["body"] or "")

        current_method = str(row["extraction_method"] or "rss")
        if skip_enriched and current_method != ExtractionMethod.RSS.value:
            unchanged += 1
            continue

        if markdown_new_rate_limited and only_method == "markdown_new":
            stopped_early = True
            logger.warning(
                "Stopping early due to markdown.new rate limit attempted=%d total=%d",
                attempted - 1, total_rows,
            )
            break

        new_body, method, rate_limit_remaining = enrich_with_rate_limit(
            url, sid, title, body, max_markdown_new, markdown_new_used, only_method,
        )

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

        new_body = truncate_for_storage(str(new_body or "").strip())
        old_body = str(row["body"] or "").strip()
        if not new_body or new_body == old_body:
            unchanged += 1
        else:
            body_norm = normalize_text(new_body)
            body_hash = sha1_hexdigest(body_norm)
            title_norm = normalize_text(str(row["title"] or ""))
            sh = str(simhash64(body_norm or title_norm))
            updates.append((new_body, body_norm, body_hash, sh, method, int(row["article_id"])))
            if len(updates) >= flush_interval:
                flushed_total += _flush_updates(conn, updates)
                flushed_batches += 1

        # Per-source tracking
        ps = per_source.setdefault(sid, {"attempted": 0, "enriched": 0, "failed": 0})
        ps["attempted"] += 1
        if method != ExtractionMethod.RSS.value and new_body:
            ps["enriched"] += 1
        elif method == ExtractionMethod.RSS.value or not new_body:
            ps["failed"] += 1

        # Record enrichment attempt
        enrichment_status = "success" if method != ExtractionMethod.RSS.value and new_body else "failed"
        attempt_ts = utc_now_iso()
        article_repo.record_enrichment_attempt(
            conn,
            article_url=url,
            source_id=sid,
            method=method,
            status=enrichment_status,
            duration_ms=None,
            error_message=None,
            output_chars=len(new_body) if new_body else None,
            created_at=attempt_ts,
        )

        if attempted % progress_interval == 0 or attempted == total_rows:
            method_summary = ", ".join(f"{k}={v}" for k, v in sorted(method_counts.items()))
            logger.info(
                "Enrich progress attempted=%d/%d enriched=%d queued=%d unchanged=%d misses=%d methods=%s",
                attempted, total_rows, enriched, len(updates), unchanged, misses, method_summary,
            )

    final_flushed = _flush_updates(conn, updates)
    if final_flushed:
        flushed_total += final_flushed
        flushed_batches += 1

    updated = flushed_total
    method_summary = ", ".join(f"{k}={v}" for k, v in sorted(method_counts.items()))
    logger.info(
        "Enrich finished attempted=%d enriched=%d updated=%d unchanged=%d misses=%d methods=%s",
        attempted, enriched, updated, unchanged, misses, method_summary,
    )

    return {
        "attempted": attempted,
        "enriched": enriched,
        "updated": updated,
        "unchanged": unchanged,
        "misses": misses,
        "markdown_new_used": markdown_new_used,
        "markdown_new_rate_limited": markdown_new_rate_limited,
        "stopped_early": stopped_early,
        "method_counts": dict(method_counts),
        "per_source": per_source,
        "flush_batches": flushed_batches,
        "defuddle_enabled": DEFUDDLE_ENABLED,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Enrich articles with body content extraction")
    parser.add_argument("--limit", type=int, default=None, help="Max articles to process (default: 500)")
    parser.add_argument("--all", action="store_true", help="Process all matching articles (ignores --limit)")
    parser.add_argument("--only-missing", action="store_true", default=True, help="Only process articles with empty/short bodies")
    parser.add_argument("--no-only-missing", action="store_false", dest="only_missing", help="Process all articles regardless of body length")
    parser.add_argument("--only-dirty", action="store_true", help="Only process articles with likely dirty bodies")
    parser.add_argument("--skip-enriched", action="store_true", default=True, help="Skip articles already enriched")
    parser.add_argument("--no-skip-enriched", action="store_false", dest="skip_enriched", help="Process articles even if already enriched")
    parser.add_argument("--max-markdown-new", type=int, default=100, help="Max markdown.new requests")
    parser.add_argument("--only-method", type=str, choices=["youtube", "trafilatura", "markdown_new", "compress_new", "jina", "defuddle"], default=None, help="Force specific extraction method only")
    parser.add_argument("--source-id", type=str, default=None, help="Filter to specific source(s), comma-separated")
    parser.add_argument("--exclude-source", type=str, default=None, help="Exclude source(s), comma-separated")
    parser.add_argument("--flush-interval", type=int, default=None, help="Flush enriched articles to the DB every N writes (default: 25)")
    parser.add_argument(
        "--export",
        action="store_true",
        default=False,
        help="Export status files after enrichment (enables feed dashboard without classify pipeline)",
    )
    args = parser.parse_args()

    return run_enrich_pipeline(
        limit=None if args.all else args.limit,
        only_missing=args.only_missing,
        only_dirty=args.only_dirty,
        skip_enriched=args.skip_enriched,
        max_markdown_new=args.max_markdown_new,
        only_method=args.only_method,
        source_id=args.source_id,
        exclude_source=args.exclude_source,
        flush_interval=args.flush_interval,
        export=args.export,
    )


if __name__ == "__main__":
    raise SystemExit(main())