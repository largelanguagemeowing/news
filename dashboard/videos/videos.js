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
const searchEl = document.getElementById('searchInput');
const channelEl = document.getElementById('videosChannel');
const sortEl = document.getElementById('videosSort');
const clearFiltersEl = document.getElementById('clearFilters');
const filterBadgeEl = document.getElementById('filterBadge');

let allVideos = [];
let availableVideos = [];

function getYouTubeId(url) {
  try {
    const u = new URL(url);
    if (u.hostname === 'youtu.be') return u.pathname.slice(1);
    const shortsMatch = u.pathname.match(/^\/shorts\/([a-zA-Z0-9_-]+)/);
    if (shortsMatch) return shortsMatch[1];
    return u.searchParams.get('v');
  } catch {
    return null;
  }
}

function filterAvailableVideos(videos) {
  return videos.filter((v) => v.available !== false);
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

  let filtered = availableVideos.filter((v) => {
    if (channelEl.value && v.source_id !== channelEl.value) return false;
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
  updateFilterBadge();

  if (!filtered.length) {
    gridEl.innerHTML = '';
    emptyEl.hidden = false;
    return;
  }
  emptyEl.hidden = true;

  gridEl.innerHTML = filtered.map(renderCard).join('');
}

function updateFilterBadge() {
  if (!filterBadgeEl) return;
  const activeCount = channelEl.value ? 1 : 0;
  if (activeCount > 0) {
    filterBadgeEl.textContent = activeCount;
    filterBadgeEl.hidden = false;
  } else {
    filterBadgeEl.hidden = true;
  }
}

function renderCard(v) {
  const id = getYouTubeId(v.url);
  const thumb = v.dearrow_thumbnail_url
    ? v.dearrow_thumbnail_url
    : id
      ? `https://img.youtube.com/vi/${id}/mqdefault.jpg`
      : '';
  const channelLink = `../?source=${encodeURIComponent(v.source_id)}`;
  const detailLink = id ? `./detail/?v=${id}` : escapeHtml(v.url);

  return `
    <div class="video-card">
      <a class="video-thumb" href="${detailLink}">
        ${thumb ? `<img src="${thumb}" alt="" loading="lazy">` : '<div class="video-thumb-fallback">No thumbnail</div>'}
        <span class="video-duration">${v.topic ? escapeHtml(v.topic) : ''}</span>
      </a>
      <div class="video-body">
        <div class="video-meta-row">
          <a class="video-channel" href="${channelLink}">${escapeHtml(v.source_name)}</a>
          <span class="video-meta-date">${formatDate(v.published_at)}</span>
        </div>
        <h3 class="video-title"><a href="${detailLink}">${escapeHtml(v.title)}</a></h3>
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

function populateChannels() {
  const channelMap = {};
  allVideos.forEach((v) => {
    if (!channelMap[v.source_id]) {
      channelMap[v.source_id] = { name: v.source_name, count: 0 };
    }
    channelMap[v.source_id].count++;
  });
  const channels = Object.entries(channelMap).sort((a, b) => a[1].name.localeCompare(b[1].name));
  channels.forEach(([id, info]) => {
    const opt = document.createElement('option');
    opt.value = id;
    opt.textContent = `${info.name} (${info.count})`;
    channelEl.appendChild(opt);
  });
}

function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

async function main() {
  try {
    const articles = await getJson(['./data/status/articles.json', '../data/status/articles.json', '../../data/status/articles.json']);
    allVideos = articles.filter((a) => (a.extraction_method === 'youtube' || a.extraction_method === 'youtube_transcript') && getYouTubeId(a.url));
    populateChannels();

    availableVideos = filterAvailableVideos(allVideos);
    render();

    loaderEl.hidden = true;
    contentEl.hidden = false;
  } catch (err) {
    loaderEl.innerHTML = `<div class="videos-empty"><h2>Failed to load videos</h2><p>${escapeHtml(err.message)}</p></div>`;
  }
}

searchEl.addEventListener('input', debounce(render, 200));
channelEl.addEventListener('change', render);
sortEl.addEventListener('change', render);
clearFiltersEl.addEventListener('click', () => {
  searchEl.value = '';
  channelEl.value = '';
  sortEl.value = 'newest';
  render();
});

main();
