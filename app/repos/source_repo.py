from __future__ import annotations

import sqlite3


def get_health(conn: sqlite3.Connection, source_id: str):
    return conn.execute(
        """
        SELECT consecutive_failures, last_success_at, auto_disabled_until,
               last_etag, last_modified, last_http_status
        FROM source_health
        WHERE source_id = ?
        """,
        (source_id,),
    ).fetchone()


def get_latency_avg(conn: sqlite3.Connection, source_id: str) -> float:
    row = conn.execute(
        "SELECT avg_latency_ms FROM source_health WHERE source_id = ?",
        (source_id,),
    ).fetchone()
    return float(row["avg_latency_ms"]) if row else 0.0


def insert_source_check(
    conn: sqlite3.Connection,
    source_id: str,
    run_id: str,
    checked_at: str,
    status: str,
    latency_ms: float | None,
    error_message: str | None,
) -> None:
    conn.execute(
        """
        INSERT INTO source_checks (source_id, run_id, checked_at, status, latency_ms, error_message)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (source_id, run_id, checked_at, status, latency_ms, error_message),
    )


def update_source_success(
    conn: sqlite3.Connection,
    source_id: str,
    last_success_at: str,
    last_item_at: str | None,
    avg_latency_ms: float,
) -> None:
    conn.execute(
        """
        UPDATE source_health
        SET last_success_at = ?, last_item_at = ?, consecutive_failures = 0,
            avg_latency_ms = ?, last_error = NULL,
            auto_disabled_until = NULL, auto_disabled_reason = NULL
        WHERE source_id = ?
        """,
        (last_success_at, last_item_at, avg_latency_ms, source_id),
    )


def get_failure_state(conn: sqlite3.Connection, source_id: str):
    return conn.execute(
        """
        SELECT consecutive_failures, last_success_at
        FROM source_health
        WHERE source_id = ?
        """,
        (source_id,),
    ).fetchone()


def update_source_failure(
    conn: sqlite3.Connection,
    source_id: str,
    failures: int,
    error_message: str,
    disabled_until: str | None,
    auto_disable_reason: str,
) -> None:
    conn.execute(
        """
        UPDATE source_health
        SET consecutive_failures = ?, last_error = ?
          , auto_disabled_until = COALESCE(?, auto_disabled_until)
          , auto_disabled_reason = CASE
              WHEN ? IS NULL THEN auto_disabled_reason
              ELSE ?
            END
        WHERE source_id = ?
        """,
        (
            failures,
            error_message,
            disabled_until,
            disabled_until,
            auto_disable_reason,
            source_id,
        ),
    )


def update_http_cursors(
    conn: sqlite3.Connection,
    source_id: str,
    etag: str | None,
    last_modified: str | None,
    http_status: int,
) -> None:
    conn.execute(
        """
        UPDATE source_health
        SET last_etag = ?, last_modified = ?, last_http_status = ?
        WHERE source_id = ?
        """,
        (etag, last_modified, http_status, source_id),
    )


def update_source_name(conn: sqlite3.Connection, source_id: str, name: str) -> None:
    conn.execute(
        "UPDATE sources SET name = ? WHERE source_id = ?",
        (name, source_id),
    )
