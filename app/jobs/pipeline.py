from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import trafilatura
from dateutil import parser as dtparser

from app.config import SourceConfig
from app.db import transaction
from app.jobs import enrichment, next_flight
from app.jobs.classifier import classify_article, extract_article_tags
from app.jobs.ml_classifier import classify_with_model
from app.models import ExtractionMethod
from app.settings import get_settings
from app.utils import utc_now_iso


STATUS_DIR = Path("data/status")
COMPRESS_NEW_CIRCUIT_BREAKER_THRESHOLD = int(os.getenv("COMPRESS_NEW_CIRCUIT_BREAKER_THRESHOLD", "5"))
COMPRESS_NEW_CIRCUIT_BREAKER_BLOCK_SECONDS = int(os.getenv("COMPRESS_NEW_CIRCUIT_BREAKER_BLOCK_SECONDS", "300"))
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
    "ai-engineer",
}


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


class _CircuitBreaker:
    """Tiny in-process circuit breaker keyed off a wall-clock timestamp.

    Shared by the markdown.new and compress.new quota paths; the two
    breakers were previously hand-rolled copies of the same pattern.
    """

    def __init__(self, name: str) -> None:
        self._name = name
        self._blocked_until = 0.0

    def reset(self) -> None:
        self._blocked_until = 0.0

    def is_blocked(self) -> bool:
        return self._blocked_until > time.time()

    def seconds_remaining(self) -> int:
        return max(0, int(self._blocked_until - time.time()))

    def block(self, seconds: int, reason: str) -> None:
        seconds = max(1, seconds)
        self._blocked_until = max(self._blocked_until, time.time() + seconds)
        logger.warning(
            "%s circuit breaker open seconds=%d reason=%s",
            self._name,
            self.seconds_remaining(),
            reason,
        )


_MARKDOWN_NEW_BREAKER = _CircuitBreaker("markdown.new")
_COMPRESS_NEW_BREAKER = _CircuitBreaker("compress.new")


def reset_markdown_new_circuit_breaker() -> None:
    _MARKDOWN_NEW_BREAKER.reset()


def reset_compress_new_circuit_breaker() -> None:
    _COMPRESS_NEW_BREAKER.reset()


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


def fetch_page_html(url: str) -> str:
    return enrichment.fetch_page_html(url, REQUEST_TIMEOUT_SECONDS)


def fetch_youtube_oembed(url: str) -> dict[str, str] | None:
    return enrichment.fetch_youtube_oembed(url, REQUEST_TIMEOUT_SECONDS)


def extract_youtube_schema_description(html: str) -> str:
    return enrichment.extract_youtube_schema_description(html)


def extract_youtube_metadata(
    url: str, rss_title: str, rss_summary: str
) -> dict[str, str]:
    return enrichment.extract_youtube_metadata(
        url, rss_title, rss_summary, REQUEST_TIMEOUT_SECONDS
    )


def build_youtube_body(metadata: dict[str, str], rss_summary: str) -> str:
    return enrichment.build_youtube_body(metadata, rss_summary)


def fetch_youtube_transcript(video_id: str) -> str | None:
    return enrichment.fetch_youtube_transcript(video_id)


def build_youtube_transcript_body(metadata: dict[str, str], transcript: str) -> str:
    return enrichment.build_youtube_transcript_body(metadata, transcript)


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
        logger.debug(
            "defuddle returned non-success for url=%s code=%s", url, result.returncode
        )
        return None, False

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        logger.debug("defuddle returned invalid json for url=%s", url)
        return None, False

    content = replace_iframes_with_markdown_links(
        str(payload.get("content") or "").strip()
    )
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


