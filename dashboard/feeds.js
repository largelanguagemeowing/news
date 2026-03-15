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
  const sort = params.get("sort");
  return {
    search: params.get("q") || "",
    source: params.get("source") || "",
    topic: params.get("topic") || "",
    tag: params.get("tag") || "",
    layout: layout === "timeline" ? "timeline" : "table",
    sort: sort || "newest",
  };
}

function syncQueryFromState(state) {
  const params = new URLSearchParams();
  if (state.search) params.set("q", state.search);
  if (state.source) params.set("source", state.source);
  if (state.topic) params.set("topic", state.topic);
  if (state.tag) params.set("tag", state.tag);
  if (state.layout && state.layout !== "table") params.set("layout", state.layout);
  if (state.sort && state.sort !== "newest") params.set("sort", state.sort);
  const query = params.toString();
  const next = query ? `${window.location.pathname}?${query}` : window.location.pathname;
  const current = `${window.location.pathname}${window.location.search}`;
  if (next !== current) {
    window.history.replaceState(null, "", next);
  }
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

const faviconUrlCache = new Map();

function sourceFaviconUrl(item) {
  const rawUrl = String(item?.url || "");
  if (!rawUrl) return "";
  if (faviconUrlCache.has(rawUrl)) return faviconUrlCache.get(rawUrl);
  let faviconUrl = "";
  try {
    const hostname = new URL(rawUrl).hostname;
    faviconUrl = `https://www.google.com/s2/favicons?domain=${encodeURIComponent(hostname)}&sz=32`;
  } catch {
    faviconUrl = "";
  }
  faviconUrlCache.set(rawUrl, faviconUrl);
  return faviconUrl;
}

function renderSourceLabel(item) {
  const faviconUrl = sourceFaviconUrl(item);
  const icon = faviconUrl
    ? `<img class="source-favicon" src="${faviconUrl}" loading="lazy" decoding="async" referrerpolicy="no-referrer" alt="${item.source_name} favicon" />`
    : "";
  return `<span class="source-label">${icon}<span>${item.source_name}</span></span>`;
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
    timeline.innerHTML = `<div class="empty-state">
      <div class="empty-state-icon">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>
      </div>
      <h3>No results found</h3>
      <p>Try adjusting your filters or search terms</p>
      <button class="empty-state-action" id="clearFiltersEmpty" type="button">Clear all filters</button>
    </div>`;
    const clearBtn = document.getElementById("clearFiltersEmpty");
    if (clearBtn) {
      clearBtn.addEventListener("click", () => {
        if (clearFilters) clearFilters.click();
      });
    }
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
                      ${renderSourceLabel(item)}
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
      const faviconUrl = sourceFaviconUrl(item);
      const favicon = faviconUrl
        ? `<img class="source-favicon" src="${faviconUrl}" loading="lazy" decoding="async" referrerpolicy="no-referrer" alt="" />`
        : "";
      return `<tr>
        <td data-label="Published" title="${formatDateTime(item.published_at)}">${formatTimestamp(item.published_at)}</td>
        <td data-label="Source">${renderSourceLabel(item)}</td>
        <td data-label="Topic"><span class="topic-pill">${item.topic}</span></td>
        <td data-label="Title">
          <a href="${item.url}" target="_blank" rel="noreferrer">${item.title}</a>
        </td>
        <td class="mobile-card-link" colspan="4">
          <a href="${item.url}" target="_blank" rel="noreferrer" class="mobile-card">
            <span class="mobile-card-title">${item.title}</span>
            <span class="mobile-card-meta">
              ${favicon}<span>${item.source_name}</span>
              <span class="mobile-card-sep">·</span>
              <span>${formatTimestamp(item.published_at)}</span>
              <span class="mobile-card-sep">·</span>
              <span class="topic-pill">${item.topic}</span>
            </span>
          </a>
        </td>
      </tr>`;
    })
    .join("");
  if (!rows) {
    tbody.innerHTML = `<tr><td colspan="5" class="empty-state-cell">
      <div class="empty-state">
        <div class="empty-state-icon">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>
        </div>
        <h3>No results found</h3>
        <p>Try adjusting your filters or search terms</p>
        <button class="empty-state-action" id="clearFiltersEmptyTable" type="button">Clear all filters</button>
      </div>
    </td></tr>`;
    const clearBtn = document.getElementById("clearFiltersEmptyTable");
    if (clearBtn && clearFilters) {
      clearBtn.addEventListener("click", () => clearFilters.click());
    }
    return;
  }
  tbody.innerHTML = rows;
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

