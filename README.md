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
```

CI note: `news-pipeline` runs this backfill step before ingest on every workflow run.
