from __future__ import annotations

from enum import Enum


class ExtractionMethod(str, Enum):
    RSS = "rss"
    YOUTUBE = "youtube"
    TRAFILATURA = "trafilatura"
    MARKDOWN_NEW = "markdown_new"
    COMPRESS_NEW = "compress_new"
    JINA = "jina"
    DEFUDDLE = "defuddle"


class SourceStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DOWN = "down"


class CheckStatus(str, Enum):
    SKIPPED = "skipped"
    FAILED = "failed"
    SUCCESS = "success"
