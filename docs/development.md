# Local Development

## Get started

```bash
uv sync --all-groups
uv run pytest -q
```

## Run the pipeline jobs locally

Each workflow job runs as a module. Fetch with `--export` writes the status
files; enrichment and classification export as part of their run:

```bash
uv run python -m app.jobs.fetch_pipeline --export
uv run python -m app.jobs.enrich_pipeline
uv run python -m app.jobs.classify_pipeline
```

Artifacts land in `data/status/`; the dashboard source lives in `dashboard/`.

Preview the dashboard locally the way production serves it (the `/news/`
subpath, with `/news/data/` mapped to `data/`) using `devserver.py` — see
`AGENTS.md` for the command.

## Enrichment

### Defuddle (optional)

`defuddle` is an experimental local extractor and is off by default. It joins
the extraction chain when enabled; default values and tuning knobs live in
`app/settings.py`, overridable through the environment
(`DEFUDDLE_ENABLED`, `DEFUDDLE_TIMEOUT_SECONDS`, `DEFUDDLE_MAX_CHARS`,
`LOG_LEVEL` — `DEBUG` for verbose Defuddle diagnostics).

```bash
npm install -g defuddle@0.13.0
DEFUDDLE_ENABLED=1 uv run python -m app.jobs.fetch_pipeline --export
```

### Backfill

Backfill re-enriches already-fetched articles that the scheduled run would
skip — short or missing bodies, or a specific source or extraction method:

```bash
# Preview impact only
DEFUDDLE_ENABLED=1 uv run python -m app.jobs.backfill_defuddle --limit 300 --dry-run

# Write updates for short/missing bodies
DEFUDDLE_ENABLED=1 uv run python -m app.jobs.backfill_defuddle --limit 300 --only-missing

# Process all missing/short items (ignores --limit)
DEFUDDLE_ENABLED=1 uv run python -m app.jobs.backfill_defuddle --all --only-missing

# Single source with a specific method
DEFUDDLE_ENABLED=1 uv run python -m app.jobs.backfill_defuddle --all --source-id openai-blog --only-method markdown_new

# Exclude a source
DEFUDDLE_ENABLED=1 uv run python -m app.jobs.backfill_defuddle --all --exclude-source openai-blog
```

Every filter, limit, and method flag is listed in `--help`.