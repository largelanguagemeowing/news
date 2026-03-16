// DB Explorer - JSON-backed interface for all exported DB tables
const PAGE_SIZE = 50;

const TABLE_TO_STATUS_FILE = {
  articles: '../data/status/articles.json',
  sources: '../data/status/sources.json',
  source_health: '../data/status/source_health.json',
  events: '../data/status/events.json',
  event_members: '../data/status/event_members.json',
  pipeline_runs: '../data/status/runs.json',
  source_checks: '../data/status/source_checks.json',
  incidents: '../data/status/incidents.json',
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
const loadBtn = document.getElementById('loadBtn');
const exportBtn = document.getElementById('exportBtn');
const loadingState = document.getElementById('loadingState');
const errorState = document.getElementById('errorState');
const tableContainer = document.getElementById('tableContainer');
const emptyState = document.getElementById('emptyState');
const dbStats = document.getElementById('dbStats');
const tableHead = document.getElementById('tableHead');
const tableBody = document.getElementById('tableBody');
const pagination = document.getElementById('pagination');

// Initialize
document.addEventListener('DOMContentLoaded', init);

function init() {
  loadBtn.addEventListener('click', loadDatabase);
  exportBtn.addEventListener('click', exportCSV);

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

async function loadDatabase() {
  showLoading(true);
  hideError();
  loadBtn.disabled = true;

  try {
    await loadCurrentTableData();
    exportBtn.disabled = false;
  } catch (err) {
    console.error('Data load error:', err);
    showError(`Error loading data: ${err.message}`);
    showEmpty();
  }

  showLoading(false);
  loadBtn.disabled = false;
}

async function loadCurrentTableData() {
  const file = TABLE_TO_STATUS_FILE[currentTable];
  if (!file) {
    throw new Error(`Unknown table: ${currentTable}`);
  }

  showLoading(true);
  hideError();

  const response = await fetch(file);
  if (!response.ok) {
    throw new Error(`Failed to fetch ${file}: ${response.status} ${response.statusText}`);
  }

  const payload = await response.json();
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
  const hasMethod = allData.some((row) => Object.prototype.hasOwnProperty.call(row, 'extraction_method'));

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
    filtered = filtered.filter((row) => (row.extraction_method || 'rss') === method);
  }

  currentData = filtered;
  totalRows = filtered.length;

  const start = (currentPage - 1) * PAGE_SIZE;
  const paginated = filtered.slice(start, start + PAGE_SIZE);

  renderData(paginated);
  renderPagination();
  updateStats();
}

function renderData(data) {
  if (!data.length) {
    tableContainer.style.display = 'none';
    emptyState.style.display = 'block';
    return;
  }

  tableContainer.style.display = 'block';
  emptyState.style.display = 'none';

  const columns = Object.keys(data[0]).filter((col) => !col.startsWith('_'));

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

            if (col === 'extraction_method') {
              const m = val || 'rss';
              cellContent = `<span class="method-badge method-${escapeHtml(m)}">${escapeHtml(m)}</span>`;
            } else if (typeof val === 'object' && val !== null) {
              const text = JSON.stringify(val);
              const short = text.length > 120 ? `${text.slice(0, 120)}...` : text;
              cellContent = `<span title="${escapeHtml(text)}">${escapeHtml(short)}</span>`;
            } else if (col.includes('_at') || col.endsWith('date') || col.endsWith('time')) {
              cellContent = escapeHtml(formatDate(val));
            } else if (col === 'url' && val) {
              const href = String(val);
              const display = href.length > 60 ? `${href.slice(0, 60)}...` : href;
              cellContent = `<a href="${escapeHtml(href)}" target="_blank" rel="noopener" title="${escapeHtml(href)}">${escapeHtml(display)}</a>`;
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
}

function renderPagination() {
  const totalPages = Math.ceil(totalRows / PAGE_SIZE);

  if (totalPages <= 1) {
    pagination.innerHTML = `<span class="page-info">Showing ${totalRows} rows</span>`;
    return;
  }

  const startRow = (currentPage - 1) * PAGE_SIZE + 1;
  const endRow = Math.min(currentPage * PAGE_SIZE, totalRows);

  pagination.innerHTML = `
    <button ${currentPage === 1 ? 'disabled' : ''} onclick="changePage(${currentPage - 1})">← Prev</button>
    <span class="page-info">${startRow}-${endRow} of ${totalRows} rows (Page ${currentPage}/${totalPages})</span>
    <button ${currentPage === totalPages ? 'disabled' : ''} onclick="changePage(${currentPage + 1})">Next →</button>
  `;
}

window.changePage = function changePage(page) {
  currentPage = page;
  applyFiltersAndRender();
  window.scrollTo({ top: 0, behavior: 'smooth' });
};

function updateStats() {
  const columns = allData.length ? Object.keys(allData[0]).length : 0;

  let methodSummary = '';
  if (allData.some((row) => Object.prototype.hasOwnProperty.call(row, 'extraction_method'))) {
    const methodCounts = {};
    allData.forEach((row) => {
      const m = row.extraction_method || 'rss';
      methodCounts[m] = (methodCounts[m] || 0) + 1;
    });
    methodSummary = Object.entries(methodCounts)
      .sort((a, b) => b[1] - a[1])
      .map(([m, c]) => `${m}: ${c}`)
      .join(' | ');
  }

  dbStats.innerHTML = `
    <span>Table: ${escapeHtml(currentTable)}</span>
    <span>Total rows: ${allData.length}</span>
    <span>Filtered: ${currentData.length}</span>
    <span>Columns: ${columns}</span>
    <span style="margin-left: auto;">${escapeHtml(methodSummary)}</span>
  `;
}

function populateSourceFilter(data) {
  const sources = [...new Set(data.map((row) => row.source_id).filter(Boolean))].sort();
  sourceFilter.innerHTML =
    '<option value="">All Sources</option>' +
    sources.map((s) => `<option value="${escapeHtml(s)}">${escapeHtml(s)}</option>`).join('');
}

function exportCSV() {
  if (!currentData.length) return;

  const columns = Object.keys(currentData[0]).filter((col) => !col.startsWith('_'));
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

function formatDate(dateStr) {
  if (!dateStr) return '';
  try {
    const date = new Date(dateStr);
    if (Number.isNaN(date.getTime())) return String(dateStr);
    return `${date.toLocaleDateString()} ${date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`;
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
}

function showEmpty() {
  tableContainer.style.display = 'none';
  emptyState.style.display = 'block';
}
