// DB Explorer - WASM SQLite interface
const DB_URL = '../data/news.db';
const PAGE_SIZE = 50;

let db = null;
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
  tableSelect.addEventListener('change', (e) => {
    currentTable = e.target.value;
    currentPage = 1;
    if (allData.length) loadTable();
  });
  searchInput.addEventListener('input', debounce(() => {
    currentPage = 1;
    if (allData.length) loadTable();
  }, 300));
  sourceFilter.addEventListener('change', () => {
    currentPage = 1;
    if (allData.length) loadTable();
  });
  methodFilter.addEventListener('change', () => {
    currentPage = 1;
    if (allData.length) loadTable();
  });
}

// Load database
async function loadDatabase() {
  showLoading(true);
  hideError();
  loadBtn.disabled = true;
  
  try {
    // Try loading JSON data first (fallback approach)
    await loadJSONData();
  } catch (err) {
    console.error('Data load error:', err);
    showError(`Error loading data: ${err.message}`);
    showEmpty();
  }
  
  showLoading(false);
  loadBtn.disabled = false;
}

// Load JSON data from status endpoint
async function loadJSONData() {
  const response = await fetch('../data/status/articles.json');
  if (!response.ok) {
    throw new Error(`Failed to fetch: ${response.status} ${response.statusText}`);
  }
  
  allData = await response.json();
  currentData = [...allData];
  totalRows = allData.length;
  
  populateSourceFilter(allData);
  loadTable();
  exportBtn.disabled = false;
  
  showSuccess(`Loaded ${allData.length} articles`);
}

// Load and filter table data
function loadTable() {
  const search = searchInput.value.trim().toLowerCase();
  const source = sourceFilter.value;
  const method = methodFilter.value;
  
  // Filter data
  let filtered = allData;
  
  if (search) {
    filtered = filtered.filter(row => {
      return Object.values(row).some(val => 
        String(val).toLowerCase().includes(search)
      );
    });
  }
  
  if (source) {
    filtered = filtered.filter(row => row.source_id === source);
  }
  
  if (method) {
    filtered = filtered.filter(row => (row.extraction_method || 'rss') === method);
  }
  
  currentData = filtered;
  totalRows = filtered.length;
  
  // Paginate
  const start = (currentPage - 1) * PAGE_SIZE;
  const paginated = filtered.slice(start, start + PAGE_SIZE);
  
  renderData(paginated);
  renderPagination();
  updateStats();
}

// Render data table
function renderData(data) {
  if (!data.length) {
    tableContainer.style.display = 'none';
    emptyState.style.display = 'block';
    return;
  }
  
  tableContainer.style.display = 'block';
  emptyState.style.display = 'none';
  
  // Get columns
  const columns = Object.keys(data[0]).filter(col => !col.startsWith('_'));
  
  // Render header
  tableHead.innerHTML = `
    <tr>
      ${columns.map(col => `<th>${escapeHtml(col)}</th>`).join('')}
    </tr>
  `;
  
  // Render body
  tableBody.innerHTML = data.map(row => {
    return `
      <tr>
        ${columns.map(col => {
          let val = row[col];
          let cellContent = '';
          
          // Special formatting for certain columns
          if (col === 'extraction_method') {
            const method = val || 'rss';
            const methodClass = `method-${method}`;
            cellContent = `<span class="method-badge ${methodClass}">${escapeHtml(method)}</span>`;
          } else if (col === 'body') {
            const text = String(val || '').substring(0, 150);
            cellContent = `<span class="truncate-text" title="${escapeHtml(String(val || '').substring(0, 500))}">${escapeHtml(text)}${val && val.length > 150 ? '...' : ''}</span>`;
          } else if (col === 'title') {
            const text = String(val || '').substring(0, 100);
            cellContent = `<span title="${escapeHtml(String(val || ''))}">${escapeHtml(text)}${val && val.length > 100 ? '...' : ''}</span>`;
          } else if (col === 'url') {
            const display = String(val || '').substring(0, 40);
            cellContent = `<a href="${escapeHtml(String(val))}" target="_blank" rel="noopener" title="${escapeHtml(String(val))}">${escapeHtml(display)}...</a>`;
          } else if (col === 'published_at' || col === 'fetched_at') {
            cellContent = escapeHtml(formatDate(val));
          } else {
            const text = String(val || '').substring(0, 100);
            cellContent = escapeHtml(text);
          }
          
          return `<td>${cellContent}</td>`;
        }).join('')}
      </tr>
    `;
  }).join('');
}