def parse_with_next_flight(url: str) -> tuple[str | None, bool]:
    """Local extraction from Next.js App Router pages via their RSC flight payloads.

    Next.js pages (e.g. openai.com) are detected by content — inline
    ``self.__next_f`` scripts — not by source config, so any client-rendered
    page gets extracted locally. The fetch falls back to a curl rescue when
    the requests transport is bot-walled, keeping the extraction entirely
    off the cloud fetchers (jina, markdown.new, ...).
    """
    if not url or is_youtube_url(url):
        return None, False
    html = fetch_page_html(url)
    if not html:
        return None, False
    try:
        flight = next_flight.extract_next_flight_content(html)
    except Exception as exc:
        logger.debug("next flight extract failed url=%s error=%s", url, exc)
        return None, False
    if not flight:
        return None, False
    _title, content = flight
    cleaned = content.strip()
    if not cleaned:
        return None, False
    return truncate_for_storage(cleaned), True


def parse_with_jina_ai(url: str) -> tuple[str | None, bool]:
    return enrichment.parse_with_jina_ai(url, _enrichment_settings())


def parse_with_markdown_new(url: str) -> tuple[str | None, bool, int, dict[str, Any]]:
    return enrichment.parse_with_markdown_new(url, _enrichment_settings())


def parse_with_compress_new(url: str) -> tuple[str | None, bool]:
    if _COMPRESS_NEW_BREAKER.is_blocked():
        logger.warning("compress.new circuit breaker open, skipping")
        return None, False
    exhausted, state = enrichment.compress_new_quota.exhausted()
    if exhausted:
        logger.info("compress.new quota exhausted date=%s requests=%d limit=%d",
                    state.get("date"), state.get("requests_made"), state.get("limit"))
        return None, False
    if not enrichment.compress_new_quota.reserve():
        return None, False
    try:
        result = enrichment.parse_with_compress_new(url, _enrichment_settings())
        body, used = result
        enrichment.record_compress_new_response(bool(used and body))
        return result
    except Exception as exc:
        enrichment.record_compress_new_response(False, str(exc))
        state = enrichment.compress_new_quota.load()
        consecutive_failures = state.get("consecutive_failures", 0)
        if consecutive_failures >= COMPRESS_NEW_CIRCUIT_BREAKER_THRESHOLD:
            _COMPRESS_NEW_BREAKER.block(
                COMPRESS_NEW_CIRCUIT_BREAKER_BLOCK_SECONDS,
                f"consecutive_failures={consecutive_failures}",
            )
        return None, False


def is_probably_dirty_body(body: str) -> bool:
    return enrichment.is_probably_dirty_body(body)


@dataclass(frozen=True)
class ExtractionAttempt:
    """Outcome of one extraction attempt; None means the method missed."""

    body: str
    method: str
    rate_limit_remaining: int = -1
    rate_limited: bool = False


@dataclass
class ExtractionContext:
    """Everything an extractor may consult for one URL."""

    url: str
    source_id: str
    title: str
    body: str
    markdown_new_budget_remaining: int | None = None
    stop_on_markdown_rate_limit: bool = False


Extractor = Callable[[ExtractionContext], ExtractionAttempt | None]


def _try_youtube(ctx: ExtractionContext) -> ExtractionAttempt | None:
    if not (is_youtube_url(ctx.url) or ctx.source_id in YOUTUBE_SOURCE_IDS):
        return None
    youtube_meta = extract_youtube_metadata(ctx.url, ctx.title, ctx.body)
    transcript = fetch_youtube_transcript(youtube_meta.get("video_id", ""))
    if transcript:
        transcript_body = truncate_for_storage(
            build_youtube_transcript_body(youtube_meta, transcript)
        )
        logger.info(
            "Enrichment success source=%s method=%s url=%s",
            ctx.source_id,
            ExtractionMethod.YOUTUBE_TRANSCRIPT.value,
            ctx.url,
        )
        return ExtractionAttempt(
            transcript_body, ExtractionMethod.YOUTUBE_TRANSCRIPT.value
        )
    logger.info(
        "Enrichment success source=%s method=%s url=%s",
        ctx.source_id,
        ExtractionMethod.YOUTUBE.value,
        ctx.url,
    )
    return ExtractionAttempt(
        build_youtube_body(youtube_meta, ctx.body), ExtractionMethod.YOUTUBE.value
    )


