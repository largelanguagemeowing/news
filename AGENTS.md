# Agent Notes

## The database is a release asset

`data/news.db` is not tracked in this repo. It lives as the `news-db` release
asset: each pipeline workflow downloads it at the start and re-uploads it at the
end, and a local run starts from an empty file. Read production rows with:

```bash
gh release download news-db -p news.db -D /tmp/rel --clobber
sqlite3 /tmp/rel/news.db "SELECT count(*) FROM articles"
```

`data/status/*.json` is exported from that DB and committed by CI. To change what
the dashboard shows, change the DB or the pipeline and regenerate it.

## Retiring a source

1. Delete the source's block from `config/sources.yml`.
2. `gh workflow run pipeline-db-maintenance -f source_id=<id>`
3. `gh workflow run pipeline-fetch --ref feat/defuddle-integration`

Done when `config/sources.yml`, `data/status/sources.json`, and
`data/status/articles.json` all lack the id (step 3 is what rewrites the status
files). Step 2 is required because `upsert_sources` in `app/jobs/pipeline.py`
only inserts and updates: a source deleted from config keeps its DB row, and
`build_sources` selects `WHERE enabled = 1`, so the dashboard keeps serving it.

## Pipelines

| Change | Run |
|---|---|
| source config, pipeline code, or a status refresh | `gh workflow run pipeline-fetch --ref feat/defuddle-integration` |
| `dashboard/**` or `data/status/**` | nothing — `deploy-pages` publishes on push |

`news-pipeline` is disabled on GitHub, so the `--ref master` command in older
notes no longer applies. The live workflows are `pipeline-fetch` (also the
30-minute cron), `pipeline-enrich` (article bodies), `pipeline-classify`,
`pipeline-all` (all three stages in one run), and `pipeline-db-maintenance`.
They share the `news-pipeline` concurrency group, so a single writer touches the
DB at a time. `--ref` takes the repo default branch, `feat/defuddle-integration`.

## Local dashboard

```bash
python3 devserver.py --port 8008   # http://127.0.0.1:8008/news/
```

Serves `dashboard/` at `/news/` and `data/` at `/news/data/`, matching the
GitHub Pages subpath. The dashboard fetches `data/status/*.json` over HTTP, so
serve it rather than opening the files directly.

## Tests

`uv run pytest`. `tests/test_categorization.py::test_classify_event_ai_models`
fails against the committed classifier (0.7 confidence, 0.8 threshold). pytest
also writes the real `data/status/markdown_new_quota.json`, so restore it before
committing: `git checkout -- data/status/markdown_new_quota.json`.

## Pages

Each page is a directory holding an `index.html`, reached by its clean path
(`dashboard/tags/index.html` -> `/news/tags/`). Point new links at the trailing
slash (`./newpage/`).

## Project skills

- `frontend-design`: `.agents/skills/frontend-design/SKILL.md`
