// DB Explorer - JSON-backed interface for all exported DB tables
const PAGE_SIZE = 50;

async function getJson(paths) {
  for (const path of paths) {
    const response = await fetch(path, { cache: 'no-store' });
    if (response.ok) return response.json();
  }
  throw new Error(`Failed to fetch ${paths.join(' or ')}`);
}

const TABLE_TO_STATUS_FILE = {
  articles: ['../data/status/articles.json', './data/status/articles.json', '../../data/status/articles.json'],
  sources: ['../data/status/sources.json', './data/status/sources.json', '../../data/status/sources.json'],
  source_health: ['../data/status/source_health.json', './data/status/source_health.json', '../../data/status/source_health.json'],
  events: ['../data/status/events.json', './data/status/events.json', '../../data/status/events.json'],
  event_members: ['../data/status/event_members.json', './data/status/event_members.json', '../../data/status/event_members.json'],
  pipeline_runs: ['../data/status/runs.json', './data/status/runs.json', '../../data/status/runs.json'],
  source_checks: ['../data/status/source_checks.json', './data/status/source_checks.json', '../../data/status/source_checks.json'],
  incidents: ['../data/status/incidents.json', './data/status/incidents.json', '../../data/status/incidents.json'],
  ingest_attempts: ['../data/status/ingest_attempts.json', './data/status/ingest_attempts.json', '../../data/status/ingest_attempts.json'],
  enrichment_attempts: ['../data/status/enrichment_attempts.json', './data/status/enrichment_attempts.json', '../../data/status/enrichment_attempts.json'],
  dead_letters: ['../data/status/dead_letters.json', './data/status/dead_letters.json', '../../data/status/dead_letters.json'],
};

let currentTable = 'articles';
let currentPage = 1;
let totalRows = 0;
let currentData = [];
let allData = [];

// DOM elements
const tableSelect = document.getElementById('tableSelect');
const searchInput = document.getElementById('searchInput');
const sourceFilter = document.getElementById('sourceFilter');
const methodFilter = document.getElementById('methodFilter');
const exportBtn = document.getElementById('exportBtn');
const loadingState = document.getElementById('loadingState');
const errorState = document.getElementById('errorState');
const tableContainer = document.getElementById('tableContainer');
const emptyState = document.getElementById('emptyState');
const dbStats = document.getElementById('dbStats');
const scrollHint = document.getElementById('scrollHint');
const tableHead = document.getElementById('tableHead');
const tableBody = document.getElementById('tableBody');
const pagination = document.getElementById('pagination');
const filterBadges = document.getElementById('filterBadges');

// Initialize
document.addEventListener('DOMContentLoaded', init);

async function init() {
  readParams();

  exportBtn.addEventListener('click', exportCSV);
  await loadDatabase();

  tableSelect.addEventListener('change', async (e) => {
    currentTable = e.target.value;
    currentPage = 1;
    await loadCurrentTableData();
  });

  searchInput.addEventListener('input', debounce(() => {
    currentPage = 1;
    applyFiltersAndRender();
  }, 300));

  sourceFilter.addEventListener('change', () => {
    currentPage = 1;
    applyFiltersAndRender();
  });

  methodFilter.addEventListener('change', () => {
    currentPage = 1;
    applyFiltersAndRender();
  });
}

function readParams() {
  const params = new URLSearchParams(location.search);
  const t = params.get('table');
  if (t && [...tableSelect.options].some((o) => o.value === t)) {
    tableSelect.value = t;
    currentTable = t;
  }
  const q = params.get('q');
  if (q) searchInput.value = q;
  const s = params.get('source');
  if (s) sourceFilter.value = s;
  const m = params.get('method');
  if (m) methodFilter.value = m;
  const p = parseInt(params.get('page'), 10);
  if (p > 1) currentPage = p;
}

function syncUrl() {
  const params = new URLSearchParams();
  if (tableSelect.value !== 'articles') params.set('table', tableSelect.value);
  if (searchInput.value.trim()) params.set('q', searchInput.value.trim());
  if (sourceFilter.value) params.set('source', sourceFilter.value);
  if (methodFilter.value) params.set('method', methodFilter.value);
  if (currentPage > 1) params.set('page', String(currentPage));

  const qs = params.toString();
  const url = qs ? `${location.pathname}?${qs}` : location.pathname;
  history.replaceState(null, '', url);
}