def _try_trafilatura(ctx: ExtractionContext) -> ExtractionAttempt | None:
    trafilatura_body, used = parse_with_trafilatura(ctx.url)
    if not (used and trafilatura_body):
        return None
    logger.info(
        "Enrichment success source=%s method=%s url=%s",
        ctx.source_id,
        ExtractionMethod.TRAFILATURA.value,
        ctx.url,
    )
    return ExtractionAttempt(trafilatura_body, ExtractionMethod.TRAFILATURA.value)


def _try_next_flight(ctx: ExtractionContext) -> ExtractionAttempt | None:
    next_flight_body, used = parse_with_next_flight(ctx.url)
    if not (used and next_flight_body):
        return None
    logger.info(
        "Enrichment success source=%s method=%s url=%s",
        ctx.source_id,
        ExtractionMethod.NEXT_FLIGHT.value,
        ctx.url,
    )
    return ExtractionAttempt(next_flight_body, ExtractionMethod.NEXT_FLIGHT.value)


def _try_jina(ctx: ExtractionContext) -> ExtractionAttempt | None:
    jina_body, used = parse_with_jina_ai(ctx.url)
    if not (used and jina_body):
        return None
    logger.info(
        "Enrichment success source=%s method=%s url=%s",
        ctx.source_id,
        ExtractionMethod.JINA.value,
        ctx.url,
    )
    return ExtractionAttempt(jina_body, ExtractionMethod.JINA.value)


def _try_defuddle(ctx: ExtractionContext) -> ExtractionAttempt | None:
    defuddle_body, used = parse_with_defuddle(ctx.url)
    if not (used and defuddle_body):
        return None
    logger.info(
        "Enrichment success source=%s method=%s url=%s",
        ctx.source_id,
        ExtractionMethod.DEFUDDLE.value,
        ctx.url,
    )
    return ExtractionAttempt(defuddle_body, ExtractionMethod.DEFUDDLE.value)


def _try_compress_new(ctx: ExtractionContext) -> ExtractionAttempt | None:
    if not enrichment.supports_markdown_family(ctx.source_id):
        logger.info(
            "Enrichment skip source=%s method=%s url=%s reason=unsupported_source",
            ctx.source_id,
            ExtractionMethod.COMPRESS_NEW.value,
            ctx.url,
        )
        return None
    compress_body, used = parse_with_compress_new(ctx.url)
    if not (used and compress_body):
        return None
    logger.info(
        "Enrichment success source=%s method=%s url=%s",
        ctx.source_id,
        ExtractionMethod.COMPRESS_NEW.value,
        ctx.url,
    )
    return ExtractionAttempt(compress_body, ExtractionMethod.COMPRESS_NEW.value)


