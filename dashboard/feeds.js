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
  if (absMs < hour) return rtf.format(Math.round(diffMs / minute), "minute");
  if (absMs < day) return rtf.format(Math.round(diffMs / hour), "hour");
  return rtf.format(Math.round(diffMs / day), "day");
}

function formatTimestamp(value) {
  const absolute = formatDateTime(value);
  const relative = formatRelative(value);
  return relative || absolute;
}

function metric(title, value) {
  return `<article class="metric"><h3>${title}</h3><p>${value}</p></article>`;
}

function normalize(v) {
  return String(v || "").toLowerCase();
}

function stateFromQuery() {
  const params = new URLSearchParams(window.location.search);
  const layout = params.get("layout");
  return {
    search: params.get("q") || "",
    source: params.get("source") || "",
    topic: params.get("topic") || "",
    tag: params.get("tag") || "",
    layout: layout === "timeline" ? "timeline" : "table",
  };
}

function syncQueryFromState(state) {
  const params = new URLSearchParams();
  if (state.search) params.set("q", state.search);
  if (state.source) params.set("source", state.source);
  if (state.topic) params.set("topic", state.topic);
  if (state.tag) params.set("tag", state.tag);
  if (state.layout && state.layout !== "table") params.set("layout", state.layout);
  const query = params.toString();
  const next = query ? `${window.location.pathname}?${query}` : window.location.pathname;
  window.history.replaceState(null, "", next);
}

function enrichArticle(article) {
  const tags = Array.isArray(article.tags) ? article.tags : [];
  return {
    ...article,
    topic: article.topic || tags[0] || "general",
    tags,
    searchText: normalize(
      `${article.title} ${article.source_name} ${tags.join(" ")} ${article.topic || ""}`
    ),
  };
}

function toWeekStartKey(value) {
  const dt = new Date(value);
  if (Number.isNaN(dt.getTime())) return null;
  dt.setHours(0, 0, 0, 0);
  const day = (dt.getDay() + 6) % 7;
  dt.setDate(dt.getDate() - day);
  return dt.toISOString().slice(0, 10);
}

function formatWeekLabel(weekKey) {
  if (!weekKey) return "Undated";
  const dt = new Date(`${weekKey}T00:00:00`);
  if (Number.isNaN(dt.getTime())) return "Undated";
  return `Week of ${dt.toLocaleDateString(undefined, { dateStyle: "medium" })}`;
}

function buildTimelineGroups(articles) {
  const weeks = new Map();
  for (const item of articles) {
    const weekKey = toWeekStartKey(item.published_at) || "undated";
    let week = weeks.get(weekKey);
    if (!week) {
      week = {
        key: weekKey,
        label: formatWeekLabel(weekKey === "undated" ? null : weekKey),
        topics: new Map(),
        count: 0,
      };
      weeks.set(weekKey, week);
    }
    let topicItems = week.topics.get(item.topic);
    if (!topicItems) {
      topicItems = [];
      week.topics.set(item.topic, topicItems);
    }
    topicItems.push(item);
    week.count += 1;
  }

  return [...weeks.values()]
    .sort((a, b) => {
      if (a.key === "undated") return 1;
      if (b.key === "undated") return -1;
      return b.key.localeCompare(a.key);
    })
    .map((week) => ({
      ...week,
      topics: [...week.topics.entries()]
        .sort((a, b) => a[0].localeCompare(b[0]))
        .map(([topic, items]) => ({
          topic,
          items: [...items].sort(
            (a, b) => new Date(b.published_at).getTime() - new Date(a.published_at).getTime()
          ),
        })),
    }));
}

