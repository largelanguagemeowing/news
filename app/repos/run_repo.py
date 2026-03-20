from __future__ import annotations

import json
import sqlite3
from typing import Any


def create_pipeline_run(conn: sqlite3.Connection, run_id: str, started_at: str, run_type: str = "pipeline") -> None:
    conn.execute(
        """
        INSERT INTO pipeline_runs (run_id, started_at, status, metrics_json, run_type)
        VALUES (?, ?, 'running', '{}', ?)
        """,
        (run_id, started_at, run_type),
    )


def complete_pipeline_run(
    conn: sqlite3.Connection,
    run_id: str,
    ended_at: str,
    metrics: dict[str, Any],
) -> None:
    conn.execute(
        """
        UPDATE pipeline_runs
        SET ended_at = ?, status = 'success', metrics_json = ?, error_message = NULL
        WHERE run_id = ?
        """,
        (ended_at, json.dumps(metrics), run_id),
    )


def fail_pipeline_run(
    conn: sqlite3.Connection,
    run_id: str,
    ended_at: str,
    error_message: str,
    metrics: dict[str, Any],
) -> None:
    conn.execute(
        """
        UPDATE pipeline_runs
        SET ended_at = ?, status = 'failed', error_message = ?, metrics_json = ?
        WHERE run_id = ?
        """,
        (ended_at, error_message, json.dumps(metrics), run_id),
    )


def create_stage_run(conn: sqlite3.Connection, run_id: str, stage_name: str, started_at: str) -> int:
    cursor = conn.execute(
        """
        INSERT INTO stage_runs (run_id, stage_name, started_at, status, metrics_json)
        VALUES (?, ?, ?, 'running', '{}')
        """,
        (run_id, stage_name, started_at),
    )
    return int(cursor.lastrowid)


def complete_stage_run(
    conn: sqlite3.Connection,
    stage_run_id: int,
    ended_at: str,
    status: str,
    metrics: dict[str, Any],
) -> None:
    conn.execute(
        """
        UPDATE stage_runs
        SET ended_at = ?, status = ?, metrics_json = ?
        WHERE stage_run_id = ?
        """,
        (ended_at, status, json.dumps(metrics), stage_run_id),
    )


def list_recent_runs(conn: sqlite3.Connection, limit: int = 20):
    return conn.execute(
        """
        SELECT run_id, started_at, ended_at, status, error_message, metrics_json
        FROM pipeline_runs
        ORDER BY started_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def list_stage_runs(conn: sqlite3.Connection, run_id: str):
    return conn.execute(
        """
        SELECT stage_name, started_at, ended_at, status, metrics_json
        FROM stage_runs
        WHERE run_id = ?
        ORDER BY stage_run_id ASC
        """,
        (run_id,),
    ).fetchall()
