# Pipeline Architecture

## Overview

The news aggregator runs a **4-stage pipeline** that ingests RSS feeds, deduplicates articles into events, classifies them by topic, and exports status JSON files for the dashboard.

```
[Sources] → Ingest → [articles table] → Cluster → [events table]
                                              → Categorize → [events.category_labels]
                                                                 → Export → [articles.json, events.json, ...]
```

## Stage 1: Ingest (`stages_ingest.py`)

Fetches RSS/Atom feeds from configured sources, parses entries, enriches bodies, and stores in SQLite.

**Flow:**

1. For each enabled source, fetch the feed URL
2. Parse feed entries with `feedparser`
3. For each entry: deduplicate (canonical URL + published_at), enrich body content via a priority chain:
   - YouTube sources: `youtube` metadata + optional transcript
   - OpenAI sources: `markdown.new` → `compress.new` → `jina` → `defuddle` → `trafilatura`
   - Other sources: `trafilatura` → `jina` → `defuddle`
4. Insert into `articles` table (title, body, url, source_id, simhash, etc.)

**Key tables:** `articles`, `source_health`, `source_checks`, `dead_letters`

**Source config:** `config/sources.yml` — list of `{id, name, feed_url, default_category, enabled}`

## Stage 2: Cluster (`stages_cluster.py:cluster_stage`)

Groups duplicate/related articles into events using pairwise similarity.

**Flow:**

1. Query articles within `cluster_lookback_days` window
2. For each article, compare against existing event groups:
   - Time window check: articles must be within `cluster_window_hours` of the group representative
   - Similarity score: `pair_similarity(title_norm, simhash)` — combination of text + hash similarity
   - Threshold: `similarity_threshold` (configurable)
3. Clear and rebuild `events` + `event_members` tables
4. Each event gets: `cluster_key`, `canonical_title`, `first_seen`, `last_seen`, `representative_article_id`, `source_count`

**Key tables:** `events` (created), `event_members` (created)

## Stage 3: Categorize (`stages_cluster.py:categorize_stage`)

Assigns a topic label and confidence to each event.

**Flow:**

1. For each event, classify the representative article's title + body
2. Classification chain (`classify_event` in `pipeline.py:249`):
   ```
   classify_event(title, body, default_category)
     → ml_classifier.classify_with_model()  # ML-based, returns (label, confidence) or None
     → classify_article()                   # Rule-based fallback (see classifier.py)
   ```
3. Update `events.category_labels` and `events.confidence`

### Classifier Rules (`classifier.py`)

**`TOPIC_RULES`**: Per-topic keyword patterns with weights. A topic score is computed as:
```
score = sum(weight * occurrences * title_boost for each matching pattern)
```
Where `title_boost = 1.8` if the pattern also appears in the title.

Available topics (2026):
`ai-models`, `agents`, `research`, `product`, `security`, `safety`, `policy`, `funding`, `infrastructure`, `coding`, `robotics`, `tutorial`, `development`, `education`, `enterprise`, `hardware`, `media`, `news`, `opinion`, `tools`

**`classify_article()`**: Returns `Classification(label, confidence, scores, evidence)`. Confidence is derived from top score + margin over runner-up.

**`extract_article_tags()`**: Generates tags from text by matching `TAG_RULES` patterns (independent of topic classification).

**`generate_weak_labels()`**: Standalone utility to batch-classify articles for training data generation.

## Stage 4: Export (`stages_export.py`)

Reads DB state and writes JSON status files consumed by the dashboard.

**Files written to `data/status/`:**

| File | Source | Description |
|------|--------|-------------|
| `summary.json` | Aggregated queries | Pipeline health, source counts, event counts |
| `sources.json` | `sources` + `source_health` | Per-source status, uptime, latency |
| `runs.json` | `pipeline_runs` + `stage_runs` | Recent pipeline run history |
| `incidents.json` | `incidents` | Open/resolved incidents |
| `events.json` | `events` | Recent event clusters |
| `articles.json` | `articles` + `events` + export logic | Enriched article list (see below) |
| `source_health.json` | `source_health` | Raw source health rows |
| `source_checks.json` | `source_checks` | Check history |
| `event_members.json` | `event_members` | Article-to-event mappings |
| `ingest_attempts.json` | `article_ingest_attempts` | Ingest attempt log |
| `enrichment_attempts.json` | `article_enrichment_attempts` | Enrichment attempt log |
| `dead_letters.json` | `dead_letters` | Failed ingest entries |

### Article Export (`build_articles`)

For each article (within `articles_export_limit`):

1. Determine **topic**:
   - If article belongs to an event: `events.category_labels`
   - Otherwise: `classify_event(title, body, default_category)` fallback
2. Generate **tags** via `extract_article_tags(title, body, source_id)`
3. Prepend topic to tags if not already present
4. Resolve YouTube DeArrow thumbnail mappings

## Classifier Modules

| Module | File | Purpose |
|--------|------|---------|
| Rule-based | `classifier.py` | Keyword scoring, tag extraction, weak label generation |
| ML-based | `ml_classifier.py` | ML model inference (fallback in `classify_event`) |
| V2 (unused) | `classifier_v2.py` | Alternative implementation, not integrated |

## Data Flow Diagram

```
                               ┌─────────────┐
                               │  Sources     │
                               │ (sources.yml)│
                               └──────┬──────┘
                                      │
                                      ▼
 ┌──────────────────────────────────────────────────────┐
 │                   Ingest Stage                       │
 │  RSS/Atom → feedparser → enrich (defuddle/jina/...) │
 │  → deduplicate → INSERT INTO articles                │
 └──────────────────────┬───────────────────────────────┘
                        │
                        ▼
 ┌──────────────────────────────────────────────────────┐
 │                  Cluster Stage                       │
 │  pairwise similarity (title_norm + simhash)          │
 │  → group articles into events                        │
 │  → INSERT INTO events, event_members                 │
 └──────────────────────┬───────────────────────────────┘
                        │
                        ▼
 ┌──────────────────────────────────────────────────────┐
 │                Categorize Stage                      │
 │  For each event: classify(title, body)               │
 │  → UPDATE events SET category_labels                 │
 │    (ML classifier → rule-based fallback)             │
 └──────────────────────┬───────────────────────────────┘
                        │
                        ▼
 ┌──────────────────────────────────────────────────────┐
 │                  Export Stage                        │
 │  Build JSON files from DB:                           │
 │  • events.category_labels → topic for clustered     │
 │  • classify_event() → topic for unclustered         │
 │  • extract_article_tags() → tags                    │
 │  → WRITE data/status/*.json                         │
 └──────────────────────────────────────────────────────┘
                        │
                        ▼
                  ┌─────────────┐
                  │  Dashboard  │
                  │ (static SPA)│
                  └─────────────┘
```

## Key Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| `similarity_threshold` | 0.85 | Min score to cluster two articles |
| `cluster_window_hours` | 48 | Max time gap between clustered articles |
| `cluster_lookback_days` | 14 | How far back to look for clustering |
| `articles_export_limit` | 500 | Max articles in exported JSON |
| `events_export_limit` | 200 | Max events in exported JSON |
