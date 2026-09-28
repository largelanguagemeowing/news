"""One-off backfill: populate articles.feed_guid from the source RSS feeds.

The feed_guid column carries the feed entry guid, which for feeds that link
X Articles (e.g. mariozechner.at/recommended-reading) holds the parent tweet
permalink — the only handle the FxTwitter extractor has on the article body.
Articles ingested before the guid was captured have a NULL feed_guid and can
never be enriched; this job re-reads the source feeds and back-fills it.

Run with:
    uv run python -m app.jobs.backfill_feed_guid [--dry-run]
"""

from __future__ import annotations

import argparse
import logging

import feedparser
import requests

from app.config import load_sources
from app.db import get_connection, init_db, transaction
from app.jobs import x_articles
from app.utils import utc_now_iso

logger = logging.getLogger("news.backfill")


def _fetch_feed_mapping(feed_url: str, timeout_seconds: float = 30.0) -> dict[str, str]:
    """Map each entry's canonical link URL to its guid."""
    response = requests.get(
        feed_url, timeout=timeout_seconds, headers={"User-Agent": "Mozilla/5.0"}
    )
    response.raise_for_status()
    feed = feedparser.parse(response.content)
    mapping: dict[str, str] = {}
    for entry in feed.entries:
        link = str(entry.get("link", "")).strip()
        guid = str(entry.get("id", "") or entry.get("guid", "") or "").strip()
        if link and guid:
            mapping[link] = guid
    return mapping


def backfill_feed_guid(dry_run: bool = False) -> int:
    conn = get_connection()
    init_db(conn)

    rows = conn.execute(
        """
        SELECT DISTINCT a.source_id, s.feed_url
        FROM articles a
        JOIN sources s ON s.source_id = a.source_id
        WHERE a.feed_guid IS NULL
          AND s.enabled = 1
        """
    ).fetchall()

    # Only feeds that actually carry X Articles need re-reading.
    candidates = []
    for row in rows:
        count = conn.execute(
            "SELECT COUNT(*) FROM articles WHERE source_id = ? AND feed_guid IS NULL",
            (row["source_id"],),
        ).fetchone()[0]
        has_article_links = conn.execute(
            "SELECT COUNT(*) FROM articles WHERE source_id = ? AND feed_guid IS NULL AND url LIKE '%/i/article/%'",
            (row["source_id"],),
        ).fetchone()[0]
        if has_article_links:
            candidates.append((row["source_id"], row["feed_url"], count))

    logger.info(
        "Backfill feed_guid: %d source(s) with unguided X Article links: %s",
        len(candidates),
        ", ".join(f"{sid} ({n})" for sid, _, n in candidates),
    )

    updated = 0
    for source_id, feed_url, _count in candidates:
        try:
            mapping = _fetch_feed_mapping(feed_url)
        except Exception as exc:
            logger.error("Feed fetch failed source=%s url=%s error=%s", source_id, feed_url, exc)
            continue

        articles = conn.execute(
            "SELECT article_id, url FROM articles WHERE source_id = ? AND feed_guid IS NULL",
            (source_id,),
        ).fetchall()
        for article in articles:
            guid = mapping.get(str(article["url"]).strip())
            if not guid:
                continue
            if not x_articles.parent_tweet_id(guid):
                continue
            if dry_run:
                logger.info("Dry run: would set feed_guid article_id=%s url=%s guid=%s", article["article_id"], article["url"], guid)
            else:
                with transaction(conn):
                    conn.execute(
                        "UPDATE articles SET feed_guid = ? WHERE article_id = ?",
                        (guid, article["article_id"]),
                    )
            updated += 1

    logger.info("Backfill feed_guid finished updated=%d dry_run=%s at %s", updated, dry_run, utc_now_iso())
    return updated


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill articles.feed_guid from source RSS feeds")
    parser.add_argument("--dry-run", action="store_true", help="Log updates without writing")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    backfill_feed_guid(dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