function renderTimeline(articles) {
  const timeline = document.getElementById("feedTimeline");
  if (!timeline) return;
  if (!articles.length) {
    timeline.innerHTML = `<p class="timeline-empty">No feed items match current filters.</p>`;
    return;
  }

  const weekSections = buildTimelineGroups(articles)
    .map((week) => {
      const topics = week.topics
        .map(
          (topicGroup) => `<details class="timeline-topic" open>
            <summary class="timeline-topic-head">
              <span class="topic-pill">${topicGroup.topic}</span>
              <span class="timeline-subcount">${topicGroup.items.length} item${topicGroup.items.length === 1 ? "" : "s"}</span>
            </summary>
            <ul class="timeline-items">
              ${topicGroup.items
                .map(
                  (item) => `<li class="timeline-item">
                    <div class="timeline-item-meta">
                      <span title="${formatDateTime(item.published_at)}">${formatTimestamp(item.published_at)}</span>
                      <span>${item.source_name}</span>
                    </div>
                    <a class="timeline-item-link" href="${item.url}" target="_blank" rel="noreferrer">${item.title}</a>
                  </li>`
                )
                .join("")}
            </ul>
          </details>`
        )
        .join("");
      return `<details class="timeline-week" open>
        <summary class="timeline-week-head">
          <h3>${week.label}</h3>
          <span class="timeline-count">${week.count} item${week.count === 1 ? "" : "s"}</span>
        </summary>
        ${topics}
      </details>`;
    })
    .join("");

  timeline.innerHTML = weekSections;
}

function renderRows(articles) {
  const tbody = document.querySelector("#feedsTable tbody");
  if (!tbody) return;
  const rows = articles
    .map((item) => {
      return `<tr>
        <td data-label="Published" title="${formatDateTime(item.published_at)}">${formatTimestamp(item.published_at)}</td>
        <td data-label="Source">${item.source_name}</td>
        <td data-label="Topic"><span class="topic-pill">${item.topic}</span></td>
        <td data-label="Title">
          <a href="${item.url}" target="_blank" rel="noreferrer">${item.title}</a>
        </td>
      </tr>`;
    })
    .join("");
  tbody.innerHTML = rows || `<tr><td colspan="4">No feed items match current filters</td></tr>`;
}

function setLayout(state, nextLayout, elements) {
  state.layout = nextLayout === "table" ? "table" : "timeline";
  const isTimeline = state.layout === "timeline";
  if (elements.timeline) elements.timeline.hidden = !isTimeline;
  if (elements.tableWrap) elements.tableWrap.hidden = isTimeline;
  if (elements.timelineBtn) elements.timelineBtn.classList.toggle("active", isTimeline);
  if (elements.tableBtn) elements.tableBtn.classList.toggle("active", !isTimeline);
  if (!isTimeline && elements.table) {
    const isMobile = window.matchMedia("(max-width: 760px)").matches;
    elements.table.classList.toggle("mobile-stack", isMobile);
  } else if (elements.table) {
    elements.table.classList.remove("mobile-stack");
  }
}

function applyFilters(articles, state) {
  return articles.filter((item) => {
    if (state.source && item.source_id !== state.source) return false;
    if (state.topic && item.topic !== state.topic) return false;
    if (state.tag && !item.tags.includes(state.tag)) return false;
    if (state.search && !item.searchText.includes(normalize(state.search))) return false;
    return true;
  });
}

function applyFiltersWithExclusion(articles, state, excludeKey) {
  const shadow = { ...state, [excludeKey]: "" };
  return applyFilters(articles, shadow);
}

function countBySource(items) {
  const counts = new Map();
  for (const item of items) {
    counts.set(item.source_id, (counts.get(item.source_id) || 0) + 1);
  }
  return counts;
}

function countByTopic(items) {
  const counts = new Map();
  for (const item of items) {
    counts.set(item.topic, (counts.get(item.topic) || 0) + 1);
  }
  return counts;
}

function countByTag(items) {
  const counts = new Map();
  for (const item of items) {
    for (const tag of item.tags) {
      counts.set(tag, (counts.get(tag) || 0) + 1);
    }
  }
  return counts;
}

function renderOptions(selectEl, defaultLabel, options, selectedValue) {
  const optionHtml = options
    .map((opt) => `<option value="${opt.value}">${opt.label}</option>`)
    .join("");
  selectEl.innerHTML = `<option value="">${defaultLabel}</option>${optionHtml}`;
  selectEl.value = selectedValue;
}

