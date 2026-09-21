"""Incremental-persistence behaviour of the enrich batch runner.

Tests cross the deepened module's interface (_enrich_articles) with an
in-memory SQLite stand-in and the extraction chain swapped at the existing
seam, asserting what lands in the DB — not internal flush state.
"""

from __future__ import annotations

import sqlite3

import pytest

from app.db import init_db
from app.jobs import enrich_pipeline


@pytest.fixture()
def conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    yield conn
    conn.close()


def _seed(conn, n: int, body_template: str = "rss body %d") -> None:
    conn.execute(
        "INSERT OR IGNORE INTO sources (source_id, name, feed_url, default_category, enabled) "
        "VALUES ('test-src', 'Test Source', 'https://example.com/feed.xml', 'ai', 1)"
    )
    for i in range(n):
        body = body_template % i
        title = f"Title {i}"
        conn.execute(
            """
            INSERT INTO articles
              (source_id, url, canonical_url, title, title_norm, body, body_norm,
               published_at, fetched_at, title_hash, body_hash, simhash)
            VALUES ('test-src', ?, ?, ?, ?, ?, ?, '2026-01-01T00:00:00Z',
                    '2026-01-01T00:00:00Z', 'th', 'bh', 'sh')
            """,
            (f"https://example.com/{i}", f"https://example.com/{i}", title, title.lower(), body, body),
        )
    conn.commit()


def _fake_enrich(results):
    """Build an enrich_with_rate_limit replacement from a result list; the last
    result repeats, or a callable can end with an exception instance."""
    calls = {"n": 0}

    def fake(*_args, **_kwargs):
        i = min(calls["n"], len(results) - 1)
        calls["n"] += 1
        result = results[i]
        if isinstance(result, Exception):
            raise result
        return result

    return fake, calls


def test_failed_run_keeps_previously_flushed_articles(conn, monkeypatch) -> None:
    _seed(conn, 3)
    fake, _calls = _fake_enrich(
        [
            ("enriched body 0", "trafilatura", -1),
            ("enriched body 1", "trafilatura", -1),
            RuntimeError("extraction chain exploded"),
        ]
    )
    monkeypatch.setattr(enrich_pipeline, "enrich_with_rate_limit", fake)

    with pytest.raises(RuntimeError):
        enrich_pipeline._enrich_articles(
            conn,
            "run-1",
            limit=None,
            only_missing=False,
            skip_enriched=True,
            max_markdown_new=0,
            flush_interval=1,
        )

    # Articles processed before the failure are persisted; the failed one is not
    rows = conn.execute(
        "SELECT extraction_method, body FROM articles ORDER BY article_id"
    ).fetchall()
    assert [r["extraction_method"] for r in rows] == [
        "trafilatura",
        "trafilatura",
        "rss",
    ]
    assert rows[0]["body"] == "enriched body 0"
    assert rows[1]["body"] == "enriched body 1"


def test_full_run_updates_all_articles_and_reports_metrics(conn, monkeypatch) -> None:
    _seed(conn, 3)
    fake, _calls = _fake_enrich([("enriched body %d" % i, "next_flight", -1) for i in range(3)])
    monkeypatch.setattr(enrich_pipeline, "enrich_with_rate_limit", fake)

    metrics = enrich_pipeline._enrich_articles(
        conn,
        "run-2",
        limit=None,
        only_missing=False,
        skip_enriched=True,
        max_markdown_new=0,
        flush_interval=1,
    )

    assert metrics["updated"] == 3
    assert metrics["flush_batches"] == 3
    assert metrics["method_counts"] == {"next_flight": 3}
    rows = conn.execute(
        "SELECT extraction_method FROM articles ORDER BY article_id"
    ).fetchall()
    assert all(r["extraction_method"] == "next_flight" for r in rows)


def test_default_interval_flushes_once_at_end(conn, monkeypatch) -> None:
    _seed(conn, 2)
    fake, _calls = _fake_enrich([("enriched body", "jina", -1)])
    monkeypatch.setattr(enrich_pipeline, "enrich_with_rate_limit", fake)

    metrics = enrich_pipeline._enrich_articles(
        conn,
        "run-3",
        limit=None,
        only_missing=False,
        skip_enriched=True,
        max_markdown_new=0,
    )

    assert metrics["updated"] == 2
    assert metrics["flush_batches"] == 1


def test_unchanged_bodies_are_not_counted_or_written(conn, monkeypatch) -> None:
    _seed(conn, 2, body_template="enriched body %d")
    # Enrichment echoes back the exact existing body → nothing to write
    def fake(url, _sid, _title, body, *_args, **_kwargs):
        return (body, "jina", -1)

    monkeypatch.setattr(enrich_pipeline, "enrich_with_rate_limit", fake)

    metrics = enrich_pipeline._enrich_articles(
        conn,
        "run-4",
        limit=None,
        only_missing=False,
        skip_enriched=True,
        max_markdown_new=0,
    )

    assert metrics["updated"] == 0
    assert metrics["unchanged"] == 2
    rows = conn.execute(
        "SELECT extraction_method FROM articles ORDER BY article_id"
    ).fetchall()
    assert all(r["extraction_method"] == "rss" for r in rows)


def test_enrichment_attempts_persist_with_flush(conn, monkeypatch) -> None:
    _seed(conn, 2)
    fake, _calls = _fake_enrich([("enriched body", "next_flight", -1)])
    monkeypatch.setattr(enrich_pipeline, "enrich_with_rate_limit", fake)

    enrich_pipeline._enrich_articles(
        conn,
        "run-5",
        limit=None,
        only_missing=False,
        skip_enriched=True,
        max_markdown_new=0,
        flush_interval=1,
    )

    attempts = conn.execute(
        "SELECT method, status FROM article_enrichment_attempts"
    ).fetchall()
    assert len(attempts) == 2
    assert all(a["method"] == "next_flight" and a["status"] == "success" for a in attempts)
