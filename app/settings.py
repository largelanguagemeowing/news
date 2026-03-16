from __future__ import annotations

import json
import os
from dataclasses import dataclass, field


DEFAULT_SOURCE_TIMEOUTS_SECONDS: dict[str, int] = {
    "google-deepmind-blog": 45,
    "openai-blog": 30,
}


def _get_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _get_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _load_source_timeouts_from_env() -> dict[str, int]:
    raw = os.getenv("SOURCE_TIMEOUTS_JSON", "").strip()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return {}
        out: dict[str, int] = {}
        for key, value in payload.items():
            if not isinstance(key, str):
                continue
            try:
                seconds = int(value)
            except (TypeError, ValueError):
                continue
            if seconds > 0:
                out[key] = seconds
        return out
    except json.JSONDecodeError:
        return {}


@dataclass(frozen=True)
class AppSettings:
    log_level: str = "INFO"

    similarity_threshold: float = 0.82
    cluster_window_hours: int = 72
    cluster_lookback_days: int = 7

    source_fail_threshold: int = 3
    source_auto_disable_failures: int = 6
    source_auto_disable_min_failure_hours: int = 12
    source_auto_disable_cooldown_hours: int = 24

    defuddle_enabled: bool = False
    defuddle_timeout_seconds: int = 20
    defuddle_max_chars: int = 12000

    request_timeout_seconds: int = 20
    source_timeouts_seconds: dict[str, int] = field(default_factory=dict)

    stale_source_hours: int = 6
    events_window_hours: int = 24
    source_checks_history_limit: int = 90
    events_export_limit: int = 300
    articles_export_limit: int = 500
    slow_source_latency_ms: int = 60000

    backfill_default_limit: int = 300
    backfill_default_markdown_new_limit: int = 400

    min_hours_between_same_incident: int = 4


def load_settings() -> AppSettings:
    return AppSettings(
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        similarity_threshold=_get_float("SIMILARITY_THRESHOLD", 0.82),
        cluster_window_hours=_get_int("CLUSTER_WINDOW_HOURS", 72),
        source_fail_threshold=_get_int("SOURCE_FAIL_THRESHOLD", 3),
        source_auto_disable_failures=_get_int("SOURCE_AUTO_DISABLE_FAILURES", 6),
        source_auto_disable_min_failure_hours=_get_int("SOURCE_AUTO_DISABLE_MIN_FAILURE_HOURS", 12),
        source_auto_disable_cooldown_hours=_get_int("SOURCE_AUTO_DISABLE_COOLDOWN_HOURS", 24),
        defuddle_enabled=_get_bool("DEFUDDLE_ENABLED", False),
        defuddle_timeout_seconds=_get_int("DEFUDDLE_TIMEOUT_SECONDS", 20),
        defuddle_max_chars=_get_int("DEFUDDLE_MAX_CHARS", 12000),
        request_timeout_seconds=_get_int("REQUEST_TIMEOUT_SECONDS", 20),
        source_timeouts_seconds={
            **DEFAULT_SOURCE_TIMEOUTS_SECONDS,
            **_load_source_timeouts_from_env(),
        },
        stale_source_hours=_get_int("STALE_SOURCE_HOURS", 6),
        events_window_hours=_get_int("EVENTS_WINDOW_HOURS", 24),
        source_checks_history_limit=_get_int("SOURCE_CHECKS_HISTORY_LIMIT", 90),
        events_export_limit=_get_int("EVENTS_EXPORT_LIMIT", 300),
        articles_export_limit=_get_int("ARTICLES_EXPORT_LIMIT", 500),
        slow_source_latency_ms=_get_int("SLOW_SOURCE_LATENCY_MS", 60000),
        backfill_default_limit=_get_int("BACKFILL_DEFAULT_LIMIT", 300),
        backfill_default_markdown_new_limit=_get_int("BACKFILL_DEFAULT_MARKDOWN_NEW_LIMIT", 400),
        min_hours_between_same_incident=_get_int("MIN_HOURS_BETWEEN_SAME_INCIDENT", 4),
    )


_SETTINGS = load_settings()


def get_settings() -> AppSettings:
    return _SETTINGS
