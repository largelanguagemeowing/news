"""Historical source-id renames.

Feed-source identity used to be keyed by the feed's original id; a handful of
sources were later renamed to human-readable ids. migrate_source_ids() replays
those renames at pipeline start so identity stays stable across the rename.
It used to live inline in app/jobs/pipeline.py; the renames are a one-way
migration, so the data (SOURCE_ID_RENAMES) and the logic sit here on their own.
"""

from __future__ import annotations

import sqlite3

from app.db import transaction


SOURCE_ID_RENAMES = {
    "deepmind-blog": "google-deepmind-blog",
    "apple-ml-blog": "apple-machine-learning",
    "simon-willison-atom": "simon-willison",
    "anyfeeds-custom-1": "cursor-blog",
    "anyfeeds-custom-2": "cursor-changelog",
    "youtube-ai-explained": "matt-wolfe",
    "youtube-threeblueonebrown": "fireship",
    "youtube-ai-coding": "ai-explained",
}


def migrate_source_ids(conn: sqlite3.Connection) -> None:
    """Rename historical source ids so feed-source identity stays human-readable."""
    with transaction(conn):
        for old_id, new_id in SOURCE_ID_RENAMES.items():
            old_exists = conn.execute(
                "SELECT 1 FROM sources WHERE source_id = ?",
                (old_id,),
            ).fetchone()
            if not old_exists:
                continue
            conn.execute(
                """
                INSERT OR IGNORE INTO sources (source_id, name, feed_url, default_category, enabled)
                SELECT ?, name, feed_url, default_category, enabled
                FROM sources
                WHERE source_id = ?
                """,
                (new_id, old_id),
            )
            new_health = conn.execute(
                "SELECT 1 FROM source_health WHERE source_id = ?",
                (new_id,),
            ).fetchone()
            if new_health:
                conn.execute("DELETE FROM source_health WHERE source_id = ?", (old_id,))
            else:
                conn.execute(
                    "UPDATE source_health SET source_id = ? WHERE source_id = ?",
                    (new_id, old_id),
                )
            conn.execute(
                """
                UPDATE articles
                SET source_id = ?
                WHERE source_id = ?
                  AND NOT EXISTS (
                    SELECT 1
                    FROM articles a2
                    WHERE a2.source_id = ?
                      AND a2.canonical_url = articles.canonical_url
                      AND a2.published_at = articles.published_at
                  )
                """,
                (new_id, old_id, new_id),
            )
            conn.execute(
                "DELETE FROM articles WHERE source_id = ?",
                (old_id,),
            )
            conn.execute(
                """
                UPDATE incidents
                SET incident_key = CASE
                      WHEN incident_key = ? THEN ?
                      ELSE incident_key
                    END,
                    target_id = CASE
                      WHEN kind = 'source' AND target_id = ? THEN ?
                      ELSE target_id
                    END
                WHERE incident_key = ?
                   OR (kind = 'source' AND target_id = ?)
                """,
                (
                    f"source:{old_id}",
                    f"source:{new_id}",
                    old_id,
                    new_id,
                    f"source:{old_id}",
                    old_id,
                ),
            )
            conn.execute("DELETE FROM sources WHERE source_id = ?", (old_id,))