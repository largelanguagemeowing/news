from __future__ import annotations

import argparse
import json
import logging
import os
import time

from app.db import get_connection, init_db, transaction
from app.jobs import pipeline
from app.utils import normalize_text, sha1_hexdigest, simhash64


LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("news.backfill")


def backfill_articles(
    limit: int,
    only_missing: bool = False,
    dry_run: bool = False,
    all_items: bool = False,
) -> dict[str, int | bool]:
    started_at = time.time()
    conn = get_connection()
    init_db(conn)

    where_missing = "AND (a.body IS NULL OR TRIM(a.body) = '' OR LENGTH(a.body) < 120)" if only_missing else ""
    limit_clause = "" if all_items else "LIMIT ?"
    query = f"""
        SELECT a.article_id, a.url, a.title, a.body
        FROM articles a
        WHERE TRIM(a.url) != ''
        {where_missing}
        ORDER BY a.published_at DESC
        {limit_clause}
    """
    params: tuple[int, ...] = () if all_items else (max(1, limit),)
    rows = conn.execute(query, params).fetchall()
    total_rows = len(rows)
    logger.info(
        "Backfill started candidates=%d only_missing=%s all_items=%s dry_run=%s",
        total_rows,
        only_missing,
        all_items,
        dry_run,
    )

    attempted = 0
    enriched = 0
    updated = 0
    unchanged = 0
    misses = 0
    updates: list[tuple[str, str, str, str, int]] = []

    progress_interval = max(1, total_rows // 10) if total_rows else 1
    for row in rows:
        attempted += 1
        extracted, used = pipeline.parse_with_defuddle(str(row["url"] or ""))
        if not used or not extracted:
            misses += 1
        else:
            enriched += 1
            new_body = pipeline.truncate_for_storage(extracted)
            old_body = str(row["body"] or "").strip()
            if new_body.strip() == old_body:
                unchanged += 1
            else:
                body_norm = normalize_text(new_body)
                body_hash = sha1_hexdigest(body_norm)
                title_norm = normalize_text(str(row["title"] or ""))
                sh = str(simhash64(body_norm or title_norm))
                updates.append((new_body, body_norm, body_hash, sh, int(row["article_id"])))

        if attempted % progress_interval == 0 or attempted == total_rows:
            logger.info(
                "Backfill progress attempted=%d/%d enriched=%d queued_updates=%d unchanged=%d misses=%d",
                attempted,
                total_rows,
                enriched,
                len(updates),
                unchanged,
                misses,
            )

    if not dry_run and updates:
        with transaction(conn):
            conn.executemany(
                """
                UPDATE articles
                SET body = ?, body_norm = ?, body_hash = ?, simhash = ?
                WHERE article_id = ?
                """,
                updates,
            )
        updated = len(updates)

    duration_ms = round((time.time() - started_at) * 1000, 2)
    logger.info(
        "Backfill finished attempted=%d enriched=%d updated=%d unchanged=%d misses=%d duration_ms=%.2f",
        attempted,
        enriched,
        updated,
        unchanged,
        misses,
        duration_ms,
    )

    conn.close()
    return {
        "defuddle_enabled": pipeline.DEFUDDLE_ENABLED,
        "dry_run": dry_run,
        "only_missing": only_missing,
        "all_items": all_items,
        "attempted": attempted,
        "enriched": enriched,
        "updated": updated,
        "unchanged": unchanged,
        "misses": misses,
        "duration_ms": duration_ms,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill existing articles with Defuddle-extracted content")
    parser.add_argument("--limit", type=int, default=300, help="Maximum number of articles to process")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Process all matching articles (ignores --limit)",
    )
    parser.add_argument(
        "--only-missing",
        action="store_true",
        help="Only process articles with empty/very short bodies",
    )
    parser.add_argument("--dry-run", action="store_true", help="Compute metrics without writing updates")
    parser.add_argument(
        "--enable-defuddle",
        action="store_true",
        help="Enable defuddle without setting DEFUDDLE_ENABLED env var",
    )
    args = parser.parse_args()

    if args.enable_defuddle:
        pipeline.DEFUDDLE_ENABLED = True

    if not pipeline.DEFUDDLE_ENABLED:
        logger.error("Backfill aborted because defuddle is disabled")
        print(
            json.dumps(
                {
                    "error": "Defuddle is disabled. Set DEFUDDLE_ENABLED=1 or pass --enable-defuddle.",
                    "defuddle_enabled": False,
                }
            )
        )
        return 2

    logger.info(
        "Backfill CLI invoked limit=%d all=%s only_missing=%s dry_run=%s defuddle_enabled=%s",
        args.limit,
        args.all,
        args.only_missing,
        args.dry_run,
        pipeline.DEFUDDLE_ENABLED,
    )
    metrics = backfill_articles(
        limit=args.limit,
        only_missing=args.only_missing,
        dry_run=args.dry_run,
        all_items=args.all,
    )
    print(json.dumps(metrics))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
