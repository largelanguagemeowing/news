from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Literal
from urllib.parse import urlparse

import requests
import tenacity
from bs4 import BeautifulSoup
from youtube_transcript_api import YouTubeTranscriptApi

from app.jobs.quota import DailyQuota, now_iso



logger = logging.getLogger("news.pipeline")
MARKDOWN_NEW_QUOTA_PATH = Path("data/status/markdown_new_quota.json")
MARKDOWN_NEW_DAILY_LIMIT = int(os.getenv("MARKDOWN_NEW_DAILY_LIMIT", "500"))
COMPRESS_NEW_QUOTA_PATH = Path("data/status/compress_new_quota.json")
COMPRESS_NEW_DAILY_LIMIT = int(os.getenv("COMPRESS_NEW_DAILY_LIMIT", "500"))

# Both daily-limit machines are instances of the same DailyQuota state machine
# (one class, two state files) — see app/jobs/quota.py.
markdown_new_quota = DailyQuota(
    name="markdown.new",
    path=MARKDOWN_NEW_QUOTA_PATH,
    daily_limit=MARKDOWN_NEW_DAILY_LIMIT,
    extra_state=lambda: {
        "header_observations": [],
        "last_response": None,
    },
)
compress_new_quota = DailyQuota(
    name="compress.new",
    path=COMPRESS_NEW_QUOTA_PATH,
    daily_limit=COMPRESS_NEW_DAILY_LIMIT,
    extra_state=lambda: {
        "consecutive_failures": 0,
        "total_successes": 0,
        "total_failures": 0,
        "last_success": None,
        "last_failure": None,
    },
)


def record_markdown_new_response(
    rate_limit_remaining: int,
    *,
    status_code: int | None = None,
    raw_remaining_header: str | None = None,
    url: str | None = None,
) -> None:
    """Record an HTTP response from markdown.new against the daily quota.

    ``rate_limit_remaining`` follows the chain's convention: -1 unknown/unset,
    0 exhausted (HTTP 429), -2 retry-after > 24h.
    """
    state = markdown_new_quota.load()
    limit = int(state.get("limit") or markdown_new_quota.daily_limit)
    response_meta = {
        "observed_at": now_iso(),
        "status_code": status_code,
        "x_rate_limit_remaining": raw_remaining_header,
        "parsed_remaining": rate_limit_remaining,
        "url": url,
    }
    observations = state.get("header_observations")
    if not isinstance(observations, list):
        observations = []
    observations.append(response_meta)
    state["header_observations"] = observations[-20:]
    state["last_response"] = response_meta
    if rate_limit_remaining >= 0:
        state["remaining"] = min(rate_limit_remaining, limit)
        state["requests_made"] = max(
            int(state.get("requests_made") or 0),
            max(0, limit - state["remaining"]),
        )
    if rate_limit_remaining in {0, -2}:
        state["remaining"] = 0
        state["exhausted"] = True
    markdown_new_quota.save(state)


def record_compress_new_response(success: bool, error: str | None = None) -> None:
    """Record a compress.new outcome (success/failure counters) in the quota state."""
    state = compress_new_quota.load()
    now = now_iso()
    if success:
        state["consecutive_failures"] = 0
        state["total_successes"] = int(state.get("total_successes", 0)) + 1
        state["last_success"] = now
    else:
        state["consecutive_failures"] = int(state.get("consecutive_failures", 0)) + 1
        state["total_failures"] = int(state.get("total_failures", 0)) + 1
        state["last_failure"] = now
    compress_new_quota.save(state)


def _request_retry(retry_error_callback, log_message: str, url: str):
    """Decorator factory: retry a request on transient errors with backoff.

    Shared retry policy (two attempts, exponential backoff capped at 10s) used
    by every requests-based fetcher in this module.
    """
    return tenacity.retry(
        stop=tenacity.stop_after_attempt(2),
        wait=tenacity.wait_exponential(multiplier=1, min=2, max=10),
        retry=tenacity.retry_if_exception_type(
            (requests.RequestException, requests.HTTPError)
        ),
        retry_error_callback=retry_error_callback,
        before_sleep=lambda retry_state: logger.debug(
            log_message, retry_state.attempt_number, url
        ),
    )


MethodName = Literal[
    "youtube",
    "youtube_transcript",
    "trafilatura",
    "markdown_new",
    "jina",
    "defuddle",
    "rss",
]


@dataclass(frozen=True)
class EnrichmentSettings:
    defuddle_enabled: bool
    defuddle_timeout_seconds: int
    max_chars: int
    request_timeout_seconds: int
    youtube_source_ids: set[str]




