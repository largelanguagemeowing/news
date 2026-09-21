"""Run envelope: one place for the pipeline run/incident bookkeeping.

The three entrypoints (fetch, enrich, classify) previously hand-rolled this
envelope — open db, create a run record, run named stages with their own
stage records, complete the run, escalate failures to incidents. Each copy
drifted: enrich never escalated at all, and failed stage runs were left
"running" in two entrypoints while the third marked them "failed".

A pipeline is a list of named stages, each a zero-argument callable returning
a metrics dict. The envelope owns:

- the run record and its metrics envelope (incl. CI attribution),
- per-stage records and summary logs,
- incident escalation on failure and resolution on success.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from app.db import get_connection, init_db
from app.incidents import (
    GitHubIssueClient,
    IncidentSignal,
    sync_incident_open_or_update,
    sync_incident_resolve,
)
from app.jobs import stages_export
from app.logging_helpers import log_stage_summary
from app.repos import run_repo
from app.utils import utc_now_iso

logger = logging.getLogger("news.pipeline")

StageFn = Callable[["RunContext"], dict[str, Any]]


def github_run_metrics() -> dict[str, str]:
    """CI workflow attribution (GITHUB_RUN_ID/GITHUB_REPOSITORY) for run metrics."""
    github_run_id = os.getenv("GITHUB_RUN_ID")
    github_repo = os.getenv("GITHUB_REPOSITORY")
    if not (github_run_id and github_repo):
        return {}
    return {
        "github_run_id": github_run_id,
        "github_run_url": f"https://github.com/{github_repo}/actions/runs/{github_run_id}",
    }


@dataclass
class RunContext:
    conn: sqlite3.Connection
    run_id: str
    issue_client: GitHubIssueClient
    metrics: dict[str, Any] = field(default_factory=dict)

    def stage(self, stage_name: str, fn: StageFn) -> dict[str, Any]:
        """Run one named stage: stage record, summary log, metrics accumulation."""
        stage_run_id = run_repo.create_stage_run(
            self.conn, self.run_id, stage_name, utc_now_iso()
        )
        self.conn.commit()
        started = time.time()
        try:
            result = fn(self)
        except Exception:
            run_repo.complete_stage_run(
                self.conn, stage_run_id, utc_now_iso(), "failed", {}
            )
            self.conn.commit()
            raise
        metrics = dict(result)
        metrics["duration_ms"] = round((time.time() - started) * 1000, 2)
        run_repo.complete_stage_run(
            self.conn, stage_run_id, utc_now_iso(), "success", metrics
        )
        self.conn.commit()
        self.metrics[stage_name] = metrics
        log_stage_summary(
            logger,
            stage_name=stage_name,
            status="success",
            metrics=metrics,
            run_id=self.run_id,
        )
        return metrics


def run_pipeline(
    *,
    run_type: str,
    stages: list[tuple[str, StageFn]],
    conn: sqlite3.Connection | None = None,
    issue_client: GitHubIssueClient | None = None,
    prepare: Callable[[sqlite3.Connection], None] | None = None,
) -> int:
    """Run a named pipeline under the shared run/incident envelope.

    ``prepare`` runs after schema init and before the run record is created
    (entrypoint-specific set-up: migrations, source upserts). Returns 0 on
    success, 1 on failure (the run is recorded as failed and the incident is
    escalated or updated through ``issue_client``).
    """
    conn = conn or get_connection()
    init_db(conn)
    if prepare:
        prepare(conn)
    issue_client = issue_client or GitHubIssueClient()

    run_id = uuid.uuid4().hex[:12]
    run_repo.create_pipeline_run(conn, run_id, utc_now_iso(), run_type=run_type)
    conn.commit()

    ctx = RunContext(conn=conn, run_id=run_id, issue_client=issue_client)
    ctx.metrics.update({"run_id": run_id})
    ctx.metrics.update(github_run_metrics())

    try:
        for stage_name, fn in stages:
            ctx.stage(stage_name, fn)
        run_repo.complete_pipeline_run(conn, run_id, utc_now_iso(), ctx.metrics)
        conn.commit()
        sync_incident_resolve(
            conn,
            incident_key=f"pipeline:{run_type}",
            run_id=run_id,
            resolution_message=f"{run_type.capitalize()} pipeline completed successfully.",
            client=issue_client,
        )
        conn.commit()
        logger.info("%s pipeline succeeded run_id=%s", run_type, run_id)
        return 0

    except Exception as exc:
        logger.exception("%s pipeline failed run_id=%s error=%s", run_type, run_id, exc)
        conn.rollback()
        traceback_text = traceback.format_exc(limit=5)
        run_repo.fail_pipeline_run(conn, run_id, utc_now_iso(), str(exc), ctx.metrics)
        conn.commit()
        sync_incident_open_or_update(
            conn,
            IncidentSignal(
                key=f"pipeline:{run_type}",
                kind="pipeline-stage",
                target_id=run_type,
                message=f"{run_type.capitalize()} pipeline failed in run {run_id}: {exc}\n\n{traceback_text}",
                severity="sev2",
            ),
            run_id=run_id,
            client=issue_client,
        )
        conn.commit()
        return 1


def export_stage(
    conn: sqlite3.Connection,
    *,
    status_dir: Path,
    summary: Callable[[sqlite3.Connection], dict[str, Any]],
    sources: Callable[[sqlite3.Connection], list[dict[str, Any]]],
    runs: Callable[[sqlite3.Connection], list[dict[str, Any]]],
    incidents: Callable[[sqlite3.Connection], list[dict[str, Any]]],
    events: Callable[[sqlite3.Connection], list[dict[str, Any]]],
    articles: Callable[[sqlite3.Connection], list[dict[str, Any]]],
) -> dict[str, Any]:
    """Export stage: derive dashboards JSON via stages_export."""
    return stages_export.export_status(
        conn,
        status_dir=status_dir,
        build_summary_fn=summary,
        build_sources_fn=sources,
        build_runs_fn=runs,
        build_incidents_fn=incidents,
        build_events_fn=events,
        build_articles_fn=articles,
    )