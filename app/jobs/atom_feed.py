from __future__ import annotations

import argparse
import json
from html import escape
from pathlib import Path

from app.utils import utc_now_iso


def build_atom_feed(summary: dict, articles: list[dict], site_url: str) -> str:
    updated = summary.get("generated_at") or utc_now_iso()
    base = site_url.rstrip("/")
    feed_link = f"{base}/feed.atom"
    home_link = f"{base}/dashboard/index.html"
    entries = []
    for article in articles[:150]:
        link = escape(article.get("url", ""))
        title = escape(article.get("title", "(untitled)"))
        source_name = escape(article.get("source_name", "unknown"))
        topic = escape(article.get("topic") or "general")
        published = article.get("published_at") or updated
        entry_id = escape(f"news-aggregator:{article.get('article_id', title)}")
        content = (
            f"&lt;p&gt;Source: {source_name}&lt;/p&gt;"
            f"&lt;p&gt;Topic: {topic}&lt;/p&gt;"
            f"&lt;p&gt;&lt;a href=&quot;{link}&quot;&gt;Open original item&lt;/a&gt;&lt;/p&gt;"
        )
        entries.append(
            f"""  <entry>
    <id>{entry_id}</id>
    <title>{title}</title>
    <link href="{link}" />
    <updated>{published}</updated>
    <published>{published}</published>
    <author><name>{source_name}</name></author>
    <category term="{topic}" />
    <content type="html">{content}</content>
  </entry>"""
        )
    return (
        f"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <id>news-aggregator:items</id>
  <title>News Aggregator Items</title>
  <updated>{updated}</updated>
  <link href="{escape(feed_link)}" rel="self" />
  <link href="{escape(home_link)}" />
  <author><name>News Aggregator</name></author>
{chr(10).join(entries)}
</feed>
"""
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--summary", default="data/status/summary.json")
    parser.add_argument("--articles", default="data/status/articles.json")
    parser.add_argument("--output", default="feed.atom")
    parser.add_argument("--site-url", default="https://example.com")
    args = parser.parse_args()

    summary = json.loads(Path(args.summary).read_text(encoding="utf-8"))
    articles = json.loads(Path(args.articles).read_text(encoding="utf-8"))
    atom = build_atom_feed(summary, articles, args.site_url)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(atom, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

