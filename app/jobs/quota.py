"""Daily quota: one state machine for the markdown.new and compress.new limits.

The two daily-limit machines in enrichment.py used to be near-identical copies
of the same load / save / exhausted / reserve / date logic, differing only in
the state-file path and a handful of flavour-specific fields. A DailyQuota owns
the persistence and arithmetic for one quota JSON state file; both machines are
now instances of it. The per-flavour response recording still lives in
enrichment.py on top of the shared load/save, because what counts as a
"recorded response" differs between the two (HTTP header observations vs.
success/failure counters).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("news.pipeline")


def today() -> str:
    """UTC date (YYYY-MM-DD) — the day a quota period applies to."""
    return datetime.now(timezone.utc).date().isoformat()


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _empty_extra_state() -> dict[str, Any]:
    """Default extra-state factory: no flavour-specific fields."""
    return {}


@dataclass
class DailyQuota:
    """Daily-limit bookkeeping persisted as a JSON state file.

    A quota period is one UTC day: state files from any other day are treated
    as stale and reset on load. ``extra_state`` supplies flavour-specific
    default fields (e.g. HTTP header observations for markdown.new, success/
    failure counters for compress.new).
    """

    name: str
    path: Path
    daily_limit: int
    extra_state: Callable[[], dict[str, Any]] = field(
        default_factory=lambda: _empty_extra_state
    )

    def new_state(self, day: str | None = None) -> dict[str, Any]:
        state = {
            "date": day or today(),
            "requests_made": 0,
            "limit": self.daily_limit,
            "remaining": self.daily_limit,
            "exhausted": False,
            "updated_at": now_iso(),
        }
        state.update(self.extra_state())
        return state

    def load(self) -> dict[str, Any]:
        day = today()
        if not self.path.exists():
            return self.new_state(day)
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(state, dict) or state.get("date") != day:
                return self.new_state(day)
            state["limit"] = int(state.get("limit") or self.daily_limit)
            state["requests_made"] = int(state.get("requests_made") or 0)
            state["remaining"] = int(state.get("remaining") or 0)
            state["exhausted"] = bool(state.get("exhausted")) or state["remaining"] <= 0
            return state
        except Exception as exc:
            logger.warning("Failed to read %s quota state error=%s", self.name, exc)
            return self.new_state(day)

    def save(self, state: dict[str, Any]) -> None:
        state["updated_at"] = now_iso()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(state, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def exhausted(self) -> tuple[bool, dict[str, Any]]:
        state = self.load()
        limit = int(state.get("limit") or self.daily_limit)
        exhausted = (
            bool(state.get("exhausted"))
            or int(state.get("remaining") or 0) <= 0
            or int(state.get("requests_made") or 0) >= limit
        )
        return exhausted, state

    def reserve(self) -> bool:
        """Reserve one request against today's quota; False when exhausted."""
        exhausted, state = self.exhausted()
        if exhausted:
            return False
        limit = int(state.get("limit") or self.daily_limit)
        requests_made = int(state.get("requests_made") or 0) + 1
        state["requests_made"] = requests_made
        state["remaining"] = max(
            0, min(int(state.get("remaining") or limit), limit - requests_made)
        )
        state["exhausted"] = state["remaining"] <= 0
        self.save(state)
        return True