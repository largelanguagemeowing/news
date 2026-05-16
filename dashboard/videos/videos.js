async function getJson(paths) {
  for (const path of paths) {
    const response = await fetch(path, { cache: 'no-store' });
    if (response.ok) return response.json();
  }
  throw new Error(`Failed to fetch ${paths.join(' or ')}`);
}

const loaderEl = document.getElementById('videosLoader');
const contentEl = document.getElementById('videosContent');
const gridEl = document.getElementById('videosGrid');
const emptyEl = document.getElementById('videosEmpty');
const countEl = document.getElementById('videosCount');
const searchEl = document.getElementById('videosSearch');
const sortEl = document.getElementById('videosSort');

let allVideos = [];

function getYouTubeId(url) {
  try {
    const u = new URL(url);
    if (u.hostname === 'youtu.be') return u.pathname.slice(1);
    return u.searchParams.get('v');
  } catch {
    return null;
  }
}

function formatDate(value) {
  if (!value) return '';
  const dt = new Date(value);
  if (Number.isNaN(dt.getTime())) return value;
  return dt.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
}

function render() {
  const q = searchEl.value.trim().toLowerCase();
  const sort = sortEl.value;

  let filtered = allVideos.filter((v) => {
    if (!q) return true;
    return v.title.toLowerCase().includes(q)
        || v.source_name.toLowerCase().includes(q)
        || (v.tags || []).some((t) => t.toLowerCase().includes(q));
  });

  switch (sort) {
    case 'oldest':
      filtered.sort((a, b) => new Date(a.published_at) - new Date(b.published_at));
      break;
    case 'channel':
      filtered.sort((a, b) => a.source_name.localeCompare(b.source_name));
      break;
    case 'title':
      filtered.sort((a, b) => a.title.localeCompare(b.title));
      break;
    default:
      filtered.sort((a, b) => new Date(b.published_at) - new Date(a.published_at));
  }

  countEl.textContent = `${filtered.length} video${filtered.length !== 1 ? 's' : ''}`;

  if (!filtered.length) {
    gridEl.innerHTML = '';
    emptyEl.hidden = false;
    return;
  }
  emptyEl.hidden = true;

  gridEl.innerHTML = filtered.map(renderCard).join('');
}

function renderCard(v) {
  const id = getYouTubeId(v.url);
  const thumb = id
    ? `https://img.youtube.com/vi/${id}/hqdefault.jpg`
    : '';
  const channelLink = `../?source=${encodeURIComponent(v.source_id)}`;

  return `
    <div class="video-card">
      <a class="video-thumb" href="${escapeHtml(v.url)}" target="_blank" rel="noreferrer">
        ${thumb ? `<img src="${thumb}" alt="" loading="lazy">` : '<div style="display:flex;align-items:center;justify-content:center;height:100%;color:var(--ink-soft);font-size:13px;">No thumbnail</div>'}
        <div class="play-overlay">
          <svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="5 3 19 12 5 21 5 3" fill="#fff" stroke="none"/></svg>
        </div>
      </a>
      <div class="video-body">
        <h3 class="video-title"><a href="${escapeHtml(v.url)}" target="_blank" rel="noreferrer">${escapeHtml(v.title)}</a></h3>
        <div class="video-meta">
          <a class="video-channel" href="${channelLink}" style="color:var(--ink-soft);text-decoration:none;">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m22 8-6 4 6 4V8Z"/><rect width="14" height="12" x="2" y="6" rx="2" ry="2"/></svg>
            ${escapeHtml(v.source_name)}
          </a>
          <span>${formatDate(v.published_at)}</span>
        </div>
        ${v.topic ? `<div style="margin-top:8px;"><span class="video-topic">${escapeHtml(v.topic)}</span></div>` : ''}
      </div>
    </div>
  `;
}

function escapeHtml(text) {
  if (text == null) return '';
  const div = document.createElement('div');
  div.textContent = String(text);
  return div.innerHTML;
}

function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

async function main() {
  try {
    const articles = await getJson(['../../data/status/articles.json', '../data/status/articles.json', './data/status/articles.json']);
    allVideos = articles.filter((a) => a.extraction_method === 'youtube' && getYouTubeId(a.url));
    render();

    loaderEl.hidden = true;
    contentEl.hidden = false;
  } catch (err) {
    loaderEl.innerHTML = `<div class="videos-empty"><h2>Failed to load videos</h2><p>${escapeHtml(err.message)}</p></div>`;
  }
}

searchEl.addEventListener('input', debounce(render, 200));
sortEl.addEventListener('change', render);

main();
