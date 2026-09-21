"""Tests for the page-fetch adapter behind next_flight extraction.

The adapter's interface: fetch_page_html(url, timeout) -> html | "". Tests
swap the transports (requests / subprocess curl) and assert the fallback
policy, keeping the suite offline.
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

from app.jobs import enrichment


def test_fetch_page_html_returns_plain_transport_when_it_succeeds(monkeypatch) -> None:
    monkeypatch.setattr(enrichment, "fetch_text_url", lambda url, t: "<html>ok</html>")

    called = []

    def fail_curl(*_args, **_kwargs):
        called.append("curl")

    monkeypatch.setattr(enrichment, "fetch_text_url_via_curl", fail_curl)

    assert enrichment.fetch_page_html("https://example.com/a", 30) == "<html>ok</html>"
    assert called == []


def test_fetch_page_html_falls_back_to_curl_on_transport_failure(monkeypatch) -> None:
    monkeypatch.setattr(enrichment, "fetch_text_url", lambda url, t: "")

    def fake_curl(url: str, timeout: int) -> str:
        assert url == "https://example.com/a"
        assert timeout == 30
        return "<html>rescued</html>"

    monkeypatch.setattr(enrichment, "fetch_text_url_via_curl", fake_curl)

    assert enrichment.fetch_page_html("https://example.com/a", 30) == "<html>rescued</html>"


def test_fetch_page_html_returns_empty_when_both_transports_fail(monkeypatch) -> None:
    monkeypatch.setattr(enrichment, "fetch_text_url", lambda url, t: "")
    monkeypatch.setattr(enrichment, "fetch_text_url_via_curl", lambda url, t: "")

    assert enrichment.fetch_page_html("https://example.com/a", 30) == ""


def test_curl_rescue_skips_youtube_urls() -> None:
    assert (
        enrichment.fetch_text_url_via_curl("https://www.youtube.com/watch?v=abc", 30)
        == ""
    )


def test_curl_rescue_returns_empty_when_curl_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(enrichment.shutil, "which", lambda name: None)
    assert enrichment.fetch_text_url_via_curl("https://example.com/a", 30) == ""


def test_curl_rescue_builds_chrome_headers_and_parses_stdout(monkeypatch) -> None:
    captured: dict = {}

    def fake_run(args, **_kwargs):
        captured["args"] = args
        return SimpleNamespace(returncode=0, stdout="<html>body</html>", stderr="")

    monkeypatch.setattr(enrichment.shutil, "which", lambda name: "/usr/bin/curl")
    monkeypatch.setattr(enrichment.subprocess, "run", fake_run)

    html = enrichment.fetch_text_url_via_curl("https://example.com/a", 30)

    assert html == "<html>body</html>"
    args = captured["args"]
    assert args[0] == "/usr/bin/curl"
    assert "--user-agent" in args
    ua_index = args.index("--user-agent")
    assert "Chrome/131" in args[ua_index + 1]
    # Real browser header set — this shape is what bot-walls accept
    assert "sec-ch-ua-mobile: ?0" in args
    assert "Sec-Fetch-Dest: document" in args
    assert args[-1] == "https://example.com/a"


def test_curl_rescue_returns_empty_on_nonzero_exit(monkeypatch) -> None:
    monkeypatch.setattr(enrichment.shutil, "which", lambda name: "/usr/bin/curl")
    monkeypatch.setattr(
        enrichment.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=22, stdout="", stderr=""),
    )
    assert enrichment.fetch_text_url_via_curl("https://example.com/a", 30) == ""


def test_curl_rescue_returns_empty_on_timeout(monkeypatch) -> None:
    monkeypatch.setattr(enrichment.shutil, "which", lambda name: "/usr/bin/curl")

    def timeout_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="curl", timeout=30)

    monkeypatch.setattr(enrichment.subprocess, "run", timeout_run)
    assert enrichment.fetch_text_url_via_curl("https://example.com/a", 30) == ""
