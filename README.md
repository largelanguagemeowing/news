# News Aggregator

Low-cost news curation pipeline designed for GitHub Actions scheduling and GitHub Pages observability.

## Local run

```bash
uv sync --all-groups
uv run pytest -q
uv run python -m app.jobs.pipeline
```

Status artifacts are written to `data/status/` and dashboard files live in `dashboard/`.

## Optional: Defuddle enrichment

The ingest stage can enrich article body text using the Defuddle CLI (`defuddle parse <url> --json`).

- Enable with `DEFUDDLE_ENABLED=1`
- Optional tuning:
  - `DEFUDDLE_TIMEOUT_SECONDS` (default `20`)
  - `DEFUDDLE_MAX_CHARS` (default `12000`)
  - `LOG_LEVEL` (default `INFO`, use `DEBUG` for verbose Defuddle diagnostics)

Example local usage:

```bash
npm install -g defuddle@0.13.0
DEFUDDLE_ENABLED=1 uv run python -m app.jobs.pipeline
```

Backfill already-fetched articles:

```bash
# Preview impact only
DEFUDDLE_ENABLED=1 uv run python -m app.jobs.backfill_defuddle --limit 300 --dry-run

# Write updates for short/missing bodies
DEFUDDLE_ENABLED=1 uv run python -m app.jobs.backfill_defuddle --limit 300 --only-missing

# Process all missing/short items (ignores --limit)
DEFUDDLE_ENABLED=1 uv run python -m app.jobs.backfill_defuddle --all --only-missing
```

CI note: `news-pipeline` keeps backfill disabled by default. On manual `workflow_dispatch`, set `enable_backfill=true` to run backfill. Optionally set `backfill_all=true` to process all missing/short items; otherwise it runs a bounded pass (`--limit 300 --only-missing`).

## Workflow Dispatch

The `news-pipeline` workflow runs every 30 minutes via cron (all sources, no backfill). Manual runs via `workflow_dispatch` support source filtering and backfill options. All selected parameters are printed at the top of each run for verification.

### Inputs

| Input | Type | Default | Description |
|-------|------|---------|-------------|
| `pipeline_source` | choice | `all` | Which sources to ingest: `all`, `openai-only`, `non-openai`, or a single source ID |
| `enable_backfill` | boolean | `false` | Run enriched backfill for existing items before the pipeline |
| `backfill_all` | boolean | `false` | Process all items (ignore default limit of 300) |
| `backfill_source` | choice | `all` | Which sources to backfill: `all`, `non-openai`, or a single source ID |
| `backfill_method` | choice | `default` | Force extraction method: `youtube`, `trafilatura`, `markdown_new`, `jina`, `defuddle` |

Available source IDs: `microsoft-ai-blog`, `google-ai-blog`, `google-deepmind-blog`, `openai-blog`, `apple-machine-learning`, `simon-willison`, `cursor-blog`, `cursor-changelog`, `matt-wolfe`, `fireship`, `ai-explained`, `hugging-face`.

### Pipeline source filtering examples

```bash
# OpenAI blog only
gh workflow run news-pipeline --repo largelanguagemeowing/news \
  -f pipeline_source=openai-only --ref master

# All sources except OpenAI
gh workflow run news-pipeline --repo largelanguagemeowing/news \
  -f pipeline_source=non-openai --ref master

# Single source
gh workflow run news-pipeline --repo largelanguagemeowing/news \
  -f pipeline_source=cursor-blog --ref master
```

### Backfill examples

```bash
# Backfill OpenAI blog with markdown.new only
gh workflow run news-pipeline --repo largelanguagemeowing/news \
  -f enable_backfill=true \
  -f backfill_all=true \
  -f backfill_source=openai-blog \
  -f backfill_method=markdown_new \
  --ref master

# Backfill cursor-blog with full fallback chain
gh workflow run news-pipeline --repo largelanguagemeowing/news \
  -f enable_backfill=true \
  -f backfill_all=true \
  -f backfill_source=cursor-blog \
  --ref master

# Backfill all sources except OpenAI
gh workflow run news-pipeline --repo largelanguagemeowing/news \
  -f enable_backfill=true \
  -f backfill_all=true \
  -f backfill_source=non-openai \
  --ref master

# Backfill all sources with default logic
gh workflow run news-pipeline --repo largelanguagemeowing/news \
  -f enable_backfill=true \
  -f backfill_all=true \
  --ref master
```

### Local backfill CLI

```bash
# Preview impact only
DEFUDDLE_ENABLED=1 uv run python -m app.jobs.backfill_defuddle --limit 300 --dry-run

# Single source with specific method
DEFUDDLE_ENABLED=1 uv run python -m app.jobs.backfill_defuddle --all --source-id openai-blog --only-method markdown_new

# Exclude a source
DEFUDDLE_ENABLED=1 uv run python -m app.jobs.backfill_defuddle --all --exclude-source openai-blog
```

CLI options: `--source-id`, `--exclude-source` (comma-separated), `--only-method`, `--skip-enriched`, `--only-missing`, `--only-dirty`, `--max-markdown-new` (default 400).