async function main() {
  const [rawArticles, summary] = await Promise.all([
    getJson(["./data/status/articles.json", "../data/status/articles.json"]),
    getJson(["./data/status/summary.json", "../data/status/summary.json"]),
  ]);
  const articles = rawArticles.map(enrichArticle);
  const uniqueSources = [...new Map(articles.map((item) => [item.source_id, item.source_name])).entries()];
  const uniqueTopics = [...new Set(articles.map((item) => item.topic))].sort();
  const allTagCounts = countByTag(articles);
  const topTags = [...allTagCounts.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, 20)
    .map((entry) => entry[0]);

  document.getElementById("feedMeta").textContent =
    `${articles.length} items loaded. Updated ${formatTimestamp(summary.generated_at)}`;
  const statsEl = document.getElementById("feedStats");
  if (statsEl) {
    statsEl.innerHTML = [
      metric("Total Items", articles.length),
      metric("Unique Sources", uniqueSources.length),
      metric("Topics", uniqueTopics.length),
      metric("Open Incidents", summary.open_incidents),
    ].join("");
  }

  const sourceFilter = document.getElementById("sourceFilter");
  const topicFilter = document.getElementById("topicFilter");
  const tagFilter = document.getElementById("tagFilter");
  const searchInput = document.getElementById("searchInput");
  const clearFilters = document.getElementById("clearFilters");
  const resultsMeta = document.getElementById("resultsMeta");
  const timelineLayoutBtn = document.getElementById("timelineLayout");
  const tableLayoutBtn = document.getElementById("tableLayout");
  const feedTimeline = document.getElementById("feedTimeline");
  const feedTableWrap = document.getElementById("feedTableWrap");
  const feedTable = document.getElementById("feedsTable");

  const state = {
    ...stateFromQuery(),
  };
  if (!uniqueSources.some(([sourceId]) => sourceId === state.source)) {
    state.source = "";
  }
  if (!uniqueTopics.includes(state.topic)) {
    state.topic = "";
  }
  if (!topTags.includes(state.tag)) {
    state.tag = "";
  }

  function refreshFilterOptionCounts() {
    const sourceBase = applyFiltersWithExclusion(articles, state, "source");
    const topicBase = applyFiltersWithExclusion(articles, state, "topic");
    const tagBase = applyFiltersWithExclusion(articles, state, "tag");
    const sourceCounts = countBySource(sourceBase);
    const topicCounts = countByTopic(topicBase);
    const tagCounts = countByTag(tagBase);

    renderOptions(
      sourceFilter,
      `All sources (${sourceBase.length})`,
      uniqueSources.map(([sourceId, sourceName]) => ({
        value: sourceId,
        label: `${sourceName} (${sourceCounts.get(sourceId) || 0})`,
      })),
      state.source
    );

    renderOptions(
      topicFilter,
      `All topics (${topicBase.length})`,
      uniqueTopics.map((topic) => ({
        value: topic,
        label: `${topic} (${topicCounts.get(topic) || 0})`,
      })),
      state.topic
    );

    renderOptions(
      tagFilter,
      `All tags (${tagBase.length})`,
      topTags.map((tag) => ({
        value: tag,
        label: `${tag} (${tagCounts.get(tag) || 0})`,
      })),
      state.tag
    );
  }

  function render() {
    const filtered = applyFilters(articles, state);
    renderTimeline(filtered);
    renderRows(filtered);
    setLayout(state, state.layout, {
      timeline: feedTimeline,
      tableWrap: feedTableWrap,
      table: feedTable,
      timelineBtn: timelineLayoutBtn,
      tableBtn: tableLayoutBtn,
    });
    resultsMeta.textContent = `${filtered.length} results`;
    refreshFilterOptionCounts();
    searchInput.value = state.search;
    syncQueryFromState(state);
  }

  searchInput.addEventListener("input", () => {
    state.search = searchInput.value.trim();
    render();
  });
  sourceFilter.addEventListener("change", () => {
    state.source = sourceFilter.value;
    render();
  });
  topicFilter.addEventListener("change", () => {
    state.topic = topicFilter.value;
    render();
  });
  tagFilter.addEventListener("change", () => {
    state.tag = tagFilter.value;
    render();
  });
  clearFilters.addEventListener("click", () => {
    state.search = "";
    state.source = "";
    state.topic = "";
    state.tag = "";
    searchInput.value = "";
    sourceFilter.value = "";
    topicFilter.value = "";
    tagFilter.value = "";
    render();
  });
  if (timelineLayoutBtn) {
    timelineLayoutBtn.addEventListener("click", () => {
      state.layout = "timeline";
      render();
    });
  }
  if (tableLayoutBtn) {
    tableLayoutBtn.addEventListener("click", () => {
      state.layout = "table";
      render();
    });
  }

  render();
}

main().catch((error) => {
  document.body.innerHTML = `<main class="shell"><h1>Feed page failed to load</h1><p>${error.message}</p></main>`;
});
