from __future__ import annotations

import json

from app.jobs import enrichment


def test_parse_with_defuddle_disabled(monkeypatch) -> None:
    monkeypatch.setattr(enrichment, "DEFUDDLE_ENABLED", False)
    content, used = enrichment.parse_with_defuddle("https://example.com/article")
    assert content is None
    assert used is False


def test_parse_with_defuddle_success(monkeypatch) -> None:
    monkeypatch.setattr(enrichment, "DEFUDDLE_ENABLED", True)
    monkeypatch.setattr(enrichment.shutil, "which", lambda _name: "/usr/bin/defuddle")

    class Completed:
        returncode = 0
        stdout = json.dumps({"content": "Extracted content from page"})

    monkeypatch.setattr(enrichment.subprocess, "run", lambda *args, **kwargs: Completed())
    content, used = enrichment.parse_with_defuddle("https://example.com/article")
    assert used is True
    assert content == "Extracted content from page"


def test_parse_with_defuddle_invalid_json(monkeypatch) -> None:
    monkeypatch.setattr(enrichment, "DEFUDDLE_ENABLED", True)
    monkeypatch.setattr(enrichment.shutil, "which", lambda _name: "/usr/bin/defuddle")

    class Completed:
        returncode = 0
        stdout = "not-json"

    monkeypatch.setattr(enrichment.subprocess, "run", lambda *args, **kwargs: Completed())
    content, used = enrichment.parse_with_defuddle("https://example.com/article")
    assert used is False
    assert content is None
