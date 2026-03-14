async function getJson(path) {
  const response = await fetch(path, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Failed to fetch ${path}: ${response.status}`);
  }
  return response.json();
}

async function main() {
  const [articles, summary] = await Promise.all([
    getJson("./data/status/articles.json"),
    getJson("./data/status/summary.json"),
  ]);
  document.getElementById("feedMeta").textContent =
    `${articles.length} items loaded. Generated: ${summary.generated_at}`;

  const rows = articles
    .map(
      (item) => `<tr>
      <td>${item.published_at || "-"}</td>
      <td>${item.source_name}</td>
      <td><a href="${item.url}" target="_blank" rel="noreferrer">${item.title}</a></td>
    </tr>`
    )
    .join("");
  document.querySelector("#feedsTable tbody").innerHTML =
    rows || `<tr><td colspan="3">No feed items yet</td></tr>`;
}

main().catch((error) => {
  document.body.innerHTML = `<main class="shell"><h1>Feed page failed to load</h1><p>${error.message}</p></main>`;
});

