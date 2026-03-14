async function getJson(paths) {
  for (const path of paths) {
    const response = await fetch(path, { cache: "no-store" });
    if (response.ok) {
      return response.json();
    }
  }
  throw new Error(`Failed to fetch ${paths.join(" or ")}`);
}

function formatDateTime(value) {
  if (!value) return "-";
  const dt = new Date(value);
  if (Number.isNaN(dt.getTime())) return value;
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
  if (absMs < hour) {
    return rtf.format(Math.round(diffMs / minute), "minute");
  }
  if (absMs < day) {
    return rtf.format(Math.round(diffMs / hour), "hour");
  }
  return rtf.format(Math.round(diffMs / day), "day");
}

function formatTimestamp(value) {
  const absolute = formatDateTime(value);
  const relative = formatRelative(value);
  return relative || absolute;
}

async function main() {
  const [articles, summary] = await Promise.all([
    getJson(["./data/status/articles.json", "../data/status/articles.json"]),
    getJson(["./data/status/summary.json", "../data/status/summary.json"]),
  ]);
  document.getElementById("feedMeta").textContent =
    `${articles.length} items loaded. Updated ${formatTimestamp(summary.generated_at)}`;
  const statsEl = document.getElementById("feedStats");
  if (statsEl) {
    const uniqueSources = new Set(articles.map((item) => item.source_id)).size;
    statsEl.innerHTML = [
      `<article class="metric"><h3>Total Items</h3><p>${articles.length}</p></article>`,
      `<article class="metric"><h3>Unique Sources</h3><p>${uniqueSources}</p></article>`,
      `<article class="metric"><h3>Pipeline</h3><p class="status-${summary.pipeline_status}">${summary.pipeline_status}</p></article>`,
      `<article class="metric"><h3>Open Incidents</h3><p>${summary.open_incidents}</p></article>`,
    ].join("");
  }

  const isMobile = window.matchMedia("(max-width: 760px)").matches;
  const rows = articles
    .map(
      (item) => `<tr>
      <td data-label="Published" title="${formatDateTime(item.published_at)}">${formatTimestamp(item.published_at)}</td>
      <td data-label="Source">${item.source_name}</td>
      <td data-label="Title"><a href="${item.url}" target="_blank" rel="noreferrer">${item.title}</a></td>
    </tr>`
    )
    .join("");
  document.querySelector("#feedsTable tbody").innerHTML =
    rows || `<tr><td colspan="3">No feed items yet</td></tr>`;
  if (isMobile) {
    document.getElementById("feedsTable").classList.add("mobile-stack");
  }
}

main().catch((error) => {
  document.body.innerHTML = `<main class="shell"><h1>Feed page failed to load</h1><p>${error.message}</p></main>`;
});
