from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class SourceConfig:
    source_id: str
    name: str
    feed_url: str
    default_category: str = "general"
    enabled: bool = True
    user_agent: str | None = None


def load_sources(path: str = "config/sources.yml") -> list[SourceConfig]:
    file_path = Path(path)
    if not file_path.exists():
        return []
    data = yaml.safe_load(file_path.read_text(encoding="utf-8")) or {}
    sources = data.get("sources", [])
    out: list[SourceConfig] = []
    for source in sources:
        out.append(
            SourceConfig(
                source_id=source["id"],
                name=source["name"],
                feed_url=source["feed_url"],
                default_category=source.get("default_category", "general"),
                enabled=bool(source.get("enabled", True)),
                user_agent=source.get("user_agent"),
            )
        )
    return out

