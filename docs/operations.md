# Pipeline Operations (GitHub Actions)

The pipeline runs as separate GitHub Actions workflows:

- `pipeline-fetch` — scheduled every 30 minutes (ingest + export)
- `pipeline-enrich` — scheduled daily (body enrichment + export)
- `pipeline-classify` — on demand (cluster, categorize, export)
- `pipeline-all` — all three back-to-back on `workflow_dispatch`, with source
  filtering and stage skipping

Enrichment backfill runs through the local CLI (`docs/development.md`), not
through workflow dispatch.

## Dispatch inputs

`pipeline-all` takes a `pipeline_source` input (`all`, `openai-only`,
`non-openai`, or a single source ID from `config/sources.yml`) and one
skip flag per stage (`skip_fetch`, `skip_enrich`, `skip_classify`).

## Examples

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

## Deploy

Pushing to `master` when `dashboard/`, `data/status/`, or the deploy workflow
changed rebuilds and deploys the Pages site; a completed pipeline run triggers
a deploy too. The site is assembled as a static copy — `dashboard/` plus
`data/status/` under the `/news/` base path, with an RSS feed generated from
the exported summary and articles.