# News Aggregator

Low-cost news curation pipeline designed for GitHub Actions scheduling and GitHub Pages observability.

## Local run

```bash
uv sync --all-groups
uv run pytest -q
uv run python -m app.jobs.pipeline
```

Status artifacts are written to `data/status/` and dashboard files live in `dashboard/`.
