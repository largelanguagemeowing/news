"""X Article reading through FxTwitter.

Tests cross the deep module's interface (is_x_article_url, parent_tweet_id,
fetch_x_article) with the HTTP call swapped for stand-ins, plus the chain
integration: registration, order construction, and the media-URL exclusion
applied by the enrich candidate query.
"""

from __future__ import annotations

import sqlite3

import pytest

from app.db import init_db
from app.jobs import enrich_pipeline, pipeline, x_articles
from app.models import ExtractionMethod


# --- URL detection ---------------------------------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://x.com/i/article/2091415861659615232", True),
        ("http://x.com/i/article/2010826317634420736", True),
        ("https://twitter.com/i/article/2091415861659615232", True),
        ("https://x.com/i/article/2091415861659615232/", True),
        ("https://x.com/i/article/notanumber", False),
        ("https://x.com/badlogicgames/status/2091549486954807456", False),
        ("https://example.com/i/article/123", False),
        ("", False),
    ],
)
def test_is_x_article_url(url: str, expected: bool) -> None:
    assert x_articles.is_x_article_url(url) is expected


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://video.twimg.com/amplify_video/2030284224985550848/vid/a.mp4", True),
        ("https://www.youtube.com/@juliaturc1?si=LkcY", True),
        ("https://www.youtube.com/channel/UCsBjURrPoezykLs9EqgamOA", True),
        ("https://www.youtube.com/watch?v=abc", False),
        ("https://example.com/article", False),
        ("", False),
    ],
)
def test_is_media_url(url: str, expected: bool) -> None:
    assert x_articles.is_media_url(url) is expected


# --- parent tweet id -------------------------------------------------------


@pytest.mark.parametrize(
    "guid,expected",
    [
        ("https://x.com/badlogicgames/status/2091549486954807456", "2091549486954807456"),
        ("https://twitter.com/user/status/12345", "12345"),
        ("https://example.com/feed-item", None),
        ("", None),
        (None, None),
    ],
)
def test_parent_tweet_id(guid, expected) -> None:
    assert x_articles.parent_tweet_id(guid) == expected


# --- fetch_x_article (network swapped at the module seam) -------------------


class _FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def test_fetch_x_article_composes_title_and_preview(monkeypatch) -> None:
    payload = {
        "status": {
            "text": "recommended reading",
            "article": {
                "title": "Joy & Curiosity #96",
                "preview_text": "Joy & Curiosity is my newsletter where I write about coding agents, simulators, and the strange joy of shipping small tools every week.",
            },
        }
    }
    monkeypatch.setattr(
        x_articles.requests, "get", lambda *a, **k: _FakeResponse(payload)
    )
    body = x_articles.fetch_x_article(
        "https://x.com/i/article/2091415861659615232",
        "https://x.com/badlogicgames/status/2091549486954807456",
    )
    assert body is not None
    assert "Joy & Curiosity #96" in body
    assert "Joy & Curiosity is my newsletter" in body


def test_fetch_x_article_returns_none_without_feed_guid(monkeypatch) -> None:
    def _boom(*a, **k):
        raise AssertionError("must not hit the network without a parent tweet id")

    monkeypatch.setattr(x_articles.requests, "get", _boom)
    assert x_articles.fetch_x_article("https://x.com/i/article/123", None) is None


def test_fetch_x_article_returns_none_on_http_error(monkeypatch) -> None:
    def _raise(*a, **k):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(x_articles.requests, "get", _raise)
    assert (
        x_articles.fetch_x_article(
            "https://x.com/i/article/123",
            "https://x.com/user/status/456",
        )
        is None
    )


def test_fetch_x_article_misses_too_short_body(monkeypatch) -> None:
    payload = {"status": {"article": {"title": "Hi", "preview_text": ""}}}
    monkeypatch.setattr(
        x_articles.requests, "get", lambda *a, **k: _FakeResponse(payload)
    )
    assert (
        x_articles.fetch_x_article(
            "https://x.com/i/article/123", "https://x.com/user/status/456"
        )
        is None
    )


# --- chain integration ------------------------------------------------------


def test_fxtwitter_runs_before_other_methods_for_article_urls(monkeypatch) -> None:
    """Behavioural: for an article URL the FxTwitter branch runs first and
    none of the login-walled fallback methods are even attempted."""
    body = "Joy & Curiosity #96\n\nA long preview text " * 5

    def other_methods_boom(*args, **kwargs):
        raise AssertionError("fallback methods must not run when FxTwitter hits")

    monkeypatch.setattr(x_articles, "fetch_x_article", lambda *a, **k: body)
    monkeypatch.setattr(pipeline, "parse_with_trafilatura", other_methods_boom)
    got_body, method, _rl, _limited = pipeline.enrich_with_policy(
        "https://x.com/i/article/123",
        "marios-rec-reading",
        "recommended reading",
        "rss body",
        feed_guid="https://x.com/user/status/456",
    )
    assert method == ExtractionMethod.FXTWITTER.value
    assert got_body == pipeline.truncate_for_storage(body)


