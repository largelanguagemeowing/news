# Agent Notes

## Post-UI update step

When dashboard pages or navigation are changed:
1. Commit all related changes.
2. Push to `master`.
3. Trigger the `news-pipeline` GitHub Actions workflow (`workflow_dispatch`) so `data/status/*` and GitHub Pages are refreshed immediately.

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
