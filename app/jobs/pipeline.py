from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import time
import traceback
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import trafilatura
from dateutil import parser as dtparser

from app.config import SourceConfig, load_sources
from app.jobs import enrichment
from app.db import get_connection, init_db, transaction
from app.logging_helpers import log_stage_summary
from app.models import ExtractionMethod
from app.repos import run_repo
from app.settings import get_settings
from app.incidents import (
    GitHubIssueClient,
    IncidentSignal,
    sync_incident_open_or_update,
    sync_incident_resolve,
)
from app.utils import (
    canonicalize_url,
    normalize_text,
    pair_similarity,
    sha1_hexdigest,
    simhash64,
    utc_now_iso,
)


STATUS_DIR = Path("data/status")
SOURCE_ID_RENAMES = {
    "deepmind-blog": "google-deepmind-blog",
    "apple-ml-blog": "apple-machine-learning",
    "simon-willison-atom": "simon-willison",
    "anyfeeds-custom-1": "cursor-blog",
    "anyfeeds-custom-2": "cursor-changelog",
    "youtube-ai-explained": "matt-wolfe",
    "youtube-threeblueonebrown": "fireship",
    "youtube-ai-coding": "ai-explained",
}
YOUTUBE_SOURCE_IDS = {
    "matt-wolfe",
    "fireship",
    "ai-explained",
    "youtube-ai-explained",
    "youtube-threeblueonebrown",
    "youtube-ai-coding",
}


@dataclass
class StageResult:
    status: str
    metrics: dict[str, Any]
    error_message: str | None = None


TAG_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("release", ("release", "launched", "launch", "announced", "introduces", "introducing")),
    ("models", ("model", "llm", "gpt", "gemini", "claude")),
    ("open-source", ("open source", "open-source", "github", "repo", "weights")),
    ("api", ("api", "sdk", "endpoint", "developers")),
    ("agents", ("agent", "agents", "automation", "workflow")),
    ("safety", ("safety", "alignment", "guardrail", "risk")),
    ("benchmark", ("benchmark", "eval", "evaluation", "score")),
    ("research", ("research", "paper", "arxiv", "study")),
    ("video", ("youtube", "video")),
]

SETTINGS = get_settings()

