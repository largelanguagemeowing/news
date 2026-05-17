from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Callable

from app.utils import utc_now_iso


DB_PATH = Path("data/news.db")


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS sources (
  source_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  feed_url TEXT NOT NULL,
  favicon TEXT,
  default_category TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS source_health (
  source_id TEXT PRIMARY KEY REFERENCES sources(source_id),
  last_success_at TEXT,
  last_item_at TEXT,
  consecutive_failures INTEGER NOT NULL DEFAULT 0,
  avg_latency_ms REAL NOT NULL DEFAULT 0,
  items_24h INTEGER NOT NULL DEFAULT 0,
  errors_24h INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  auto_disabled_until TEXT,
  auto_disabled_reason TEXT,
  last_etag TEXT,
  last_modified TEXT,
  last_http_status INTEGER
);

CREATE TABLE IF NOT EXISTS articles (
  article_id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id TEXT NOT NULL REFERENCES sources(source_id),
  url TEXT NOT NULL,
  canonical_url TEXT NOT NULL,
  title TEXT NOT NULL,
  title_norm TEXT NOT NULL,
  body TEXT NOT NULL,
  body_norm TEXT NOT NULL,
  published_at TEXT NOT NULL,
  fetched_at TEXT NOT NULL,
  title_hash TEXT NOT NULL,
  body_hash TEXT NOT NULL,
  simhash TEXT NOT NULL,
  extraction_method TEXT NOT NULL DEFAULT 'rss',
  published_at_inferred INTEGER NOT NULL DEFAULT 0,
  UNIQUE(source_id, canonical_url, published_at)
);

CREATE TABLE IF NOT EXISTS events (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
  cluster_key TEXT UNIQUE NOT NULL,
  canonical_title TEXT NOT NULL,
  category_labels TEXT NOT NULL DEFAULT '',
  confidence REAL NOT NULL DEFAULT 0,
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  representative_article_id INTEGER REFERENCES articles(article_id),
  source_count INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS event_members (
  event_id INTEGER NOT NULL REFERENCES events(event_id),
  article_id INTEGER NOT NULL REFERENCES articles(article_id),
  similarity REAL NOT NULL,
  reason TEXT NOT NULL,
  PRIMARY KEY (event_id, article_id)
);

CREATE TABLE IF NOT EXISTS pipeline_runs (
  run_id TEXT PRIMARY KEY,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  status TEXT NOT NULL,
  error_message TEXT,
  metrics_json TEXT NOT NULL DEFAULT '{}',
  run_type TEXT NOT NULL DEFAULT 'pipeline'
);

CREATE TABLE IF NOT EXISTS stage_runs (
  stage_run_id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL REFERENCES pipeline_runs(run_id),
  stage_name TEXT NOT NULL,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  status TEXT NOT NULL,
  metrics_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS incidents (
  incident_id INTEGER PRIMARY KEY AUTOINCREMENT,
  incident_key TEXT UNIQUE NOT NULL,
  kind TEXT NOT NULL,
  target_id TEXT NOT NULL,
  status TEXT NOT NULL,
  opened_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  resolved_at TEXT,
  issue_number INTEGER,
  last_message TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_checks (
  check_id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id TEXT NOT NULL REFERENCES sources(source_id),
  run_id TEXT,
  checked_at TEXT NOT NULL,
  status TEXT NOT NULL,
  latency_ms REAL,
  error_message TEXT
);

CREATE TABLE IF NOT EXISTS article_ingest_attempts (
  attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  source_id TEXT NOT NULL,
  url TEXT NOT NULL,
  status TEXT NOT NULL,
  reason TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS article_enrichment_attempts (
  attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
  article_url TEXT NOT NULL,
  source_id TEXT NOT NULL,
  method TEXT NOT NULL,
  status TEXT NOT NULL,
  duration_ms REAL,
  error_message TEXT,
  output_chars INTEGER,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dead_letters (
  dead_letter_id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  source_id TEXT NOT NULL,
  url TEXT,
  error_message TEXT NOT NULL,
  raw_entry_json TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS schema_migrations (
  version INTEGER PRIMARY KEY,
  applied_at TEXT NOT NULL
);
"""


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    _run_migrations(conn)


# ---------------------------------------------------------------------------
# Schema migrations
# ---------------------------------------------------------------------------

def _get_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _add_column_if_missing(
    conn: sqlite3.Connection, table: str, column: str, typedef: str, columns: set[str],
) -> None:
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {typedef}")


def _migration_0001(conn: sqlite3.Connection) -> None:
    """Legacy column back-fill (source_health + articles)."""
    sh_cols = _get_columns(conn, "source_health")
    _add_column_if_missing(conn, "source_health", "auto_disabled_until", "TEXT", sh_cols)
    _add_column_if_missing(conn, "source_health", "auto_disabled_reason", "TEXT", sh_cols)

    art_cols = _get_columns(conn, "articles")
    _add_column_if_missing(conn, "articles", "extraction_method", "TEXT NOT NULL DEFAULT 'rss'", art_cols)
    _add_column_if_missing(conn, "articles", "published_at_inferred", "INTEGER NOT NULL DEFAULT 0", art_cols)


def _migration_0002(conn: sqlite3.Connection) -> None:
    """Add run_type to pipeline_runs and HTTP cursor columns to source_health."""
    pr_cols = _get_columns(conn, "pipeline_runs")
    _add_column_if_missing(conn, "pipeline_runs", "run_type", "TEXT NOT NULL DEFAULT 'pipeline'", pr_cols)

    sh_cols = _get_columns(conn, "source_health")
    _add_column_if_missing(conn, "source_health", "last_etag", "TEXT", sh_cols)
    _add_column_if_missing(conn, "source_health", "last_modified", "TEXT", sh_cols)
    _add_column_if_missing(conn, "source_health", "last_http_status", "INTEGER", sh_cols)


MIGRATIONS: list[tuple[int, Callable[[sqlite3.Connection], None]]] = [
    (1, _migration_0001),
    (2, _migration_0002),
]


def _run_migrations(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations "
        "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    applied = {
        row[0]
        for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
    }
    for version, migrate in MIGRATIONS:
        if version in applied:
            continue
        migrate(conn)
        conn.execute(
            "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
            (version, utc_now_iso()),
        )
        conn.commit()


@contextmanager
def transaction(conn: sqlite3.Connection):
    try:
        yield
        conn.commit()
    except Exception:
        conn.rollback()
        raise
