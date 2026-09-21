"""Enrichment-chain integration for the next_flight method.

Tests cross the enrichment seam (enrichment.enrich_with_policy) with parse
adapters swapped out, asserting chain order rather than extraction behaviour.
"""

from __future__ import annotations

from app.jobs import enrichment


def _miss(name: str, calls: list[str]):
    def fail(url: str):
        calls.append(name)
        if name == "parse_with_markdown_new":
            return None, False, -1, {}
        return None, False

    return fail


def test_openai_source_prefers_next_flight_over_cloud_fetchers(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        enrichment, "parse_with_next_flight", lambda url: ("flight body " * 30, True)
    )
    for name in (
        "parse_with_markdown_new",
        "parse_with_compress_new",
        "parse_with_jina_ai",
        "parse_with_defuddle",
        "parse_with_trafilatura",
    ):
        monkeypatch.setattr(enrichment, name, _miss(name, calls))

    body, method, remaining, rate_limited = enrichment.enrich_with_policy(
        "https://openai.com/news/some-post",
        "openai-blog",
        "Some post",
        "rss body",
    )

    assert method == "next_flight"
    assert "flight body" in body
    assert remaining == -1
    assert rate_limited is False
    # No cloud fetcher was ever invoked
    assert calls == []


def test_non_openai_source_tries_trafilatura_before_next_flight(monkeypatch) -> None:
    order: list[str] = []

    def trafilatura_miss(url: str):
        order.append("trafilatura")
        return None, False

    def flight_hit(url: str):
        order.append("next_flight")
        return "extracted body " * 20, True

    monkeypatch.setattr(enrichment, "parse_with_trafilatura", trafilatura_miss)
    monkeypatch.setattr(enrichment, "parse_with_next_flight", flight_hit)
    monkeypatch.setattr(
        enrichment, "parse_with_jina_ai", _miss("parse_with_jina_ai", [])
    )
    monkeypatch.setattr(
        enrichment, "parse_with_defuddle", _miss("parse_with_defuddle", [])
    )

    body, method, _remaining, _rate_limited = enrichment.enrich_with_policy(
        "https://example.com/article",
        "example-source",
        "Example article",
        "RSS summary",
    )

    assert order == ["trafilatura", "next_flight"]
    assert method == "next_flight"
    assert "extracted body" in body


def test_next_flight_miss_falls_through_to_cloud_fetchers(monkeypatch) -> None:
    calls: list[str] = []

    def flight_miss(url: str):
        return None, False

    def jina_hit(url: str):
        calls.append("jina")
        return "jina body " * 20, True

    monkeypatch.setattr(enrichment, "parse_with_next_flight", flight_miss)
    monkeypatch.setattr(enrichment, "parse_with_jina_ai", jina_hit)
    monkeypatch.setattr(
        enrichment, "parse_with_defuddle", _miss("parse_with_defuddle", calls)
    )

    body, method, _remaining, _rate_limited = enrichment.enrich_with_policy(
        "https://openai.com/news/some-post",
        "openai-blog",
        "Some post",
        "rss body",
    )

    assert calls[0] == "jina"
    assert method == "jina"
    assert "jina body" in body
