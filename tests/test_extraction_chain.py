"""The preference chain as a registry.

enrich_with_policy used to be a nested if/elif dispatch over extraction
methods with the order built inline. It is now a registry (enrichment.EXTRACTORS)
keyed by method name plus an order builder (_extraction_methods) — adding an
extractor is one attempt function and one registration line.

These tests cross the internal seam (registry completeness, order
construction) and the external seam (behaviour of the chain through
enrich_with_policy) with the parse adapters swapped at the existing seam and
the markdown.new quota/breaker pointed at isolated stand-ins, so no real
data/status files or network are involved.
"""

from __future__ import annotations

from app.jobs import enrichment
from app.jobs.quota import DailyQuota


def _tmp_quota(tmp_path, limit: int = 500) -> DailyQuota:
    return DailyQuota(name="test", path=tmp_path / "quota.json", daily_limit=limit)


class _FakeBreaker:
    """Stands in for enrichment._MARKDOWN_NEW_BREAKER in rate-limit tests."""

    def __init__(self) -> None:
        self.blocks: list[tuple[int, str]] = []

    def is_blocked(self) -> bool:
        return False

    def seconds_remaining(self) -> int:
        return 0

    def block(self, seconds: int, reason: str) -> None:
        self.blocks.append((seconds, reason))


# --- order construction (internal seam) -------------------------------------


def test_markdown_family_order_prefers_next_flight() -> None:
    assert enrichment._extraction_methods(
        "openai-blog", "https://openai.com/news/x", None
    ) == ["next_flight", "markdown_new", "compress_new", "jina", "defuddle", "trafilatura"]


def test_other_sources_start_with_trafilatura() -> None:
    assert enrichment._extraction_methods(
        "some-blog", "https://example.com/x", None
    ) == ["trafilatura", "next_flight", "jina", "defuddle"]


def test_youtube_prepended_for_youtube_sources_and_urls() -> None:
    by_source = enrichment._extraction_methods("matt-wolfe", "https://example.com/x", None)
    assert by_source[0] == "youtube"
    by_url = enrichment._extraction_methods(
        "some-blog", "https://www.youtube.com/watch?v=abc", None
    )
    assert by_url[0] == "youtube"
    neither = enrichment._extraction_methods("some-blog", "https://example.com/x", None)
    assert "youtube" not in neither


def test_only_method_pins_the_chain() -> None:
    assert enrichment._extraction_methods("openai-blog", "https://openai.com/x", "defuddle") == [
        "defuddle"
    ]


def test_every_ordered_method_is_registered() -> None:
    samples = [
        ("openai-blog", "https://openai.com/news/x"),
        ("some-blog", "https://example.com/x"),
        ("matt-wolfe", "https://www.youtube.com/watch?v=abc"),
    ]
    for source_id, url in samples:
        for method in enrichment._extraction_methods(source_id, url, None):
            assert method in enrichment.EXTRACTORS, f"{method} missing from EXTRACTORS"
            assert callable(enrichment.EXTRACTORS[method])


# --- chain behaviour (external seam) ----------------------------------------


def test_trafilatura_hit_returns_body(monkeypatch) -> None:
    monkeypatch.setattr(enrichment, "parse_with_trafilatura", lambda _url: ("traf body", True))
    body, method, remaining, rate_limited = enrichment.enrich_with_policy(
        "https://example.com/x", "some-blog", "t", "rss"
    )
    assert method == "trafilatura"
    assert body == "traf body"
    assert remaining == -1
    assert rate_limited is False


