from __future__ import annotations

import argparse
import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any


TOPIC_RULES: dict[str, tuple[tuple[str, float], ...]] = {
    "ai-models": (
        ("gpt-5", 3.5),
        ("gpt-4", 3.0),
        ("gpt-4o", 3.0),
        ("o3", 2.5),
        ("claude 4", 2.8),
        ("claude 3.5", 2.8),
        ("claude sonnet", 2.5),
        ("gemini 2.5", 2.8),
        ("gemini 2.0", 2.5),
        ("llama 4", 2.8),
        ("llama 3", 2.5),
        ("deepseek", 2.5),
        ("frontier model", 2.5),
        ("model release", 2.5),
        ("open weights", 2.2),
        ("model weights", 2.0),
        ("multimodal", 1.6),
        ("foundation model", 2.0),
        ("large language model", 2.0),
    ),
    "agents": (
        ("ai agent", 2.5),
        ("coding agent", 2.5),
        ("software agent", 2.2),
        ("agentic", 2.5),
        ("agent framework", 2.0),
        ("codex", 2.5),
        ("claude code", 2.8),
        ("computer use", 2.2),
        ("tool calling", 2.0),
        ("tool use", 2.0),
        ("mcp", 2.0),
        ("model context protocol", 2.0),
        ("workflow automation", 1.8),
        ("agent loop", 2.0),
        ("agent harness", 2.0),
        ("autonomous", 1.5),
    ),
    "research": (
        ("research paper", 2.5),
        ("research", 2.0),
        ("paper", 1.8),
        ("arxiv", 2.2),
        ("benchmark", 2.0),
        ("evaluation", 1.6),
        ("dataset", 1.4),
        ("training", 1.3),
        ("architecture", 1.4),
    ),
    "product": (
        ("launch", 2.0),
        ("launched", 2.0),
        ("introducing", 2.2),
        ("announcing", 2.0),
        ("chatgpt", 2.2),
        ("cursor", 2.2),
        ("api", 1.3),
        ("plugin", 1.4),
        ("sdk", 1.4),
        ("product update", 2.0),
        ("changelog", 2.0),
        ("new feature", 2.0),
    ),
    "security": (
        ("security", 2.4),
        ("cybersecurity", 2.6),
        ("vulnerability", 2.5),
        ("exploit", 2.5),
        ("breach", 2.3),
        ("cve", 2.2),
        ("malware", 2.0),
        ("hack", 2.0),
        ("jailbreak", 2.2),
        ("prompt injection", 2.5),
    ),
    "safety": (
        ("safety", 2.2),
        ("alignment", 2.2),
        ("ai safety", 2.5),
        ("guardrail", 2.2),
        ("risk", 1.5),
        ("privacy", 1.6),
        ("misalignment", 2.2),
        ("ai risk", 2.5),
    ),
    "policy": (
        ("regulation", 2.4),
        ("ai act", 2.5),
        ("eu ai", 2.2),
        ("compliance", 2.0),
        ("copyright", 2.0),
        ("governance", 2.0),
        ("government", 1.6),
        ("law", 1.8),
        ("executive order", 2.2),
    ),
    "funding": (
        ("funding", 2.5),
        ("series a", 2.4),
        ("series b", 2.4),
        ("series c", 2.4),
        ("valuation", 2.4),
        ("raises", 2.2),
        ("acquires", 2.2),
        ("acquisition", 2.2),
        ("startup", 1.3),
        ("investment", 1.5),
    ),
    "infrastructure": (
        ("gpu", 2.2),
        ("tpu", 2.2),
        ("datacenter", 2.2),
        ("data center", 2.2),
        ("cluster", 1.6),
        ("latency", 1.5),
        ("serving", 1.6),
        ("compute", 1.5),
        ("inference", 1.4),
    ),
    "coding": (
        ("programming", 2.0),
        ("code generation", 2.0),
        ("code assistant", 2.0),
        ("copilot", 2.2),
        ("ide", 2.0),
        ("cursor", 2.2),
        ("vs code", 1.8),
        ("code review", 1.8),
        ("developer tool", 1.8),
        ("debugging", 1.6),
    ),
    "robotics": (
        ("robot", 2.2),
        ("robotics", 2.2),
        ("humanoid", 2.2),
        ("autonomous vehicle", 2.0),
        ("self-driving", 2.0),
        ("drone", 1.6),
    ),
    "tutorials": (
        ("tutorial", 2.2),
        ("how to", 2.0),
        ("guide", 1.8),
        ("getting started", 2.0),
        ("walkthrough", 2.0),
        ("build", 1.4),
        ("implement", 1.4),
    ),
    "development": (
        ("vibe coding", 2.5),
        ("software development", 2.0),
        ("development", 1.6),
        ("full-stack", 1.6),
        ("debugging", 1.2),
        ("codebase", 1.2),
        ("refactor", 1.5),
    ),
    "education": (
        ("course", 2.2),
        ("learn", 2.0),
        ("learning", 1.6),
        ("lecture", 2.0),
        ("lesson", 1.8),
        ("explained", 1.6),
        ("concept", 1.4),
        ("teach", 1.5),
        ("classroom", 1.5),
    ),
    "enterprise": (
        ("enterprise", 2.2),
        ("enterprises", 2.0),
        ("customer story", 1.8),
        ("business", 1.3),
        ("corporate", 1.5),
        ("organization", 1.2),
    ),
    "hardware": (
        ("chip", 2.2),
        ("processor", 2.0),
        ("hardware", 1.8),
        ("cpu", 1.6),
        ("device", 1.2),
        ("semiconductor", 1.8),
    ),
    "media": (
        ("ai news", 2.5),
        ("this week", 1.5),
        ("weekly", 1.2),
        ("newsletter", 1.8),
        ("roundup", 1.8),
        ("recap", 1.6),
        ("stories", 1.2),
        ("breaking", 1.5),
        ("explained", 1.3),
        ("trial explained", 2.0),
        ("officially here", 1.5),
    ),
    "news": (
        ("newsletter", 2.0),
        ("live blog", 2.5),
        ("sponsor", 1.5),
        ("announcement", 1.2),
        ("update", 0.8),
    ),
    "opinion": (
        ("quoting", 3.0),
        ("opinion", 2.2),
        ("analysis", 1.8),
        ("perspective", 1.8),
        ("thoughts on", 1.6),
        ("view", 1.2),
        ("take", 1.0),
    ),
    "tools": (
        ("datasette", 3.0),
        ("llm-", 2.5),
        ("tool", 1.5),
        ("utility", 1.3),
        ("plugin", 1.5),
        ("sdk", 1.2),
        ("github repo", 1.5),
        ("open source tool", 1.8),
    ),
}