def _try_markdown_new(ctx: ExtractionContext) -> ExtractionAttempt | None:
    source_id, url = ctx.source_id, ctx.url
    if not enrichment.supports_markdown_family(source_id):
        logger.info(
            "Enrichment skip source=%s method=%s url=%s reason=unsupported_source",
            source_id,
            ExtractionMethod.MARKDOWN_NEW.value,
            url,
        )
        return None
    if _MARKDOWN_NEW_BREAKER.is_blocked():
        logger.warning(
            "Enrichment skip source=%s method=%s url=%s reason=rate_limited_circuit_open retry_after_seconds=%d",
            source_id,
            ExtractionMethod.MARKDOWN_NEW.value,
            url,
            _MARKDOWN_NEW_BREAKER.seconds_remaining(),
        )
        compress_body, compress_used = parse_with_compress_new(url)
        if compress_used and compress_body:
            logger.info(
                "Enrichment fallback success source=%s method=%s url=%s reason=markdown_circuit_open",
                source_id,
                ExtractionMethod.COMPRESS_NEW.value,
                url,
            )
            return ExtractionAttempt(
                compress_body,
                ExtractionMethod.COMPRESS_NEW.value,
                rate_limit_remaining=0,
                rate_limited=True,
            )
        return None
    quota_exhausted, quota_state = enrichment.markdown_new_quota.exhausted()
    if quota_exhausted:
        logger.warning(
            "Enrichment skip source=%s method=%s url=%s reason=daily_quota_exhausted date=%s requests_made=%s limit=%s",
            source_id,
            ExtractionMethod.MARKDOWN_NEW.value,
            url,
            quota_state.get("date"),
            quota_state.get("requests_made"),
            quota_state.get("limit"),
        )
        return None
    if (
        ctx.markdown_new_budget_remaining is not None
        and ctx.markdown_new_budget_remaining <= 0
    ):
        logger.info(
            "Enrichment skip source=%s method=%s url=%s reason=budget_exhausted",
            source_id,
            ExtractionMethod.MARKDOWN_NEW.value,
            url,
        )
        return None
    if not enrichment.markdown_new_quota.reserve():
        logger.warning(
            "Enrichment skip source=%s method=%s url=%s reason=daily_quota_reserve_failed",
            source_id,
            ExtractionMethod.MARKDOWN_NEW.value,
            url,
        )
        return None
    markdown_result = parse_with_markdown_new(url)
    if len(markdown_result) == 3:
        markdown_body, used, rate_limit_remaining = markdown_result
        response_meta = {}
    else:
        markdown_body, used, rate_limit_remaining, response_meta = markdown_result
    enrichment.record_markdown_new_response(
        rate_limit_remaining,
        status_code=response_meta.get("status_code"),
        raw_remaining_header=response_meta.get("x_rate_limit_remaining"),
        url=response_meta.get("url") or url,
    )
    if used and markdown_body:
        logger.info(
            "Enrichment success source=%s method=%s url=%s rate_limit_remaining=%d",
            source_id,
            ExtractionMethod.MARKDOWN_NEW.value,
            url,
            rate_limit_remaining,
        )
        return ExtractionAttempt(
            markdown_body,
            ExtractionMethod.MARKDOWN_NEW.value,
            rate_limit_remaining=rate_limit_remaining,
        )
    if rate_limit_remaining == -2:
        _MARKDOWN_NEW_BREAKER.block(24 * 3600, "retry_after_gt_24h")
        compress_body, compress_used = parse_with_compress_new(url)
        if compress_used and compress_body:
            logger.info(
                "Enrichment fallback success source=%s method=%s url=%s reason=markdown_long_rate_limit",
                source_id,
                ExtractionMethod.COMPRESS_NEW.value,
                url,
            )
            return ExtractionAttempt(
                compress_body,
                ExtractionMethod.COMPRESS_NEW.value,
                rate_limit_remaining=0,
                rate_limited=True,
            )
        return None
    if rate_limit_remaining == 0:
        _MARKDOWN_NEW_BREAKER.block(MARKDOWN_NEW_BLOCK_SECONDS_ON_429, "http_429")
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
            return ExtractionAttempt(
                compress_body,
                ExtractionMethod.COMPRESS_NEW.value,
                rate_limit_remaining=0,
                rate_limited=True,
            )
        if ctx.stop_on_markdown_rate_limit:
            logger.warning(
                "Enrichment stopping source=%s method=%s url=%s reason=markdown_rate_limit",
                source_id,
                ExtractionMethod.RSS.value,
                url,
            )
            return ExtractionAttempt(
                ctx.body,
                ExtractionMethod.RSS.value,
                rate_limit_remaining=0,
                rate_limited=True,
            )
    return None


