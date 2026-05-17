async function getJson(paths) {
  for (const path of paths) {
    const response = await fetch(path, { cache: 'no-store' });
    if (response.ok) return response.json();
  }
  throw new Error(`Failed to fetch ${paths.join(' or ')}`);
}

async function main() {
  const params = new URLSearchParams(location.search);
  const articleId = params.get('id');
  const container = document.getElementById('articleContent');

  if (!articleId) {
    container.innerHTML = '<div class="article-not-found"><h2>No article ID specified</h2><p>Add <code>?id=ARTICLE_ID</code> to the URL.</p></div>';
    return;
  }

  try {
    const articles = await getJson(['./data/status/articles.json', '../data/status/articles.json', '../../data/status/articles.json']);
    const article = articles.find((a) => String(a.article_id) === articleId);

    if (!article) {
      container.innerHTML = `<div class="article-not-found"><h2>Article not found</h2><p>No article with ID <code>${escapeHtml(articleId)}</code>.</p></div>`;
      return;
    }

    container.innerHTML = renderArticle(article);
  } catch (err) {
    container.innerHTML = `<div class="article-not-found"><h2>Failed to load article</h2><p>${escapeHtml(err.message)}</p></div>`;
  }
}

function renderArticle(article) {
  const pubDate = formatDateTime(article.published_at);
  const fetchDate = formatDateTime(article.fetched_at);
  const tags = Array.isArray(article.tags) ? article.tags : [];
  const bodyHtml = renderBody(article.body);

  return `
    <h1 class="article-title">${escapeHtml(article.title)}</h1>
    <div class="article-layout">
      <aside class="article-sidebar">
        <div class="article-sidebar-section">
          <div class="sidebar-label">Identification</div>
          <div class="sidebar-value">#${escapeHtml(String(article.article_id))}</div>
        </div>

        <div class="article-sidebar-section">
          <div class="sidebar-label">Source</div>
          <div class="sidebar-value"><a href="../?source=${encodeURIComponent(article.source_id)}">${escapeHtml(article.source_name || article.source_id)}</a></div>
        </div>

        <div class="article-sidebar-section">
          <div class="sidebar-label">Topic</div>
          <div class="sidebar-value"><a href="../?topic=${encodeURIComponent(article.topic || '')}">${escapeHtml(article.topic || 'general')}</a></div>
        </div>

        <div class="article-sidebar-section">
          <div class="sidebar-label">Extraction</div>
          <div class="sidebar-value">${escapeHtml(article.extraction_method || 'rss')}</div>
        </div>

        <div class="article-sidebar-section">
          <div class="sidebar-label">Published</div>
          <div class="sidebar-value">${pubDate}</div>
        </div>

        <div class="article-sidebar-section">
          <div class="sidebar-label">Fetched</div>
          <div class="sidebar-value">${fetchDate}</div>
        </div>

        ${article.url ? `
        <div class="article-sidebar-section">
          <div class="sidebar-label">Original</div>
          <div class="sidebar-value"><a class="sidebar-original" href="${escapeHtml(article.url)}" target="_blank" rel="noreferrer">Open article ↗</a></div>
        </div>
        ` : ''}

        ${tags.length ? `
        <div class="article-sidebar-section">
          <div class="sidebar-label">Tags</div>
          <div class="sidebar-tags">
            ${tags.map((t) => `<a class="sidebar-tag" href="../?tag=${encodeURIComponent(t)}">${escapeHtml(t)}</a>`).join('')}
          </div>
        </div>
        ` : ''}
      </aside>

      <div class="article-body-wrap">
        ${bodyHtml}
      </div>
    </div>
  `;
}

function renderBody(body) {
  if (!body) return '<em>No body content available.</em>';
  return `<pre style="font-family: 'IBM Plex Mono', 'SF Mono', Monaco, Consolas, monospace; font-size: 13px; line-height: 1.6; margin: 0; white-space: pre-wrap; word-wrap: break-word; overflow-wrap: anywhere;">${escapeHtml(body)}</pre>`;
}

function formatDateTime(value) {
  if (!value) return '-';
  const dt = new Date(value);
  if (Number.isNaN(dt.getTime())) return value;
  return dt.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
}

function escapeHtml(text) {
  if (text == null) return '';
  const div = document.createElement('div');
  div.textContent = String(text);
  return div.innerHTML;
}

main().catch((err) => {
  document.getElementById('articleContent').innerHTML =
    `<div class="article-not-found"><h2>Error</h2><p>${escapeHtml(err.message)}</p></div>`;
});
