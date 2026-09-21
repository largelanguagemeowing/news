"""Fetch-only pipeline: ingest RSS/Atom feeds and store articles without enrichment.

This pipeline fetches RSS feeds, parses entries, and inserts articles into the
database using only the RSS summary as the body. No enrichment (trafilatura,
jina, defuddle, markdown.new, etc.) is performed.

Triggered separately from enrich and classify pipelines so that:
- Feed ingestion stays fast and isolated from slow enrichment calls.
- Source health checks are not delayed by enrichment failures/rate limits.
- Each pipeline can be tuned independently.
"""

from __future__ import annotations

import argparse
import logging
import os
import time
import traceback
import uuid
from typing import Any

from app.config import load_sources
from app.db import get_connection, init_db
from app.incidents import GitHubIssueClient, IncidentSignal, sync_incident_open_or_update, sync_incident_resolve
from app.jobs import stages_ingest, stages_export
from app.jobs.pipeline import (
    DEFUDDLE_ENABLED,
    SLOW_SOURCE_LATENCY_MS,
    SOURCE_AUTO_DISABLE_COOLDOWN_HOURS,
    SOURCE_FAIL_THRESHOLD,
    STATUS_DIR,
    build_articles,
    build_events,
    build_incidents,
    build_runs,
    build_sources,
    build_summary,
    complete_stage_run,
    create_stage_run,
    get_source_timeout_seconds,
    github_run_metrics,
    iso,
    migrate_source_ids,
    parse_date,
    parse_date_inferred,
    reset_markdown_new_circuit_breaker,
    should_auto_disable_source,
    source_is_in_cooldown,
    upsert_sources,
    utc_now_iso,
)
from app.logging_helpers import log_stage_summary
from app.models import ExtractionMethod
from app.repos import run_repo
from app.utils import canonicalize_url, normalize_text, sha1_hexdigest, simhash64

logger = logging.getLogger("news.pipeline")


def _noop_enrich(
    url: str, source_id: str, title: str, body: str
) -> tuple[str, str, list[dict]]:
    """No-op enrichment: return the RSS body as-is with method='rss'."""
    return body, ExtractionMethod.RSS.value, []


def run_export(conn) -> dict[str, Any]:
    logger.info("Running lightweight export for feed dashboard")
    metrics = stages_export.export_status(
        conn,
        status_dir=STATUS_DIR,
        build_summary_fn=lambda c: build_summary(c),
        build_sources_fn=lambda c: build_sources(c),
        build_runs_fn=lambda c: build_runs(c),
        build_incidents_fn=lambda c: build_incidents(c),
        build_events_fn=lambda c: build_events(c),
        build_articles_fn=lambda c: build_articles(c),
    )
    logger.info("Export completed: %s", metrics)
    return metrics


def run_fetch_pipeline(export: bool = False) -> int:
    reset_markdown_new_circuit_breaker()
    conn = get_connection()
    init_db(conn)
    migrate_source_ids(conn)
    sources = load_sources()

    requested_source = (os.getenv("PIPELINE_SOURCE_ID") or "").strip()
    if requested_source and requested_source.lower() != "all":
        sources = [s for s in sources if s.source_id == requested_source]
        if not sources:
            logger.warning(
                "PIPELINE_SOURCE_ID=%s did not match any configured source",
                requested_source,
            )

    exclude_source = (os.getenv("PIPELINE_EXCLUDE_SOURCE") or "").strip()
    if exclude_source:
        exclude_ids = {s.strip() for s in exclude_source.split(",") if s.strip()}
        before = len(sources)
        sources = [s for s in sources if s.source_id not in exclude_ids]
        logger.info(
            "PIPELINE_EXCLUDE_SOURCE=%s excluded %d sources",
            exclude_source,
            before - len(sources),
        )

    upsert_sources(conn, sources)

    run_id = uuid.uuid4().hex[:12]
    run_repo.create_pipeline_run(conn, run_id, utc_now_iso(), run_type="fetch")
    conn.commit()

    issue_client = GitHubIssueClient()
    pipeline_metrics: dict[str, Any] = {"run_id": run_id}
    pipeline_metrics.update(github_run_metrics())

    stage_run_id = create_stage_run(conn, run_id, "fetch")
    started = time.time()
    try:
        metrics = stages_ingest.ingest_stage(
            conn,
            run_id,
            sources,
            issue_client,
            defuddle_enabled=DEFUDDLE_ENABLED,
            source_fail_threshold=SOURCE_FAIL_THRESHOLD,
            source_auto_disable_cooldown_hours=SOURCE_AUTO_DISABLE_COOLDOWN_HOURS,
            source_is_in_cooldown=source_is_in_cooldown,
            should_auto_disable_source=should_auto_disable_source,
            utc_now_iso=utc_now_iso,
            iso=iso,
            parse_date=parse_date,
            parse_date_inferred=parse_date_inferred,
            canonicalize_url=canonicalize_url,
            normalize_text=normalize_text,
            sha1_hexdigest=sha1_hexdigest,
            simhash64=simhash64,
            enrich_article_content=_noop_enrich,
            get_source_timeout_seconds=get_source_timeout_seconds,
            slow_source_latency_ms=SLOW_SOURCE_LATENCY_MS,
        )
        metrics["duration_ms"] = round((time.time() - started) * 1000, 2)
        complete_stage_run(conn, stage_run_id, "success", metrics)

        pipeline_metrics["fetch"] = metrics
        run_repo.complete_pipeline_run(conn, run_id, utc_now_iso(), pipeline_metrics)
        conn.commit()

        log_stage_summary(logger, stage_name="fetch", status="success", metrics=metrics, run_id=run_id)
        logger.info("Fetch pipeline succeeded run_id=%s", run_id)

        if export:
            export_metrics = run_export(conn)
            pipeline_metrics["export"] = export_metrics
            run_repo.complete_pipeline_run(conn, run_id, utc_now_iso(), pipeline_metrics)
            conn.commit()

        sync_incident_resolve(
            conn,
            incident_key="pipeline:fetch",
            run_id=run_id,
            resolution_message="Fetch pipeline completed successfully.",
            client=issue_client,
        )
        conn.commit()
        return 0

    except Exception as exc:
        logger.exception("Fetch pipeline failed run_id=%s error=%s", run_id, exc)
        conn.rollback()
        traceback_text = traceback.format_exc(limit=5)
        run_repo.fail_pipeline_run(conn, run_id, utc_now_iso(), str(exc), pipeline_metrics)
        conn.commit()
        sync_incident_open_or_update(
            conn,
            IncidentSignal(
                key="pipeline:fetch",
                kind="pipeline-stage",
                target_id="fetch",
                message=f"Fetch pipeline failed in run {run_id}: {exc}\n\n{traceback_text}",
                severity="sev2",
            ),
            run_id=run_id,
            client=issue_client,
        )
        conn.commit()
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch-only pipeline: ingest RSS feeds")
    parser.add_argument(
        "--export",
        action="store_true",
        default=False,
        help="Export status files after fetch (enables feed dashboard without classify pipeline)",
    )
    args = parser.parse_args()
    return run_fetch_pipeline(export=args.export)


if __name__ == "__main__":
    raise SystemExit(main())