def test_chain_falls_through_a_miss(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(enrichment, "parse_with_trafilatura", lambda _url: (None, False))

    def flight_hit(url: str):
        calls.append("next_flight")
        return "nf body", True

    monkeypatch.setattr(enrichment, "parse_with_next_flight", flight_hit)
    body, method, _remaining, _rate_limited = enrichment.enrich_with_policy(
        "https://example.com/x", "some-blog", "t", "rss"
    )
    assert method == "next_flight"
    assert calls == ["next_flight"]


def test_all_miss_returns_rss_body(monkeypatch) -> None:
    for name in (
        "parse_with_trafilatura",
        "parse_with_next_flight",
        "parse_with_jina_ai",
        "parse_with_defuddle",
    ):
        monkeypatch.setattr(enrichment, name, lambda _url: (None, False))
    body, method, remaining, rate_limited = enrichment.enrich_with_policy(
        "https://example.com/x", "some-blog", "t", "rss body"
    )
    assert method == "rss"
    assert body == "rss body"
    assert remaining == -1
    assert rate_limited is False


# --- markdown.new behaviour (isolated quota + breaker) ----------------------


def test_markdown_budget_exhausted_skips_parse(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(enrichment, "markdown_new_quota", _tmp_quota(tmp_path))
    calls: list[str] = []
    monkeypatch.setattr(
        enrichment, "parse_with_markdown_new", lambda _url: calls.append("parse") or (None, False, -1)
    )
    body, method, _remaining, _rate_limited = enrichment.enrich_with_policy(
        "https://openai.com/news/x",
        "openai-blog",
        "t",
        "rss",
        only_method="markdown_new",
        markdown_new_budget_remaining=0,
    )
    assert method == "rss"
    assert calls == []  # parse never invoked once the budget is spent


def test_markdown_quota_exhausted_skips_reserve_and_parse(monkeypatch, tmp_path) -> None:
    quota = _tmp_quota(tmp_path, limit=1)
    assert quota.reserve() is True  # spend the single allowed request
    monkeypatch.setattr(enrichment, "markdown_new_quota", quota)
    calls: list[str] = []
    monkeypatch.setattr(
        enrichment, "parse_with_markdown_new", lambda _url: calls.append("parse") or (None, False, -1)
    )
    body, method, _remaining, _rate_limited = enrichment.enrich_with_policy(
        "https://openai.com/news/x", "openai-blog", "t", "rss", only_method="markdown_new"
    )
    assert method == "rss"
    assert calls == []


def test_markdown_hit_carries_rate_limit_remaining(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(enrichment, "markdown_new_quota", _tmp_quota(tmp_path))
    monkeypatch.setattr(
        enrichment,
        "parse_with_markdown_new",
        lambda _url: ("md body", True, 42, {"status_code": 200}),
    )
    body, method, remaining, rate_limited = enrichment.enrich_with_policy(
        "https://openai.com/news/x", "openai-blog", "t", "rss", only_method="markdown_new"
    )
    assert method == "markdown_new"
    assert body == "md body"
    assert remaining == 42
    assert rate_limited is False


def test_markdown_429_with_stop_flag_stops_the_run(monkeypatch, tmp_path) -> None:
    breaker = _FakeBreaker()
    monkeypatch.setattr(enrichment, "_MARKDOWN_NEW_BREAKER", breaker)
    monkeypatch.setattr(enrichment, "markdown_new_quota", _tmp_quota(tmp_path))
    monkeypatch.setattr(
        enrichment,
        "parse_with_markdown_new",
        lambda _url: (None, False, 0, {"status_code": 429}),
    )
    monkeypatch.setattr(enrichment, "parse_with_compress_new", lambda _url: (None, False))
    body, method, remaining, rate_limited = enrichment.enrich_with_policy(
        "https://openai.com/news/x",
        "openai-blog",
        "t",
        "rss",
        only_method="markdown_new",
        stop_on_markdown_rate_limit=True,
    )
    assert method == "rss"
    assert body == "rss"
    assert remaining == 0
    assert rate_limited is True
    assert any(reason == "http_429" for _seconds, reason in breaker.blocks)


def test_markdown_429_falls_back_to_compress(monkeypatch, tmp_path) -> None:
    breaker = _FakeBreaker()
    monkeypatch.setattr(enrichment, "_MARKDOWN_NEW_BREAKER", breaker)
    monkeypatch.setattr(enrichment, "markdown_new_quota", _tmp_quota(tmp_path))
    monkeypatch.setattr(
        enrichment,
        "parse_with_markdown_new",
        lambda _url: (None, False, 0, {"status_code": 429}),
    )
    monkeypatch.setattr(
        enrichment,
        "parse_with_compress_new",
        lambda _url: ("compress body", True),
    )
    body, method, remaining, rate_limited = enrichment.enrich_with_policy(
        "https://openai.com/news/x", "openai-blog", "t", "rss", only_method="markdown_new"
    )
    assert method == "compress_new"
    assert body == "compress body"
    assert remaining == 0
    assert rate_limited is True
    assert any(reason == "http_429" for _seconds, reason in breaker.blocks)