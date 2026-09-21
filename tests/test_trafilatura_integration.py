from app.jobs import enrichment


def test_parse_with_trafilatura_skips_youtube() -> None:
    content, used = enrichment.parse_with_trafilatura("https://www.youtube.com/watch?v=abc123")
    assert content is None
    assert used is False


def test_parse_with_trafilatura_success(monkeypatch) -> None:
    monkeypatch.setattr(
        enrichment,
        "fetch_text_url",
        lambda _url, _timeout: "<html><body><article><p>Hello extracted world</p></article></body></html>",
    )
    content, used = enrichment.parse_with_trafilatura("https://example.com/article")
    assert used is True
    assert "Hello extracted world" in content


def test_parse_with_trafilatura_empty_html(monkeypatch) -> None:
    monkeypatch.setattr(enrichment, "fetch_text_url", lambda _url, _timeout: "")
    content, used = enrichment.parse_with_trafilatura("https://example.com/article")
    assert content is None
    assert used is False
