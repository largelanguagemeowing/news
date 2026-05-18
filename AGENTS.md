# Agent Notes

## Local Development

Run a local HTTP server to test the dashboard with proper path resolution.

### Recommended (matches production subpath)

Production is hosted under `/news/` on GitHub Pages. Use `devserver.py` to match
that locally (and to serve `/news/data/` from the repo `data/` directory):

```bash
python3 devserver.py --port 8008
```

Then open http://127.0.0.1:8008/news/

### Legacy (root-served)

```bash
# From the project root, serve the dashboard directory
python3 -m http.server 8080 --directory dashboard

# Or with Python 2
python -m SimpleHTTPServer 8080
```

Then open http://localhost:8080/ in your browser.

**Note:** The dashboard expects an HTTP server (won't work with direct `file://` URLs).

## Post-update workflow guidance

After pushing to `master`, trigger `news-pipeline` **only when fresh generated data is needed** (for example: source config changes, pipeline/backend changes, or when you want to refresh `data/status/*` immediately).

For **dashboard-only UI/JS/CSS changes** that do not affect data generation, **do not trigger** `news-pipeline`.

```bash
gh workflow run news-pipeline --repo largelanguagemeowing/news --ref master
```

## Project Skills

- `frontend-design`: `.agents/skills/frontend-design/SKILL.md`

## URL Structure

Pages use clean URLs without `.html` extension via the folder + `index.html` pattern:

| Page | File Location | URL |
|------|---------------|-----|
| Feed | `dashboard/feed/index.html` | `/news/feed/` |
| Health | `dashboard/health/index.html` | `/news/health/` |
| Tags | `dashboard/tags/index.html` | `/news/tags/` |
| RSS | `dashboard/rss/index.html` | `/news/rss/` |
| Root | `dashboard/index.html` | `/news/` |

When adding new pages, create a folder with `index.html` inside and link using the clean path (e.g., `./newpage/` instead of `./newpage.html`).
