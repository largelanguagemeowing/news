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
from urllib.parse import urlparse

import feedparser
import requests
import trafilatura
from bs4 import BeautifulSoup
from dateutil import parser as dtparser

from app.config import SourceConfig, load_sources
from app.db import get_connection, init_db, transaction
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
SIMILARITY_THRESHOLD = 0.82
CLUSTER_WINDOW_HOURS = 72
SOURCE_FAIL_THRESHOLD = 3
SOURCE_AUTO_DISABLE_FAILURES = 6
SOURCE_AUTO_DISABLE_MIN_FAILURE_HOURS = 12
SOURCE_AUTO_DISABLE_COOLDOWN_HOURS = 24
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

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("news.pipeline")

DEFUDDLE_ENABLED = os.getenv("DEFUDDLE_ENABLED", "0").lower() in {"1", "true", "yes", "on"}
DEFUDDLE_TIMEOUT_SECONDS = int(os.getenv("DEFUDDLE_TIMEOUT_SECONDS", "20"))
DEFUDDLE_MAX_CHARS = int(os.getenv("DEFUDDLE_MAX_CHARS", "12000"))
REQUEST_TIMEOUT_SECONDS = 20


def parse_date(value: Any) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
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
        return datetime.now(timezone.utc)


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
    cursor = conn.execute(
        """
        INSERT INTO stage_runs (run_id, stage_name, started_at, status, metrics_json)
        VALUES (?, ?, ?, 'running', '{}')
        """,
        (run_id, stage_name, started_at),
    )
    conn.commit()
    return int(cursor.lastrowid)