TAG_RULES: dict[str, tuple[str, ...]] = {
    "release": (
        "release",
        "released",
        "launch",
        "launched",
        "announced",
        "introducing",
        "announcing",
        "changelog",
    ),
    "models": (
        "model",
        "models",
        "llm",
        "gpt",
        "gemini",
        "claude",
        "llama",
        "deepseek",
        "open weights",
        "weights",
    ),
    "open-source": ("open source", "open-source", "github", "repo", "weights"),
    "api": ("api", "sdk", "endpoint"),
    "agents": (
        "agent",
        "agents",
        "agentic",
        "automation",
        "workflow",
        "codex",
        "mcp",
        "tool calling",
        "tool use",
    ),
    "safety": (
        "safety",
        "alignment",
        "guardrail",
        "risk",
        "privacy",
        "security",
        "jailbreak",
    ),
    "benchmark": ("benchmark", "eval", "evaluation", "score"),
    "research": ("research", "paper", "arxiv", "study"),
    "video": ("youtube", "video", "transcript"),
    "infrastructure": (
        "gpu",
        "tpu",
        "datacenter",
        "data center",
        "latency",
        "compute",
        "inference",
    ),
    "coding": (
        "programming",
        "code",
        "copilot",
        "ide",
        "cursor",
        "developer tool",
        "debugging",
        "cli",
    ),
    "robotics": ("robot", "robotics", "humanoid"),
    "tutorials": ("tutorial", "how to", "guide", "getting started", "walkthrough"),
    "funding": (
        "funding",
        "series a",
        "series b",
        "valuation",
        "raises",
        "acquisition",
        "investment",
    ),
    "policy": ("regulation", "ai act", "compliance", "copyright", "governance", "law"),
    "development": ("development", "vibe coding", "debugging", "full-stack", "refactor"),
    "education": ("course", "learn", "learning", "lecture", "lesson", "explained", "concept", "teach"),
    "enterprise": ("enterprise", "enterprises", "customer story", "business", "corporate"),
    "events": ("event", "conference", "summit", "keynote", "meetup"),
    "hardware": ("chip", "processor", "hardware", "cpu", "device", "semiconductor"),
    "media": ("ai news", "newsletter", "weekly roundup", "recap", "breaking", "stories"),
    "news": ("newsletter", "live blog", "sponsor"),
    "opinion": ("quoting", "opinion", "analysis", "perspective", "thoughts"),
    "tools": ("datasette", "llm-", "tool", "utility", "plugin", "github repo"),
}


