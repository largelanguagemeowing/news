from __future__ import annotations

from app.db import get_connection, init_db, transaction
from app.jobs.purge_source import purge_source
from app.utils import normalize_text, sha1_hexdigest, simhash64

TS = "2026-03-15T00:00:00+00:00"


def _seed_source() -> None:
    conn = get_connection()
    init_db(conn)
    with transaction(conn):
        conn.execute(
            "INSERT INTO sources (source_id, name, feed_url, default_category, enabled) "
            "VALUES ('test-source', 'Test Source', 'https://example.com/feed.xml', 'general', 1)"
        )
        conn.execute(
            "INSERT INTO source_health (source_id, consecutive_failures) "
            "VALUES ('test-source', 3)"
        )
        conn.execute(
            "INSERT INTO source_checks (source_id, checked_at, status) "
            "VALUES ('test-source', ?, 'failed')",
            (TS,),
        )
        conn.execute(
            "INSERT INTO article_ingest_attempts (run_id, source_id, url, status, created_at) "
            "VALUES ('run-1', 'test-source', 'https://example.com/a', 'failed', ?)",
            (TS,),
        )
        cursor = conn.execute(
            """
            INSERT INTO articles (
              source_id, url, canonical_url, title, title_norm, body, body_norm,
              published_at, fetched_at, title_hash, body_hash, simhash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "test-source",
                "https://example.com/a",
                "https://example.com/a",
                "Sample",
                normalize_text("Sample"),
                "body",
                normalize_text("body"),
                TS,
                TS,
                sha1_hexdigest(normalize_text("Sample")),
                sha1_hexdigest(normalize_text("body")),
                str(simhash64(normalize_text("body"))),
            ),
        )
        article_id = int(cursor.lastrowid)
        conn.execute(
            """
            INSERT INTO events (
              cluster_key, canonical_title, first_seen, last_seen, representative_article_id
            ) VALUES ('k1', 'Sample', ?, ?, ?)
            """,
            (TS, TS, article_id),
        )
        event_id = conn.execute(
            "SELECT event_id FROM events WHERE cluster_key = 'k1'"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO event_members (event_id, article_id, similarity, reason) "
            "VALUES (?, ?, 1.0, 'seed')",
            (event_id, article_id),
        )
    conn.close()


def test_purge_source_removes_source_row_and_children() -> None:
    _seed_source()

    assert purge_source("test-source") == 1

    conn = get_connection()
    for table in ("sources", "articles", "source_health", "source_checks",
                  "article_ingest_attempts", "event_members"):
        count = conn.execute(
            f"SELECT COUNT(*) FROM {table}"
        ).fetchone()[0]
        assert count == 0, f"{table} still has rows"
    representative = conn.execute(
        "SELECT representative_article_id FROM events WHERE cluster_key = 'k1'"
    ).fetchone()[0]
    assert representative is None
    conn.close()


def test_purge_source_is_noop_when_absent() -> None:
    conn = get_connection()
    init_db(conn)
    conn.close()

    assert purge_source("does-not-exist") == 0
