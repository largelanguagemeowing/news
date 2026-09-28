"""X Article (x.com/i/article/<id>) reading via the public FxTwitter API.

X Articles are login-walled: every logged-out route (browser, syndication,
guest GraphQL, jina, markdown.new) returns only the login-wall shell or a
preview. The one unauthenticated source that returns *some* content is the
public FxTwitter API, which resolves an article through its **parent tweet**
(the post that links the article) and exposes `article.title` and
`article.preview_text`. The full body requires authenticated X API v2 keys
(tweet.fields=article) and is out of scope here.

Module design: a deep module — a small three-function interface hiding URL
detection, guid parsing, the HTTP call, and body composition. The parent
tweet id comes from the feed entry guid (e.g. `x.com/<user>/status/<id>`),
captured at ingest into `articles.feed_guid` and passed through the
enrichment chain via `ExtractionContext.feed_guid`.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

import requests

logger = logging.getLogger("news.pipeline")

FXTWITTER_API = "https://api.fxtwitter.com"

_ARTICLE_URL_RE = re.compile(r"^https?://(?:www\.)?(?:x|twitter)\.com/i/article/\d+/?$")
_STATUS_URL_RE = re.compile(r"/status/(\d+)")
_MIN_BODY_CHARS = 100

# URL patterns that are media assets or hub pages, not articles — there is no
# text to extract and every attempt is a wasted HTTP round trip.
MEDIA_URL_PATTERNS = (
    re.compile(r"^https?://video\.twimg\.com/"),
    re.compile(r"^https?://(?:www\.)?youtube\.com/@[^/]+/?$"),
    re.compile(r"^https?://(?:www\.)?youtube\.com/channel/[^/?]+$"),
)


def is_x_article_url(url: str) -> bool:
    """True if the URL points at an X Article page (x.com/i/article/<id>)."""
    return bool(_ARTICLE_URL_RE.match(str(url or "").strip()))


def is_media_url(url: str) -> bool:
    """True for media assets / hub pages that can never be text-enriched."""
    stripped = str(url or "").strip()
    if "?" in stripped:
        base = stripped.split("?", 1)[0]
    else:
        base = stripped
    return any(p.match(base) for p in MEDIA_URL_PATTERNS)


def media_url_exclusion_sql() -> str:
    """SQL fragment excluding media/hub URLs from enrichment candidate queries."""
    return (
        "AND a.url NOT LIKE 'https://video.twimg.com/%' "
        "AND a.url NOT LIKE 'http://video.twimg.com/%' "
        "AND a.url NOT LIKE '%youtube.com/@%' "
        "AND a.url NOT LIKE '%youtube.com/channel/%'"
    )


def parent_tweet_id(feed_guid: str | None) -> str | None:
    """Extract the parent tweet id from a feed entry guid.

    Feeds that carry X Articles (e.g. mariozechner.at/recommended-reading)
    point <link> at the article page but keep the sharing tweet's permalink
    in the guid. Returns the numeric tweet id, or None when the guid is
    absent or not a status permalink.
    """
    guid = str(feed_guid or "").strip()
    if not guid:
        return None
    path = urlparse(guid).path
    match = _STATUS_URL_RE.search(path)
    return match.group(1) if match else None


def fetch_x_article(
    article_url: str,
    feed_guid: str | None,
    request_timeout_seconds: float = 30.0,
) -> str | None:
    """Fetch an X Article's title + preview text via FxTwitter.

    Requires the parent tweet id (from the feed guid); without it there is
    no known route to the article body, so this returns None. Returns the
    composed article text (title, preview, canonical link) or None on any
    miss — the chain treats None as "method missed" and falls through.
    """
    if not is_x_article_url(article_url):
        return None
    tweet_id = parent_tweet_id(feed_guid)
    if not tweet_id:
        logger.debug(
            "FxTwitter skip url=%s reason=no_parent_tweet_id", article_url
        )
        return None

    api_url = f"{FXTWITTER_API}/2/thread/{tweet_id}"
    try:
        response = requests.get(
            api_url,
            timeout=request_timeout_seconds,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        logger.debug(
            "FxTwitter fetch failed url=%s tweet_id=%s error=%s",
            article_url,
            tweet_id,
            exc,
        )
        return None

    status = payload.get("status") or payload.get("tweet") or {}
    # The article sits on the tweet that *is* the article, or on the article
    # quoted by the sharing tweet ("recommended reading" style posts).
    article = status.get("article") or (status.get("quote") or {}).get("article") or {}
    title = str(article.get("title") or "").strip()
    preview = str(article.get("preview_text") or "").strip()
    body = "\n\n".join(part for part in (title, preview) if part)
    if len(body) < _MIN_BODY_CHARS:
        logger.debug(
            "FxTwitter miss url=%s tweet_id=%s body_chars=%d",
            article_url,
            tweet_id,
            len(body),
        )
        return None
    logger.info(
        "FxTwitter success url=%s tweet_id=%s title=%s body_chars=%d",
        article_url,
        tweet_id,
        title[:80],
        len(body),
    )
    return body
