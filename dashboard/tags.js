async function getJson(paths) {
  for (const path of paths) {
    const response = await fetch(path, { cache: "no-store" });
    if (response.ok) return response.json();
  }
  throw new Error(`Failed to fetch ${paths.join(" or ")}`);
}

function metric(title, value) {
  return `<article class="metric"><h3>${title}</h3><p>${value}</p></article>`;
}

function formatDateTime(value) {
  if (!value) return "-";
  const dt = new Date(value);
  if (Number.isNaN(dt.getTime())) return "-";
  return dt.toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

function formatRelative(value) {
  if (!value) return "";
  const dt = new Date(value);
  if (Number.isNaN(dt.getTime())) return "";
  const diffMs = dt.getTime() - Date.now();
  const absMs = Math.abs(diffMs);
  const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  const minute = 60 * 1000;
  const hour = 60 * minute;
  const day = 24 * hour;
  if (absMs < hour) return rtf.format(Math.round(diffMs / minute), "minute");
  if (absMs < day) return rtf.format(Math.round(diffMs / hour), "hour");
  return rtf.format(Math.round(diffMs / day), "day");
}

function formatTimestamp(value) {
  const relative = formatRelative(value);
  return relative || formatDateTime(value);
}

function scale(value, min, max) {
  if (max <= min) return 1;
  return (value - min) / (max - min);
}

function slugLink(tag) {
  return `./index.html?tag=${encodeURIComponent(tag)}`;
}

function hideLoader() {
  const loader = document.getElementById('tagsLoader');
  const content = document.getElementById('tagsContent');
  if (loader) loader.hidden = true;
  if (content) content.hidden = false;
}

async function main() {
  const [articles, summary] = await Promise.all([
    getJson(["../../data/status/articles.json", "../data/status/articles.json", "./data/status/articles.json"]),
    getJson(["../../data/status/summary.json", "../data/status/summary.json", "./data/status/summary.json"]),
  ]);

  const tagCounts = new Map();
  for (const item of articles) {
    const tags = Array.isArray(item.tags) ? item.tags : [];
    for (const tag of tags) {
      tagCounts.set(tag, (tagCounts.get(tag) || 0) + 1);
    }
  }
  const sorted = [...tagCounts.entries()].sort((a, b) => b[1] - a[1]);
  const min = sorted.length ? sorted[sorted.length - 1][1] : 0;
  const max = sorted.length ? sorted[0][1] : 0;
  const totalTagMentions = sorted.reduce((sum, [, count]) => sum + count, 0);
  const avgMentions = sorted.length ? Math.round(totalTagMentions / sorted.length) : 0;

  const tagMeta = document.getElementById("tagMeta");
  if (tagMeta) {
    tagMeta.textContent = `${sorted.length} tags from ${articles.length} items. Updated ${formatTimestamp(summary.generated_at)}`;
    tagMeta.title = formatDateTime(summary.generated_at);
  }

  document.getElementById("tagStats").innerHTML = [
    metric("Unique Tags", sorted.length),
    metric("Tag Mentions", totalTagMentions),
    metric("Average Mentions", avgMentions),
    metric("Top Tag", sorted[0] ? `${sorted[0][0]} (${sorted[0][1]})` : "-"),
  ].join("");

  document.getElementById("tagCloud").innerHTML =
    sorted
      .map(([tag, count]) => {
        const weight = scale(count, min, max);
        const size = (0.85 + weight * 1.55).toFixed(2);
        const tone = (200 - Math.round(weight * 80)).toString();
        return `<a
          class="cloud-tag"
          style="--tag-size:${size}rem;--tag-tone:${tone}"
          href="${slugLink(tag)}"
          title="${tag}: ${count} items"
        >${tag} <span>${count}</span></a>`;
      })
      .join("") || `<p class="result-meta">No tags available yet.</p>`;

  document.getElementById("topTagsList").innerHTML =
    sorted
      .slice(0, 20)
      .map(
        ([tag, count], index) => `<a class="tag-rank-row" href="${slugLink(tag)}">
      <span class="rank-index">${index + 1}.</span>
      <span class="rank-name">${tag}</span>
      <span class="rank-count">${count}</span>
    </a>`
      )
      .join("") || `<p class="result-meta">No top tags yet.</p>`;

  // Hide skeleton loader and show content
  hideLoader();
}

main().catch((error) => {
  console.error('Tags page error:', error);
  hideLoader();
  const content = document.getElementById('tagsContent');
  if (content) {
    content.innerHTML = `
      <div class="panel" style="text-align: center; padding: 48px 24px;">
        <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="color: var(--bad); margin-bottom: 16px;">
          <circle cx="12" cy="12" r="10"/>
          <line x1="12" y1="8" x2="12" y2="12"/>
          <line x1="12" y1="16" x2="12.01" y2="16"/>
        </svg>
        <h2 style="margin-bottom: 8px;">Failed to load tags data</h2>
        <p style="color: var(--ink-soft);">${error.message}</p>
        <button onclick="location.reload()" class="filter-btn filter-btn-primary" style="margin-top: 16px;">
          Retry
        </button>
      </div>
    `;
    content.hidden = false;
  }
});
