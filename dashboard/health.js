async function getJson(paths) {
  for (const path of paths) {
    const response = await fetch(path, { cache: "no-store" });
    if (response.ok) {
      return response.json();
    }
  }
  throw new Error(`Failed to fetch ${paths.join(" or ")}`);
}

function metric(title, value) {
  return `<article class="metric"><h3>${title}</h3><p>${value}</p></article>`;
}

function statusClass(status) {
  return `status-${String(status || "").toLowerCase()}`;
}

function formatLatency(ms) {
  if (!ms || ms <= 0) return '-';
  const units = [
    { label: 'd', divisor: 86400000 },
    { label: 'h', divisor: 3600000 },
    { label: 'm', divisor: 60000 },
    { label: 's', divisor: 1000 },
  ];
  for (const u of units) {
    if (ms >= u.divisor) {
      const val = ms / u.divisor;
      return `${val < 10 ? val.toFixed(1) : Math.round(val)}${u.label}`;
    }
  }
  return `${Math.round(ms)}ms`;
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

function formatCompactDate(value) {
  if (!value) return "-";
  const dt = new Date(value);
  if (Number.isNaN(dt.getTime())) return value;
  const now = new Date();
  const includeYear = dt.getFullYear() !== now.getFullYear();
  return dt.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    ...(includeYear ? { year: "numeric" } : {}),
  });
}

const RELATIVE_TIMESTAMP_MAX_DAYS = 7;

function formatRelative(value) {
  if (!value) return "";
  const dt = new Date(value);
  if (Number.isNaN(dt.getTime())) return "";

  const diffMs = dt.getTime() - Date.now();
  const absMs = Math.abs(diffMs);
  const isFuture = diffMs > 0;
  const minute = 60 * 1000;
  const hour = 60 * minute;
  const day = 24 * hour;

  if (absMs >= RELATIVE_TIMESTAMP_MAX_DAYS * day) return "";
  if (absMs < minute) return isFuture ? "soon" : "now";

  const formatShort = (amount, unit) => (isFuture ? `in ${amount}${unit}` : `${amount}${unit}`);

  if (absMs < hour) return formatShort(Math.round(absMs / minute), "m");
  if (absMs < day) return formatShort(Math.round(absMs / hour), "h");
  return formatShort(Math.round(absMs / day), "d");
}

function formatTimestamp(value) {
  const relative = formatRelative(value);
  return relative || formatCompactDate(value);
}

function formatDuration(startDate) {
  if (!startDate) return "";
  const start = new Date(startDate);
  if (Number.isNaN(start.getTime())) return "";
  
  const diffMs = Date.now() - start.getTime();
  const minute = 60 * 1000;
  const hour = 60 * minute;
  const day = 24 * hour;
  
  if (diffMs < minute) return "<1m";
  if (diffMs < hour) return `${Math.floor(diffMs / minute)}m`;
  if (diffMs < day) return `${Math.floor(diffMs / hour)}h`;
  return `${Math.floor(diffMs / day)}d`;
}

function shortId(value) {
  if (!value) return "-";
  return String(value).slice(0, 8);
}

function sourceFaviconUrl(feedUrl) {
  try {
    const hostname = new URL(feedUrl).hostname;
    return `https://www.google.com/s2/favicons?domain=${encodeURIComponent(hostname)}&sz=32`;
  } catch {
    return "";
  }
}

function renderSourceLink(source) {
  const faviconUrl = sourceFaviconUrl(source.feed_url);
  const icon = faviconUrl
    ? `<img class="source-favicon" src="${faviconUrl}" loading="lazy" decoding="async" referrerpolicy="no-referrer" alt="${source.name} favicon" />`
    : "";
  const name = `${icon}<span>${source.name}</span>`;
  const hasItems = source.last_item_at || (source.items_24h && source.items_24h > 0);
  if (!hasItems) return `<span class="source-label">${name}</span>`;
  return `<a class="table-link source-label" href="../?source=${encodeURIComponent(source.source_id)}" title="${source.feed_url || source.name}">${name}</a>`;
}