# The preference chain is a registry keyed by ExtractionMethod value: adding an
# extractor is one attempt function plus one registration line here. The chain
# loop (enrich_with_policy) has no per-method branches left.
EXTRACTORS: dict[str, Extractor] = {
    ExtractionMethod.YOUTUBE.value: _try_youtube,
    ExtractionMethod.TRAFILATURA.value: _try_trafilatura,
    ExtractionMethod.NEXT_FLIGHT.value: _try_next_flight,
    ExtractionMethod.MARKDOWN_NEW.value: _try_markdown_new,
    ExtractionMethod.COMPRESS_NEW.value: _try_compress_new,
    ExtractionMethod.JINA.value: _try_jina,
    ExtractionMethod.DEFUDDLE.value: _try_defuddle,
}


def _extraction_methods(source_id: str, url: str, only_method: str | None) -> list[str]:
    """Ordered preference chain for a source (a chain, not a fallback stack)."""
    if only_method:
        return [only_method]
    # Source-aware extraction priority:
    # - OpenAI sources: next_flight -> markdown.new -> compress.new -> jina -> defuddle -> trafilatura
    # - Other sources: trafilatura -> next_flight -> jina -> defuddle
    # - YouTube is only included for YouTube URLs/sources.
    # next_flight is the local tier for client-rendered Next.js pages: it only
    # succeeds on flight payloads, and when it does it avoids the cloud
    # fetchers (and their rate limits) entirely.
    if enrichment.supports_markdown_family(source_id):
        methods = [
            ExtractionMethod.NEXT_FLIGHT.value,
            ExtractionMethod.MARKDOWN_NEW.value,
            ExtractionMethod.COMPRESS_NEW.value,
            ExtractionMethod.JINA.value,
            ExtractionMethod.DEFUDDLE.value,
            ExtractionMethod.TRAFILATURA.value,
        ]
    else:
        methods = [
            ExtractionMethod.TRAFILATURA.value,
            ExtractionMethod.NEXT_FLIGHT.value,
            ExtractionMethod.JINA.value,
            ExtractionMethod.DEFUDDLE.value,
        ]
    if is_youtube_url(url) or source_id in YOUTUBE_SOURCE_IDS:
        methods.insert(0, ExtractionMethod.YOUTUBE.value)
    return methods


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
    """Run the preference chain; first successful extractor wins.

    Returns (body, method, rate_limit_remaining, rate_limited). When no
    extractor succeeds the untouched rss body is returned with method "rss".
    """
    current_body = str(body or "").strip()
    ctx = ExtractionContext(
        url=url,
        source_id=source_id,
        title=title,
        body=current_body,
        markdown_new_budget_remaining=markdown_new_budget_remaining,
        stop_on_markdown_rate_limit=stop_on_markdown_rate_limit,
    )
    for method in _extraction_methods(source_id, url, only_method):
        logger.info(
            "Enrichment attempt source=%s method=%s url=%s",
            source_id,
            method,
            url,
        )
        attempt = EXTRACTORS[method](ctx)
        if attempt is None:
            logger.info(
                "Enrichment miss source=%s method=%s url=%s", source_id, method, url
            )
            continue
        return (
            attempt.body,
            attempt.method,
            attempt.rate_limit_remaining,
            attempt.rate_limited,
        )
    logger.info(
        "Enrichment fallback source=%s method=%s url=%s",
        source_id,
        ExtractionMethod.RSS.value,
        url,
    )
    return current_body, ExtractionMethod.RSS.value, -1, False


def enrich_with_rate_limit(
    url: str,
    source_id: str,
    title: str,
    body: str,
    max_markdown_new: int,
    markdown_new_used: int,
    only_method: str | None = None,
) -> tuple[str, str, int]:
    """Enrich with a per-run markdown.new budget; stop on rate limit."""
    budget_remaining = max_markdown_new - markdown_new_used
    enriched_body, method, rate_limit_remaining, _rate_limited = enrich_with_policy(
        url,
        source_id,
        title,
        body,
        only_method=only_method,
        markdown_new_budget_remaining=budget_remaining,
        stop_on_markdown_rate_limit=True,
    )
    return enriched_body, method, rate_limit_remaining


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
