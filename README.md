# News Aggregator

A low-cost news curation pipeline that turns 20 AI and ML RSS feeds into
deduplicated, topic-labeled stories on one page. Scheduled on GitHub Actions'
free tier and served as static files from GitHub Pages — no server and no
database to run.

**See it live:** https://largelanguagemeowing.github.io/news/

- **Gathers** articles from 20 AI/ML sources (OpenAI, Google DeepMind,
  Microsoft AI, Anthropic, Hugging Face, Simon Willison — the full list is in
  `config/sources.yml`).
- **Merges** duplicate coverage of the same story into one event with a source
  count.
- **Enriches** each article with its full body text.
- **Labels** each event with a topic and a confidence score.
- **Refreshes** on schedule — ingest every 30 minutes, enrichment daily,
  classification and export on demand — and republishes an RSS feed alongside
  the dashboard.

Contributor docs: local development in `docs/development.md`, workflow dispatch
in `docs/operations.md`, design decisions in `docs/architecture.md`.