async function loadDatabase() {
  showLoading(true);
  hideError();

  try {
    await loadCurrentTableData();
    exportBtn.disabled = false;
  } catch (err) {
    console.error('Data load error:', err);
    showError(`Error loading data: ${err.message}`);
    showEmpty();
  }

  showLoading(false);
}

async function loadCurrentTableData() {
  const file = TABLE_TO_STATUS_FILE[currentTable];
  if (!file) {
    throw new Error(`Unknown table: ${currentTable}`);
  }

  showLoading(true);
  hideError();

  const payload = await getJson(file);
  allData = Array.isArray(payload) ? payload : [];
  currentData = [...allData];
  totalRows = allData.length;

  populateSourceFilter(allData);
  updateFilterVisibility();
  applyFiltersAndRender();
  showLoading(false);
}

function updateFilterVisibility() {
  const hasSource = allData.some((row) => Object.prototype.hasOwnProperty.call(row, 'source_id'));
  const hasMethod = allData.some((row) => Object.prototype.hasOwnProperty.call(row, 'extraction_method') || Object.prototype.hasOwnProperty.call(row, 'method'));

  sourceFilter.disabled = !hasSource;
  methodFilter.disabled = !hasMethod;

  if (!hasSource) sourceFilter.value = '';
  if (!hasMethod) methodFilter.value = '';
}

function applyFiltersAndRender() {
  const search = searchInput.value.trim().toLowerCase();
  const source = sourceFilter.value;
  const method = methodFilter.value;

  let filtered = allData;

  if (search) {
    filtered = filtered.filter((row) =>
      Object.values(row).some((val) => String(val ?? '').toLowerCase().includes(search))
    );
  }

  if (source) {
    filtered = filtered.filter((row) => row.source_id === source);
  }

  if (method) {
    filtered = filtered.filter((row) => (row.extraction_method || row.method || 'rss') === method);
  }

  currentData = filtered;
  totalRows = filtered.length;

  const start = (currentPage - 1) * PAGE_SIZE;
  const paginated = filtered.slice(start, start + PAGE_SIZE);

  renderData(paginated);
  renderPagination();
  renderFilterBadges();
  syncUrl();
  updateStats();
}

