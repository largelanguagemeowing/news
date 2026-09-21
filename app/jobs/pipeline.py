"""Pipeline facade: a small forwarding interface over the real modules.

pipeline.py used to be ~950 lines: the settings snapshot, the source-id
migrations, the circuit breakers, the parse adapters and the whole extraction
preference chain (EXTRACTORS, the _try_* extractors, enrich_with_policy)
shared one import surface, and tests monkeypatched re-export wrappers that
only existed because the chain happened to live here.

The real logic now lives below, in its owner modules:

- app/settings.py — the one env-read snapshot (module-level constants)
- app/jobs/enrichment.py — extraction policy, extractors, breakers, adapters
- app/jobs/migrations.py — SOURCE_ID_RENAMES replay
- app/jobs/stages_export.py — the real build_* stages

This module keeps only what the pipeline genuinely owns (date parsing, the
cooldown/auto-disable policy, source upserts, the classify wrapper, the export
facade legs) plus the re-export surface call sites still name. New code should
import from the owner modules directly; tests patch the real modules, not
these re-exports.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any

from dateutil import parser as dtparser

from app.config import SourceConfig
from app.db import transaction
from app.jobs import enrichment
from app.jobs.classifier import classify_article, extract_article_tags
from app.jobs.enrichment import (
    DEFUDDLE_ENABLED as DEFUDDLE_ENABLED,
    EXTRACTORS as EXTRACTORS,
    ExtractionAttempt as ExtractionAttempt,
    ExtractionContext as ExtractionContext,
    Extractor as Extractor,
    _extraction_methods as _extraction_methods,
    build_youtube_body as build_youtube_body,
    build_youtube_transcript_body as build_youtube_transcript_body,
    enrich_with_policy as enrich_with_policy,
    enrich_with_rate_limit as enrich_with_rate_limit,
    extract_youtube_metadata as extract_youtube_metadata,
    extract_youtube_schema_description as extract_youtube_schema_description,
    fetch_page_html as fetch_page_html,
    fetch_text_url as fetch_text_url,
    fetch_youtube_oembed as fetch_youtube_oembed,
    fetch_youtube_transcript as fetch_youtube_transcript,
    get_hostname as get_hostname,
    get_youtube_embed_url as get_youtube_embed_url,
    get_youtube_video_id as get_youtube_video_id,
    is_probably_dirty_body as is_probably_dirty_body,
    is_youtube_url as is_youtube_url,
    normalize_youtube_watch_url as normalize_youtube_watch_url,
    parse_with_compress_new as parse_with_compress_new,
    parse_with_defuddle as parse_with_defuddle,
    parse_with_jina_ai as parse_with_jina_ai,
    parse_with_markdown_new as parse_with_markdown_new,
    parse_with_next_flight as parse_with_next_flight,
    parse_with_trafilatura as parse_with_trafilatura,
    replace_iframes_with_markdown_links as replace_iframes_with_markdown_links,
    reset_compress_new_circuit_breaker as reset_compress_new_circuit_breaker,
    reset_markdown_new_circuit_breaker as reset_markdown_new_circuit_breaker,
)
from app.jobs.migrations import SOURCE_ID_RENAMES as SOURCE_ID_RENAMES
from app.jobs.migrations import migrate_source_ids as migrate_source_ids
from app.jobs.ml_classifier import classify_with_model
from app.settings import (
    ARTICLES_EXPORT_LIMIT,
    CLUSTER_LOOKBACK_DAYS as CLUSTER_LOOKBACK_DAYS,
    CLUSTER_WINDOW_HOURS as CLUSTER_WINDOW_HOURS,
    DEFUDDLE_MAX_CHARS,
    EVENTS_EXPORT_LIMIT,
    EVENTS_WINDOW_HOURS,
    REQUEST_TIMEOUT_SECONDS,
    SIMILARITY_THRESHOLD as SIMILARITY_THRESHOLD,
    SLOW_SOURCE_LATENCY_MS as SLOW_SOURCE_LATENCY_MS,
    SOURCE_AUTO_DISABLE_COOLDOWN_HOURS as SOURCE_AUTO_DISABLE_COOLDOWN_HOURS,
    SOURCE_AUTO_DISABLE_FAILURES,
    SOURCE_AUTO_DISABLE_MIN_FAILURE_HOURS,
    SOURCE_CHECKS_HISTORY_LIMIT,
    SOURCE_FAIL_THRESHOLD,
    SOURCE_TIMEOUTS_SECONDS,
    STALE_SOURCE_HOURS,
    STATUS_DIR as STATUS_DIR,
    YOUTUBE_SOURCE_IDS,
    get_settings,
)
from app.utils import utc_now_iso


SETTINGS = get_settings()

logging.basicConfig(
    level=getattr(logging, SETTINGS.log_level, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("news.pipeline")


def get_source_timeout_seconds(source_id: str) -> int:
    return SOURCE_TIMEOUTS_SECONDS.get(source_id, REQUEST_TIMEOUT_SECONDS)


def parse_date(value: Any) -> datetime:
    if value is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    try:
        dt = dtparser.parse(str(value))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return datetime.min.replace(tzinfo=timezone.utc)


def parse_date_inferred(value: Any) -> tuple[datetime, bool]:
    if value is None:
        return datetime.min.replace(tzinfo=timezone.utc), True
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc), False
        return value.astimezone(timezone.utc), False
    try:
        dt = dtparser.parse(str(value))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc), False
    except Exception:
        return datetime.min.replace(tzinfo=timezone.utc), True


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def source_is_in_cooldown(
    auto_disabled_until: str | None, now: datetime | None = None
) -> bool:
    if not auto_disabled_until:
        return False
    current = now or datetime.now(timezone.utc)
    return parse_date(auto_disabled_until) > current


def should_auto_disable_source(
    failures: int,
    last_success_at: str | None,
    now: datetime | None = None,
) -> bool:
    if failures < SOURCE_AUTO_DISABLE_FAILURES:
        return False
    if not last_success_at:
        return True
    current = now or datetime.now(timezone.utc)
    since_success = current - parse_date(last_success_at)
    return since_success.total_seconds() >= (
        SOURCE_AUTO_DISABLE_MIN_FAILURE_HOURS * 3600
    )


def upsert_sources(conn: sqlite3.Connection, sources: list[SourceConfig]) -> None:
    with transaction(conn):
        for source in sources:
            conn.execute(
                """
                INSERT INTO sources (source_id, name, feed_url, default_category, enabled)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                  name=excluded.name,
                  feed_url=excluded.feed_url,
                  default_category=excluded.default_category,
                  enabled=excluded.enabled
                """,
                (
                    source.source_id,
                    source.name,
                    source.feed_url,
                    source.default_category,
                    1 if source.enabled else 0,
                ),
            )
            conn.execute(
                """
                INSERT INTO source_health (source_id)
                VALUES (?)
                ON CONFLICT(source_id) DO NOTHING
                """,
                (source.source_id,),
            )


def classify_event(title: str, body: str, default_category: str) -> tuple[str, float]:
    ml_result = classify_with_model(title, body, default_category=default_category)
    if ml_result:
        return ml_result
    result = classify_article(title, body, default_category)
    return result.label, result.confidence


def extract_tags(title: str, body: str, source_id: str) -> list[str]:
    return extract_article_tags(title, body, source_id, YOUTUBE_SOURCE_IDS)


def truncate_for_storage(text: str, max_chars: int = DEFUDDLE_MAX_CHARS) -> str:
    return enrichment.truncate_for_storage(text, max_chars)


def build_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    from app.jobs import stages_export

    return stages_export.build_summary(
        conn,
        parse_date=parse_date,
        iso_now_fn=utc_now_iso,
        stale_source_hours=STALE_SOURCE_HOURS,
        events_window_hours=EVENTS_WINDOW_HOURS,
    )


def build_sources(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    from app.jobs import stages_export

    return stages_export.build_sources(
        conn,
        source_fail_threshold=SOURCE_FAIL_THRESHOLD,
        source_is_in_cooldown=source_is_in_cooldown,
        source_checks_history_limit=SOURCE_CHECKS_HISTORY_LIMIT,
    )


def build_runs(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    from app.jobs import stages_export

    return stages_export.build_runs(conn)


def build_incidents(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    from app.jobs import stages_export

    return stages_export.build_incidents(conn)


def build_events(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    from app.jobs import stages_export

    return stages_export.build_events(conn, events_export_limit=EVENTS_EXPORT_LIMIT)


def build_articles(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    from app.jobs import stages_export

    return stages_export.build_articles(
        conn,
        classify_event=classify_event,
        extract_tags=extract_tags,
        articles_export_limit=ARTICLES_EXPORT_LIMIT,
    )