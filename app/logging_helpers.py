from __future__ import annotations

import json
import logging
from typing import Any


def _log_structured(logger: logging.Logger, event: str, **fields: Any) -> None:
    payload = {"event": event, **fields}
    logger.info(json.dumps(payload, sort_keys=True, default=str))


def log_source_complete(
    logger: logging.Logger,
    *,
    source_id: str,
    inserted: int,
    entries: int,
    latency_ms: float,
    latest_item_at: str | None,
    methods: dict[str, int],
    skipped_entries: int = 0,
) -> None:
    _log_structured(
        logger,
        "source_complete",
        source_id=source_id,
        inserted=inserted,
        entries=entries,
        latency_ms=latency_ms,
        latest_item_at=latest_item_at,
        methods=methods,
        skipped_entries=skipped_entries,
    )


def log_stage_summary(
    logger: logging.Logger,
    *,
    stage_name: str,
    status: str,
    metrics: dict[str, Any],
    run_id: str | None = None,
) -> None:
    _log_structured(
        logger,
        "stage_summary",
        stage_name=stage_name,
        status=status,
        run_id=run_id,
        metrics=metrics,
    )
