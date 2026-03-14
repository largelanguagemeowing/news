async function getJson(path) {
  const response = await fetch(path, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Failed to fetch ${path}: ${response.status}`);
  }
  return response.json();
}

function metric(title, value) {
  return `<article class="metric"><h3>${title}</h3><p>${value}</p></article>`;
}

function statusClass(status) {
  return `status-${String(status || "").toLowerCase()}`;
}

async function main() {
  const [summary, sources, incidents, runs, events] = await Promise.all([
    getJson("./data/status/summary.json"),
    getJson("./data/status/sources.json"),
    getJson("./data/status/incidents.json"),
    getJson("./data/status/runs.json"),
    getJson("./data/status/events.json"),
  ]);

  document.getElementById("generatedAt").textContent =
    `Generated: ${summary.generated_at}`;
  document.getElementById("metrics").innerHTML = [
    metric("Pipeline", `<span class="${statusClass(summary.pipeline_status)}">${summary.pipeline_status}</span>`),
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
        (inc) => `<li class="card">
      <strong>${inc.incident_key}</strong>
      <div class="${statusClass(inc.status)}">${inc.status}</div>
      <div>${inc.last_message || ""}</div>
      <div>${inc.issue_number ? `Issue #${inc.issue_number}` : "No linked issue"}</div>
    </li>`
      )
      .join("") || `<li class="card">No open incidents</li>`;

  document.getElementById("runsList").innerHTML =
    runs
      .slice(0, 8)
      .map(
        (run) => `<li class="card">
      <strong>${run.run_id}</strong>
      <div class="${statusClass(run.status)}">${run.status}</div>
      <div>Started: ${run.started_at}</div>
      <div>Ended: ${run.ended_at || "-"}</div>
    </li>`
      )
      .join("") || `<li class="card">No runs yet</li>`;

  document.getElementById("eventsList").innerHTML =
    events
      .slice(0, 10)
      .map(
        (ev) => `<li class="card">
      <strong>${ev.canonical_title}</strong>
      <div>Category: ${ev.category_labels} (${Math.round((ev.confidence || 0) * 100)}%)</div>
      <div>Sources: ${ev.source_count}</div>
      <a href="${ev.representative_url}" target="_blank" rel="noreferrer">Representative link</a>
    </li>`
      )
      .join("") || `<li class="card">No events yet</li>`;
}

main().catch((error) => {
  document.body.innerHTML = `<main class="shell"><h1>Status page failed to load</h1><p>${error.message}</p></main>`;
});