def test_fxtwitter_not_attempted_for_normal_urls(monkeypatch) -> None:
    """Behavioural: non-article URLs never touch the FxTwitter extractor."""
    seen = []

    def fetch_boom(*args, **kwargs):
        seen.append("fxtwitter")
        return None

    monkeypatch.setattr(x_articles, "fetch_x_article", fetch_boom)
    monkeypatch.setattr(pipeline, "parse_with_trafilatura", lambda *a, **k: (None, False))
    got_body, method, _rl, _limited = pipeline.enrich_with_policy(
        "https://example.com/post",
        "simon-willison",
        "a post",
        "rss body",
        feed_guid="https://x.com/user/status/456",
    )
    assert seen == []
    assert method != ExtractionMethod.FXTWITTER.value


def test_chain_uses_fxtwitter_attempt_for_article_url(monkeypatch) -> None:
    """Behavioural: enrich_with_policy returns the FxTwitter body + method."""
    body = "Joy & Curiosity #96\n\nA long preview text " * 5
    monkeypatch.setattr(x_articles, "fetch_x_article", lambda *a, **k: body)
    got_body, method, _rl, _limited = pipeline.enrich_with_policy(
        "https://x.com/i/article/123",
        "marios-rec-reading",
        "recommended reading",
        "rss body",
        feed_guid="https://x.com/user/status/456",
        only_method=ExtractionMethod.FXTWITTER.value,
    )
    assert method == ExtractionMethod.FXTWITTER.value
    assert got_body == pipeline.truncate_for_storage(body)


def test_chain_falls_through_when_fxtwitter_misses(monkeypatch) -> None:
    seen = []

    def fetch_miss(*args, **kwargs):
        seen.append("fxtwitter")
        return None

    monkeypatch.setattr(x_articles, "fetch_x_article", fetch_miss)
    monkeypatch.setattr(
        pipeline, "parse_with_trafilatura", lambda *a, **k: ("traf body", True)
    )
    got_body, method, _rl, _limited = pipeline.enrich_with_policy(
        "https://x.com/i/article/123",
        "marios-rec-reading",
        "recommended reading",
        "rss body",
        feed_guid="https://x.com/user/status/456",
    )
    assert seen == ["fxtwitter"]
    assert method == ExtractionMethod.TRAFILATURA.value
    assert got_body == "traf body"


# --- media exclusion in the enrich candidate query ---------------------------


@pytest.fixture()
def conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    yield conn
    conn.close()


def _seed_article(conn, article_id: int, url: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO sources (source_id, name, feed_url, default_category, enabled) "
        "VALUES ('test-src', 'Test Source', 'https://example.com/feed.xml', 'ai', 1)"
    )
    title = f"Title {article_id}"
    conn.execute(
        """
        INSERT INTO articles
          (source_id, url, canonical_url, title, title_norm, body, body_norm,
           published_at, fetched_at, title_hash, body_hash, simhash, extraction_method, feed_guid)
        VALUES ('test-src', ?, ?, ?, ?, ?, ?, '2026-01-01T00:00:00Z',
                '2026-01-01T00:00:00Z', 'th', 'bh', 'sh', 'rss', NULL)
        """,
        (url, url, title, title.lower(), "rss body", "rss body"),
    )


def test_media_urls_excluded_from_candidates(conn, monkeypatch) -> None:
    _seed_article(conn, 1, "https://example.com/post")
    _seed_article(conn, 2, "https://video.twimg.com/amplify_video/123/vid/a.mp4")
    _seed_article(conn, 3, "https://www.youtube.com/@somechannel")

    monkeypatch.setattr(
        enrich_pipeline, "_enrich_with_rate_limit", lambda *a, **k: ("rss body", "rss", -1)
    )
    enrich_pipeline._enrich_articles(conn, "run-1", limit=None)
    attempted = [
        r["article_url"] for r in conn.execute("SELECT article_url FROM article_enrichment_attempts").fetchall()
    ]
    assert attempted == ["https://example.com/post"]


def test_feed_guid_reaches_the_enrichment_chain(conn, monkeypatch) -> None:
    _seed_article(conn, 1, "https://x.com/i/article/123")
    conn.execute(
        "UPDATE articles SET feed_guid = 'https://x.com/user/status/456' WHERE article_id = 1"
    )
    conn.commit()

    captured = {}

    def fake_enrich(url, source_id, title, body, max_markdown_new, markdown_new_used, only_method=None, feed_guid=None):
        captured["url"] = url
        captured["feed_guid"] = feed_guid
        return ("enriched body", "fxtwitter", -1)

    monkeypatch.setattr(enrich_pipeline, "_enrich_with_rate_limit", fake_enrich)
    enrich_pipeline._enrich_articles(conn, "run-1", limit=None)
    assert captured["url"] == "https://x.com/i/article/123"
    assert captured["feed_guid"] == "https://x.com/user/status/456"