function renderData(data) {
  if (!data.length) {
    tableContainer.style.display = 'none';
    pagination.style.display = 'none';
    if (scrollHint) scrollHint.style.display = 'none';
    emptyState.style.display = 'block';
    return;
  }

  tableContainer.style.display = 'block';
  pagination.style.display = '';
  emptyState.style.display = 'none';

  const columns = orderColumns(Object.keys(data[0]).filter((col) => !col.startsWith('_')));

  tableHead.innerHTML = `
    <tr>
      ${columns.map((col) => `<th>${escapeHtml(col)}</th>`).join('')}
    </tr>
  `;

  tableBody.innerHTML = data
    .map((row) => {
      return `
      <tr>
        ${columns
          .map((col) => {
            const val = row[col];
            let cellContent = '';

            if (col === 'extraction_method' || col === 'method') {
              const m = val || 'rss';
              cellContent = `<a href="#" class="method-link" onclick="filterByMethod('${escapeHtml(m)}'); return false;"><span class="method-badge method-${escapeHtml(m)}">${escapeHtml(m)}</span></a>`;
            } else if (typeof val === 'object' && val !== null) {
              const text = JSON.stringify(val);
              const short = text.length > 120 ? `${text.slice(0, 120)}...` : text;
              cellContent = `<span title="${escapeHtml(text)}">${escapeHtml(short)}</span>`;
            } else if (col.includes('_at') || col.endsWith('date') || col.endsWith('time')) {
              cellContent = escapeHtml(formatDate(val));
            } else if ((col === 'url' || col === 'article_url' || col === 'github_run_url') && val) {
              const href = String(val);
              const display = href.length > 60 ? `${href.slice(0, 60)}...` : href;
              cellContent = `<a href="${escapeHtml(href)}" target="_blank" rel="noopener" title="${escapeHtml(href)}">${escapeHtml(display)}</a>`;
            } else if (col === 'issue_number' && val) {
              cellContent = `<a href="https://github.com/largelanguagemeowing/news/issues/${encodeURIComponent(val)}" target="_blank" rel="noopener">#${escapeHtml(String(val))}</a>`;
            } else if (col === 'title' && row.url) {
              cellContent = `<a href="${escapeHtml(String(row.url))}" target="_blank" rel="noopener">${escapeHtml(String(val ?? ''))}</a>`;
            } else if (col === 'article_id') {
              cellContent = `<a href="../article/?id=${encodeURIComponent(val)}">${escapeHtml(String(val))}</a>`;
            } else if ((col === 'source_id' || col === 'source_name') && row.source_id) {
              const sid = String(row.source_id);
              cellContent = `<a href="#" class="source-link" data-source="${escapeHtml(sid)}" onclick="filterBySource('${escapeHtml(sid)}'); return false;">${escapeHtml(String(val ?? ''))}</a>`;
            } else {
              const text = String(val ?? '');
              const short = text.length > 150 ? `${text.slice(0, 150)}...` : text;
              cellContent = `<span title="${escapeHtml(text)}">${escapeHtml(short)}</span>`;
            }

            return `<td>${cellContent}</td>`;
          })
          .join('')}
      </tr>
    `;
    })
    .join('');

  requestAnimationFrame(() => {
    if (!scrollHint) return;
    const needsHorizontalScroll = tableContainer.scrollWidth > tableContainer.clientWidth + 2;
    scrollHint.style.display = needsHorizontalScroll ? 'block' : 'none';
  });
}

function renderPagination() {
  const totalUnfiltered = allData.length;
  const totalPages = Math.ceil(totalRows / PAGE_SIZE);

  if (totalPages <= 1) {
    const info = totalRows < totalUnfiltered
      ? `Showing ${totalRows} of ${totalUnfiltered} rows`
      : `Showing ${totalRows} rows`;
    pagination.innerHTML = `<span class="page-info">${info}</span>`;
    return;
  }

  const startRow = (currentPage - 1) * PAGE_SIZE + 1;
  const endRow = Math.min(currentPage * PAGE_SIZE, totalRows);

  const info = totalRows < totalUnfiltered
    ? `${startRow}-${endRow} of ${totalRows} rows (filtered from ${totalUnfiltered})`
    : `${startRow}-${endRow} of ${totalRows} rows (Page ${currentPage}/${totalPages})`;

  pagination.innerHTML = `
    <button ${currentPage === 1 ? 'disabled' : ''} onclick="changePage(${currentPage - 1})">← Prev</button>
    <span class="page-info">${info}</span>
    <button ${currentPage === totalPages ? 'disabled' : ''} onclick="changePage(${currentPage + 1})">Next →</button>
  `;
}

window.changePage = function changePage(page) {
  currentPage = page;
  applyFiltersAndRender();
  window.scrollTo({ top: 0, behavior: 'smooth' });
};

function renderFilterBadges() {
  const badges = [];

  if (sourceFilter.value) {
    badges.push({ type: 'source', label: `Source: ${sourceFilter.value}`, clear: 'clearSourceFilter' });
  }
  if (methodFilter.value) {
    badges.push({ type: 'method', label: `Method: ${methodFilter.value}`, clear: 'clearMethodFilter' });
  }

  if (!badges.length) {
    filterBadges.style.display = 'none';
    return;
  }

  filterBadges.style.display = 'flex';
  filterBadges.innerHTML = badges
    .map(
      (b) =>
        `<span class="filter-badge">
          <span class="badge-label">${escapeHtml(b.label)}</span>
          <button class="badge-remove" onclick="${b.clear}(); return false;">✕</button>
        </span>`
    )
    .join('');
}

window.clearSourceFilter = function () {
  sourceFilter.value = '';
  currentPage = 1;
  applyFiltersAndRender();
};

window.clearMethodFilter = function () {
  methodFilter.value = '';
  currentPage = 1;
  applyFiltersAndRender();
};

function updateStats() {
  dbStats.innerHTML = '';

  const methodsEl = document.getElementById('dbMethods') || (() => {
    const el = document.createElement('div');
    el.id = 'dbMethods';
    el.className = 'db-methods';
    dbStats.parentNode.insertBefore(el, dbStats.nextSibling);
    return el;
  })();

  const hasMethod = allData.some((row) =>
    Object.prototype.hasOwnProperty.call(row, 'extraction_method') ||
    Object.prototype.hasOwnProperty.call(row, 'method')
  );

  if (hasMethod) {
    const methodCounts = {};
    allData.forEach((row) => {
      const m = row.extraction_method || row.method || 'rss';
      methodCounts[m] = (methodCounts[m] || 0) + 1;
    });
    methodsEl.innerHTML = '<span class="db-methods-label">Methods</span>' +
      Object.entries(methodCounts)
        .sort((a, b) => b[1] - a[1])
        .map(([m, c]) => `<a href="#" class="db-method-pill" onclick="filterByMethod('${escapeHtml(m)}'); return false;"><span class="count">${c}</span> ${escapeHtml(m)}</a>`)
        .join('');
    methodsEl.style.display = '';
  } else {
    methodsEl.style.display = 'none';
  }
}

window.filterBySource = function filterBySource(source) {
  sourceFilter.value = source;
  currentPage = 1;
  applyFiltersAndRender();
};

window.filterByMethod = function filterByMethod(method) {
  methodFilter.value = method;
  currentPage = 1;
  applyFiltersAndRender();
};

function populateSourceFilter(data) {
  const sources = [...new Set(data.map((row) => row.source_id).filter(Boolean))].sort();
  sourceFilter.innerHTML =
    '<option value="">All Sources</option>' +
    sources.map((s) => `<option value="${escapeHtml(s)}">${escapeHtml(s)}</option>`).join('');
}

function exportCSV() {
  if (!currentData.length) return;

  const columns = orderColumns(Object.keys(currentData[0]).filter((col) => !col.startsWith('_')));
  let csv = `${columns.join(',')}\n`;

  currentData.forEach((row) => {
    const values = columns.map((col) => {
      let val = row[col] ?? '';
      if (typeof val === 'object' && val !== null) {
        val = JSON.stringify(val);
      }
      val = String(val).replace(/"/g, '""');
      if (val.includes(',') || val.includes('"') || val.includes('\n')) {
        val = `"${val}"`;
      }
      return val;
    });
    csv += `${values.join(',')}\n`;
  });

  const blob = new Blob([csv], { type: 'text/csv' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `news-${currentTable}-${new Date().toISOString().split('T')[0]}.csv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

