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

## Workflow Dispatch Backfill Examples

### Backfill OpenAI blog with markdown.new only:

```bash
gh workflow run news-pipeline --repo largelanguagemeowing/news \
  -f enable_backfill=true \
  -f backfill_all=true \
  -f backfill_source=openai-blog \
  -f backfill_method=markdown_new \
  --ref feat/defuddle-integration
```

### Backfill cursor-blog with full fallback:

```bash
gh workflow run news-pipeline --repo largelanguagemeowing/news \
  -f enable_backfill=true \
  -f backfill_all=true \
  -f backfill_source=cursor-blog \
  --ref feat/defuddle-integration
```

### Backfill all sources with default logic:

```bash
gh workflow run news-pipeline --repo largelanguagemeowing/news \
  -f enable_backfill=true \
  -f backfill_all=true \
  --ref feat/defuddle-integration
```

### Backfill options:

- `backfill_source`: Filter to specific source (e.g., `openai-blog`, `cursor-blog`)
- `backfill_method`: Force specific extraction method (`youtube`, `trafilatura`, `markdown_new`, `jina`, `defuddle`)
- `backfill_all=true`: Process all matching articles (ignores default limit)
- `--skip-enriched`: Skip articles already enriched (useful for resuming)
- `--max-markdown-new`: Limit markdown.new requests (default 400, stays under 500/day limit)
