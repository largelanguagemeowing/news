// Shared helpers for loading data files in both prod (/news) and local dev.
//
// With devserver.py, local dev matches prod structure:
//   /news/ -> dashboard/
//   /news/data/ -> data/
//
// We still keep this tiny helper so pages can run either:
// - under /news/ (prod + devserver.py)
// - at / (optional), if someone serves dashboard directly.

function newsBasePrefix() {
  return location.pathname.startsWith('/news/') ? '/news' : '';
}

async function getJson(path) {
  const res = await fetch(`${newsBasePrefix()}${path}`, { cache: 'no-store' });
  if (!res.ok) throw new Error(`Failed to fetch ${path} (${res.status})`);
  return res.json();
}
