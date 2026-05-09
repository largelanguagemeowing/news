from app.jobs import pipeline


def test_enrich_article_content_returns_attempt_metadata(monkeypatch) -> None:
    def fake_enrich_with_policy(*_args, **_kwargs):
        return "Extracted article body", "trafilatura", -1, False

    monkeypatch.setattr(pipeline, "enrich_with_policy", fake_enrich_with_policy)

    body, method, attempts = pipeline.enrich_article_content(
        "https://example.com/article",
        "example-source",
        "Example article",
        "RSS summary",
    )

    assert body == "Extracted article body"
    assert method == "trafilatura"
    assert attempts == [
        {
            "method": "trafilatura",
            "status": "success",
            "duration_ms": attempts[0]["duration_ms"],
            "error_message": None,
            "output_chars": len("Extracted article body"),
        }
    ]


def test_enrich_article_content_marks_rss_fallback_failed(monkeypatch) -> None:
    def fake_enrich_with_policy(*_args, **_kwargs):
        return "RSS summary", "rss", -1, False

    monkeypatch.setattr(pipeline, "enrich_with_policy", fake_enrich_with_policy)

    _body, method, attempts = pipeline.enrich_article_content(
        "https://example.com/article",
        "example-source",
        "Example article",
        "RSS summary",
    )

    assert method == "rss"
    assert attempts[0]["status"] == "failed"
    assert attempts[0]["output_chars"] == len("RSS summary")
