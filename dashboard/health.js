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
  return `<a class="table-link source-label" href="./index.html?source=${encodeURIComponent(source.source_id)}" title="${source.feed_url || source.name}">${icon}<span>${source.name}</span></a>`;
}

function renderEventTitle(event) {
  const faviconUrl = sourceFaviconUrl(event.representative_url);
  const icon = faviconUrl
    ? `<img class="event-favicon" src="${faviconUrl}" loading="lazy" decoding="async" referrerpolicy="no-referrer" alt="" />`
    : "";
  return `<a class="event-title card-link-inline" href="${event.representative_url}" target="_blank" rel="noreferrer">${icon}<span>${event.canonical_title}</span></a>`;
}

async function main() {
  const [summary, sources, incidents, runs, events] = await Promise.all([
    getJson(["./data/status/summary.json", "../data/status/summary.json"]),
    getJson(["./data/status/sources.json", "../data/status/sources.json"]),
    getJson(["./data/status/incidents.json", "../data/status/incidents.json"]),
    getJson(["./data/status/runs.json", "../data/status/runs.json"]),
    getJson(["./data/status/events.json", "../data/status/events.json"]),
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
      const latency = Math.round(s.avg_latency_ms || 0);
      const metaItems = [
        lastSuccess !== "-" ? lastSuccess : null,
        failures > 0 ? `${failures} fail${failures > 1 ? "s" : ""}` : null,
        latency > 0 ? `${latency}ms` : null
      ].filter(Boolean);
      const metaText = metaItems.length > 0 ? metaItems.join(" · ") : "";
      const statusDotColor = s.status === "healthy" ? "var(--good)" : s.status === "degraded" ? "var(--warn)" : "var(--bad)";
      return `<tr>
      <td data-label="Source"><span class="source-with-status" style="--status-color: ${statusDotColor}">${renderSourceLink(s)}</span></td>
      <td data-label="Status" class="${statusClass(s.status)}">${s.status}</td>
      <td data-label="Last Success" title="${formatDateTime(s.last_success_at)}">${lastSuccess}</td>
      <td data-label="Failures">${failures}</td>
      <td data-label="Latency">${latency} ms</td>
      <td data-label="History">${historyCell}</td>
      <td class="mobile-meta">${metaText}</td>
    </tr>`
    })
    .join("");
  document.querySelector("#sourcesTable tbody").innerHTML =
    sourceRows || `<tr><td colspan="6">No sources configured</td></tr>`;

  const openIncidents = incidents.filter((i) => i.status === "open");
  document.getElementById("incidentsList").innerHTML =
    openIncidents
      .map(
        (inc) => `<li class="card incident-card">
      <div class="card-head">
        <strong>${inc.incident_key}</strong>
        <span class="chip ${statusClass(inc.status)}">${inc.status}</span>
      </div>
      <div class="meta-row">${inc.last_message || ""}</div>
      <div class="meta-row">${
        inc.issue_number && repoSlug
          ? `<a class="card-link" href="https://github.com/${repoSlug}/issues/${inc.issue_number}" target="_blank" rel="noreferrer">Open issue #${inc.issue_number}</a>`
          : inc.issue_number
            ? `Issue #${inc.issue_number}`
            : "No linked issue"
      }</div>
    </li>`
      )
      .join("") || `<li class="card">No open incidents</li>`;

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
        <strong>Last ${recentRuns.length} runs</strong>
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
}

main().catch((error) => {
  document.body.innerHTML = `<main class="shell"><h1>Status page failed to load</h1><p>${error.message}</p></main>`;
});
