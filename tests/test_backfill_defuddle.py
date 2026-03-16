from __future__ import annotations

from uuid import uuid4

import pytest

import app.db as db_module
from app.db import get_connection, init_db, transaction
from app.jobs.backfill_defuddle import backfill_articles
from app.jobs import pipeline
from app.utils import normalize_text, sha1_hexdigest, simhash64


@pytest.fixture(autouse=True)
def isolate_db() -> None:
    if db_module.DB_PATH.exists():
        db_module.DB_PATH.unlink()


def _seed_article() -> int:
    conn = get_connection()
    init_db(conn)
    article_url = f"https://example.com/article-{uuid4().hex[:10]}"
    with transaction(conn):
        conn.execute(
            """
            INSERT OR REPLACE INTO sources (source_id, name, feed_url, default_category, enabled)
            VALUES ('test-source', 'Test Source', 'https://example.com/feed.xml', 'general', 1)
            """
        )
        title = "Sample title"
        body = "short"
        body_norm = normalize_text(body)
        cursor = conn.execute(
            """
            INSERT INTO articles (
              source_id, url, canonical_url, title, title_norm, body, body_norm,
              published_at, fetched_at, title_hash, body_hash, simhash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "test-source",
                article_url,
                article_url,
                title,
                normalize_text(title),
                body,
                body_norm,
                "2026-03-15T00:00:00+00:00",
                "2026-03-15T00:00:00+00:00",
                sha1_hexdigest(normalize_text(title)),
                sha1_hexdigest(body_norm),
                str(simhash64(body_norm or normalize_text(title))),
            ),
        )
    article_id = int(cursor.lastrowid)
    conn.close()
    return article_id


def test_backfill_updates_existing_article(monkeypatch) -> None:
    article_id = _seed_article()
    monkeypatch.setattr(pipeline, "DEFUDDLE_ENABLED", True)
    monkeypatch.setattr(
        pipeline,
        "parse_with_trafilatura",
        lambda _url: (None, False),
    )
    monkeypatch.setattr(
        pipeline,
        "parse_with_markdown_new",
        lambda _url: (None, False, -1),
    )
    monkeypatch.setattr(
        pipeline,
        "parse_with_jina_ai",
        lambda _url: (None, False),
    )
    monkeypatch.setattr(
        pipeline,
        "parse_with_defuddle",
        lambda _url: ("This is enriched body content from defuddle.", True),
    )

    metrics = backfill_articles(limit=10, only_missing=False, dry_run=False)
    assert metrics["attempted"] >= 1
    assert metrics["updated"] >= 1

    conn = get_connection()
    row = conn.execute("SELECT body FROM articles WHERE article_id = ?", (article_id,)).fetchone()
    conn.close()
    assert row is not None
    assert "enriched body content" in row["body"]


def test_backfill_dry_run_does_not_write(monkeypatch) -> None:
    article_id = _seed_article()
    monkeypatch.setattr(pipeline, "DEFUDDLE_ENABLED", True)
    monkeypatch.setattr(
        pipeline,
        "parse_with_trafilatura",
        lambda _url: (None, False),
    )
    monkeypatch.setattr(
        pipeline,
        "parse_with_markdown_new",
        lambda _url: (None, False, -1),
    )
    monkeypatch.setattr(
        pipeline,
        "parse_with_jina_ai",
        lambda _url: (None, False),
    )
    monkeypatch.setattr(
        pipeline,
        "parse_with_defuddle",
        lambda _url: ("Dry run content that should not persist.", True),
    )

    metrics = backfill_articles(limit=10, only_missing=False, dry_run=True)
    assert metrics["updated"] == 0
    assert metrics["enriched"] >= 1

    conn = get_connection()
    row = conn.execute("SELECT body FROM articles WHERE article_id = ?", (article_id,)).fetchone()
    conn.close()
    assert row is not None
    assert row["body"] == "short"


def test_backfill_all_items_ignores_limit(monkeypatch) -> None:
    _seed_article()
    _seed_article()
    monkeypatch.setattr(pipeline, "DEFUDDLE_ENABLED", True)
    monkeypatch.setattr(
        pipeline,
        "parse_with_trafilatura",
        lambda _url: (None, False),
    )
    monkeypatch.setattr(
        pipeline,
        "parse_with_markdown_new",
        lambda _url: (None, False, -1),
    )
    monkeypatch.setattr(
        pipeline,
        "parse_with_jina_ai",
        lambda _url: (None, False),
    )
    monkeypatch.setattr(
        pipeline,
        "parse_with_defuddle",
        lambda _url: ("Bulk backfill content.", True),
    )

    metrics = backfill_articles(limit=1, all_items=True, only_missing=False, dry_run=True)
    assert metrics["all_items"] is True
    assert metrics["attempted"] >= 2
