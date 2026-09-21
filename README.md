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

## Workflow Dispatch

The pipeline runs as separate GitHub Actions workflows: `pipeline-fetch` (scheduled every 30 minutes), `pipeline-enrich` (scheduled daily), and `pipeline-classify`. `pipeline-all` runs all three stages back-to-back on manual `workflow_dispatch`, supporting source filtering and stage skipping. Enrichment backfill is done via the local CLI below, not via workflow dispatch.

### Inputs (`pipeline-all`)

| Input | Type | Default | Description |
|-------|------|---------|-------------|
| `pipeline_source` | choice | `all` | Which sources to ingest: `all`, `openai-only`, `non-openai`, or a single source ID |
| `skip_fetch` | boolean | `false` | Skip the fetch stage |
| `skip_enrich` | boolean | `false` | Skip the enrich stage |
| `skip_classify` | boolean | `false` | Skip the classify stage |

Available source IDs: `microsoft-ai-blog`, `google-ai-blog`, `google-deepmind-blog`, `openai-blog`, `apple-machine-learning`, `simon-willison`, `cursor-blog`, `cursor-changelog`, `matt-wolfe`, `fireship`, `ai-explained`, `hugging-face`.

### Pipeline source filtering examples

```bash
# OpenAI blog only
gh workflow run pipeline-all --repo largelanguagemeowing/news \
  -f pipeline_source=openai-only --ref master

# All sources except OpenAI
gh workflow run pipeline-all --repo largelanguagemeowing/news \
  -f pipeline_source=non-openai --ref master

# Single source
gh workflow run pipeline-all --repo largelanguagemeowing/news \
  -f pipeline_source=cursor-blog --ref master
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
