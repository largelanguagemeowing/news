from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Callable


def cluster_stage(
    conn: sqlite3.Connection,
    *,
    parse_date: Callable[[Any], datetime],
    iso: Callable[[datetime], str],
    pair_similarity: Callable[[str, str, int, int], float],
    sha1_hexdigest: Callable[[str], str],
    similarity_threshold: float,
    cluster_window_hours: int,
    cluster_lookback_days: int,
) -> dict[str, Any]:
    lower_bound = iso(datetime.now(timezone.utc) - timedelta(days=cluster_lookback_days))
    rows = conn.execute(
        """
        SELECT article_id, source_id, title, title_norm, body_norm, published_at, simhash
        FROM articles
        WHERE published_at >= ?
        ORDER BY published_at DESC
        """,
        (lower_bound,),
    ).fetchall()
    articles = [dict(row) for row in rows]
    groups: list[list[dict[str, Any]]] = []
    for article in articles:
        assigned = False
        art_published = parse_date(article["published_at"])
        art_simhash = int(article["simhash"])
        for group in groups:
            rep = group[0]
            rep_published = parse_date(rep["published_at"])
            if abs((art_published - rep_published).total_seconds()) > (cluster_window_hours * 3600):
                continue
            score = pair_similarity(
                article["title_norm"],
                rep["title_norm"],
                art_simhash,
                int(rep["simhash"]),
            )
            if score >= similarity_threshold:
                article["score"] = score
                group.append(article)
                assigned = True
                break
        if not assigned:
            article["score"] = 1.0
            groups.append([article])

    conn.execute("DELETE FROM event_members")
    conn.execute("DELETE FROM events")
    for group in groups:
        first_seen = min(item["published_at"] for item in group)
        last_seen = max(item["published_at"] for item in group)
        source_count = len({item["source_id"] for item in group})
        representative = max(group, key=lambda item: (len(item["title"]), -parse_date(item["published_at"]).timestamp()))
        cluster_key = sha1_hexdigest(f"{representative['title_norm']}:{first_seen}")
        cursor = conn.execute(
            """
            INSERT INTO events (
              cluster_key, canonical_title, first_seen, last_seen,
              representative_article_id, source_count
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                cluster_key,
                representative["title"],
                first_seen,
                last_seen,
                representative["article_id"],
                source_count,
            ),
        )
        event_id = int(cursor.lastrowid)
        for item in group:
            conn.execute(
                """
                INSERT INTO event_members (event_id, article_id, similarity, reason)
                VALUES (?, ?, ?, ?)
                """,
                (event_id, item["article_id"], float(item.get("score", 1.0)), "title+simhash"),
            )

    return {"events": len(groups), "articles_clustered": len(articles)}


def categorize_stage(
    conn: sqlite3.Connection,
    *,
    classify_event: Callable[[str, str, str], tuple[str, float]],
) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT e.event_id, e.canonical_title, s.default_category, a.body
        FROM events e
        JOIN articles a ON e.representative_article_id = a.article_id
        JOIN sources s ON a.source_id = s.source_id
        """
    ).fetchall()
    updated = 0
    for row in rows:
        label, confidence = classify_event(
            row["canonical_title"], row["body"], row["default_category"]
        )
        conn.execute(
            """
            UPDATE events
            SET category_labels = ?, confidence = ?
            WHERE event_id = ?
            """,
            (label, confidence, row["event_id"]),
        )
        updated += 1
    return {"events_categorized": updated}
