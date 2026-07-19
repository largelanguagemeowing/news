async function getJson(paths) {
  for (const path of paths) {
    const response = await fetch(path, { cache: 'no-store' });
    if (response.ok) return response.json();
  }
  throw new Error(`Failed to fetch ${paths.join(' or ')}`);
}

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

function formatDate(value) {
  if (!value) return '';
  const dt = new Date(value);
  if (Number.isNaN(dt.getTime())) return value;
  return dt.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
}

function escapeHtml(text) {
  if (text == null) return '';
  const div = document.createElement('div');
  div.textContent = String(text);
  return div.innerHTML;
}

async function main() {
  const contentEl = document.getElementById('videoDetailContent');
  const params = new URLSearchParams(window.location.search);
  const videoId = params.get('v');

  if (!videoId) {
    contentEl.innerHTML = `
      <div class="video-detail-not-found">
        <h2>Video not found</h2>
        <p>No video ID provided.</p>
        <a class="video-detail-back" href="../" style="margin-top: 16px;">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m15 18-6-6 6-6"/></svg>
          Back to videos
        </a>
      </div>
    `;
    return;
  }

  try {
    const articles = await getJson(['../data/status/articles.json', '../../data/status/articles.json', '../../../data/status/articles.json']);
    const video = articles.find((a) => getYouTubeId(a.url) === videoId);

    if (!video) {
      contentEl.innerHTML = `
        <div class="video-detail-not-found">
          <h2>Video not found</h2>
          <p>Could not find details for this video.</p>
          <a class="video-detail-back" href="../" style="margin-top: 16px;">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m15 18-6-6 6-6"/></svg>
            Back to videos
          </a>
        </div>
      `;
      return;
    }

    const channelLink = `../../?source=${encodeURIComponent(video.source_id)}`;

    contentEl.innerHTML = `
      <div class="video-detail-container">
        <a class="video-detail-back" href="../">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m15 18-6-6 6-6"/></svg>
          Back to videos
        </a>
        <div class="video-detail-embed">
          <iframe src="https://www.youtube-nocookie.com/embed/${escapeHtml(videoId)}?rel=0" title="${escapeHtml(video.title)}" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" allowfullscreen></iframe>
        </div>
        <div class="video-detail-info">
          <h1 class="video-detail-title">${escapeHtml(video.title)}</h1>
          <div class="video-detail-meta">
            <a class="video-detail-channel" href="${channelLink}">${escapeHtml(video.source_name)}</a>
            <span class="video-detail-separator"></span>
            <span>${formatDate(video.published_at)}</span>
          </div>
          <a class="video-detail-youtube-link" href="${escapeHtml(video.url)}" target="_blank" rel="noreferrer">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>
            Watch on YouTube
          </a>
        </div>
      </div>
    `;

    document.title = `${video.title} | Videos | News Aggregator`;
  } catch (err) {
    contentEl.innerHTML = `
      <div class="video-detail-not-found">
        <h2>Failed to load video</h2>
        <p>${escapeHtml(err.message)}</p>
        <a class="video-detail-back" href="../" style="margin-top: 16px;">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m15 18-6-6 6-6"/></svg>
          Back to videos
        </a>
      </div>
    `;
  }
}

main();
