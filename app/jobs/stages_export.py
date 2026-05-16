from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from app.models import CheckStatus, SourceStatus
from app.repos import incident_repo, run_repo


def export_status(
    conn: sqlite3.Connection,
    *,
    status_dir: Path,
    build_summary_fn: Callable[[sqlite3.Connection], dict[str, Any]],
    build_sources_fn: Callable[[sqlite3.Connection], list[dict[str, Any]]],
    build_runs_fn: Callable[[sqlite3.Connection], list[dict[str, Any]]],
    build_incidents_fn: Callable[[sqlite3.Connection], list[dict[str, Any]]],
    build_events_fn: Callable[[sqlite3.Connection], list[dict[str, Any]]],
    build_articles_fn: Callable[[sqlite3.Connection], list[dict[str, Any]]],
) -> dict[str, Any]:
    status_dir.mkdir(parents=True, exist_ok=True)
    summary = build_summary_fn(conn)
    sources = build_sources_fn(conn)
    runs = build_runs_fn(conn)
    incidents = build_incidents_fn(conn)
    events = build_events_fn(conn)
    articles = build_articles_fn(conn)
    source_health = build_source_health(conn)
    source_checks = build_source_checks(conn)
    event_members = build_event_members(conn)
    ingest_attempts = build_ingest_attempts(conn)
    enrichment_attempts = build_enrichment_attempts(conn)
    dead_letters = build_dead_letters(conn)

    files = {
        "summary.json": summary,
        "sources.json": sources,
        "runs.json": runs,
        "incidents.json": incidents,
        "events.json": events,
        "articles.json": articles,
        "source_health.json": source_health,
        "source_checks.json": source_checks,
        "event_members.json": event_members,
        "ingest_attempts.json": ingest_attempts,
        "enrichment_attempts.json": enrichment_attempts,
        "dead_letters.json": dead_letters,
    }
    for filename, payload in files.items():
        (status_dir / filename).write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )

    return {"exported_files": len(files), "open_incidents": summary["open_incidents"]}


