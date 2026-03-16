from __future__ import annotations

import sqlite3


def list_incidents(conn: sqlite3.Connection, limit: int = 100):
    return conn.execute(
        """
        SELECT incident_key, kind, target_id, status, opened_at, updated_at, resolved_at, issue_number, last_message
        FROM incidents
        ORDER BY opened_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def count_open_incidents(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT COUNT(*) c FROM incidents WHERE status = 'open'"
    ).fetchone()["c"]
