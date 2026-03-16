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
) -> bool:
    before = conn.total_changes
    conn.execute(
        """
        INSERT OR IGNORE INTO articles (
          source_id, url, canonical_url, title, title_norm, body, body_norm,
          published_at, fetched_at, title_hash, body_hash, simhash, extraction_method
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        ),
    )
    return conn.total_changes > before


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
