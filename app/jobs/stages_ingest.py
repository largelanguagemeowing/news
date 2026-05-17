from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import feedparser
import requests
import tenacity

from app.config import SourceConfig
from app.db import transaction
from app.incidents import IncidentSignal, sync_incident_open_or_update, sync_incident_resolve
from app.logging_helpers import log_source_complete
from app.models import CheckStatus, ExtractionMethod
from app.repos import article_repo, source_repo
from app.utils import clean_title


logger = logging.getLogger("news.pipeline")


def ingest_stage(
    conn: sqlite3.Connection,
    run_id: str,
    sources: list[SourceConfig],
    issue_client,
    *,
    defuddle_enabled: bool,
    source_fail_threshold: int,
    source_auto_disable_cooldown_hours: int,
    source_is_in_cooldown: Callable[[str | None, datetime | None], bool],
    should_auto_disable_source: Callable[[int, str | None, datetime | None], bool],
    utc_now_iso: Callable[[], str],
    iso: Callable[[datetime], str],
    parse_date: Callable[[Any], datetime],
    parse_date_inferred: Callable[[Any], tuple[datetime, bool]],
    canonicalize_url: Callable[[str], str],
    normalize_text: Callable[[str], str],
    sha1_hexdigest: Callable[[str], str],
    simhash64: Callable[[str], int],
    enrich_article_content: Callable[[str, str, str, str], tuple[str, str, list[dict]]],
    get_source_timeout_seconds: Callable[[str], int],
    slow_source_latency_ms: int,
) -> dict[str, Any]:
    inserted = 0
    duplicates = 0
    dead_letter_count = 0
    failed_sources = 0
    auto_disabled_sources = 0
    skipped_sources = 0
    not_modified_sources = 0
    defuddle_enriched = 0

    enabled_sources = [s for s in sources if s.enabled]
    logger.info(
        "Ingest stage started: sources=%d defuddle_enabled=%s",
        len(enabled_sources),
        defuddle_enabled,
    )

    for source in enabled_sources:
        now = datetime.now(timezone.utc)
        logger.info("Ingesting source=%s url=%s", source.source_id, source.feed_url)
        source_health = source_repo.get_health(conn, source.source_id)

        if source_health and source_is_in_cooldown(source_health["auto_disabled_until"], now):
            skipped_sources += 1
            logger.info(
                "Skipping source=%s reason=cooldown until=%s",
                source.source_id,
                source_health["auto_disabled_until"],
            )
            with transaction(conn):
                source_repo.insert_source_check(
                    conn,
                    source.source_id,
                    run_id,
                    utc_now_iso(),
                    CheckStatus.SKIPPED.value,
                    None,
                    f"Auto-disabled until {source_health['auto_disabled_until']}",
                )
            continue

        start = time.time()
        try:
            source_timeout_seconds = get_source_timeout_seconds(source.source_id)

            request_headers: dict[str, str] = {
                "User-Agent": "News-Aggregator/1.0 (RSS feed reader; +https://github.com/largelanguagemeowing/news)",
            }
            if source_health:
                if source_health["last_etag"]:
                    request_headers["If-None-Match"] = source_health["last_etag"]
                if source_health["last_modified"]:
                    request_headers["If-Modified-Since"] = source_health["last_modified"]

            @tenacity.retry(
                stop=tenacity.stop_after_attempt(2),
                wait=tenacity.wait_exponential(multiplier=1, min=2, max=10),
                retry=tenacity.retry_if_exception_type((requests.RequestException, RuntimeError)),
                retry_error_callback=lambda retry_state: None,
                before_sleep=lambda retry_state: logger.warning(
                    "Feed fetch failed, retrying (attempt %d/2) for source=%s timeout=%ss",
                    retry_state.attempt_number,
                    source.source_id,
                    source_timeout_seconds,
                ),
            )
            def _fetch_feed(feed_url: str) -> tuple[bytes | None, str | None, str | None, int]:
                logger.info(
                    "Feed fetch start source=%s url=%s timeout=%ss",
                    source.source_id,
                    feed_url,
                    source_timeout_seconds,
                )
                response = requests.get(feed_url, timeout=source_timeout_seconds, headers=request_headers)
                logger.info(
                    "Feed fetch response source=%s url=%s status=%d bytes=%d",
                    source.source_id,
                    feed_url,
                    response.status_code,
                    len(response.content or b""),
                )
                if response.status_code == 304:
                    return None, response.headers.get("ETag"), response.headers.get("Last-Modified"), 304
                response.raise_for_status()
                return (
                    response.content,
                    response.headers.get("ETag"),
                    response.headers.get("Last-Modified"),
                    response.status_code,
                )

            result = _fetch_feed(source.feed_url)
            if result is None:
                raise RuntimeError("Failed to fetch feed content after retries")

            feed_content, resp_etag, resp_last_modified, resp_status = result

            if resp_status == 304:
                not_modified_sources += 1
                logger.info("Feed not modified source=%s (304)", source.source_id)
                latency_ms = round((time.time() - start) * 1000, 2)
                old_avg = source_repo.get_latency_avg(conn, source.source_id)
                new_avg = round((old_avg * 0.8) + (latency_ms * 0.2), 2)
                with transaction(conn):
                    source_repo.update_http_cursors(conn, source.source_id, resp_etag, resp_last_modified, resp_status)
                    source_repo.update_source_success(conn, source.source_id, utc_now_iso(), None, new_avg)
                    source_repo.insert_source_check(conn, source.source_id, run_id, utc_now_iso(), CheckStatus.SUCCESS.value, latency_ms, None)
                continue

            if not feed_content:
                raise RuntimeError("Failed to fetch feed content after retries")

            feed = feedparser.parse(feed_content)
            bozo_exception = getattr(feed, "bozo_exception", None)
            if getattr(feed, "bozo", 0) or bozo_exception:
                raise RuntimeError(str(bozo_exception or "feed parse failed"))

            if "youtube.com/feeds/videos.xml" in source.feed_url:
                feed_title = str(getattr(feed, "feed", {}).get("title", "")).strip()
                if feed_title and feed_title != source.name:
                    with transaction(conn):
                        source_repo.update_source_name(conn, source.source_id, feed_title)
                    source.name = feed_title

            source_inserted = 0
            source_extraction_methods: dict[str, int] = {}
            latest_item_at: str | None = None

            with transaction(conn):
                for entry in feed.entries:
                    try:
                        link = str(entry.get("link", "")).strip()
                        title = clean_title(str(entry.get("title", "")).strip() or "(untitled)")
                        body = str(entry.get("summary", "")).strip()

                        body, method, enrichment_attempts = enrich_article_content(link, source.source_id, title, body)
                        source_extraction_methods[method] = source_extraction_methods.get(method, 0) + 1
                        if method == ExtractionMethod.DEFUDDLE.value:
                            defuddle_enriched += 1

                        attempt_ts = utc_now_iso()
                        for attempt in enrichment_attempts:
                            article_repo.record_enrichment_attempt(
                                conn,
                                article_url=link,
                                source_id=source.source_id,
                                method=attempt["method"],
                                status=attempt["status"],
                                duration_ms=attempt.get("duration_ms"),
                                error_message=attempt.get("error_message"),
                                output_chars=attempt.get("output_chars"),
                                created_at=attempt_ts,
                            )

                        fetched_at = utc_now_iso()
                        raw_published = entry.get("published") or entry.get("updated")
                        published, published_inferred = parse_date_inferred(raw_published)
                        if published_inferred:
                            published = parse_date(fetched_at)
                        canonical_url = canonicalize_url(link)
                        title_norm = normalize_text(title)
                        body_norm = normalize_text(body)
                        title_hash = sha1_hexdigest(title_norm)
                        body_hash = sha1_hexdigest(body_norm)
                        sh = str(simhash64(body_norm or title_norm))

                        was_inserted = article_repo.insert_article_if_new(
                            conn,
                            source.source_id,
                            link,
                            canonical_url,
                            title,
                            title_norm,
                            body,
                            body_norm,
                            iso(published),
                            fetched_at,
                            title_hash,
                            body_hash,
                            sh,
                            method,
                            published_inferred,
                        )
                        if was_inserted:
                            source_inserted += 1
                            article_repo.record_ingest_attempt(conn, run_id, source.source_id, link, "inserted", None, fetched_at)
                        else:
                            duplicates += 1
                            article_repo.record_ingest_attempt(conn, run_id, source.source_id, link, "duplicate", None, fetched_at)
                        latest_item_at = iso(published) if latest_item_at is None else max(latest_item_at, iso(published))
                    except Exception as entry_exc:
                        dead_letter_count += 1
                        entry_url = str(entry.get("link", "")).strip() or None
                        raw_json = None
                        try:
                            raw_json = json.dumps(dict(entry), default=str)[:2000]
                        except Exception:
                            pass
                        article_repo.record_dead_letter(
                            conn,
                            run_id,
                            source.source_id,
                            entry_url,
                            str(entry_exc)[:500],
                            raw_json,
                            utc_now_iso(),
                        )
                        logger.warning(
                            "Dead letter recorded source=%s url=%s error=%s",
                            source.source_id,
                            entry_url,
                            entry_exc,
                        )

                latency_ms = round((time.time() - start) * 1000, 2)
                old_avg = source_repo.get_latency_avg(conn, source.source_id)
                new_avg = round((old_avg * 0.8) + (latency_ms * 0.2), 2)

                source_repo.update_http_cursors(conn, source.source_id, resp_etag, resp_last_modified, resp_status)
                source_repo.update_source_success(
                    conn,
                    source.source_id,
                    utc_now_iso(),
                    latest_item_at,
                    new_avg,
                )
                source_repo.insert_source_check(
                    conn,
                    source.source_id,
                    run_id,
                    utc_now_iso(),
                    CheckStatus.SUCCESS.value,
                    latency_ms,
                    None,
                )

            inserted += source_inserted
            extraction_summary = ", ".join(f"{k}={v}" for k, v in sorted(source_extraction_methods.items()))

            if latency_ms > slow_source_latency_ms:
                logger.warning(
                    "SLOW SOURCE source=%s latency_ms=%.2f entries=%d methods=%s",
                    source.source_id,
                    latency_ms,
                    len(feed.entries),
                    extraction_summary,
                )
            log_source_complete(
                logger,
                source_id=source.source_id,
                inserted=source_inserted,
                entries=len(feed.entries),
                latency_ms=latency_ms,
                latest_item_at=latest_item_at,
                methods=source_extraction_methods,
            )

            sync_incident_resolve(
                conn,
                incident_key=f"source:{source.source_id}",
                run_id=run_id,
                resolution_message="Feed recovered and ingest succeeded.",
                client=issue_client,
            )
            conn.commit()

        except Exception as exc:
            failed_sources += 1
            logger.exception("Source ingest failed source=%s error=%s", source.source_id, exc)
            error_message_full = str(exc)
            error_message_short = error_message_full[:500]
            with transaction(conn):
                row = source_repo.get_failure_state(conn, source.source_id)
                failures = int(row["consecutive_failures"]) + 1 if row else 1
                should_disable = should_auto_disable_source(
                    failures=failures,
                    last_success_at=row["last_success_at"] if row else None,
                    now=now,
                )
                disabled_until = (
                    iso(now + timedelta(hours=source_auto_disable_cooldown_hours))
                    if should_disable
                    else None
                )
                if should_disable:
                    auto_disabled_sources += 1
                # Keep source_health.last_error compact for dashboards
                source_repo.update_source_failure(
                    conn,
                    source.source_id,
                    failures,
                    error_message_short,
                    disabled_until,
                    f"Auto-disabled after {failures} consecutive failures.",
                )
                # Store full error text in source_checks for deep troubleshooting
                source_repo.insert_source_check(
                    conn,
                    source.source_id,
                    run_id,
                    utc_now_iso(),
                    CheckStatus.FAILED.value,
                    None,
                    error_message_full,
                )
                if failures >= source_fail_threshold:
                    disable_note = (
                        f" Auto-disabled until {disabled_until}." if disabled_until else ""
                    )
                    signal = IncidentSignal(
                        key=f"source:{source.source_id}",
                        kind="source",
                        target_id=source.source_id,
                        message=f"Source failed {failures} consecutive times. Error: {exc}.{disable_note}",
                    )
                    sync_incident_open_or_update(conn, signal, run_id, issue_client)

    logger.info(
        "Ingest stage finished inserted_articles=%d failed_sources=%d skipped_sources=%d not_modified_sources=%d auto_disabled_sources=%d defuddle_enriched_articles=%d dead_letters=%d",
        inserted,
        failed_sources,
        skipped_sources,
        not_modified_sources,
        auto_disabled_sources,
        defuddle_enriched,
        dead_letter_count,
    )

    return {
        "inserted_articles": inserted,
        "duplicate_articles": duplicates,
        "dead_letters": dead_letter_count,
        "failed_sources": failed_sources,
        "skipped_sources": skipped_sources,
        "not_modified_sources": not_modified_sources,
        "auto_disabled_sources": auto_disabled_sources,
        "defuddle_enriched_articles": defuddle_enriched,
        "defuddle_enabled": defuddle_enabled,
    }