function sortArticles(articles, sortType) {
  const sorted = [...articles];
  switch (sortType) {
    case "oldest":
      return sorted.sort((a, b) => new Date(a.published_at).getTime() - new Date(b.published_at).getTime());
    case "source":
      return sorted.sort((a, b) => a.source_name.localeCompare(b.source_name));
    case "title":
      return sorted.sort((a, b) => a.title.localeCompare(b.title));
    case "newest":
    default:
      return sorted.sort((a, b) => new Date(b.published_at).getTime() - new Date(a.published_at).getTime());
  }
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

function debounce(fn, delayMs = 150) {
  let timeoutId;
  return (...args) => {
    clearTimeout(timeoutId);
    timeoutId = setTimeout(() => fn(...args), delayMs);
  };
}

async function main() {
  const [rawArticles, summary] = await Promise.all([
    getJson(["../../data/status/articles.json", "../data/status/articles.json", "./data/status/articles.json"]),
    getJson(["../../data/status/summary.json", "../data/status/summary.json", "./data/status/summary.json"]),
  ]);
  const articles = rawArticles.map(enrichArticle);
  const uniqueSources = [...new Map(articles.map((item) => [item.source_id, item.source_name])).entries()];
  const uniqueTopics = [...new Set(articles.map((item) => item.topic))].sort();
  const allTagCounts = countByTag(articles);
  const topTags = [...allTagCounts.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, 20)
    .map((entry) => entry[0]);

  const feedMeta = document.getElementById("feedMeta");
  if (feedMeta) {
    feedMeta.textContent = `${articles.length} items loaded. Updated ${formatTimestamp(summary.generated_at)}`;
  }
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
  const sortFilter = document.getElementById("sortFilter");
  const searchInput = document.getElementById("searchInput");
  const clearFilters = document.getElementById("clearFilters");
  const filterBadge = document.getElementById("filterBadge");
  const timelineLayoutBtn = document.getElementById("timelineLayout");
  const tableLayoutBtn = document.getElementById("tableLayout");
  const feedLoader = document.getElementById("feedLoader");
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
  if (!["newest", "oldest", "source", "title"].includes(state.sort)) {
    state.sort = "newest";
  }

  const MAX_CACHE_ENTRIES = 100;
  const filteredSortedCache = new Map();
  const filterBaseCache = new Map();

  function memoSet(cache, key, value) {
    if (cache.has(key)) {
      cache.delete(key);
    }
    cache.set(key, value);
    if (cache.size > MAX_CACHE_ENTRIES) {
      const oldestKey = cache.keys().next().value;
      cache.delete(oldestKey);
    }
    return value;
  }

  function filterKey(stateObj) {
    return [
      normalize(stateObj.search),
      stateObj.source || "",
      stateObj.topic || "",
      stateObj.tag || "",
    ].join("|");
  }

  function filteredSortedKey(stateObj) {
    return `${filterKey(stateObj)}|${stateObj.sort || "newest"}`;
  }

  function getFilteredSortedArticles() {
    const key = filteredSortedKey(state);
    if (filteredSortedCache.has(key)) {
      return filteredSortedCache.get(key);
    }
    const result = sortArticles(applyFilters(articles, state), state.sort);
    return memoSet(filteredSortedCache, key, result);
  }

  function getFilterBaseWithExclusion(excludeKey) {
    const shadow = { ...state, [excludeKey]: "" };
    const key = `exclude:${excludeKey}|${filterKey(shadow)}`;
    if (filterBaseCache.has(key)) {
      return filterBaseCache.get(key);
    }
    return memoSet(filterBaseCache, key, applyFilters(articles, shadow));
  }

  function refreshFilterOptionCounts() {
    const sourceBase = getFilterBaseWithExclusion("source");
    const topicBase = getFilterBaseWithExclusion("topic");
    const tagBase = getFilterBaseWithExclusion("tag");
    const sourceCounts = countBySource(sourceBase);
    const topicCounts = countByTopic(topicBase);
    const tagCounts = countByTag(tagBase);

    if (sourceFilter) {
      renderOptions(
        sourceFilter,
        `All sources (${sourceBase.length})`,
        uniqueSources.map(([sourceId, sourceName]) => ({
          value: sourceId,
          label: `${sourceName} (${sourceCounts.get(sourceId) || 0})`,
        })),
        state.source
      );
    }

    if (topicFilter) {
      renderOptions(
        topicFilter,
        `All topics (${topicBase.length})`,
        uniqueTopics.map((topic) => ({
          value: topic,
          label: `${topic} (${topicCounts.get(topic) || 0})`,
        })),
        state.topic
      );
    }

    if (tagFilter) {
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
  }

  function updateFilterBadge() {
    if (!filterBadge) return;
    const activeCount = [state.source, state.topic, state.tag].filter(Boolean).length;
    if (activeCount > 0) {
      filterBadge.textContent = activeCount;
      filterBadge.hidden = false;
    } else {
      filterBadge.hidden = true;
    }
  }

  function hideLoader() {
    if (feedLoader) feedLoader.hidden = true;
    if (feedTimeline) feedTimeline.hidden = false;
  }

  function render() {
    const filtered = getFilteredSortedArticles();
    hideLoader();

    if (state.layout === "timeline") {
      renderTimeline(filtered);
    } else {
      renderRows(filtered);
    }

    setLayout(state, state.layout, {
      timeline: feedTimeline,
      tableWrap: feedTableWrap,
      table: feedTable,
      timelineBtn: timelineLayoutBtn,
      tableBtn: tableLayoutBtn,
    });
    updateFilterBadge();
    refreshFilterOptionCounts();
    if (searchInput) {
      searchInput.value = state.search;
    }
    updateSortUI();
    syncQueryFromState(state);
  }

  function updateSortUI() {
    if (sortFilter) {
      sortFilter.value = state.sort;
    }
  }

  if (searchInput) {
    const debouncedSearchRender = debounce(() => {
      state.search = searchInput.value.trim();
      render();
    }, 160);

    searchInput.addEventListener("input", debouncedSearchRender);
  }
  if (sourceFilter) {
    sourceFilter.addEventListener("change", () => {
      state.source = sourceFilter.value;
      render();
    });
  }
  if (topicFilter) {
    topicFilter.addEventListener("change", () => {
      state.topic = topicFilter.value;
      render();
    });
  }
  if (tagFilter) {
    tagFilter.addEventListener("change", () => {
      state.tag = tagFilter.value;
      render();
    });
  }

  if (sortFilter) {
    sortFilter.addEventListener("change", () => {
      state.sort = sortFilter.value;
      render();
    });
  }
  if (clearFilters) {
    clearFilters.addEventListener("click", () => {
      state.search = "";
      state.source = "";
      state.topic = "";
      state.tag = "";
      state.sort = "newest";
      if (searchInput) {
        searchInput.value = "";
      }
      if (sourceFilter) {
        sourceFilter.value = "";
      }
      if (topicFilter) {
        topicFilter.value = "";
      }
      if (tagFilter) {
        tagFilter.value = "";
      }
      render();
    });
  }
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
