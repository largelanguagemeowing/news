"""Classify pipeline: cluster articles into events, categorize, and export status.

This pipeline takes articles from the database and:
1. Clusters similar articles into events (title similarity + simhash).
2. Categorizes events using ML model or rule-based classifier.
3. Exports all data to JSON files consumed by the dashboard.

Triggered separately from fetch and enrich pipelines so that:
- Classification/export runs independently of feed ingestion.
- Event clustering can be tuned without affecting fetch or enrichment.
- Export failures don't lose fetched content.

Run orchestration (run records, stage records, incident escalation) lives in
app.jobs.runner — this module supplies the cluster / categorize / export
stages.
"""

from __future__ import annotations

import logging

from app.jobs import runner, stages_cluster
from app.jobs.pipeline import (
    CLUSTER_LOOKBACK_DAYS,
    CLUSTER_WINDOW_HOURS,
    SIMILARITY_THRESHOLD,
    STATUS_DIR,
    build_articles,
    build_events,
    build_incidents,
    build_runs,
    build_sources,
    build_summary,
    classify_event,
    iso,
    parse_date,
)
from app.utils import pair_similarity, sha1_hexdigest

logger = logging.getLogger("news.pipeline")


def _export_status(conn) -> dict:
    """Export stage: derive all dashboards JSON (matches fetch/enrich wiring)."""
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


def run_classify_pipeline() -> int:
    def cluster(ctx) -> dict:
        return stages_cluster.cluster_stage(
            ctx.conn,
            parse_date=parse_date,
            iso=iso,
            pair_similarity=pair_similarity,
            sha1_hexdigest=sha1_hexdigest,
            similarity_threshold=SIMILARITY_THRESHOLD,
            cluster_window_hours=CLUSTER_WINDOW_HOURS,
            cluster_lookback_days=CLUSTER_LOOKBACK_DAYS,
        )

    def categorize(ctx) -> dict:
        return stages_cluster.categorize_stage(
            ctx.conn,
            classify_event=classify_event,
        )

    stages = [
        ("cluster", cluster),
        ("categorize", categorize),
        ("export", _export_status),
    ]

    return runner.run_pipeline(run_type="classify", stages=stages)


def main() -> int:
    return run_classify_pipeline()


if __name__ == "__main__":
    raise SystemExit(main())