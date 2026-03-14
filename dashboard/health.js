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

async function main() {
  const [summary, sources, incidents, runs, events] = await Promise.all([
    getJson(["./data/status/summary.json", "../data/status/summary.json"]),
    getJson(["./data/status/sources.json", "../data/status/sources.json"]),
    getJson(["./data/status/incidents.json", "../data/status/incidents.json"]),
    getJson(["./data/status/runs.json", "../data/status/runs.json"]),
    getJson(["./data/status/events.json", "../data/status/events.json"]),
  ]);

  const generatedLabel = document.getElementById("generatedAt");
  generatedLabel.textContent = `Updated ${formatTimestamp(summary.generated_at)}`;
  generatedLabel.title = formatDateTime(summary.generated_at);
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
    .map(
      (s) => `<tr>
      <td>${s.name}</td>
      <td class="${statusClass(s.status)}">${s.status}</td>
      <td>${s.last_success_at || "-"}</td>
      <td>${s.consecutive_failures}</td>
      <td>${Math.round(s.avg_latency_ms || 0)} ms</td>
    </tr>`
    )
    .join("");
  document.querySelector("#sourcesTable tbody").innerHTML =
    sourceRows || `<tr><td colspan="5">No sources configured</td></tr>`;

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

  document.getElementById("runsList").innerHTML =
    runs
      .slice(0, 8)
      .map(
        (run) => `<li class="card run-card">
      <div class="card-head">
        <strong>${
          run.github_run_url
            ? `<a class="card-link-inline" href="${run.github_run_url}" target="_blank" rel="noreferrer">${shortId(run.run_id)}</a>`
            : shortId(run.run_id)
        }</strong>
        <span class="chip ${statusClass(run.status)}">${run.status}</span>
      </div>
      <div class="meta-row" title="${formatDateTime(run.ended_at)}">Ended ${formatTimestamp(run.ended_at)}</div>
    </li>`
      )
      .join("") || `<li class="card">No runs yet</li>`;

  document.getElementById("eventsList").innerHTML =
    events
      .slice(0, 10)
      .map(
        (ev) => `<li class="card event-card">
      <strong>${ev.canonical_title}</strong>
      <div class="meta-row">Category: ${ev.category_labels} (${Math.round((ev.confidence || 0) * 100)}%)</div>
      <div class="meta-row">Sources: ${ev.source_count}</div>
      <a class="card-link" href="${ev.representative_url}" target="_blank" rel="noreferrer">Representative link</a>
    </li>`
      )
      .join("") || `<li class="card">No events yet</li>`;
}

main().catch((error) => {
  document.body.innerHTML = `<main class="shell"><h1>Status page failed to load</h1><p>${error.message}</p></main>`;
});