@dataclass(frozen=True)
class Classification:
    label: str
    confidence: float
    scores: dict[str, float]
    evidence: dict[str, list[str]]


def _normalize_text(title: str, body: str) -> str:
    return re.sub(r"\s+", " ", f"{title} {body}".lower()).strip()


def _count_pattern(text: str, pattern: str) -> int:
    escaped = re.escape(pattern)
    return len(re.findall(rf"(?<![a-z0-9-]){escaped}(?![a-z0-9-])", text))


def _score_topic(
    text: str, title_text: str, patterns: tuple[tuple[str, float], ...]
) -> tuple[float, list[str]]:
    score = 0.0
    evidence: list[str] = []
    for pattern, weight in patterns:
        occurrences = min(_count_pattern(text, pattern), 3)
        if occurrences:
            title_boost = 1.8 if _count_pattern(title_text, pattern) else 1.0
            score += weight * occurrences * title_boost
            evidence.append(pattern)
    return round(score, 3), evidence[:8]


def classify_article(
    title: str, body: str, default_category: str = "general"
) -> Classification:
    text = _normalize_text(title, body)
    title_text = title.lower()
    scores: dict[str, float] = {}
    evidence: dict[str, list[str]] = {}
    for label, patterns in TOPIC_RULES.items():
        score, hits = _score_topic(text, title_text, patterns)
        if score:
            scores[label] = score
            evidence[label] = hits

    if not scores:
        fallback = default_category or "general"
        return Classification(fallback, 0.55, {fallback: 0.0}, {})

    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    label, top_score = ranked[0]
    confidence = 0.50 + min(top_score, 10.0) * 0.03
    if len(ranked) > 1:
        margin = top_score - ranked[1][1]
        confidence += min(margin, 6.0) * 0.025
    if default_category == label:
        confidence += 0.05
    confidence = round(min(confidence, 0.94), 2)
    return Classification(label, confidence, scores, evidence)


def label_quality(confidence: float, scores: dict[str, float]) -> str:
    ranked = sorted(scores.values(), reverse=True)
    top = ranked[0] if ranked else 0.0
    runner_up = ranked[1] if len(ranked) > 1 else 0.0
    if confidence >= 0.82 and top >= 4.0 and top - runner_up >= 1.5:
        return "high"
    if confidence >= 0.7 and top >= 2.5 and top - runner_up >= 1.0:
        return "medium"
    return "review"


def extract_article_tags(
    title: str, body: str, source_id: str, youtube_source_ids: set[str]
) -> list[str]:
    text = _normalize_text(title, body)
    tags = [
        label
        for label, patterns in TAG_RULES.items()
        if any(pattern in text for pattern in patterns)
    ]
    if source_id in youtube_source_ids and "video" not in tags:
        tags.append("video")
    return (tags or ["general"])[:6]


def generate_weak_labels(
    conn: sqlite3.Connection, limit: int | None = None
) -> list[dict[str, Any]]:
    query = """
        SELECT a.article_id, a.title, a.body, a.url, a.source_id, a.extraction_method,
               s.default_category
        FROM articles a
        JOIN sources s ON s.source_id = a.source_id
        ORDER BY a.published_at DESC
    """
    params: tuple[Any, ...] = ()
    if limit is not None:
        query += " LIMIT ?"
        params = (limit,)
    rows = conn.execute(query, params).fetchall()
    labels = []
    for row in rows:
        item = dict(row)
        classification = classify_article(
            item["title"],
            item.get("body") or "",
            item.get("default_category") or "general",
        )
        labels.append(
            {
                "article_id": item["article_id"],
                "source_id": item["source_id"],
                "url": item["url"],
                "title": item["title"],
                "extraction_method": item["extraction_method"],
                "label": classification.label,
                "confidence": classification.confidence,
                "quality": label_quality(
                    classification.confidence, classification.scores
                ),
                "scores": classification.scores,
                "evidence": classification.evidence,
                "body_chars": len(item.get("body") or ""),
            }
        )
    return labels


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate weak labels from enriched article bodies."
    )
    parser.add_argument("--db", default="data/news.db")
    parser.add_argument("--output", default="data/classifier/weak_labels.json")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    labels = generate_weak_labels(conn, args.limit)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(labels, indent=2), encoding="utf-8")
    print(json.dumps({"labels": len(labels), "output": str(output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
