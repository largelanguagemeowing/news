"""Fetch-only pipeline: ingest RSS/Atom feeds and store articles without enrichment.

This pipeline fetches RSS feeds, parses entries, and inserts articles into the
database using only the RSS summary as the body. No enrichment (trafilatura,
jina, defuddle, markdown.new, etc.) is performed.

Triggered separately from enrich and classify pipelines so that:
- Feed ingestion stays fast and isolated from slow enrichment calls.
- Source health checks are not delayed by enrichment failures/rate limits.
- Each pipeline can be tuned independently.

Run orchestration (run records, stage records, incident escalation) lives in
app.jobs.runner — this module supplies the fetch stage and its set-up.
"""

from __future__ import annotations

import argparse
import logging
import os

from app.config import load_sources
from app.jobs import runner, stages_ingest
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
    get_source_timeout_seconds,
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
from app.models import ExtractionMethod
from app.utils import canonicalize_url, normalize_text, sha1_hexdigest, simhash64

logger = logging.getLogger("news.pipeline")


def _noop_enrich(
    url: str, source_id: str, title: str, body: str
) -> tuple[str, str, list[dict]]:
    """No-op enrichment: return the RSS body as-is with method='rss'."""
    return body, ExtractionMethod.RSS.value, []


def _export_status(conn) -> dict:
    """Export stage for the feed dashboard (matches classify/enrich wiring)."""
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


def run_fetch_pipeline(export: bool = False) -> int:
    reset_markdown_new_circuit_breaker()
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

    def prepare(conn) -> None:
        migrate_source_ids(conn)
        upsert_sources(conn, sources)

    def ingest(ctx) -> dict:
        return stages_ingest.ingest_stage(
            ctx.conn,
            ctx.run_id,
            sources,
            ctx.issue_client,
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

    stages = [("fetch", ingest)]
    if export:
        stages.append(("export", _export_status))

    return runner.run_pipeline(run_type="fetch", prepare=prepare, stages=stages)


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