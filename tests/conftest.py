from __future__ import annotations

from pathlib import Path

import pytest

import app.db as db_module


@pytest.fixture(autouse=True)
def isolate_test_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Use a per-test SQLite file so tests never touch data/news.db."""
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "news-test.db")