// Render pagination
function renderPagination() {
  const totalPages = Math.ceil(totalRows / PAGE_SIZE);
  
  if (totalPages <= 1) {
    pagination.innerHTML = `<span class="page-info">Showing ${totalRows} rows</span>`;
    return;
  }
  
  let html = '';
  
  // Previous button
  html += `<button ${currentPage === 1 ? 'disabled' : ''} onclick="changePage(${currentPage - 1})">← Prev</button>`;
  
  // Page info
  const startRow = (currentPage - 1) * PAGE_SIZE + 1;
  const endRow = Math.min(currentPage * PAGE_SIZE, totalRows);
  html += `<span class="page-info">${startRow}-${endRow} of ${totalRows} rows (Page ${currentPage}/${totalPages})</span>`;
  
  // Next button
  html += `<button ${currentPage === totalPages ? 'disabled' : ''} onclick="changePage(${currentPage + 1})">Next →</button>`;
  
  pagination.innerHTML = html;
}

// Change page
window.changePage = function(page) {
  currentPage = page;
  loadTable();
  window.scrollTo({ top: 0, behavior: 'smooth' });
};

// Update stats display
function updateStats() {
  const methodCounts = {};
  allData.forEach(row => {
    const method = row.extraction_method || 'rss';
    methodCounts[method] = (methodCounts[method] || 0) + 1;
  });
  
  const methodSummary = Object.entries(methodCounts)
    .sort((a, b) => b[1] - a[1])
    .map(([method, count]) => `${method}: ${count}`)
    .join(' | ');
  
  const sourceCounts = {};
  allData.forEach(row => {
    const source = row.source_id || 'unknown';
    sourceCounts[source] = (sourceCounts[source] || 0) + 1;
  });
  
  dbStats.innerHTML = `
    <span>Total: ${allData.length} articles</span>
    <span>Showing: ${currentData.length} filtered</span>
    <span>Sources: ${Object.keys(sourceCounts).length}</span>
    <span style="margin-left: auto;">${methodSummary}</span>
  `;
}

// Populate source filter dropdown
function populateSourceFilter(data) {
  const sources = [...new Set(data.map(row => row.source_id))].sort();
  sourceFilter.innerHTML = '<option value="">All Sources</option>' +
    sources.map(s => `<option value="${escapeHtml(s)}">${escapeHtml(s)}</option>`).join('');
}

// Export to CSV
function exportCSV() {
  if (!currentData.length) return;
  
  const columns = Object.keys(currentData[0]).filter(col => !col.startsWith('_'));
  
  // Header
  let csv = columns.join(',') + '\n';
  
  // Rows
  currentData.forEach(row => {
    const values = columns.map(col => {
      let val = row[col] || '';
      val = String(val).replace(/"/g, '""');
      if (val.includes(',') || val.includes('"') || val.includes('\n')) {
        val = `"${val}"`;
      }
      return val;
    });
    csv += values.join(',') + '\n';
  });
  
  // Download
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

// Utility functions
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
    return date.toLocaleDateString() + ' ' + date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  } catch {
    return dateStr;
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

function showSuccess(msg) {
  // Could add a toast notification here
  console.log(msg);
}

function showEmpty() {
  tableContainer.style.display = 'none';
  emptyState.style.display = 'block';
}