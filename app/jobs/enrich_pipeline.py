"""Enrichment pipeline: enrich articles that only have RSS body content.

This pipeline queries articles that have not yet been enriched
(extraction_method = 'rss' or body is short/empty) and applies the
priority chain of content extraction methods (trafilatura, jina.ai,
defuddle, markdown.new, etc.).

Triggered separately from fetch and classify pipelines so that:
- Enrichment can be tuned independently (timeouts, rate limits, budgets).
- Enrichment failures do not block feed ingestion.
- The pipeline can be re-run selectively on articles that need it.
"""

from __future__ import annotations

import argparse
import logging
import os
import time
import traceback
import uuid
from typing import Any

from app.db import get_connection, init_db
from app.incidents import GitHubIssueClient, IncidentSignal, sync_incident_open_or_update
from app.jobs import pipeline, stages_export
from app.jobs.pipeline import (
    DEFUDDLE_ENABLED,
    SETTINGS,
    STATUS_DIR,
    build_articles,
    build_events,
    build_incidents,
    build_runs,
    build_sources,
    build_summary,
    log_stage_summary,
    reset_markdown_new_circuit_breaker,
    utc_now_iso,
)
from app.models import ExtractionMethod
from app.repos import article_repo, run_repo
from app.settings import get_settings
from app.utils import normalize_text, sha1_hexdigest, simhash64

logger = logging.getLogger("news.pipeline")


def _enrich_with_rate_limit(
    url: str,
    source_id: str,
    title: str,
    body: str,
    max_markdown_new: int,
    markdown_new_used: int,
    only_method: str | None = None,
) -> tuple[str, str, int]:
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


def run_enrich_pipeline(
    limit: int | None = None,
    only_missing: bool = True,
    only_dirty: bool = False,
    skip_enriched: bool = True,
    max_markdown_new: int = 100,
    only_method: str | None = None,
    source_id: str | None = None,
    exclude_source: str | None = None,
    export: bool = False,
) -> int:
    reset_markdown_new_circuit_breaker()
    conn = get_connection()
    init_db(conn)

    run_id = uuid.uuid4().hex[:12]
    run_repo.create_pipeline_run(conn, run_id, utc_now_iso(), run_type="enrich")
    conn.commit()

    pipeline_metrics: dict[str, Any] = {"run_id": run_id}
    github_run_id = os.getenv("GITHUB_RUN_ID")
    github_repo = os.getenv("GITHUB_REPOSITORY")
    if github_run_id and github_repo:
        pipeline_metrics["github_run_id"] = github_run_id
        pipeline_metrics["github_run_url"] = (
            f"https://github.com/{github_repo}/actions/runs/{github_run_id}"
        )

    stage_run_id = _create_stage_run(conn, run_id, "enrich")
    started = time.time()
    try:
        metrics = _enrich_articles(
            conn,
            run_id,
            limit=limit,
            only_missing=only_missing,
            only_dirty=only_dirty,
            skip_enriched=skip_enriched,
            max_markdown_new=max_markdown_new,
            only_method=only_method,
            source_id=source_id,
            exclude_source=exclude_source,
        )
        metrics["duration_ms"] = round((time.time() - started) * 1000, 2)
        _complete_stage_run(conn, stage_run_id, "success", metrics)

        pipeline_metrics["enrich"] = metrics
        run_repo.complete_pipeline_run(conn, run_id, utc_now_iso(), pipeline_metrics)
        conn.commit()

        log_stage_summary(logger, stage_name="enrich", status="success", metrics=metrics, run_id=run_id)
        logger.info("Enrich pipeline succeeded run_id=%s enriched=%d", run_id, metrics.get("updated", 0))

        if export:
            logger.info("Running lightweight export for feed dashboard")
            export_metrics = stages_export.export_status(
                conn,
                status_dir=STATUS_DIR,
                build_summary_fn=lambda c: build_summary(c),
                build_sources_fn=lambda c: build_sources(c),
                build_runs_fn=lambda c: build_runs(c),
                build_incidents_fn=lambda c: build_incidents(c),
                build_events_fn=lambda c: build_events(c),
                build_articles_fn=lambda c: build_articles(c),
            )
            logger.info("Export completed: %s", export_metrics)
            pipeline_metrics["export"] = export_metrics
            run_repo.complete_pipeline_run(conn, run_id, utc_now_iso(), pipeline_metrics)
            conn.commit()

        return 0

    except Exception as exc:
        logger.exception("Enrich pipeline failed run_id=%s error=%s", run_id, exc)
        conn.rollback()
        traceback_text = traceback.format_exc(limit=5)
        run_repo.fail_pipeline_run(conn, run_id, utc_now_iso(), str(exc), pipeline_metrics)
        conn.commit()
        return 1


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
) -> dict[str, Any]:
    where_missing = "AND (a.body IS NULL OR TRIM(a.body) = '' OR LENGTH(a.body) < 120)" if only_missing else ""
    where_skip_enriched = "AND (a.extraction_method IS NULL OR a.extraction_method = 'rss')" if skip_enriched else ""
    where_source = "AND a.source_id = ?" if source_id else ""
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
    if source_id:
        params_list.append(source_id)
    params_list.extend(exclude_ids)
    if limit is not None:
        params_list.append(max(1, limit))
    params = tuple(params_list) if params_list else ()
    rows = conn.execute(query, params).fetchall()

    if only_dirty:
        rows = [r for r in rows if pipeline.is_probably_dirty_body(str(r["body"] or ""))]

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
    updates: list[tuple[str, str, str, str, str, int]] = []

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

        new_body, method, rate_limit_remaining = _enrich_with_rate_limit(
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

    if updates:
        conn.executemany(
            """
            UPDATE articles
            SET body = ?, body_norm = ?, body_hash = ?, simhash = ?, extraction_method = ?
            WHERE article_id = ?
            """,
            updates,
        )
    conn.commit()

    updated = len(updates)
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
        "defuddle_enabled": DEFUDDLE_ENABLED,
    }


def _create_stage_run(conn, run_id: str, stage_name: str) -> int:
    started_at = utc_now_iso()
    stage_run_id = run_repo.create_stage_run(conn, run_id, stage_name, started_at)
    conn.commit()
    return stage_run_id


def _complete_stage_run(conn, stage_run_id: int, status: str, metrics: dict) -> None:
    run_repo.complete_stage_run(conn, stage_run_id, utc_now_iso(), status, metrics)
    conn.commit()


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
    parser.add_argument("--source-id", type=str, default=None, help="Filter to a specific source")
    parser.add_argument("--exclude-source", type=str, default=None, help="Exclude source(s), comma-separated")
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
        export=args.export,
    )


if __name__ == "__main__":
    raise SystemExit(main())