function escapeHtml(text) {
  if (text == null) return '';
  const div = document.createElement('div');
  div.textContent = String(text);
  return div.innerHTML;
}

function orderColumns(cols) {
  const late = new Set(['url', 'article_url', 'published_at', 'fetched_at', 'source_id']);
  const front = [];
  const bodyCol = [];
  const back = [];
  for (const col of cols) {
    if (col === 'body') bodyCol.push(col);
    else if (late.has(col)) back.push(col);
    else front.push(col);
  }
  return [...front, ...bodyCol, ...back];
}

function formatDate(dateStr) {
  if (!dateStr) return '';
  try {
    const date = new Date(dateStr);
    if (Number.isNaN(date.getTime())) return String(dateStr);
    const d = String(date.getDate()).padStart(2, '0');
    const m = String(date.getMonth() + 1).padStart(2, '0');
    const y = date.getFullYear();
    const time = date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    return `${d}/${m}/${y} ${time}`;
  } catch {
    return String(dateStr);
  }
}

function debounce(fn, delay) {
  let timeout;
  return (...args) => {
    clearTimeout(timeout);
    timeout = setTimeout(() => fn(...args), delay);
  };
}

function showLoading(show) {
  loadingState.style.display = show ? 'block' : 'none';
}

function hideError() {
  errorState.style.display = 'none';
}

function showError(msg) {
  errorState.textContent = msg;
  errorState.style.display = 'block';
  tableContainer.style.display = 'none';
  pagination.style.display = 'none';
}

function showEmpty() {
  tableContainer.style.display = 'none';
  pagination.style.display = 'none';
  if (scrollHint) scrollHint.style.display = 'none';
  emptyState.style.display = 'block';
}
