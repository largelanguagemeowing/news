"""Retire a source: remove its articles, attempt/dead-letter history, health
rows, and the ``sources`` row itself. Used when a source should no longer
exist in the corpus or in exported status JSON.

Run with:
    uv run python -m app.jobs.purge_source --source-id sakana-ai [--dry-run]
"""

from __future__ import annotations

import argparse
import logging

from app.db import get_connection, init_db, transaction
from app.utils import utc_now_iso

logger = logging.getLogger("news.purge")


def purge_source(source_id: str, dry_run: bool = False) -> int:
    conn = get_connection()
    init_db(conn)

    count = conn.execute(
        "SELECT COUNT(*) FROM articles WHERE source_id = ?", (source_id,)
    ).fetchone()[0]
    source_exists = (
        conn.execute(
            "SELECT 1 FROM sources WHERE source_id = ?", (source_id,)
        ).fetchone()
        is not None
    )
    if not count and not source_exists:
        logger.info("No source or articles for source=%s; nothing to purge", source_id)
        return 0

    if dry_run:
        logger.info(
            "Dry run: would delete source=%s articles=%d (source row: %s)",
            source_id,
            count,
            "yes" if source_exists else "no",
        )
        return count

    with transaction(conn):
        # Content and history keyed by the source's articles.
        conn.execute(
            """
            DELETE FROM article_enrichment_attempts
            WHERE article_url IN (SELECT url FROM articles WHERE source_id = ?)
            """,
            (source_id,),
        )
        conn.execute(
            """
            DELETE FROM event_members
            WHERE article_id IN (SELECT article_id FROM articles WHERE source_id = ?)
            """,
            (source_id,),
        )
        conn.execute(
            """
            UPDATE events SET representative_article_id = NULL
            WHERE representative_article_id IN (
              SELECT article_id FROM articles WHERE source_id = ?
            )
            """,
            (source_id,),
        )
        deleted = conn.execute(
            "DELETE FROM articles WHERE source_id = ?", (source_id,)
        ).rowcount
        # Source-scoped history and the source row itself.
        conn.execute(
            "DELETE FROM article_ingest_attempts WHERE source_id = ?", (source_id,)
        )
        conn.execute("DELETE FROM dead_letters WHERE source_id = ?", (source_id,))
        conn.execute("DELETE FROM source_checks WHERE source_id = ?", (source_id,))
        conn.execute("DELETE FROM source_health WHERE source_id = ?", (source_id,))
        conn.execute("DELETE FROM sources WHERE source_id = ?", (source_id,))

    logger.info(
        "Purged source=%s articles=%d source_row=%s at %s",
        source_id,
        deleted,
        "removed" if source_exists else "absent",
        utc_now_iso(),
    )
    return deleted


def main() -> int:
    parser = argparse.ArgumentParser(description="Retire a source from the DB")
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    purge_source(args.source_id, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
