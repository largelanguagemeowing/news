from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Literal
from urllib.parse import urlparse

import requests
import tenacity
import trafilatura
from bs4 import BeautifulSoup

from app.models import ExtractionMethod


logger = logging.getLogger("news.pipeline")


MethodName = Literal["youtube", "trafilatura", "markdown_new", "jina", "defuddle", "rss"]


@dataclass(frozen=True)
class EnrichmentSettings:
    defuddle_enabled: bool
    defuddle_timeout_seconds: int
    max_chars: int
    request_timeout_seconds: int
    youtube_source_ids: set[str]


@dataclass(frozen=True)
class EnrichOptions:
    only_method: MethodName | None = None
    markdown_new_budget_remaining: int | None = None
    stop_on_markdown_rate_limit: bool = False


@dataclass(frozen=True)
class EnrichResult:
    body: str
    method: MethodName
    rate_limit_remaining: int = -1
    markdown_new_rate_limited: bool = False
    retry_after_seconds: int = 0
    stop_processing: bool = False
    attempts: tuple[dict, ...] = ()


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


def fetch_text_url(url: str, request_timeout_seconds: int) -> str:
    """Fetch page text with retry logic for transient failures."""
    try:
        @tenacity.retry(
            stop=tenacity.stop_after_attempt(2),
            wait=tenacity.wait_exponential(multiplier=1, min=2, max=10),
            retry=tenacity.retry_if_exception_type((requests.RequestException, requests.HTTPError)),
            retry_error_callback=lambda retry_state: "",
            before_sleep=lambda retry_state: logger.debug(
                "Page fetch failed, retrying (attempt %d/2) for url=%s",
                retry_state.attempt_number,
                url,
            ),
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


def fetch_youtube_oembed(url: str, request_timeout_seconds: int) -> dict[str, str] | None:
    """Fetch YouTube oembed data with retry logic for transient failures."""
    try:
        @tenacity.retry(
            stop=tenacity.stop_after_attempt(2),
            wait=tenacity.wait_exponential(multiplier=1, min=2, max=10),
            retry=tenacity.retry_if_exception_type((requests.RequestException, requests.HTTPError)),
            retry_error_callback=lambda retry_state: None,
            before_sleep=lambda retry_state: logger.debug(
                "YouTube oembed fetch failed, retrying (attempt %d/2) for url=%s",
                retry_state.attempt_number,
                url,
            ),
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


def extract_youtube_metadata(
    url: str,
    rss_title: str,
    rss_summary: str,
    request_timeout_seconds: int,
) -> dict[str, str]:
    oembed = fetch_youtube_oembed(url, request_timeout_seconds) or {}
    page_html = fetch_text_url(url, request_timeout_seconds)
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


def parse_with_defuddle(url: str, settings: EnrichmentSettings) -> tuple[str | None, bool]:
    """Return extracted content and whether defuddle was used successfully."""
    if not url:
        return None, False
    if not settings.defuddle_enabled:
        return None, False
    if not shutil.which("defuddle"):
        logger.warning("defuddle not found on PATH; continuing without enrichment")
        return None, False
    try:
        result = subprocess.run(
            ["defuddle", "parse", url, "--json"],
            capture_output=True,
            text=True,
            timeout=settings.defuddle_timeout_seconds,
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
    return truncate_for_storage(extracted, settings.max_chars), True


def parse_with_trafilatura(url: str, settings: EnrichmentSettings) -> tuple[str | None, bool]:
    if not url or is_youtube_url(url):
        return None, False
    html = fetch_text_url(url, settings.request_timeout_seconds)
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
    return truncate_for_storage(cleaned, settings.max_chars), True


def parse_with_jina_ai(url: str, settings: EnrichmentSettings) -> tuple[str | None, bool]:
    """Use r.jina.ai as a fallback for blocked sites.

    Uses tenacity to retry on transient failures with exponential backoff.
    """
    if not url or is_youtube_url(url):
        return None, False

    @tenacity.retry(
        stop=tenacity.stop_after_attempt(2),
        wait=tenacity.wait_exponential(multiplier=1, min=2, max=10),
        retry=tenacity.retry_if_exception_type((requests.RequestException, requests.HTTPError)),
        retry_error_callback=lambda retry_state: None,
        before_sleep=lambda retry_state: logger.debug(
            "jina.ai fetch failed, retrying (attempt %d/2) for url=%s",
            retry_state.attempt_number,
            url,
        ),
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
                if line.startswith("Title:") or line.startswith("URL Source:") or line.startswith("Markdown Content:"):
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


def parse_with_markdown_new(url: str, settings: EnrichmentSettings) -> tuple[str | None, bool, int]:
    """Use markdown.new as a fallback for blocked sites.

    Returns: (content, success, rate_limit_remaining)
    rate_limit_remaining values:
      -1: unknown / not rate-limited
       0: rate-limited (429)
      -2: rate-limited with retry-after > 24h (stop processing early)
      >0: remaining quota from header
    """
    if not url or is_youtube_url(url):
        return None, False, -1

    @tenacity.retry(
        stop=tenacity.stop_after_attempt(3),
        wait=tenacity.wait_exponential(multiplier=2, min=4, max=30),
        retry=tenacity.retry_if_exception_type(requests.HTTPError),
        retry_error_callback=lambda retry_state: None,
        before_sleep=lambda retry_state: logger.warning(
            "markdown.new rate limited, retrying (attempt %d/%d) for url=%s",
            retry_state.attempt_number,
            3,
            url,
        ),
    )
    def _fetch_markdown_new(u: str) -> tuple[str, int]:
        response = requests.post(
            "https://markdown.new/",
            json={"url": u, "method": "auto"},
            timeout=settings.request_timeout_seconds,
            headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
        )

        rate_limit_remaining = -1
        if "x-rate-limit-remaining" in response.headers:
            try:
                rate_limit_remaining = int(response.headers["x-rate-limit-remaining"])
            except (ValueError, TypeError):
                pass

        if response.status_code == 429:
            retry_after_seconds = get_retry_after_seconds(response)
            if retry_after_seconds > 24 * 3600:
                logger.warning(
                    "markdown.new rate limit exceeded and retry-after is too long (%ss) for url=%s",
                    retry_after_seconds,
                    u,
                )
                return "", -2
            logger.warning("markdown.new rate limit exceeded (429) for url=%s", u)
            raise requests.HTTPError(f"Rate limited (429) for {u}", response=response)

        response.raise_for_status()
        payload = response.json()
        if not payload.get("success"):
            return "", rate_limit_remaining
        content = payload.get("content", "").strip()
        if not content or len(content) < 100:
            return "", rate_limit_remaining
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                content = parts[2].strip()
        return truncate_for_storage(content, settings.max_chars), rate_limit_remaining

    try:
        content, rate_limit_remaining = _fetch_markdown_new(url)
        if content:
            return content, True, rate_limit_remaining
        return None, False, rate_limit_remaining
    except requests.HTTPError as exc:
        if getattr(exc, "response", None) is not None and exc.response.status_code == 429:
            return None, False, 0
        logger.debug("markdown.new extract failed url=%s error=%s", url, exc)
        return None, False, -1
    except Exception as exc:
        logger.debug("markdown.new extract failed url=%s error=%s", url, exc)
        return None, False, -1


def parse_with_compress_new(url: str, settings: EnrichmentSettings) -> tuple[str | None, bool]:
    """Fallback extractor using compress.new when markdown.new is rate-limited."""
    if not url or is_youtube_url(url):
        return None, False
    try:
        response = requests.post(
            "https://compress.new/",
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


def enrich_with_policy(
    url: str,
    source_id: str,
    title: str,
    body: str,
    settings: EnrichmentSettings,
    options: EnrichOptions | None = None,
) -> EnrichResult:
    current_body = str(body or "").strip()
    opts = options or EnrichOptions()
    rate_limit_remaining = -1
    attempts: list[dict] = []

    methods_order: list[MethodName] = [
        ExtractionMethod.YOUTUBE.value,
        ExtractionMethod.TRAFILATURA.value,
        ExtractionMethod.MARKDOWN_NEW.value,
        ExtractionMethod.JINA.value,
        ExtractionMethod.DEFUDDLE.value,
    ]
    methods_to_try = [opts.only_method] if opts.only_method else methods_order

    for method in methods_to_try:
        if method == ExtractionMethod.YOUTUBE.value:
            if is_youtube_url(url) or source_id in settings.youtube_source_ids:
                t0 = time.monotonic()
                youtube_meta = extract_youtube_metadata(
                    url,
                    title,
                    current_body,
                    settings.request_timeout_seconds,
                )
                yt_body = build_youtube_body(youtube_meta, current_body)
                dur = round((time.monotonic() - t0) * 1000, 2)
                attempts.append({"method": method, "status": "success", "duration_ms": dur, "error_message": None, "output_chars": len(yt_body)})
                return EnrichResult(
                    body=yt_body,
                    method=ExtractionMethod.YOUTUBE.value,
                    attempts=tuple(attempts),
                )
            else:
                attempts.append({"method": method, "status": "skipped", "duration_ms": 0, "error_message": None, "output_chars": None})
            continue

        if method == ExtractionMethod.TRAFILATURA.value:
            t0 = time.monotonic()
            trafilatura_body, used = parse_with_trafilatura(url, settings)
            dur = round((time.monotonic() - t0) * 1000, 2)
            if used and trafilatura_body:
                attempts.append({"method": method, "status": "success", "duration_ms": dur, "error_message": None, "output_chars": len(trafilatura_body)})
                return EnrichResult(body=trafilatura_body, method=ExtractionMethod.TRAFILATURA.value, attempts=tuple(attempts))
            attempts.append({"method": method, "status": "failed", "duration_ms": dur, "error_message": None, "output_chars": None})
            continue

        if method == ExtractionMethod.MARKDOWN_NEW.value:
            if not supports_markdown_family(source_id):
                attempts.append({"method": method, "status": "skipped", "duration_ms": 0, "error_message": None, "output_chars": None})
                continue
            if opts.markdown_new_budget_remaining is not None and opts.markdown_new_budget_remaining <= 0:
                attempts.append({"method": method, "status": "skipped", "duration_ms": 0, "error_message": "budget exhausted", "output_chars": None})
                continue
            t0 = time.monotonic()
            markdown_new_body, used, rate_limit_remaining = parse_with_markdown_new(url, settings)
            dur = round((time.monotonic() - t0) * 1000, 2)
            if used and markdown_new_body:
                attempts.append({"method": method, "status": "success", "duration_ms": dur, "error_message": None, "output_chars": len(markdown_new_body)})
                return EnrichResult(
                    body=markdown_new_body,
                    method=ExtractionMethod.MARKDOWN_NEW.value,
                    rate_limit_remaining=rate_limit_remaining,
                    attempts=tuple(attempts),
                )

            attempts.append({"method": method, "status": "failed", "duration_ms": dur, "error_message": None, "output_chars": None})

            if rate_limit_remaining == -2:
                return EnrichResult(
                    body=current_body,
                    method=ExtractionMethod.RSS.value,
                    rate_limit_remaining=-2,
                    markdown_new_rate_limited=True,
                    stop_processing=True,
                    attempts=tuple(attempts),
                )

            if rate_limit_remaining == 0:
                t0c = time.monotonic()
                compress_body, compress_used = parse_with_compress_new(url, settings)
                durc = round((time.monotonic() - t0c) * 1000, 2)
                if compress_used and compress_body:
                    attempts.append({"method": ExtractionMethod.COMPRESS_NEW.value, "status": "success", "duration_ms": durc, "error_message": None, "output_chars": len(compress_body)})
                    return EnrichResult(
                        body=compress_body,
                        method=ExtractionMethod.COMPRESS_NEW.value,
                        rate_limit_remaining=0,
                        markdown_new_rate_limited=True,
                        attempts=tuple(attempts),
                    )
                attempts.append({"method": ExtractionMethod.COMPRESS_NEW.value, "status": "failed", "duration_ms": durc, "error_message": None, "output_chars": None})
                if opts.stop_on_markdown_rate_limit:
                    return EnrichResult(
                        body=current_body,
                        method=ExtractionMethod.RSS.value,
                        rate_limit_remaining=0,
                        markdown_new_rate_limited=True,
                        attempts=tuple(attempts),
                    )
            continue

        if method == ExtractionMethod.COMPRESS_NEW.value:
            if not supports_markdown_family(source_id):
                attempts.append({"method": method, "status": "skipped", "duration_ms": 0, "error_message": None, "output_chars": None})
                continue
            t0 = time.monotonic()
            compress_body, used = parse_with_compress_new(url, settings)
            dur = round((time.monotonic() - t0) * 1000, 2)
            if used and compress_body:
                attempts.append({"method": method, "status": "success", "duration_ms": dur, "error_message": None, "output_chars": len(compress_body)})
                return EnrichResult(body=compress_body, method=ExtractionMethod.COMPRESS_NEW.value, attempts=tuple(attempts))
            attempts.append({"method": method, "status": "failed", "duration_ms": dur, "error_message": None, "output_chars": None})
            continue

        if method == ExtractionMethod.JINA.value:
            t0 = time.monotonic()
            jina_body, used = parse_with_jina_ai(url, settings)
            dur = round((time.monotonic() - t0) * 1000, 2)
            if used and jina_body:
                attempts.append({"method": method, "status": "success", "duration_ms": dur, "error_message": None, "output_chars": len(jina_body)})
                return EnrichResult(body=jina_body, method=ExtractionMethod.JINA.value, attempts=tuple(attempts))
            attempts.append({"method": method, "status": "failed", "duration_ms": dur, "error_message": None, "output_chars": None})
            continue

        if method == ExtractionMethod.DEFUDDLE.value:
            t0 = time.monotonic()
            defuddle_body, used = parse_with_defuddle(url, settings)
            dur = round((time.monotonic() - t0) * 1000, 2)
            if used and defuddle_body:
                attempts.append({"method": method, "status": "success", "duration_ms": dur, "error_message": None, "output_chars": len(defuddle_body)})
                return EnrichResult(body=defuddle_body, method=ExtractionMethod.DEFUDDLE.value, attempts=tuple(attempts))
            attempts.append({"method": method, "status": "failed", "duration_ms": dur, "error_message": None, "output_chars": None})
            continue

    return EnrichResult(body=current_body, method=ExtractionMethod.RSS.value, rate_limit_remaining=rate_limit_remaining, attempts=tuple(attempts))


def enrich_article_content(
    url: str,
    source_id: str,
    title: str,
    body: str,
    settings: EnrichmentSettings,
) -> tuple[str, str, list[dict]]:
    result = enrich_with_policy(url, source_id, title, body, settings)
    return result.body, result.method, list(result.attempts)