def stage_end(
    conn: sqlite3.Connection,
    stage_run_id: int,
    result: StageResult,
) -> None:
    conn.execute(
        """
        UPDATE stage_runs
        SET ended_at = ?, status = ?, metrics_json = ?
        WHERE stage_run_id = ?
        """,
        (utc_now_iso(), result.status, json.dumps(result.metrics), stage_run_id),
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


def truncate_for_storage(text: str, max_chars: int = DEFUDDLE_MAX_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars]


def replace_iframes_with_markdown_links(html: str) -> str:
    if "<iframe" not in html.lower():
        return html
    soup = BeautifulSoup(html, "html.parser")
    for iframe in soup.find_all("iframe"):
        src = (iframe.get("src") or "").strip()
        title = (iframe.get("title") or "Embedded content").strip()
        replacement = f"[iframe: {title}]({src})" if src else f"iframe: {title}"
        iframe.replace_with(replacement)
    return str(soup)


def get_hostname(url: str) -> str:
    try:
        return urlparse(url).hostname.lower() if urlparse(url).hostname else ""
    except Exception:
        return ""


def is_youtube_url(url: str) -> bool:
    hostname = get_hostname(url)
    return (
        hostname == "youtu.be"
        or hostname == "youtube.com"
        or hostname == "www.youtube.com"
        or hostname.endswith(".youtube.com")
    )


def get_youtube_video_id(url: str) -> str:
    try:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()
        if hostname == "youtu.be":
            return parsed.path.lstrip("/").split("/")[0]
        if hostname in {"youtube.com", "www.youtube.com"} or hostname.endswith(".youtube.com"):
            if parsed.path == "/watch":
                for part in (parsed.query or "").split("&"):
                    if part.startswith("v="):
                        return part.split("=", 1)[1]
            if parsed.path.startswith("/shorts/"):
                return parsed.path.replace("/shorts/", "", 1).split("/")[0]
            if parsed.path.startswith("/embed/"):
                return parsed.path.replace("/embed/", "", 1).split("/")[0]
    except Exception:
        return ""
    return ""


def get_youtube_embed_url(url: str) -> str:
    video_id = get_youtube_video_id(url)
    return f"https://www.youtube.com/embed/{video_id}" if video_id else ""


def normalize_youtube_watch_url(url: str) -> str:
    video_id = get_youtube_video_id(url)
    return f"https://www.youtube.com/watch?v={video_id}" if video_id else url


def fetch_text_url(url: str) -> str:
    try:
        response = requests.get(
            url,
            timeout=REQUEST_TIMEOUT_SECONDS,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        response.raise_for_status()
        return response.text
    except Exception as exc:
        logger.debug("fetch_text_url failed url=%s error=%s", url, exc)
        return ""


def fetch_youtube_oembed(url: str) -> dict[str, str] | None:
    try:
        endpoint = "https://www.youtube.com/oembed"
        response = requests.get(
            endpoint,
            params={"url": normalize_youtube_watch_url(url), "format": "json"},
            timeout=REQUEST_TIMEOUT_SECONDS,
            headers={"accept": "application/json", "User-Agent": "Mozilla/5.0"},
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            return None
        return {
            "title": str(payload.get("title") or "").strip(),
            "author": str(payload.get("author_name") or "").strip(),
            "thumbnail_url": str(payload.get("thumbnail_url") or "").strip(),
        }
    except Exception as exc:
        logger.debug("fetch_youtube_oembed failed url=%s error=%s", url, exc)
        return None


def extract_youtube_schema_description(html: str) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        content = (script.string or script.get_text() or "").strip()
        if not content:
            continue
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            continue

        def walk(node: Any) -> str:
            if isinstance(node, list):
                for item in node:
                    found = walk(item)
                    if found:
                        return found
                return ""
            if not isinstance(node, dict):
                return ""
            type_value = node.get("@type")
            is_video = type_value == "VideoObject" or (
                isinstance(type_value, list) and "VideoObject" in type_value
            )
            if is_video and isinstance(node.get("description"), str) and node["description"].strip():
                return node["description"].strip()
            for value in node.values():
                found = walk(value)
                if found:
                    return found
            return ""

        found = walk(payload)
        if found:
            return found
    return ""


def extract_youtube_metadata(url: str, rss_title: str, rss_summary: str) -> dict[str, str]:
    oembed = fetch_youtube_oembed(url) or {}
    page_html = fetch_text_url(url)
    schema_description = extract_youtube_schema_description(page_html)
    description = schema_description or rss_summary.strip()
    return {
        "title": rss_title.strip() or oembed.get("title", ""),
        "author": oembed.get("author", ""),
        "description": description,
        "thumbnail_url": oembed.get("thumbnail_url", ""),
        "embed_url": get_youtube_embed_url(url),
        "video_id": get_youtube_video_id(url),
    }


def build_youtube_body(metadata: dict[str, str], rss_summary: str) -> str:
    parts = []
    description = (metadata.get("description") or rss_summary or "").strip()
    if description:
        parts.append(description)
    author = (metadata.get("author") or "").strip()
    if author:
        parts.append(f"author: {author}")
    embed_url = (metadata.get("embed_url") or "").strip()
    if embed_url:
        parts.append(f"video: {embed_url}")
    return "\n\n".join(parts).strip()


def parse_with_defuddle(url: str) -> tuple[str | None, bool]:
    """Return extracted content and whether defuddle was used successfully."""
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
    """Use r.jina.ai as a fallback for blocked sites."""
    if not url or is_youtube_url(url):
        return None, False
    try:
        jina_url = f"https://r.jina.ai/http://{url.replace('https://', '').replace('http://', '')}"
        response = requests.get(
            jina_url,
            timeout=REQUEST_TIMEOUT_SECONDS,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        response.raise_for_status()
        text = response.text.strip()
        if not text or len(text) < 100:
            return None, False
        # Remove the jina.ai headers: Title, URL Source, Markdown Content
        lines = text.split('\n')
        cleaned_lines = []
        in_header = True
        for line in lines:
            if in_header:
                if line.startswith('Title:') or line.startswith('URL Source:') or line.startswith('Markdown Content:'):
                    continue
                if line.strip() == '':
                    continue
                in_header = False
            cleaned_lines.append(line)
        cleaned = '\n'.join(cleaned_lines).strip()
        if not cleaned or len(cleaned) < 100:
            return None, False
        return truncate_for_storage(cleaned), True
    except Exception as exc:
        logger.debug("jina.ai extract failed url=%s error=%s", url, exc)
        return None, False


def parse_with_markdown_new(url: str) -> tuple[str | None, bool]:
    """Use markdown.new as a fallback for blocked sites."""
    if not url or is_youtube_url(url):
        return None, False
    try:
        response = requests.post(
            "https://markdown.new/",
            json={"url": url, "method": "auto"},
            timeout=REQUEST_TIMEOUT_SECONDS,
            headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("success"):
            return None, False
        content = payload.get("content", "").strip()
        if not content or len(content) < 100:
            return None, False
        # Remove frontmatter if present
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                content = parts[2].strip()
        return truncate_for_storage(content), True
    except Exception as exc:
        logger.debug("markdown.new extract failed url=%s error=%s", url, exc)
        return None, False


def is_probably_dirty_body(body: str) -> bool:
    text = (body or "").lower()
    if not text:
        return True
    dirty_markers = (
        "<iframe",
        "<script",
        "referrerpolicy",
        "allowfullscreen",
        "youtube.com/embed",
        "window.__next",
        "googletagmanager",
    )
    if any(marker in text for marker in dirty_markers):
        return True
    if "<" in text and ">" in text:
        return True
    return False


def enrich_article_content(url: str, source_id: str, title: str, body: str) -> tuple[str, str]:
    current_body = str(body or "").strip()
    if is_youtube_url(url) or source_id in YOUTUBE_SOURCE_IDS:
        youtube_meta = extract_youtube_metadata(url, title, current_body)
        return build_youtube_body(youtube_meta, current_body), "youtube"

    trafilatura_body, used_trafilatura = parse_with_trafilatura(url)
    if used_trafilatura and trafilatura_body:
        return trafilatura_body, "trafilatura"

    markdown_new_body, used_markdown_new = parse_with_markdown_new(url)
    if used_markdown_new and markdown_new_body:
        return markdown_new_body, "markdown_new"

    jina_body, used_jina = parse_with_jina_ai(url)
    if used_jina and jina_body:
        return jina_body, "jina"

    defuddle_body, used_defuddle = parse_with_defuddle(url)
    if used_defuddle and defuddle_body:
        return defuddle_body, "defuddle"

    return current_body, "rss"


def ingest_stage(
    conn: sqlite3.Connection,
    run_id: str,
    sources: list[SourceConfig],
    issue_client: GitHubIssueClient,
) -> StageResult:
    inserted = 0
    failed_sources = 0
    auto_disabled_sources = 0
    skipped_sources = 0
    defuddle_enriched = 0
    enabled_sources = [s for s in sources if s.enabled]
    logger.info(
        "Ingest stage started: sources=%d defuddle_enabled=%s",
        len(enabled_sources),
        DEFUDDLE_ENABLED,
    )
    for source in enabled_sources:
        now = datetime.now(timezone.utc)
        logger.info("Ingesting source=%s url=%s", source.source_id, source.feed_url)
        source_health = conn.execute(
            """
            SELECT consecutive_failures, last_success_at, auto_disabled_until
            FROM source_health
            WHERE source_id = ?
            """,
            (source.source_id,),
        ).fetchone()
        if source_health and source_is_in_cooldown(source_health["auto_disabled_until"], now):
            skipped_sources += 1
            logger.info(
                "Skipping source=%s reason=cooldown until=%s",
                source.source_id,
                source_health["auto_disabled_until"],
            )
            with transaction(conn):
                conn.execute(
                    """
                    INSERT INTO source_checks (source_id, run_id, checked_at, status, latency_ms, error_message)
                    VALUES (?, ?, ?, 'skipped', NULL, ?)
                    """,
                    (
                        source.source_id,
                        run_id,
                        utc_now_iso(),
                        f"Auto-disabled until {source_health['auto_disabled_until']}",
                    ),
                )
            continue
        start = time.time()
        try:
            feed = feedparser.parse(source.feed_url)
            if getattr(feed, "bozo", 0):
                raise RuntimeError(str(getattr(feed, "bozo_exception", "feed parse failed")))
            if "youtube.com/feeds/videos.xml" in source.feed_url:
                feed_title = str(getattr(feed, "feed", {}).get("title", "")).strip()
                if feed_title and feed_title != source.name:
                    with transaction(conn):
                        conn.execute(
                            "UPDATE sources SET name = ? WHERE source_id = ?",
                            (feed_title, source.source_id),
                        )
                    source.name = feed_title
            source_inserted = 0
            latest_item_at: str | None = None
            with transaction(conn):
                for entry in feed.entries:
                    link = str(entry.get("link", "")).strip()
                    title = str(entry.get("title", "")).strip() or "(untitled)"
                    body = str(entry.get("summary", "")).strip()
                    body, method = enrich_article_content(link, source.source_id, title, body)
                    if method == "defuddle":
                        defuddle_enriched += 1
                    published = parse_date(
                        entry.get("published")
                        or entry.get("updated")
                        or datetime.now(timezone.utc)
                    )
                    fetched_at = utc_now_iso()
                    canonical_url = canonicalize_url(link)
                    title_norm = normalize_text(title)
                    body_norm = normalize_text(body)
                    title_hash = sha1_hexdigest(title_norm)
                    body_hash = sha1_hexdigest(body_norm)
                    sh = str(simhash64(body_norm or title_norm))
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO articles (
                          source_id, url, canonical_url, title, title_norm, body, body_norm,
                          published_at, fetched_at, title_hash, body_hash, simhash, extraction_method
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            source.source_id,
                            link,
                            canonical_url,
                            title,
                            title_norm,
                            body,
                            body_norm,
                            iso(published),
                            fetched_at,
                            title_hash,
                            body_hash,
                            sh,
                            method,
                        ),
                    )
                    if conn.total_changes > 0:
                        source_inserted += 1
                    latest_item_at = iso(published) if latest_item_at is None else max(latest_item_at, iso(published))

                latency_ms = round((time.time() - start) * 1000, 2)
                health = conn.execute(
                    "SELECT avg_latency_ms FROM source_health WHERE source_id = ?",
                    (source.source_id,),
                ).fetchone()
                old_avg = float(health["avg_latency_ms"]) if health else 0.0
                new_avg = round((old_avg * 0.8) + (latency_ms * 0.2), 2)
                conn.execute(
                    """
                    UPDATE source_health
                    SET last_success_at = ?, last_item_at = ?, consecutive_failures = 0,
                        avg_latency_ms = ?, last_error = NULL,
                        auto_disabled_until = NULL, auto_disabled_reason = NULL
                    WHERE source_id = ?
                    """,
                    (utc_now_iso(), latest_item_at, new_avg, source.source_id),
                )
                conn.execute(
                    """
                    INSERT INTO source_checks (source_id, run_id, checked_at, status, latency_ms, error_message)
                    VALUES (?, ?, ?, 'success', ?, NULL)
                    """,
                    (source.source_id, run_id, utc_now_iso(), latency_ms),
                )
            inserted += source_inserted
            logger.info(
                "Source complete source=%s inserted=%d latency_ms=%.2f latest_item_at=%s",
                source.source_id,
                source_inserted,
                latency_ms,
                latest_item_at,
            )
            sync_incident_resolve(
                conn,
                incident_key=f"source:{source.source_id}",
                run_id=run_id,
                resolution_message="Feed recovered and ingest succeeded.",
                client=issue_client,
            )
            conn.commit()
        except Exception as exc:
            failed_sources += 1
            logger.exception("Source ingest failed source=%s error=%s", source.source_id, exc)
            with transaction(conn):
                row = conn.execute(
                    """
                    SELECT consecutive_failures, last_success_at
                    FROM source_health
                    WHERE source_id = ?
                    """,
                    (source.source_id,),
                ).fetchone()
                failures = int(row["consecutive_failures"]) + 1 if row else 1
                should_disable = should_auto_disable_source(
                    failures=failures,
                    last_success_at=row["last_success_at"] if row else None,
                    now=now,
                )
                disabled_until = (
                    iso(now + timedelta(hours=SOURCE_AUTO_DISABLE_COOLDOWN_HOURS))
                    if should_disable
                    else None
                )
                if should_disable:
                    auto_disabled_sources += 1
                conn.execute(
                    """
                    UPDATE source_health
                    SET consecutive_failures = ?, last_error = ?, errors_24h = errors_24h + 1
                      , auto_disabled_until = COALESCE(?, auto_disabled_until)
                      , auto_disabled_reason = CASE
                          WHEN ? IS NULL THEN auto_disabled_reason
                          ELSE ?
                        END
                    WHERE source_id = ?
                    """,
                    (
                        failures,
                        str(exc)[:500],
                        disabled_until,
                        disabled_until,
                        f"Auto-disabled after {failures} consecutive failures.",
                        source.source_id,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO source_checks (source_id, run_id, checked_at, status, latency_ms, error_message)
                    VALUES (?, ?, ?, 'failed', NULL, ?)
                    """,
                    (source.source_id, run_id, utc_now_iso(), str(exc)[:500]),
                )
                if failures >= SOURCE_FAIL_THRESHOLD:
                    disable_note = (
                        f" Auto-disabled until {disabled_until}."
                        if disabled_until
                        else ""
                    )
                    signal = IncidentSignal(
                        key=f"source:{source.source_id}",
                        kind="source",
                        target_id=source.source_id,
                        message=f"Source failed {failures} consecutive times. Error: {exc}.{disable_note}",
                    )
                    sync_incident_open_or_update(conn, signal, run_id, issue_client)
    logger.info(
        "Ingest stage finished inserted_articles=%d failed_sources=%d skipped_sources=%d auto_disabled_sources=%d defuddle_enriched_articles=%d",
        inserted,
        failed_sources,
        skipped_sources,
        auto_disabled_sources,
        defuddle_enriched,
    )
    return StageResult(
        status="success",
        metrics={
            "inserted_articles": inserted,
            "failed_sources": failed_sources,
            "skipped_sources": skipped_sources,
            "auto_disabled_sources": auto_disabled_sources,
            "defuddle_enriched_articles": defuddle_enriched,
            "defuddle_enabled": DEFUDDLE_ENABLED,
        },
    )


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
    lower_bound = iso(datetime.now(timezone.utc) - timedelta(days=7))
    rows = conn.execute(
        """
        SELECT article_id, source_id, title, title_norm, body_norm, published_at, simhash
        FROM articles
        WHERE published_at >= ?
        ORDER BY published_at DESC
        """,
        (lower_bound,),
    ).fetchall()
    articles = [dict(row) for row in rows]
    groups: list[list[dict[str, Any]]] = []
    for article in articles:
        assigned = False
        art_published = parse_date(article["published_at"])
        art_simhash = int(article["simhash"])
        for group in groups:
            rep = group[0]
            rep_published = parse_date(rep["published_at"])
            if abs((art_published - rep_published).total_seconds()) > (CLUSTER_WINDOW_HOURS * 3600):
                continue
            score = pair_similarity(
                article["title_norm"],
                rep["title_norm"],
                art_simhash,
                int(rep["simhash"]),
            )
            if score >= SIMILARITY_THRESHOLD:
                article["score"] = score
                group.append(article)
                assigned = True
                break
        if not assigned:
            article["score"] = 1.0
            groups.append([article])

    with transaction(conn):
        conn.execute("DELETE FROM event_members")
        conn.execute("DELETE FROM events")
        for group in groups:
            first_seen = min(item["published_at"] for item in group)
            last_seen = max(item["published_at"] for item in group)
            source_count = len({item["source_id"] for item in group})
            representative = max(group, key=lambda item: (len(item["title"]), -parse_date(item["published_at"]).timestamp()))
            cluster_key = sha1_hexdigest(f"{representative['title_norm']}:{first_seen}")
            cursor = conn.execute(
                """
                INSERT INTO events (
                  cluster_key, canonical_title, first_seen, last_seen,
                  representative_article_id, source_count
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    cluster_key,
                    representative["title"],
                    first_seen,
                    last_seen,
                    representative["article_id"],
                    source_count,
                ),
            )
            event_id = int(cursor.lastrowid)
            for item in group:
                conn.execute(
                    """
                    INSERT INTO event_members (event_id, article_id, similarity, reason)
                    VALUES (?, ?, ?, ?)
                    """,
                    (event_id, item["article_id"], float(item.get("score", 1.0)), "title+simhash"),
                )

    return StageResult(
        status="success",
        metrics={"events": len(groups), "articles_clustered": len(articles)},
    )


def categorize_stage(conn: sqlite3.Connection) -> StageResult:
    rows = conn.execute(
        """
        SELECT e.event_id, e.canonical_title, s.default_category, a.body
        FROM events e
        JOIN articles a ON e.representative_article_id = a.article_id
        JOIN sources s ON a.source_id = s.source_id
        """
    ).fetchall()
    updated = 0
    with transaction(conn):
        for row in rows:
            label, confidence = classify_event(
                row["canonical_title"], row["body"], row["default_category"]
            )
            conn.execute(
                """
                UPDATE events
                SET category_labels = ?, confidence = ?
                WHERE event_id = ?
                """,
                (label, confidence, row["event_id"]),
            )
            updated += 1
    return StageResult(status="success", metrics={"events_categorized": updated})


def export_status(conn: sqlite3.Connection) -> StageResult:
    STATUS_DIR.mkdir(parents=True, exist_ok=True)
    summary = build_summary(conn)
    sources = build_sources(conn)
    runs = build_runs(conn)
    incidents = build_incidents(conn)
    events = build_events(conn)
    articles = build_articles(conn)
    (STATUS_DIR / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (STATUS_DIR / "sources.json").write_text(json.dumps(sources, indent=2), encoding="utf-8")
    (STATUS_DIR / "runs.json").write_text(json.dumps(runs, indent=2), encoding="utf-8")
    (STATUS_DIR / "incidents.json").write_text(json.dumps(incidents, indent=2), encoding="utf-8")
    (STATUS_DIR / "events.json").write_text(json.dumps(events, indent=2), encoding="utf-8")
    (STATUS_DIR / "articles.json").write_text(json.dumps(articles, indent=2), encoding="utf-8")
    return StageResult(
        status="success",
        metrics={"exported_files": 6, "open_incidents": summary["open_incidents"]},
    )


def build_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    total_sources = conn.execute("SELECT COUNT(*) c FROM sources WHERE enabled = 1").fetchone()["c"]
    sources_health = conn.execute(
        """
        SELECT sh.source_id, sh.last_success_at, sh.consecutive_failures
        FROM source_health sh
        JOIN sources s ON s.source_id = sh.source_id
        WHERE s.enabled = 1
        """
    ).fetchall()
    healthy = 0
    stale = 0
    now = datetime.now(timezone.utc)
    for row in sources_health:
        if int(row["consecutive_failures"]) == 0:
            healthy += 1
        if not row["last_success_at"]:
            stale += 1
            continue
        if (now - parse_date(row["last_success_at"])).total_seconds() > 6 * 3600:
            stale += 1
    events_24h = conn.execute(
        "SELECT COUNT(*) c FROM events WHERE last_seen >= ?",
        (iso(now - timedelta(hours=24)),),
    ).fetchone()["c"]
    open_incidents = conn.execute(
        "SELECT COUNT(*) c FROM incidents WHERE status = 'open'"
    ).fetchone()["c"]
    article_count = conn.execute("SELECT COUNT(*) c FROM articles").fetchone()["c"]
    event_count = conn.execute("SELECT COUNT(*) c FROM events").fetchone()["c"]
    dedupe_ratio = round(1 - (event_count / article_count), 4) if article_count else 0.0
    pipeline_status = "healthy"
    if open_incidents:
        pipeline_status = "degraded"
    if total_sources and healthy == 0:
        pipeline_status = "down"
    github_repository = os.getenv("GITHUB_REPOSITORY")
    if not github_repository:
        latest = conn.execute(
            "SELECT metrics_json FROM pipeline_runs ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        if latest:
            latest_metrics = json.loads(latest["metrics_json"] or "{}")
            github_run_url = latest_metrics.get("github_run_url")
            if isinstance(github_run_url, str) and "github.com/" in github_run_url:
                parts = github_run_url.split("github.com/", 1)[1].split("/", 2)
                if len(parts) >= 2:
                    github_repository = f"{parts[0]}/{parts[1]}"
    return {
        "generated_at": utc_now_iso(),
        "pipeline_status": pipeline_status,
        "total_sources": total_sources,
        "healthy_sources": healthy,
        "stale_sources": stale,
        "total_events_24h": events_24h,
        "dedupe_ratio_24h": dedupe_ratio,
        "open_incidents": open_incidents,
        "github_repository": github_repository,
    }


def build_sources(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT s.source_id, s.name, s.feed_url, sh.last_success_at, sh.last_item_at,
               sh.consecutive_failures, sh.avg_latency_ms, sh.items_24h, sh.errors_24h, sh.last_error,
               sh.auto_disabled_until
        FROM sources s
        LEFT JOIN source_health sh ON sh.source_id = s.source_id
        WHERE s.enabled = 1
        ORDER BY s.name
        """
    ).fetchall()
    out = []
    for row in rows:
        status = "healthy"
        if int(row["consecutive_failures"] or 0) > 0:
            status = "degraded"
        if int(row["consecutive_failures"] or 0) >= SOURCE_FAIL_THRESHOLD:
            status = "down"
        if source_is_in_cooldown(row["auto_disabled_until"]):
            status = "down"
        checks = conn.execute(
            """
            SELECT status
            FROM source_checks
            WHERE source_id = ?
            ORDER BY checked_at DESC
            LIMIT 90
            """,
            (row["source_id"],),
        ).fetchall()
        recent_statuses = [check["status"] for check in checks][::-1]
        total_checks = len(recent_statuses)
        success_checks = sum(1 for status in recent_statuses if status == "success")
        uptime_pct = round((success_checks / total_checks) * 100, 2) if total_checks else 0.0
        out.append(
            {
                "source_id": row["source_id"],
                "name": row["name"],
                "feed_url": row["feed_url"],
                "status": status,
                "last_success_at": row["last_success_at"],
                "last_item_at": row["last_item_at"],
                "consecutive_failures": int(row["consecutive_failures"] or 0),
                "avg_latency_ms": float(row["avg_latency_ms"] or 0),
                "items_24h": int(row["items_24h"] or 0),
                "errors_24h": int(row["errors_24h"] or 0),
                "last_error": row["last_error"],
                "auto_disabled_until": row["auto_disabled_until"],
                "recent_statuses": recent_statuses,
                "uptime_pct_recent": uptime_pct,
                "checks_count_recent": total_checks,
            }
        )
    return out


def build_runs(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT run_id, started_at, ended_at, status, error_message, metrics_json
        FROM pipeline_runs
        ORDER BY started_at DESC
        LIMIT 20
        """
    ).fetchall()
    out = []
    for row in rows:
        run_metrics = json.loads(row["metrics_json"] or "{}")
        stage_rows = conn.execute(
            """
            SELECT stage_name, started_at, ended_at, status, metrics_json
            FROM stage_runs
            WHERE run_id = ?
            ORDER BY stage_run_id ASC
            """,
            (row["run_id"],),
        ).fetchall()
        stages = []
        for stage in stage_rows:
            stages.append(
                {
                    "stage_name": stage["stage_name"],
                    "started_at": stage["started_at"],
                    "ended_at": stage["ended_at"],
                    "status": stage["status"],
                    "metrics": json.loads(stage["metrics_json"] or "{}"),
                }
            )
        out.append(
            {
                "run_id": row["run_id"],
                "started_at": row["started_at"],
                "ended_at": row["ended_at"],
                "status": row["status"],
                "error_message": row["error_message"],
                "github_run_id": run_metrics.get("github_run_id"),
                "github_run_url": run_metrics.get("github_run_url"),
                "stage_stats": stages,
            }
        )
    return out


def build_incidents(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT incident_key, kind, target_id, status, opened_at, updated_at, resolved_at, issue_number, last_message
        FROM incidents
        ORDER BY opened_at DESC
        LIMIT 100
        """
    ).fetchall()
    return [
        {
            "incident_key": row["incident_key"],
            "kind": row["kind"],
            "target_id": row["target_id"],
            "status": row["status"],
            "opened_at": row["opened_at"],
            "updated_at": row["updated_at"],
            "resolved_at": row["resolved_at"],
            "issue_number": row["issue_number"],
            "last_message": row["last_message"],
        }
        for row in rows
    ]


def build_events(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT e.event_id, e.canonical_title, e.category_labels, e.confidence,
               e.first_seen, e.last_seen, e.source_count, a.url AS representative_url
        FROM events e
        JOIN articles a ON e.representative_article_id = a.article_id
        ORDER BY e.last_seen DESC
        LIMIT 300
        """
    ).fetchall()
    return [
        {
            "event_id": row["event_id"],
            "canonical_title": row["canonical_title"],
            "category_labels": row["category_labels"],
            "confidence": row["confidence"],
            "first_seen": row["first_seen"],
            "last_seen": row["last_seen"],
            "source_count": row["source_count"],
            "representative_url": row["representative_url"],
        }
        for row in rows
    ]


def build_articles(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT a.article_id, a.title, a.body, a.url, a.published_at, a.fetched_at,
               s.source_id, s.name AS source_name, s.default_category,
               e.category_labels AS event_category
        FROM articles a
        JOIN sources s ON s.source_id = a.source_id
        LEFT JOIN event_members em ON em.article_id = a.article_id
        LEFT JOIN events e ON e.event_id = em.event_id
        ORDER BY a.published_at DESC
        LIMIT 500
        """
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        title = row["title"]
        body = row["body"] or ""
        topic = row["event_category"] or classify_event(title, body, row["default_category"])[0]
        tags = extract_tags(title, body, row["source_id"])
        if topic not in tags:
            tags = [topic] + tags
        out.append(
            {
                "article_id": row["article_id"],
                "title": title,
                "url": row["url"],
                "published_at": row["published_at"],
                "fetched_at": row["fetched_at"],
                "source_id": row["source_id"],
                "source_name": row["source_name"],
                "topic": topic,
                "tags": tags[:7],
            }
        )
    return out


def run_pipeline() -> int:
    conn = get_connection()
    init_db(conn)
    migrate_source_ids(conn)
    sources = load_sources()
    upsert_sources(conn, sources)

    run_id = uuid.uuid4().hex[:12]
    conn.execute(
        """
        INSERT INTO pipeline_runs (run_id, started_at, status, metrics_json)
        VALUES (?, ?, 'running', '{}')
        """,
        (run_id, utc_now_iso()),
    )
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
            logger.info(
                "Stage finished stage=%s status=%s duration_ms=%.2f metrics=%s",
                stage_name,
                result.status,
                result.metrics.get("duration_ms", 0.0),
                json.dumps(result.metrics, sort_keys=True),
            )
            if result.status != "success":
                raise RuntimeError(result.error_message or f"{stage_name} failed")
            pipeline_metrics[stage_name] = result.metrics
        conn.execute(
            """
            UPDATE pipeline_runs
            SET ended_at = ?, status = 'success', metrics_json = ?, error_message = NULL
            WHERE run_id = ?
            """,
            (utc_now_iso(), json.dumps(pipeline_metrics), run_id),
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
        traceback_text = traceback.format_exc(limit=5)
        conn.execute(
            """
            UPDATE pipeline_runs
            SET ended_at = ?, status = 'failed', error_message = ?, metrics_json = ?
            WHERE run_id = ?
            """,
            (utc_now_iso(), str(exc), json.dumps(pipeline_metrics), run_id),
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