def truncate_for_storage(text: str, max_chars: int) -> str:
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
        if hostname in {"youtube.com", "www.youtube.com"} or hostname.endswith(
            ".youtube.com"
        ):
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


def fetch_text_url(url: str, request_timeout_seconds: int) -> str:
    """Fetch page text with retry logic for transient failures."""
    try:

        @_request_retry(
            lambda retry_state: "",
            "Page fetch failed, retrying (attempt %d/2) for url=%s",
            url,
        )
        def _fetch(u: str) -> str:
            response = requests.get(
                u,
                timeout=request_timeout_seconds,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            response.raise_for_status()
            return response.text

        return _fetch(url)
    except Exception as exc:
        logger.debug("fetch_text_url failed url=%s error=%s", url, exc)
        return ""


# Bot-walled pages (e.g. openai.com) reject the plain requests transport on
# request-header shape rather than deep TLS fingerprinting, so system curl
# with a real Chrome header set is accepted where requests gets a 403.
_CURL_CHROME_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
_CURL_CHROME_HEADERS = (
    "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,"
    "image/avif,image/webp,*/*;q=0.8",
    "Accept-Language: en-US,en;q=0.9",
    'sec-ch-ua: "Chromium";v="131", "Not_A Brand";v="24"',
    "sec-ch-ua-mobile: ?0",
    'sec-ch-ua-platform: "Linux"',
    "Sec-Fetch-Dest: document",
    "Sec-Fetch-Mode: navigate",
    "Sec-Fetch-Site: none",
    "Sec-Fetch-User: ?1",
)


def fetch_text_url_via_curl(url: str, request_timeout_seconds: int) -> str:
    """Curl rescue for pages that reject the requests transport.

    Shells out to system curl with a real Chrome header set (preferring no
    deep-fingerprint impersonation: plain curl is present everywhere). Returns
    "" when curl is unavailable or the transport fails — the caller keeps its
    fallback chain.
    """
    if not url or is_youtube_url(url):
        return ""
    curl = shutil.which("curl")
    if not curl:
        logger.debug("curl rescue unavailable: curl not on PATH url=%s", url)
        return ""
    seconds = max(1, int(request_timeout_seconds))
    args = [
        curl,
        "-sS",
        "--http2",
        "--compressed",
        "-L",
        "--max-redirs",
        "5",
        "--max-time",
        str(seconds),
        "--user-agent",
        _CURL_CHROME_UA,
    ]
    for header in _CURL_CHROME_HEADERS:
        args.extend(["--header", header])
    args.extend(["--", url])
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=seconds + 5,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.debug("curl rescue failed url=%s error=%s", url, exc)
        return ""
    if result.returncode != 0 or not result.stdout:
        logger.debug(
            "curl rescue non-success url=%s code=%s", url, result.returncode
        )
        return ""
    return result.stdout


def fetch_page_html(url: str, request_timeout_seconds: int) -> str:
    """Fetch page HTML for content extraction.

    Tries the plain requests transport first; on failure (including 403s from
    bot-walled pages) retries once via system curl with Chrome headers. Returns
    "" when both transports fail — the caller keeps its fallback chain.
    """
    html = fetch_text_url(url, request_timeout_seconds)
    if html:
        return html
    return fetch_text_url_via_curl(url, request_timeout_seconds)


def fetch_youtube_oembed(
    url: str, request_timeout_seconds: int
) -> dict[str, str] | None:
    """Fetch YouTube oembed data with retry logic for transient failures."""
    try:

        @_request_retry(
            lambda retry_state: None,
            "YouTube oembed fetch failed, retrying (attempt %d/2) for url=%s",
            url,
        )
        def _fetch_oembed(u: str) -> dict[str, str]:
            endpoint = "https://www.youtube.com/oembed"
            response = requests.get(
                endpoint,
                params={"url": normalize_youtube_watch_url(u), "format": "json"},
                timeout=request_timeout_seconds,
                headers={"accept": "application/json", "User-Agent": "Mozilla/5.0"},
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                return {}
            return {
                "title": str(payload.get("title") or "").strip(),
                "author": str(payload.get("author_name") or "").strip(),
                "thumbnail_url": str(payload.get("thumbnail_url") or "").strip(),
            }

        result = _fetch_oembed(url)
        if not result:
            return None
        return result
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
            if (
                is_video
                and isinstance(node.get("description"), str)
                and node["description"].strip()
            ):
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


def fetch_dearrow_thumbnail(video_id: str, request_timeout_seconds: int) -> str | None:
    """Fetch DeArrow community thumbnail URL for a YouTube video."""
    try:
        resp = requests.get(
            "https://sponsor.ajay.app/api/branding",
            params={"videoID": video_id},
            timeout=request_timeout_seconds,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        thumb = next(
            (
                t
                for t in data.get("thumbnails", [])
                if t.get("locked") or t.get("votes", 0) >= 0
            ),
            None,
        )
        if thumb and not thumb.get("original") and thumb.get("timestamp") is not None:
            return f"https://dearrow-thumb.ajay.app/api/v1/getThumbnail?videoID={video_id}&time={thumb['timestamp']}"
    except Exception as exc:
        logger.debug(
            "dearrow thumbnail fetch failed video_id=%s error=%s", video_id, exc
        )
    return None


def _save_dearrow_thumbnail(video_id: str, thumbnail_url: str) -> None:
    """Persist a DeArrow thumbnail URL to the shared JSON cache."""
    if not video_id or not thumbnail_url:
        return
    try:
        cache_path = Path("data/status/dearrow_thumbnails.json")
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        data: dict[str, str] = {}
        if cache_path.exists():
            try:
                data = json.loads(cache_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        if video_id not in data:
            data[video_id] = thumbnail_url
            cache_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.debug(
            "failed to persist dearrow thumbnail video_id=%s error=%s", video_id, exc
        )


def _save_video_availability(video_id: str, available: bool) -> None:
    """Persist video availability to the shared JSON cache."""
    if not video_id:
        return
    try:
        cache_path = Path("data/status/video_availability.json")
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        data: dict[str, bool] = {}
        if cache_path.exists():
            try:
                data = json.loads(cache_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        data[video_id] = available
        cache_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.debug(
            "failed to persist video availability video_id=%s error=%s", video_id, exc
        )


def extract_youtube_metadata(
    url: str,
    rss_title: str,
    rss_summary: str,
    request_timeout_seconds: int,
) -> dict[str, str]:
    oembed_result = fetch_youtube_oembed(url, request_timeout_seconds)
    oembed = oembed_result or {}
    page_html = fetch_text_url(url, request_timeout_seconds)
    schema_description = extract_youtube_schema_description(page_html)
    description = schema_description or rss_summary.strip()
    video_id = get_youtube_video_id(url)
    dearrow_thumb = (
        fetch_dearrow_thumbnail(video_id, request_timeout_seconds) if video_id else None
    )
    if dearrow_thumb:
        _save_dearrow_thumbnail(video_id, dearrow_thumb)
    available = oembed_result is not None
    if video_id:
        _save_video_availability(video_id, available)
    return {
        "title": rss_title.strip() or oembed.get("title", ""),
        "author": oembed.get("author", ""),
        "description": description,
        "thumbnail_url": oembed.get("thumbnail_url", ""),
        "embed_url": get_youtube_embed_url(url),
        "video_id": video_id,
        "dearrow_thumbnail_url": dearrow_thumb or "",
        "available": available,
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


def fetch_youtube_transcript(video_id: str) -> str | None:
    if not video_id:
        return None
    try:
        fetched = YouTubeTranscriptApi().fetch(video_id, languages=["en"])
        text = " ".join(snippet.text for snippet in fetched if snippet.text).strip()
    except Exception as exc:
        logger.debug(
            "youtube transcript fetch failed video_id=%s error=%s", video_id, exc
        )
        return None
    if len(text) < 300:
        return None
    return text


def build_youtube_transcript_body(metadata: dict[str, str], transcript: str) -> str:
    parts = [transcript.strip()]
    title = (metadata.get("title") or "").strip()
    if title:
        parts.append(f"title: {title}")
    author = (metadata.get("author") or "").strip()
    if author:
        parts.append(f"author: {author}")
    embed_url = (metadata.get("embed_url") or "").strip()
    if embed_url:
        parts.append(f"video: {embed_url}")
    return "\n\n".join(parts).strip()


def parse_with_jina_ai(
    url: str, settings: EnrichmentSettings
) -> tuple[str | None, bool]:
    """Use r.jina.ai as a fallback for blocked sites.

    Uses tenacity to retry on transient failures with exponential backoff.
    """
    if not url or is_youtube_url(url):
        return None, False

    @_request_retry(
        lambda retry_state: None,
        "jina.ai fetch failed, retrying (attempt %d/2) for url=%s",
        url,
    )
    def _fetch_jina(u: str) -> str:
        jina_url = f"https://r.jina.ai/http://{u.replace('https://', '').replace('http://', '')}"
        response = requests.get(
            jina_url,
            timeout=settings.request_timeout_seconds,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        response.raise_for_status()
        return response.text

    try:
        text = _fetch_jina(url)
        if not text or len(text) < 100:
            return None, False
        lines = text.split("\n")
        cleaned_lines = []
        in_header = True
        for line in lines:
            if in_header:
                if (
                    line.startswith("Title:")
                    or line.startswith("URL Source:")
                    or line.startswith("Markdown Content:")
                ):
                    continue
                if line.strip() == "":
                    continue
                in_header = False
            cleaned_lines.append(line)
        cleaned = "\n".join(cleaned_lines).strip()
        if not cleaned or len(cleaned) < 100:
            return None, False
        return truncate_for_storage(cleaned, settings.max_chars), True
    except Exception as exc:
        logger.debug("jina.ai extract failed url=%s error=%s", url, exc)
        return None, False


def parse_with_markdown_new(
    url: str, settings: EnrichmentSettings
) -> tuple[str | None, bool, int, dict[str, Any]]:
    """Use markdown.new as a fallback for blocked sites.

    Returns: (content, success, rate_limit_remaining, response_metadata)
    rate_limit_remaining values:
      -1: unknown / not rate-limited
       0: rate-limited (429)
      -2: rate-limited with retry-after > 24h (stop processing early)
      >0: remaining quota from header
    """
    if not url or is_youtube_url(url):
        return None, False, -1, {}

    def _fetch_markdown_new(u: str) -> tuple[str, int, dict[str, Any]]:
        response = requests.post(
            "https://markdown.new/",
            json={"url": u, "method": "auto"},
            timeout=settings.request_timeout_seconds,
            headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
        )

        rate_limit_remaining = -1
        raw_remaining_header = response.headers.get("x-rate-limit-remaining")
        if "x-rate-limit-remaining" in response.headers:
            try:
                rate_limit_remaining = int(raw_remaining_header or "")
            except (ValueError, TypeError):
                pass
        response_meta = {
            "status_code": response.status_code,
            "x_rate_limit_remaining": raw_remaining_header,
            "url": u,
        }

        if response.status_code == 429:
            retry_after_seconds = get_retry_after_seconds(response)
            if retry_after_seconds > 24 * 3600:
                logger.warning(
                    "markdown.new rate limit exceeded and retry-after is too long (%ss) for url=%s",
                    retry_after_seconds,
                    u,
                )
                return "", -2, response_meta
            logger.warning("markdown.new rate limit exceeded (429) for url=%s", u)
            raise requests.HTTPError(f"Rate limited (429) for {u}", response=response)

        response.raise_for_status()
        payload = response.json()
        if not payload.get("success"):
            return "", rate_limit_remaining, response_meta
        content = payload.get("content", "").strip()
        if not content or len(content) < 100:
            return "", rate_limit_remaining, response_meta
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                content = parts[2].strip()
        return (
            truncate_for_storage(content, settings.max_chars),
            rate_limit_remaining,
            response_meta,
        )

    try:
        content, rate_limit_remaining, response_meta = _fetch_markdown_new(url)
        if content:
            return content, True, rate_limit_remaining, response_meta
        return None, False, rate_limit_remaining, response_meta
    except requests.HTTPError as exc:
        if (
            getattr(exc, "response", None) is not None
            and exc.response.status_code == 429
        ):
            response = exc.response
            return (
                None,
                False,
                0,
                {
                    "status_code": response.status_code,
                    "x_rate_limit_remaining": response.headers.get(
                        "x-rate-limit-remaining"
                    ),
                    "url": url,
                },
            )
        logger.debug("markdown.new extract failed url=%s error=%s", url, exc)
        return None, False, -1, {"url": url}
    except Exception as exc:
        logger.debug("markdown.new extract failed url=%s error=%s", url, exc)
        return None, False, -1, {"url": url}


def parse_with_compress_new(
    url: str, settings: EnrichmentSettings
) -> tuple[str | None, bool]:
    """Fallback extractor using compress.new when markdown.new is rate-limited."""
    if not url or is_youtube_url(url):
        return None, False
    try:
        response = requests.post(
            "https://compress.new/?main_only=true",
            json={"url": url, "method": "auto"},
            timeout=settings.request_timeout_seconds,
            headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
        )
        response.raise_for_status()
        payload = response.json()
        content = str(payload.get("content") or "").strip()
        if not content or len(content) < 100:
            return None, False
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                content = parts[2].strip()
        return truncate_for_storage(content, settings.max_chars), True
    except Exception as exc:
        logger.debug("compress.new extract failed url=%s error=%s", url, exc)
        return None, False


def supports_markdown_family(source_id: str) -> bool:
    normalized = (source_id or "").strip().lower()
    return normalized == "openai-blog" or normalized.startswith("openai")


def get_retry_after_seconds(response: requests.Response | None) -> int:
    if response is None:
        return 0
    value = (response.headers.get("retry-after") or "").strip()
    if not value:
        return 0
    try:
        return max(0, int(value))
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return max(0, int((dt - now).total_seconds()))
    except Exception:
        return 0


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


