# Data Engineering Improvements Plan

> Generated: 2026-03-17
> Scope: Data ingestion state tracking and storage best practices

## Summary

The pipeline has decent **current-state** tracking (`pipeline_runs`, `stage_runs`, `source_checks`, `source_health`), but weak **auditability**. The biggest gaps are: no per-entry ingest lineage, no enrichment attempt history, drifting source counters, and a **P0 safety issue** where a failed `cluster` run can commit partial destructive changes.

---

## 🔴 P0 — Safety / Data Integrity

### 1. Cluster stage is destructive and non-atomic

**File:** `stages_cluster.py:55-56`

`DELETE FROM event_members` + `DELETE FROM events` runs before rebuild. If an exception occurs mid-rebuild, partial state gets committed because the stage calls `conn.commit()` internally while the orchestrator also commits on failure.

**Fix:** Remove stage-owned commits; let the orchestrator own commit/rollback per stage. Wrap each stage in an explicit transaction in the orchestrator so a failed stage rolls back cleanly.

### 2. `parse_date()` silently defaults to `now`

**File:** `pipeline.py:116-129`

When `published_at` is missing/unparseable, it defaults to `datetime.now()`. Since `published_at` is part of the dedup key (`UNIQUE(source_id, canonical_url, published_at)`), this breaks idempotency — re-running the pipeline with the same broken entry creates a *new* row each time.

**Fix:** Add `published_at_inferred` boolean flag to the articles table; use a deterministic fallback (e.g., `fetched_at` or a stable hash-derived timestamp) instead of `now()`.

---

## 🟠 P1 — Observability / Audit Gaps

### 3. No per-entry ingest audit trail

`INSERT OR IGNORE` silently drops duplicates. You can't distinguish "0 new articles because feed is stale" from "0 new articles because all were duplicates" vs. "0 because parsing failed silently."

**Fix:** Add `article_ingest_attempts` table tracking `status=inserted|duplicate|invalid|failed` per entry per run.

### 4. No enrichment attempt history

`articles.extraction_method` stores only the final winner. You can't answer: "Was trafilatura tried and failed? Did markdown.new rate-limit?"

**Fix:** Add `article_enrichment_attempts` table logging each method tried, status, duration_ms, error_message, output_chars, old_body_hash, new_body_hash.

### 5. `items_24h` / `errors_24h` counters drift

**File:** `source_repo.py:84`

`errors_24h` only increments, never resets or decays. `items_24h` appears never updated at all. Dashboards will show stale/inflated numbers over time.

**Fix:** Derive these at export time from `source_checks` and `articles.fetched_at` instead of storing mutable counters. SQLite can handle the queries at this scale with small indexes.

### 6. Failed/rejected articles have no dead-letter record

If an individual feed entry fails to parse (bad URL, empty title), it vanishes with no trace.

**Fix:** Add a `dead_letters` table with columns: `run_id`, `source_id`, `url`, `error_message`, `raw_entry_json`, `created_at`. Enables debugging and retry.

---

## 🟡 P2 — Operational Gaps

### 7. Backfill runs are invisible

**File:** `backfill_defuddle.py`

Backfills mutate production article state but create no `pipeline_runs` record. No audit trail, no metrics history, no linkage to article changes.

**Fix:** Add `run_type` column to `pipeline_runs` (values: `pipeline`, `backfill`, `repair`) and log backfill runs with their filter parameters and summary metrics.

### 8. No feed-level cursors

Every run re-fetches and re-parses the full feed. No `ETag`/`Last-Modified` tracking means wasted bandwidth and no way to distinguish "feed unchanged" from "feed empty."

**Fix:** Add `last_etag`, `last_modified`, `last_http_status` columns to `source_health`. Use conditional requests (`If-None-Match`, `If-Modified-Since`) in feed fetching.

### 9. Ad-hoc schema migration

**File:** `db.py:131-147`

`_ensure_columns()` is a hand-rolled `ALTER TABLE` check. No schema version, no ordered migrations, no history. Hard to test upgrades from old DBs.

**Fix:** Add a `schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT)` table with numbered migration functions run transactionally on startup.

---

## ✅ What's Already Good

- `pipeline_runs` + `stage_runs` with metrics JSON
- `source_checks` per-run per-source history
- `source_health` with circuit breaker / auto-disable
- Incident management with GitHub Issue integration
- Structured logging
- Concurrency guard in CI (`cancel-in-progress: false`)
- Clean repo layer separation (`app/repos/`)
- Enrichment fallback chain with rate-limit awareness

---

## Implementation Order

| # | Item | Priority | Effort | Status |
|---|------|----------|--------|--------|
| 1 | Cluster stage atomicity | P0 | S | ✅ |
| 2 | Deterministic `parse_date` fallback | P0 | S | ✅ |
| 3 | Per-entry ingest audit table | P1 | M | ✅ |
| 4 | Enrichment attempt history table | P1 | M | ✅ |
| 5 | Derive rolling counters at export | P1 | S | ✅ |
| 6 | Dead letter table | P1 | M | ✅ |
| 7 | Backfill run tracking | P2 | S | ✅ |
| 8 | Feed-level HTTP cursors | P2 | M | ✅ |
| 9 | Schema migration system | P2 | M | ✅ |
