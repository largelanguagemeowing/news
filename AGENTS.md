# Agent Notes

## Local Development

Run a local HTTP server so the dashboard resolves paths the way production
does. Production is served under `/news/` on GitHub Pages, so use
`devserver.py` — it matches that subpath and serves `/news/data/` from the repo
`data/` directory:

```bash
python3 devserver.py --port 8008
```

Then open http://127.0.0.1:8008/news/

The dashboard needs an HTTP server; direct `file://` URLs won't work.

## Post-update workflow guidance

After pushing to `master`, trigger `pipeline-all` only when the generated data
needs refreshing: source config changes, pipeline/backend changes, or when you
want fresh `data/status/*` now. Dashboard-only UI/JS/CSS changes leave the
generated data unchanged — there's nothing to refresh.

```bash
gh workflow run pipeline-all --repo largelanguagemeowing/news --ref master
```

## Pages

A page is a folder containing an `index.html`; its URL is the clean path
`/news/<folder>/`. Add a page by creating `<folder>/index.html` and linking
with that clean path (e.g. `./newpage/`).