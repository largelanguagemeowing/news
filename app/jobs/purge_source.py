"""One-off: purge all articles (and their enrichment attempt history) for a
single source from the database. Used when retiring a source whose articles
should not remain in the corpus.

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

    row = conn.execute(
        "SELECT COUNT(*) FROM articles WHERE source_id = ?", (source_id,)
    ).fetchone()
    count = row[0]
    if not count:
        logger.info("No articles for source=%s; nothing to purge", source_id)
        return 0

    if dry_run:
        logger.info("Dry run: would delete %d article(s) for source=%s", count, source_id)
        return count

    with transaction(conn):
        conn.execute(
            """
            DELETE FROM article_enrichment_attempts
            WHERE article_url IN (SELECT url FROM articles WHERE source_id = ?)
            """,
            (source_id,),
        )
        deleted = conn.execute(
            "DELETE FROM articles WHERE source_id = ?", (source_id,)
        ).rowcount

    logger.info(
        "Purged source=%s articles=%d at %s", source_id, deleted, utc_now_iso()
    )
    return deleted


def main() -> int:
    parser = argparse.ArgumentParser(description="Purge a source's articles from the DB")
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    purge_source(args.source_id, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
