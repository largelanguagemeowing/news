async function getJson(paths) {
  for (const path of paths) {
    const response = await fetch(path, { cache: "no-store" });
    if (response.ok) return response.json();
  }
  throw new Error(`Failed to fetch ${paths.join(" or ")}`);
}

function metric(title, value) {
  return `<article class="metric"><h3>${title}</h3><p>${value}</p></article>`;
}

function scale(value, min, max) {
  if (max <= min) return 1;
  return (value - min) / (max - min);
}

function slugLink(tag) {
  return `./index.html?tag=${encodeURIComponent(tag)}`;
}

async function main() {
  const [articles, summary] = await Promise.all([
    getJson(["./data/status/articles.json", "../data/status/articles.json"]),
    getJson(["./data/status/summary.json", "../data/status/summary.json"]),
  ]);

  const tagCounts = new Map();
  for (const item of articles) {
    const tags = Array.isArray(item.tags) ? item.tags : [];
    for (const tag of tags) {
      tagCounts.set(tag, (tagCounts.get(tag) || 0) + 1);
    }
  }
  const sorted = [...tagCounts.entries()].sort((a, b) => b[1] - a[1]);
  const min = sorted.length ? sorted[sorted.length - 1][1] : 0;
  const max = sorted.length ? sorted[0][1] : 0;
  const totalTagMentions = sorted.reduce((sum, [, count]) => sum + count, 0);
  const avgMentions = sorted.length ? Math.round(totalTagMentions / sorted.length) : 0;

  document.getElementById("tagMeta").textContent =
    `${sorted.length} tags from ${articles.length} items. Updated ${summary.generated_at}`;

  document.getElementById("tagStats").innerHTML = [
    metric("Unique Tags", sorted.length),
    metric("Tag Mentions", totalTagMentions),
    metric("Average Mentions", avgMentions),
    metric("Top Tag", sorted[0] ? `${sorted[0][0]} (${sorted[0][1]})` : "-"),
  ].join("");

  document.getElementById("tagCloud").innerHTML =
    sorted
      .map(([tag, count]) => {
        const weight = scale(count, min, max);
        const size = (0.85 + weight * 1.55).toFixed(2);
        const tone = (200 - Math.round(weight * 80)).toString();
        return `<a
          class="cloud-tag"
          style="--tag-size:${size}rem;--tag-tone:${tone}"
          href="${slugLink(tag)}"
          title="${tag}: ${count} items"
        >${tag} <span>${count}</span></a>`;
      })
      .join("") || `<p class="result-meta">No tags available yet.</p>`;

  document.getElementById("topTagsList").innerHTML =
    sorted
      .slice(0, 20)
      .map(
        ([tag, count], index) => `<a class="tag-rank-row" href="${slugLink(tag)}">
      <span class="rank-index">${index + 1}.</span>
      <span class="rank-name">${tag}</span>
      <span class="rank-count">${count}</span>
    </a>`
      )
      .join("") || `<p class="result-meta">No top tags yet.</p>`;
}

main().catch((error) => {
  document.body.innerHTML = `<main class="shell"><h1>Tag cloud failed to load</h1><p>${error.message}</p></main>`;
});