def build_summary(
    conn: sqlite3.Connection,
    *,
    parse_date: Callable[[Any], datetime],
    iso_now_fn: Callable[[], str],
    stale_source_hours: int,
    events_window_hours: int,
) -> dict[str, Any]:
    total_sources = conn.execute(
        "SELECT COUNT(*) c FROM sources WHERE enabled = 1"
    ).fetchone()["c"]
    sources_health = conn.execute(
        """
        SELECT sh.source_id, sh.last_success_at, sh.consecutive_failures
        FROM source_health sh
        JOIN sources s ON s.source_id = sh.source_id
        WHERE s.enabled = 1
        """
    ).fetchall()
    healthy = 0
    stale = 0
    now = datetime.now(timezone.utc)
    for row in sources_health:
        if int(row["consecutive_failures"]) == 0:
            healthy += 1
        if not row["last_success_at"]:
            stale += 1
            continue
        if (
            now - parse_date(row["last_success_at"])
        ).total_seconds() > stale_source_hours * 3600:
            stale += 1
    events_24h = conn.execute(
        "SELECT COUNT(*) c FROM events WHERE last_seen >= ?",
        (
            (now - timedelta(hours=events_window_hours))
            .replace(microsecond=0)
            .isoformat(),
        ),
    ).fetchone()["c"]
    open_incidents = incident_repo.count_open_incidents(conn)
    article_count = conn.execute("SELECT COUNT(*) c FROM articles").fetchone()["c"]
    event_count = conn.execute("SELECT COUNT(*) c FROM events").fetchone()["c"]
    dedupe_ratio = round(1 - (event_count / article_count), 4) if article_count else 0.0
    pipeline_status = SourceStatus.HEALTHY.value
    if open_incidents:
        pipeline_status = SourceStatus.DEGRADED.value
    if total_sources and healthy == 0:
        pipeline_status = SourceStatus.DOWN.value
    github_repository = os.getenv("GITHUB_REPOSITORY")
    if not github_repository:
        latest = conn.execute(
            "SELECT metrics_json FROM pipeline_runs ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        if latest:
            latest_metrics = json.loads(latest["metrics_json"] or "{}")
            github_run_url = latest_metrics.get("github_run_url")
            if isinstance(github_run_url, str) and "github.com/" in github_run_url:
                parts = github_run_url.split("github.com/", 1)[1].split("/", 2)
                if len(parts) >= 2:
                    github_repository = f"{parts[0]}/{parts[1]}"
    return {
        "generated_at": iso_now_fn(),
        "pipeline_status": pipeline_status,
        "total_sources": total_sources,
        "healthy_sources": healthy,
        "stale_sources": stale,
        "total_events_24h": events_24h,
        "dedupe_ratio_24h": dedupe_ratio,
        "open_incidents": open_incidents,
        "github_repository": github_repository,
    }


def build_sources(
    conn: sqlite3.Connection,
    *,
    source_fail_threshold: int,
    source_is_in_cooldown: Callable[[str | None], bool],
    source_checks_history_limit: int,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT s.source_id, s.name, s.feed_url, sh.last_success_at, sh.last_item_at,
               sh.consecutive_failures, sh.avg_latency_ms, sh.last_error,
               sh.auto_disabled_until,
               COALESCE((
                 SELECT COUNT(*) FROM articles a
                 WHERE a.source_id = s.source_id
                   AND a.fetched_at >= datetime('now', '-24 hours')
               ), 0) AS items_24h,
               COALESCE((
                 SELECT COUNT(*) FROM source_checks sc
                 WHERE sc.source_id = s.source_id
                   AND sc.status = 'failed'
                   AND sc.checked_at >= datetime('now', '-24 hours')
               ), 0) AS errors_24h
        FROM sources s
        LEFT JOIN source_health sh ON sh.source_id = s.source_id
        WHERE s.enabled = 1
        ORDER BY s.name
        """
    ).fetchall()
    out = []
    for row in rows:
        status = SourceStatus.HEALTHY.value
        if int(row["consecutive_failures"] or 0) > 0:
            status = SourceStatus.DEGRADED.value
        if int(row["consecutive_failures"] or 0) >= source_fail_threshold:
            status = SourceStatus.DOWN.value
        if source_is_in_cooldown(row["auto_disabled_until"]):
            status = SourceStatus.DOWN.value
        checks = conn.execute(
            """
            SELECT status
            FROM source_checks
            WHERE source_id = ?
            ORDER BY checked_at DESC
            LIMIT ?
            """,
            (row["source_id"], source_checks_history_limit),
        ).fetchall()
        recent_statuses = [check["status"] for check in checks][::-1]
        total_checks = len(recent_statuses)
        success_checks = sum(
            1 for status in recent_statuses if status == CheckStatus.SUCCESS.value
        )
        uptime_pct = (
            round((success_checks / total_checks) * 100, 2) if total_checks else 0.0
        )
        out.append(
            {
                "source_id": row["source_id"],
                "name": row["name"],
                "feed_url": row["feed_url"],
                "status": status,
                "last_success_at": row["last_success_at"],
                "last_item_at": row["last_item_at"],
                "consecutive_failures": int(row["consecutive_failures"] or 0),
                "avg_latency_ms": float(row["avg_latency_ms"] or 0),
                "items_24h": int(row["items_24h"] or 0),
                "errors_24h": int(row["errors_24h"] or 0),
                "last_error": row["last_error"],
                "auto_disabled_until": row["auto_disabled_until"],
                "recent_statuses": recent_statuses,
                "uptime_pct_recent": uptime_pct,
                "checks_count_recent": total_checks,
            }
        )
    return out


def build_runs(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = run_repo.list_recent_runs(conn, limit=20)
    out = []
    for row in rows:
        run_metrics = json.loads(row["metrics_json"] or "{}")
        stage_rows = run_repo.list_stage_runs(conn, row["run_id"])
        stages = []
        for stage in stage_rows:
            stages.append(
                {
                    "stage_name": stage["stage_name"],
                    "started_at": stage["started_at"],
                    "ended_at": stage["ended_at"],
                    "status": stage["status"],
                    "metrics": json.loads(stage["metrics_json"] or "{}"),
                }
            )
        out.append(
            {
                "run_id": row["run_id"],
                "started_at": row["started_at"],
                "ended_at": row["ended_at"],
                "status": row["status"],
                "error_message": row["error_message"],
                "github_run_id": run_metrics.get("github_run_id"),
                "github_run_url": run_metrics.get("github_run_url"),
                "stage_stats": stages,
            }
        )
    return out


def build_incidents(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = incident_repo.list_incidents(conn, limit=100)
    return [
        {
            "incident_key": row["incident_key"],
            "kind": row["kind"],
            "target_id": row["target_id"],
            "status": row["status"],
            "opened_at": row["opened_at"],
            "updated_at": row["updated_at"],
            "resolved_at": row["resolved_at"],
            "issue_number": row["issue_number"],
            "last_message": row["last_message"],
        }
        for row in rows
    ]


def build_events(
    conn: sqlite3.Connection, *, events_export_limit: int
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT e.event_id, e.canonical_title, e.category_labels, e.confidence,
               e.first_seen, e.last_seen, e.source_count, a.url AS representative_url
        FROM events e
        JOIN articles a ON e.representative_article_id = a.article_id
        ORDER BY e.last_seen DESC
        LIMIT ?
        """,
        (events_export_limit,),
    ).fetchall()
    return [
        {
            "event_id": row["event_id"],
            "canonical_title": row["canonical_title"],
            "category_labels": row["category_labels"],
            "confidence": row["confidence"],
            "first_seen": row["first_seen"],
            "last_seen": row["last_seen"],
            "source_count": row["source_count"],
            "representative_url": row["representative_url"],
        }
        for row in rows
    ]


def build_articles(
    conn: sqlite3.Connection,
    *,
    classify_event: Callable[[str, str, str], tuple[str, float]],
    extract_tags: Callable[[str, str, str], list[str]],
    articles_export_limit: int,
) -> list[dict[str, Any]]:
    dearrow_map: dict[str, str] = {}
    dearrow_path = Path("data/status/dearrow_thumbnails.json")
    if dearrow_path.exists():
        try:
            dearrow_map = json.loads(dearrow_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    from app.jobs.enrichment import get_youtube_video_id

    rows = conn.execute(
        """
        SELECT a.article_id, a.title, a.body, a.url, a.published_at, a.fetched_at,
               a.extraction_method,
               s.source_id, s.name AS source_name, s.default_category,
               e.category_labels AS event_category
        FROM articles a
        JOIN sources s ON s.source_id = a.source_id
        LEFT JOIN event_members em ON em.article_id = a.article_id
        LEFT JOIN events e ON e.event_id = em.event_id
        ORDER BY a.published_at DESC
        LIMIT ?
        """,
        (articles_export_limit,),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        title = row["title"]
        body = row["body"] or ""
        topic = (
            row["event_category"]
            or classify_event(title, body, row["default_category"])[0]
        )
        tags = extract_tags(title, body, row["source_id"])
        if topic not in tags:
            tags = [topic] + tags
        video_id = get_youtube_video_id(row["url"])
        dearrow_url = dearrow_map.get(video_id, "")
        out.append(
            {
                "article_id": row["article_id"],
                "title": title,
                "url": row["url"],
                "published_at": row["published_at"],
                "fetched_at": row["fetched_at"],
                "source_id": row["source_id"],
                "source_name": row["source_name"],
                "extraction_method": row["extraction_method"] or "rss",
                "body": body,
                "topic": topic,
                "tags": tags[:7],
                "dearrow_thumbnail_url": dearrow_url,
            }
        )
    return out


def build_source_health(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT sh.source_id, sh.last_success_at, sh.last_item_at, sh.consecutive_failures,
               sh.avg_latency_ms, sh.last_error,
               sh.auto_disabled_until, sh.auto_disabled_reason,
               COALESCE((
                 SELECT COUNT(*) FROM articles a
                 WHERE a.source_id = sh.source_id
                   AND a.fetched_at >= datetime('now', '-24 hours')
               ), 0) AS items_24h,
               COALESCE((
                 SELECT COUNT(*) FROM source_checks sc
                 WHERE sc.source_id = sh.source_id
                   AND sc.status = 'failed'
                   AND sc.checked_at >= datetime('now', '-24 hours')
               ), 0) AS errors_24h
        FROM source_health sh
        ORDER BY sh.source_id
        """
    ).fetchall()
    return [dict(row) for row in rows]


def build_source_checks(
    conn: sqlite3.Connection, limit: int = 1000
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT check_id, source_id, run_id, checked_at, status, latency_ms, error_message
        FROM source_checks
        ORDER BY checked_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def build_event_members(
    conn: sqlite3.Connection, limit: int = 2000
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT event_id, article_id, similarity, reason
        FROM event_members
        ORDER BY event_id DESC, article_id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def build_ingest_attempts(
    conn: sqlite3.Connection, limit: int = 2000
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT attempt_id, run_id, source_id, url, status, reason, created_at
        FROM article_ingest_attempts
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def build_enrichment_attempts(
    conn: sqlite3.Connection, limit: int = 2000
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT attempt_id, article_url, source_id, method, status,
               duration_ms, error_message, output_chars, created_at
        FROM article_enrichment_attempts
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def build_dead_letters(
    conn: sqlite3.Connection, limit: int = 1000
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT dead_letter_id, run_id, source_id, url, error_message,
               raw_entry_json, created_at
        FROM dead_letters
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]