function renderEventTitle(event) {
  const faviconUrl = sourceFaviconUrl(event.representative_url);
  const icon = faviconUrl
    ? `<img class="event-favicon" src="${faviconUrl}" loading="lazy" decoding="async" referrerpolicy="no-referrer" alt="" />`
    : "";
  return `<a class="event-title card-link-inline" href="${event.representative_url}" target="_blank" rel="noreferrer">${icon}<span>${event.canonical_title}</span></a>`;
}

function hideLoader() {
  const loader = document.getElementById('healthLoader');
  const content = document.getElementById('healthContent');
  if (loader) loader.hidden = true;
  if (content) content.hidden = false;
}

async function main() {
  const [summary, sources, incidents, runs, events] = await Promise.all([
    getJson(["./data/status/summary.json", "../data/status/summary.json", "../../data/status/summary.json"]),
    getJson(["./data/status/sources.json", "../data/status/sources.json", "../../data/status/sources.json"]),
    getJson(["./data/status/incidents.json", "../data/status/incidents.json", "../../data/status/incidents.json"]),
    getJson(["./data/status/runs.json", "../data/status/runs.json", "../../data/status/runs.json"]),
    getJson(["./data/status/events.json", "../data/status/events.json", "../../data/status/events.json"]),
  ]);

  const generatedLabel = document.getElementById("generatedAt");
  if (generatedLabel) {
    generatedLabel.textContent = `Updated ${formatTimestamp(summary.generated_at)}`;
    generatedLabel.title = formatDateTime(summary.generated_at);
  }
  const repoSlug = summary.github_repository || "";
  document.getElementById("metrics").innerHTML = [
    metric(
      "Pipeline",
      `<span class="${statusClass(summary.pipeline_status)}">${summary.pipeline_status}</span>`
    ),
    metric("Sources", `${summary.healthy_sources}/${summary.total_sources}`),
    metric("Stale Sources", summary.stale_sources),
    metric("Events 24h", summary.total_events_24h),
    metric("Dedupe Ratio", `${Math.round((summary.dedupe_ratio_24h || 0) * 100)}%`),
    metric("Open Incidents", summary.open_incidents),
  ].join("");

  const sourceRows = sources
    .map((s) => {
      const statuses = Array.isArray(s.recent_statuses) ? s.recent_statuses : [];
      const showHistory = statuses.length > 1;
      // Limit to last 15 statuses on mobile (will be scrolled)
      const displayStatuses = statuses.slice(-15);
      const historyBars = displayStatuses
        .map((status) => `<span class="status-dot ${status === "success" ? "ok" : "bad"}"></span>`)
        .join("");
      const historyCell = showHistory
        ? `<div class="history-inline" title="${s.checks_count_recent || 0} checks · ${s.uptime_pct_recent || 0}% uptime">${historyBars}</div><div class="history-meta">${s.uptime_pct_recent || 0}%</div>`
        : `<span class="history-meta">-</span>`;
      const lastSuccess = formatTimestamp(s.last_success_at);
      const failures = s.consecutive_failures || 0;
      const latency = s.avg_latency_ms || 0;
      const latencyLabel = formatLatency(latency);
      const metaItems = [
        lastSuccess !== "-" ? lastSuccess : null,
        failures > 0 ? `${failures} fail${failures > 1 ? "s" : ""}` : null,
        latency > 0 ? latencyLabel : null
      ].filter(Boolean);
      const metaText = metaItems.length > 0 ? metaItems.join(" · ") : "";
      const statusDotColor = s.status === "healthy" ? "var(--good)" : s.status === "degraded" ? "var(--warn)" : "var(--bad)";
      return `<tr>
      <td data-label="Source"><span class="source-with-status" style="--status-color: ${statusDotColor}">${renderSourceLink(s)}</span></td>
      <td data-label="Status" class="${statusClass(s.status)}">${s.status}</td>
      <td data-label="Last Success" title="${formatDateTime(s.last_success_at)}">${lastSuccess}</td>
      <td data-label="Failures">${failures}</td>
      <td data-label="Latency">${latencyLabel}</td>
      <td data-label="History">${historyCell}</td>
      <td class="mobile-meta">${metaText}</td>
    </tr>`
    })
    .join("");
  document.querySelector("#sourcesTable tbody").innerHTML =
    sourceRows || `<tr><td colspan="6">No sources configured</td></tr>`;

  const openIncidents = incidents.filter((i) => i.status === "open");
  
  document.getElementById("incidentsList").innerHTML =
    openIncidents.length > 0
      ? openIncidents
          .map(
            (inc) => {
              const issueUrl = inc.issue_number && repoSlug
                ? `https://github.com/${repoSlug}/issues/${inc.issue_number}`
                : null;
              
              const title = issueUrl 
                ? `<a href="${issueUrl}" target="_blank" rel="noreferrer" class="incident-title">${inc.incident_key}</a>`
                : `<span class="incident-title" style="color: var(--ink);">${inc.incident_key}</span>`;
              
              const metaParts = [];
              if (inc.last_message) metaParts.push(inc.last_message);
              if (inc.issue_number) metaParts.push(`#${inc.issue_number}`);
              
              return `<li class="card incident-card">
                <div class="card-head">
                  ${title}
                  <span class="chip ${statusClass(inc.status)}">${inc.status}</span>
                </div>
                ${metaParts.length > 0 ? `<div class="meta-row">${metaParts.join(" · ")}</div>` : ""}
              </li>`;
            }
          )
          .join("")
      : `<li class="incident-empty">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>
          <span>All systems operational — no open incidents</span>
        </li>`;

  const recentRuns = runs.slice(0, 10);
  const successCount = recentRuns.filter((run) => run.status === "success").length;
  const successRate = recentRuns.length
    ? Math.round((successCount / recentRuns.length) * 100)
    : 0;
  const orderedRuns = [...recentRuns].reverse();
  const runBars = orderedRuns
    .map((run) => {
      const title = `${shortId(run.run_id)} • ${run.status} • ${formatDateTime(run.ended_at || run.started_at)}`;
      if (run.github_run_url) {
        return `<a class="run-dot ${statusClass(run.status)}" href="${run.github_run_url}" target="_blank" rel="noreferrer" title="${title}"></a>`;
      }
      return `<span class="run-dot ${statusClass(run.status)}" title="${title}"></span>`;
    })
    .join("");

  document.getElementById("runsList").innerHTML =
    recentRuns.length
      ? `<li class="card">
      <div class="card-head">
        <strong>
          <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align: middle; margin-right: 6px;"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>
          Last ${recentRuns.length} runs
        </strong>
        <span class="chip">${successRate}% success</span>
      </div>
      <div class="run-bars">${runBars}</div>
    </li>`
      : `<li class="card">No runs yet</li>`;

  document.getElementById("eventsList").innerHTML =
    events
      .slice(0, 10)
      .map(
        (ev) => `<li class="card event-card">
      <strong>${renderEventTitle(ev)}</strong>
      <div class="meta-row">Category: ${ev.category_labels} (${Math.round((ev.confidence || 0) * 100)}%)</div>
      <div class="meta-row">Sources: ${ev.source_count}</div>
    </li>`
      )
      .join("") || `<li class="card">No events yet</li>`;

  // Hide skeleton loader and show content
  hideLoader();
}

main().catch((error) => {
  console.error('Health page error:', error);
  hideLoader();
  const content = document.getElementById('healthContent');
  if (content) {
    content.innerHTML = `
      <div class="panel" style="text-align: center; padding: 48px 24px;">
        <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="color: var(--bad); margin-bottom: 16px;">
          <circle cx="12" cy="12" r="10"/>
          <line x1="12" y1="8" x2="12" y2="12"/>
          <line x1="12" y1="16" x2="12.01" y2="16"/>
        </svg>
        <h2 style="margin-bottom: 8px;">Failed to load health data</h2>
        <p style="color: var(--ink-soft);">${error.message}</p>
        <button onclick="location.reload()" class="filter-btn filter-btn-primary" style="margin-top: 16px;">
          Retry
        </button>
      </div>
    `;
    content.hidden = false;
  }
});
