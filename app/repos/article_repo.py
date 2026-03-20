from __future__ import annotations

import sqlite3


def insert_article_if_new(
    conn: sqlite3.Connection,
    source_id: str,
    url: str,
    canonical_url: str,
    title: str,
    title_norm: str,
    body: str,
    body_norm: str,
    published_at: str,
    fetched_at: str,
    title_hash: str,
    body_hash: str,
    simhash: str,
    extraction_method: str,
    published_at_inferred: bool = False,
) -> bool:
    before = conn.total_changes
    conn.execute(
        """
        INSERT OR IGNORE INTO articles (
          source_id, url, canonical_url, title, title_norm, body, body_norm,
          published_at, fetched_at, title_hash, body_hash, simhash, extraction_method,
          published_at_inferred
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source_id,
            url,
            canonical_url,
            title,
            title_norm,
            body,
            body_norm,
            published_at,
            fetched_at,
            title_hash,
            body_hash,
            simhash,
            extraction_method,
            1 if published_at_inferred else 0,
        ),
    )
    return conn.total_changes > before


def record_ingest_attempt(
    conn: sqlite3.Connection,
    run_id: str,
    source_id: str,
    url: str,
    status: str,
    reason: str | None,
    created_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO article_ingest_attempts (run_id, source_id, url, status, reason, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (run_id, source_id, url, status, reason, created_at),
    )


def record_dead_letter(
    conn: sqlite3.Connection,
    run_id: str,
    source_id: str,
    url: str | None,
    error_message: str,
    raw_entry_json: str | None,
    created_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO dead_letters (run_id, source_id, url, error_message, raw_entry_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (run_id, source_id, url, error_message, raw_entry_json, created_at),
    )


def record_enrichment_attempt(
    conn: sqlite3.Connection,
    article_url: str,
    source_id: str,
    method: str,
    status: str,
    duration_ms: float | None,
    error_message: str | None,
    output_chars: int | None,
    created_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO article_enrichment_attempts
          (article_url, source_id, method, status, duration_ms, error_message, output_chars, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (article_url, source_id, method, status, duration_ms, error_message, output_chars, created_at),
    )


def update_articles_backfill(
    conn: sqlite3.Connection,
    updates: list[tuple[str, str, str, str, str, int]],
) -> int:
    if not updates:
        return 0
    conn.executemany(
        """
        UPDATE articles
        SET body = ?, body_norm = ?, body_hash = ?, simhash = ?, extraction_method = ?
        WHERE article_id = ?
        """,
        updates,
    )
    return len(updates)