logging.basicConfig(
    level=getattr(logging, SETTINGS.log_level, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("news.pipeline")

SIMILARITY_THRESHOLD = SETTINGS.similarity_threshold
CLUSTER_WINDOW_HOURS = SETTINGS.cluster_window_hours
SOURCE_FAIL_THRESHOLD = SETTINGS.source_fail_threshold
SOURCE_AUTO_DISABLE_FAILURES = SETTINGS.source_auto_disable_failures
SOURCE_AUTO_DISABLE_MIN_FAILURE_HOURS = SETTINGS.source_auto_disable_min_failure_hours
SOURCE_AUTO_DISABLE_COOLDOWN_HOURS = SETTINGS.source_auto_disable_cooldown_hours
DEFUDDLE_ENABLED = SETTINGS.defuddle_enabled
DEFUDDLE_TIMEOUT_SECONDS = SETTINGS.defuddle_timeout_seconds
DEFUDDLE_MAX_CHARS = SETTINGS.defuddle_max_chars
REQUEST_TIMEOUT_SECONDS = SETTINGS.request_timeout_seconds
SOURCE_TIMEOUTS_SECONDS = SETTINGS.source_timeouts_seconds
CLUSTER_LOOKBACK_DAYS = SETTINGS.cluster_lookback_days
STALE_SOURCE_HOURS = SETTINGS.stale_source_hours
EVENTS_WINDOW_HOURS = SETTINGS.events_window_hours
SOURCE_CHECKS_HISTORY_LIMIT = SETTINGS.source_checks_history_limit
EVENTS_EXPORT_LIMIT = SETTINGS.events_export_limit
ARTICLES_EXPORT_LIMIT = SETTINGS.articles_export_limit
SLOW_SOURCE_LATENCY_MS = SETTINGS.slow_source_latency_ms
MARKDOWN_NEW_BLOCK_SECONDS_ON_429 = 600
MARKDOWN_NEW_BLOCKED_UNTIL_TS = 0.0


def get_source_timeout_seconds(source_id: str) -> int:
    return SOURCE_TIMEOUTS_SECONDS.get(source_id, REQUEST_TIMEOUT_SECONDS)


def reset_markdown_new_circuit_breaker() -> None:
    global MARKDOWN_NEW_BLOCKED_UNTIL_TS
    MARKDOWN_NEW_BLOCKED_UNTIL_TS = 0.0


def _is_markdown_new_blocked() -> bool:
    return MARKDOWN_NEW_BLOCKED_UNTIL_TS > time.time()


def _markdown_new_block_seconds_remaining() -> int:
    return max(0, int(MARKDOWN_NEW_BLOCKED_UNTIL_TS - time.time()))


def _block_markdown_new(seconds: int, reason: str) -> None:
    global MARKDOWN_NEW_BLOCKED_UNTIL_TS
    seconds = max(1, seconds)
    MARKDOWN_NEW_BLOCKED_UNTIL_TS = max(MARKDOWN_NEW_BLOCKED_UNTIL_TS, time.time() + seconds)
    logger.warning(
        "markdown.new circuit breaker open seconds=%d reason=%s",
        _markdown_new_block_seconds_remaining(),
        reason,
    )


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


def source_is_in_cooldown(auto_disabled_until: str | None, now: datetime | None = None) -> bool:
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
    return since_success.total_seconds() >= (SOURCE_AUTO_DISABLE_MIN_FAILURE_HOURS * 3600)


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


def stage_start(conn: sqlite3.Connection, run_id: str, stage_name: str) -> int:
    started_at = utc_now_iso()
    stage_run_id = run_repo.create_stage_run(conn, run_id, stage_name, started_at)
    conn.commit()
    return stage_run_id


def stage_end(
    conn: sqlite3.Connection,
    stage_run_id: int,
    result: StageResult,
) -> None:
    run_repo.complete_stage_run(
        conn,
        stage_run_id,
        utc_now_iso(),
        result.status,
        result.metrics,
    )
    conn.commit()


def classify_event(title: str, body: str, default_category: str) -> tuple[str, float]:
    text = f"{title} {body}".lower()
    rules: list[tuple[str, tuple[str, ...], float]] = [
        ("ai-models", ("model release", "llm", "gpt", "openai", "anthropic", "gemini"), 0.85),
        ("security", ("vulnerability", "cve", "exploit", "breach"), 0.84),
        ("policy", ("regulation", "policy", "law", "compliance"), 0.8),
        ("funding", ("funding", "series a", "series b", "valuation"), 0.8),
        ("product", ("launch", "released", "announced", "introduces"), 0.74),
    ]
    for label, tokens, score in rules:
        if any(token in text for token in tokens):
            return label, score
    return default_category, 0.55


def extract_tags(title: str, body: str, source_id: str) -> list[str]:
    text = f"{title} {body}".lower()
    tags: list[str] = []
    for label, patterns in TAG_RULES:
        if any(pattern in text for pattern in patterns):
            tags.append(label)
    if source_id in YOUTUBE_SOURCE_IDS and "video" not in tags:
        tags.append("video")
    if not tags:
        tags.append("general")
    return tags[:6]


def _enrichment_settings() -> enrichment.EnrichmentSettings:
    return enrichment.EnrichmentSettings(
        defuddle_enabled=DEFUDDLE_ENABLED,
        defuddle_timeout_seconds=DEFUDDLE_TIMEOUT_SECONDS,
        max_chars=DEFUDDLE_MAX_CHARS,
        request_timeout_seconds=REQUEST_TIMEOUT_SECONDS,
        youtube_source_ids=YOUTUBE_SOURCE_IDS,
    )


def truncate_for_storage(text: str, max_chars: int = DEFUDDLE_MAX_CHARS) -> str:
    return enrichment.truncate_for_storage(text, max_chars)


def replace_iframes_with_markdown_links(html: str) -> str:
    return enrichment.replace_iframes_with_markdown_links(html)


def get_hostname(url: str) -> str:
    return enrichment.get_hostname(url)


def is_youtube_url(url: str) -> bool:
    return enrichment.is_youtube_url(url)


def get_youtube_video_id(url: str) -> str:
    return enrichment.get_youtube_video_id(url)


def get_youtube_embed_url(url: str) -> str:
    return enrichment.get_youtube_embed_url(url)


def normalize_youtube_watch_url(url: str) -> str:
    return enrichment.normalize_youtube_watch_url(url)


def fetch_text_url(url: str) -> str:
    return enrichment.fetch_text_url(url, REQUEST_TIMEOUT_SECONDS)


def fetch_youtube_oembed(url: str) -> dict[str, str] | None:
    return enrichment.fetch_youtube_oembed(url, REQUEST_TIMEOUT_SECONDS)


def extract_youtube_schema_description(html: str) -> str:
    return enrichment.extract_youtube_schema_description(html)


def extract_youtube_metadata(url: str, rss_title: str, rss_summary: str) -> dict[str, str]:
    return enrichment.extract_youtube_metadata(url, rss_title, rss_summary, REQUEST_TIMEOUT_SECONDS)


def build_youtube_body(metadata: dict[str, str], rss_summary: str) -> str:
    return enrichment.build_youtube_body(metadata, rss_summary)


def parse_with_defuddle(url: str) -> tuple[str | None, bool]:
    """Compatibility wrapper kept for tests and monkeypatching."""
    if not url:
        return None, False
    if not DEFUDDLE_ENABLED:
        return None, False
    if not shutil.which("defuddle"):
        logger.warning("defuddle not found on PATH; continuing without enrichment")
        return None, False
    try:
        result = subprocess.run(
            ["defuddle", "parse", url, "--json"],
            capture_output=True,
            text=True,
            timeout=DEFUDDLE_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        logger.debug("defuddle timeout for url=%s", url)
        return None, False
    except OSError as exc:
        logger.debug("defuddle invocation error for url=%s: %s", url, exc)
        return None, False

    if result.returncode != 0 or not result.stdout.strip():
        logger.debug("defuddle returned non-success for url=%s code=%s", url, result.returncode)
        return None, False

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        logger.debug("defuddle returned invalid json for url=%s", url)
        return None, False

    content = replace_iframes_with_markdown_links(str(payload.get("content") or "").strip())
    markdown = str(payload.get("contentMarkdown") or "").strip()
    description = str(payload.get("description") or "").strip()
    extracted = content or markdown or description
    if not extracted:
        logger.debug("defuddle produced empty content for url=%s", url)
        return None, False
    return truncate_for_storage(extracted), True


def parse_with_trafilatura(url: str) -> tuple[str | None, bool]:
    """Compatibility wrapper kept for tests and monkeypatching."""
    if not url or is_youtube_url(url):
        return None, False
    html = fetch_text_url(url)
    if not html:
        return None, False
    try:
        extracted = trafilatura.extract(
            html,
            output_format="txt",
            include_links=True,
            include_images=False,
            favor_recall=True,
            deduplicate=True,
        )
    except Exception as exc:
        logger.debug("trafilatura extract failed url=%s error=%s", url, exc)
        return None, False
    cleaned = str(extracted or "").strip()
    if not cleaned:
        return None, False
    return truncate_for_storage(cleaned), True


def parse_with_jina_ai(url: str) -> tuple[str | None, bool]:
    return enrichment.parse_with_jina_ai(url, _enrichment_settings())


def parse_with_markdown_new(url: str) -> tuple[str | None, bool, int]:
    return enrichment.parse_with_markdown_new(url, _enrichment_settings())


def parse_with_compress_new(url: str) -> tuple[str | None, bool]:
    return enrichment.parse_with_compress_new(url, _enrichment_settings())


def is_probably_dirty_body(body: str) -> bool:
    return enrichment.is_probably_dirty_body(body)


def enrich_with_policy(
    url: str,
    source_id: str,
    title: str,
    body: str,
    *,
    only_method: str | None = None,
    markdown_new_budget_remaining: int | None = None,
    stop_on_markdown_rate_limit: bool = False,
) -> tuple[str, str, int, bool]:
    current_body = str(body or "").strip()
    rate_limit_remaining = -1

    # Source-aware extraction priority:
    # - OpenAI sources: markdown.new -> compress.new -> jina -> defuddle -> trafilatura
    # - Other sources: trafilatura -> jina -> defuddle
    # - YouTube is only included for YouTube URLs/sources.
    is_youtube_candidate = is_youtube_url(url) or source_id in YOUTUBE_SOURCE_IDS
    if enrichment.supports_markdown_family(source_id):
        methods_order = [
            ExtractionMethod.MARKDOWN_NEW.value,
            ExtractionMethod.COMPRESS_NEW.value,
            ExtractionMethod.JINA.value,
            ExtractionMethod.DEFUDDLE.value,
            ExtractionMethod.TRAFILATURA.value,
        ]
    else:
        methods_order = [
            ExtractionMethod.TRAFILATURA.value,
            ExtractionMethod.JINA.value,
            ExtractionMethod.DEFUDDLE.value,
        ]
    if is_youtube_candidate:
        methods_order = [ExtractionMethod.YOUTUBE.value, *methods_order]
    methods_to_try = [only_method] if only_method else methods_order

    for method in methods_to_try:
        logger.info(
            "Enrichment attempt source=%s method=%s url=%s",
            source_id,
            method,
            url,
        )
        if method == ExtractionMethod.YOUTUBE.value:
            if is_youtube_url(url) or source_id in YOUTUBE_SOURCE_IDS:
                youtube_meta = extract_youtube_metadata(url, title, current_body)
                logger.info(
                    "Enrichment success source=%s method=%s url=%s",
                    source_id,
                    ExtractionMethod.YOUTUBE.value,
                    url,
                )
                return build_youtube_body(youtube_meta, current_body), ExtractionMethod.YOUTUBE.value, -1, False
            logger.info("Enrichment miss source=%s method=%s url=%s", source_id, method, url)
            continue

        if method == ExtractionMethod.TRAFILATURA.value:
            trafilatura_body, used = parse_with_trafilatura(url)
            if used and trafilatura_body:
                logger.info(
                    "Enrichment success source=%s method=%s url=%s",
                    source_id,
                    ExtractionMethod.TRAFILATURA.value,
                    url,
                )
                return trafilatura_body, ExtractionMethod.TRAFILATURA.value, -1, False
            logger.info("Enrichment miss source=%s method=%s url=%s", source_id, method, url)
            continue

        if method == ExtractionMethod.MARKDOWN_NEW.value:
            if not enrichment.supports_markdown_family(source_id):
                logger.info(
                    "Enrichment skip source=%s method=%s url=%s reason=unsupported_source",
                    source_id,
                    method,
                    url,
                )
                continue
            if _is_markdown_new_blocked():
                logger.warning(
                    "Enrichment skip source=%s method=%s url=%s reason=rate_limited_circuit_open retry_after_seconds=%d",
                    source_id,
                    method,
                    url,
                    _markdown_new_block_seconds_remaining(),
                )
                compress_body, compress_used = parse_with_compress_new(url)
                if compress_used and compress_body:
                    logger.info(
                        "Enrichment fallback success source=%s method=%s url=%s reason=markdown_circuit_open",
                        source_id,
                        ExtractionMethod.COMPRESS_NEW.value,
                        url,
                    )
                    return compress_body, ExtractionMethod.COMPRESS_NEW.value, 0, True
                logger.info(
                    "Enrichment fallback miss source=%s method=%s url=%s reason=markdown_circuit_open",
                    source_id,
                    ExtractionMethod.COMPRESS_NEW.value,
                    url,
                )
                continue
            if markdown_new_budget_remaining is not None and markdown_new_budget_remaining <= 0:
                logger.info(
                    "Enrichment skip source=%s method=%s url=%s reason=budget_exhausted",
                    source_id,
                    method,
                    url,
                )
                continue
            markdown_body, used, rate_limit_remaining = parse_with_markdown_new(url)
            if used and markdown_body:
                logger.info(
                    "Enrichment success source=%s method=%s url=%s rate_limit_remaining=%d",
                    source_id,
                    ExtractionMethod.MARKDOWN_NEW.value,
                    url,
                    rate_limit_remaining,
                )
                return markdown_body, ExtractionMethod.MARKDOWN_NEW.value, rate_limit_remaining, False
            if rate_limit_remaining == -2:
                _block_markdown_new(24 * 3600, "retry_after_gt_24h")
                compress_body, compress_used = parse_with_compress_new(url)
                if compress_used and compress_body:
                    logger.info(
                        "Enrichment fallback success source=%s method=%s url=%s reason=markdown_long_rate_limit",
                        source_id,
                        ExtractionMethod.COMPRESS_NEW.value,
                        url,
                    )
                    return compress_body, ExtractionMethod.COMPRESS_NEW.value, 0, True
                logger.info("Enrichment miss source=%s method=%s url=%s", source_id, method, url)
                continue
            if rate_limit_remaining == 0:
                _block_markdown_new(MARKDOWN_NEW_BLOCK_SECONDS_ON_429, "http_429")
                logger.warning(
                    "Enrichment rate_limited source=%s method=%s url=%s",
                    source_id,
                    ExtractionMethod.MARKDOWN_NEW.value,
                    url,
                )
                compress_body, compress_used = parse_with_compress_new(url)
                if compress_used and compress_body:
                    logger.info(
                        "Enrichment fallback success source=%s method=%s url=%s",
                        source_id,
                        ExtractionMethod.COMPRESS_NEW.value,
                        url,
                    )
                    return compress_body, ExtractionMethod.COMPRESS_NEW.value, 0, True
                if stop_on_markdown_rate_limit:
                    logger.warning(
                        "Enrichment stopping source=%s method=%s url=%s reason=markdown_rate_limit",
                        source_id,
                        ExtractionMethod.RSS.value,
                        url,
                    )
                    return current_body, ExtractionMethod.RSS.value, 0, True
            logger.info("Enrichment miss source=%s method=%s url=%s", source_id, method, url)
            continue

        if method == ExtractionMethod.COMPRESS_NEW.value:
            if not enrichment.supports_markdown_family(source_id):
                logger.info(
                    "Enrichment skip source=%s method=%s url=%s reason=unsupported_source",
                    source_id,
                    method,
                    url,
                )
                continue
            compress_body, used = parse_with_compress_new(url)
            if used and compress_body:
                logger.info(
                    "Enrichment success source=%s method=%s url=%s",
                    source_id,
                    ExtractionMethod.COMPRESS_NEW.value,
                    url,
                )
                return compress_body, ExtractionMethod.COMPRESS_NEW.value, -1, False
            logger.info("Enrichment miss source=%s method=%s url=%s", source_id, method, url)
            continue

        if method == ExtractionMethod.JINA.value:
            jina_body, used = parse_with_jina_ai(url)
            if used and jina_body:
                logger.info(
                    "Enrichment success source=%s method=%s url=%s",
                    source_id,
                    ExtractionMethod.JINA.value,
                    url,
                )
                return jina_body, ExtractionMethod.JINA.value, -1, False
            logger.info("Enrichment miss source=%s method=%s url=%s", source_id, method, url)
            continue

        if method == ExtractionMethod.DEFUDDLE.value:
            defuddle_body, used = parse_with_defuddle(url)
            if used and defuddle_body:
                logger.info(
                    "Enrichment success source=%s method=%s url=%s",
                    source_id,
                    ExtractionMethod.DEFUDDLE.value,
                    url,
                )
                return defuddle_body, ExtractionMethod.DEFUDDLE.value, -1, False
            logger.info("Enrichment miss source=%s method=%s url=%s", source_id, method, url)
            continue

    logger.info("Enrichment fallback source=%s method=%s url=%s", source_id, ExtractionMethod.RSS.value, url)
    return current_body, ExtractionMethod.RSS.value, rate_limit_remaining, False


def enrich_article_content(url: str, source_id: str, title: str, body: str) -> tuple[str, str, list[dict]]:
    started = time.monotonic()
    enriched_body, method, _rate_limit_remaining, _rate_limited = enrich_with_policy(
        url, source_id, title, body
    )
    duration_ms = round((time.monotonic() - started) * 1000, 2)
    output_chars = len(enriched_body or "")
    status = "success" if method != ExtractionMethod.RSS.value and output_chars > 0 else "failed"
    return enriched_body, method, [
        {
            "method": method,
            "status": status,
            "duration_ms": duration_ms,
            "error_message": None,
            "output_chars": output_chars if output_chars else None,
        }
    ]


def ingest_stage(
    conn: sqlite3.Connection,
    run_id: str,
    sources: list[SourceConfig],
    issue_client: GitHubIssueClient,
) -> StageResult:
    from app.jobs import stages_ingest

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
        enrich_article_content=enrich_article_content,
        get_source_timeout_seconds=get_source_timeout_seconds,
        slow_source_latency_ms=SLOW_SOURCE_LATENCY_MS,
    )
    return StageResult(status="success", metrics=metrics)


def migrate_source_ids(conn: sqlite3.Connection) -> None:
    """Rename historical source ids so feed-source identity stays human-readable."""
    with transaction(conn):
        for old_id, new_id in SOURCE_ID_RENAMES.items():
            old_exists = conn.execute(
                "SELECT 1 FROM sources WHERE source_id = ?",
                (old_id,),
            ).fetchone()
            if not old_exists:
                continue
            conn.execute(
                """
                INSERT OR IGNORE INTO sources (source_id, name, feed_url, default_category, enabled)
                SELECT ?, name, feed_url, default_category, enabled
                FROM sources
                WHERE source_id = ?
                """,
                (new_id, old_id),
            )
            new_health = conn.execute(
                "SELECT 1 FROM source_health WHERE source_id = ?",
                (new_id,),
            ).fetchone()
            if new_health:
                conn.execute("DELETE FROM source_health WHERE source_id = ?", (old_id,))
            else:
                conn.execute(
                    "UPDATE source_health SET source_id = ? WHERE source_id = ?",
                    (new_id, old_id),
                )
            conn.execute(
                """
                UPDATE articles
                SET source_id = ?
                WHERE source_id = ?
                  AND NOT EXISTS (
                    SELECT 1
                    FROM articles a2
                    WHERE a2.source_id = ?
                      AND a2.canonical_url = articles.canonical_url
                      AND a2.published_at = articles.published_at
                  )
                """,
                (new_id, old_id, new_id),
            )
            conn.execute(
                "DELETE FROM articles WHERE source_id = ?",
                (old_id,),
            )
            conn.execute(
                """
                UPDATE incidents
                SET incident_key = CASE
                      WHEN incident_key = ? THEN ?
                      ELSE incident_key
                    END,
                    target_id = CASE
                      WHEN kind = 'source' AND target_id = ? THEN ?
                      ELSE target_id
                    END
                WHERE incident_key = ?
                   OR (kind = 'source' AND target_id = ?)
                """,
                (
                    f"source:{old_id}",
                    f"source:{new_id}",
                    old_id,
                    new_id,
                    f"source:{old_id}",
                    old_id,
                ),
            )
            conn.execute("DELETE FROM sources WHERE source_id = ?", (old_id,))


def cluster_stage(conn: sqlite3.Connection) -> StageResult:
    from app.jobs import stages_cluster

    metrics = stages_cluster.cluster_stage(
        conn,
        parse_date=parse_date,
        iso=iso,
        pair_similarity=pair_similarity,
        sha1_hexdigest=sha1_hexdigest,
        similarity_threshold=SIMILARITY_THRESHOLD,
        cluster_window_hours=CLUSTER_WINDOW_HOURS,
        cluster_lookback_days=CLUSTER_LOOKBACK_DAYS,
    )
    return StageResult(status="success", metrics=metrics)


def categorize_stage(conn: sqlite3.Connection) -> StageResult:
    from app.jobs import stages_cluster

    metrics = stages_cluster.categorize_stage(conn, classify_event=classify_event)
    return StageResult(status="success", metrics=metrics)


def export_status(conn: sqlite3.Connection) -> StageResult:
    from app.jobs import stages_export

    metrics = stages_export.export_status(
        conn,
        status_dir=STATUS_DIR,
        build_summary_fn=build_summary,
        build_sources_fn=build_sources,
        build_runs_fn=build_runs,
        build_incidents_fn=build_incidents,
        build_events_fn=build_events,
        build_articles_fn=build_articles,
    )
    return StageResult(status="success", metrics=metrics)


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


def run_pipeline() -> int:
    reset_markdown_new_circuit_breaker()
    conn = get_connection()
    init_db(conn)
    migrate_source_ids(conn)
    sources = load_sources()

    requested_source = (os.getenv("PIPELINE_SOURCE_ID") or "").strip()
    if requested_source and requested_source.lower() != "all":
        sources = [s for s in sources if s.source_id == requested_source]
        if not sources:
            logger.warning("PIPELINE_SOURCE_ID=%s did not match any configured source", requested_source)

    exclude_source = (os.getenv("PIPELINE_EXCLUDE_SOURCE") or "").strip()
    if exclude_source:
        exclude_ids = {s.strip() for s in exclude_source.split(",") if s.strip()}
        before = len(sources)
        sources = [s for s in sources if s.source_id not in exclude_ids]
        logger.info("PIPELINE_EXCLUDE_SOURCE=%s excluded %d sources", exclude_source, before - len(sources))

    upsert_sources(conn, sources)

    run_id = uuid.uuid4().hex[:12]
    run_repo.create_pipeline_run(conn, run_id, utc_now_iso())
    conn.commit()
    issue_client = GitHubIssueClient()
    pipeline_metrics: dict[str, Any] = {"run_id": run_id}
    github_run_id = os.getenv("GITHUB_RUN_ID")
    github_repo = os.getenv("GITHUB_REPOSITORY")
    if github_run_id and github_repo:
        pipeline_metrics["github_run_id"] = github_run_id
        pipeline_metrics["github_run_url"] = f"https://github.com/{github_repo}/actions/runs/{github_run_id}"
    stages = [
        ("ingest", lambda: ingest_stage(conn, run_id, sources, issue_client)),
        ("cluster", lambda: cluster_stage(conn)),
        ("categorize", lambda: categorize_stage(conn)),
        ("export", lambda: export_status(conn)),
    ]

    logger.info(
        "Pipeline run started run_id=%s sources=%d defuddle_enabled=%s",
        run_id,
        len([s for s in sources if s.enabled]),
        DEFUDDLE_ENABLED,
    )
    try:
        for stage_name, stage_fn in stages:
            logger.info("Stage started stage=%s run_id=%s", stage_name, run_id)
            stage_run_id = stage_start(conn, run_id, stage_name)
            started = time.time()
            result = stage_fn()
            result.metrics["duration_ms"] = round((time.time() - started) * 1000, 2)
            stage_end(conn, stage_run_id, result)
            log_stage_summary(
                logger,
                stage_name=stage_name,
                status=result.status,
                metrics=result.metrics,
                run_id=run_id,
            )
            if result.status != "success":
                raise RuntimeError(result.error_message or f"{stage_name} failed")
            pipeline_metrics[stage_name] = result.metrics
        run_repo.complete_pipeline_run(
            conn,
            run_id,
            utc_now_iso(),
            pipeline_metrics,
        )
        conn.commit()
        logger.info("Pipeline run succeeded run_id=%s", run_id)
        sync_incident_resolve(
            conn,
            incident_key="pipeline:orchestrator",
            run_id=run_id,
            resolution_message="Pipeline completed successfully.",
            client=issue_client,
        )
        conn.commit()
        return 0
    except Exception as exc:
        logger.exception("Pipeline run failed run_id=%s error=%s", run_id, exc)
        conn.rollback()
        traceback_text = traceback.format_exc(limit=5)
        run_repo.fail_pipeline_run(
            conn,
            run_id,
            utc_now_iso(),
            str(exc),
            pipeline_metrics,
        )
        conn.commit()
        sync_incident_open_or_update(
            conn,
            IncidentSignal(
                key="pipeline:orchestrator",
                kind="pipeline-stage",
                target_id="orchestrator",
                message=f"Pipeline failed in run {run_id}: {exc}\n\n{traceback_text}",
                severity="sev2",
            ),
            run_id=run_id,
            client=issue_client,
        )
        conn.commit()
        export_status(conn)
        return 1


if __name__ == "__main__":
    raise SystemExit(run_pipeline())
