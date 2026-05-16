"""Classify pipeline: cluster articles into events, categorize, and export status.

This pipeline takes articles from the database and:
1. Clusters similar articles into events (title similarity + simhash).
2. Categorizes events using ML model or rule-based classifier.
3. Exports all data to JSON files consumed by the dashboard.

Triggered separately from fetch and enrich pipelines so that:
- Classification/export runs independently of feed ingestion.
- Event clustering can be tuned without affecting fetch or enrichment.
- Export failures don't lose fetched content.
"""

from __future__ import annotations

import logging
import os
import time
import traceback
import uuid
from typing import Any

from app.db import get_connection, init_db
from app.incidents import GitHubIssueClient, IncidentSignal, sync_incident_open_or_update, sync_incident_resolve
from app.jobs import stages_cluster, stages_export
from app.jobs.pipeline import (
    ARTICLES_EXPORT_LIMIT,
    CLUSTER_LOOKBACK_DAYS,
    CLUSTER_WINDOW_HOURS,
    EVENTS_EXPORT_LIMIT,
    EVENTS_WINDOW_HOURS,
    SETTINGS,
    SIMILARITY_THRESHOLD,
    SLOW_SOURCE_LATENCY_MS,
    SOURCE_CHECKS_HISTORY_LIMIT,
    SOURCE_FAIL_THRESHOLD,
    STALE_SOURCE_HOURS,
    STATUS_DIR,
    build_articles,
    build_events,
    build_incidents,
    build_runs,
    build_sources,
    build_summary,
    categorize_stage,
    classify_event,
    cluster_stage,
    export_status,
    extract_tags,
    iso,
    log_stage_summary,
    pair_similarity,
    parse_date,
    sha1_hexdigest,
    source_is_in_cooldown,
    utc_now_iso,
)
from app.repos import run_repo

logger = logging.getLogger("news.pipeline")


def run_classify_pipeline() -> int:
    conn = get_connection()
    init_db(conn)

    run_id = uuid.uuid4().hex[:12]
    run_repo.create_pipeline_run(conn, run_id, utc_now_iso(), run_type="classify")
    conn.commit()

    issue_client = GitHubIssueClient()
    pipeline_metrics: dict[str, Any] = {"run_id": run_id}
    github_run_id = os.getenv("GITHUB_RUN_ID")
    github_repo = os.getenv("GITHUB_REPOSITORY")
    if github_run_id and github_repo:
        pipeline_metrics["github_run_id"] = github_run_id
        pipeline_metrics["github_run_url"] = (
            f"https://github.com/{github_repo}/actions/runs/{github_run_id}"
        )

    stages = [
        ("cluster", lambda: _run_cluster_stage(conn, run_id)),
        ("categorize", lambda: _run_categorize_stage(conn, run_id)),
        ("export", lambda: _run_export_stage(conn, run_id)),
    ]

    try:
        for stage_name, stage_fn in stages:
            logger.info("Classify stage started stage=%s run_id=%s", stage_name, run_id)
            started = time.time()
            result = stage_fn()
            result["duration_ms"] = round((time.time() - started) * 1000, 2)
            log_stage_summary(logger, stage_name=stage_name, status="success", metrics=result, run_id=run_id)
            pipeline_metrics[stage_name] = result

        run_repo.complete_pipeline_run(conn, run_id, utc_now_iso(), pipeline_metrics)
        conn.commit()
        logger.info("Classify pipeline succeeded run_id=%s", run_id)

        sync_incident_resolve(
            conn,
            incident_key="pipeline:classify",
            run_id=run_id,
            resolution_message="Classify pipeline completed successfully.",
            client=issue_client,
        )
        conn.commit()
        return 0

    except Exception as exc:
        logger.exception("Classify pipeline failed run_id=%s error=%s", run_id, exc)
        conn.rollback()
        traceback_text = traceback.format_exc(limit=5)
        run_repo.fail_pipeline_run(conn, run_id, utc_now_iso(), str(exc), pipeline_metrics)
        conn.commit()
        sync_incident_open_or_update(
            conn,
            IncidentSignal(
                key="pipeline:classify",
                kind="pipeline-stage",
                target_id="classify",
                message=f"Classify pipeline failed in run {run_id}: {exc}\n\n{traceback_text}",
                severity="sev2",
            ),
            run_id=run_id,
            client=issue_client,
        )
        conn.commit()
        return 1


def _run_stage(conn, run_id: str, stage_name: str, stage_fn) -> dict[str, Any]:
    started_at = utc_now_iso()
    stage_run_id = run_repo.create_stage_run(conn, run_id, stage_name, started_at)
    conn.commit()
    started = time.time()
    try:
        result = stage_fn()
        result["duration_ms"] = round((time.time() - started) * 1000, 2)
        run_repo.complete_stage_run(conn, stage_run_id, utc_now_iso(), "success", result)
        conn.commit()
        return result
    except Exception:
        run_repo.complete_stage_run(conn, stage_run_id, utc_now_iso(), "failed", {})
        conn.commit()
        raise


def _run_cluster_stage(conn, run_id: str) -> dict[str, Any]:
    return _run_stage(conn, run_id, "cluster", lambda: stages_cluster.cluster_stage(
        conn,
        parse_date=parse_date,
        iso=iso,
        pair_similarity=pair_similarity,
        sha1_hexdigest=sha1_hexdigest,
        similarity_threshold=SIMILARITY_THRESHOLD,
        cluster_window_hours=CLUSTER_WINDOW_HOURS,
        cluster_lookback_days=CLUSTER_LOOKBACK_DAYS,
    ))


def _run_categorize_stage(conn, run_id: str) -> dict[str, Any]:
    return _run_stage(conn, run_id, "categorize", lambda: stages_cluster.categorize_stage(
        conn,
        classify_event=classify_event,
    ))


def _run_export_stage(conn, run_id: str) -> dict[str, Any]:
    return _run_stage(conn, run_id, "export", lambda: stages_export.export_status(
        conn,
        status_dir=STATUS_DIR,
        build_summary_fn=lambda c: build_summary(c),
        build_sources_fn=lambda c: build_sources(c),
        build_runs_fn=lambda c: build_runs(c),
        build_incidents_fn=lambda c: build_incidents(c),
        build_events_fn=lambda c: build_events(c),
        build_articles_fn=lambda c: build_articles(c),
    ))


if __name__ == "__main__":
    raise SystemExit(run_classify_pipeline())
