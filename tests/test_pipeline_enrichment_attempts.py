from app.jobs import enrichment
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


def test_markdown_new_quota_state_records_header_metadata(monkeypatch, tmp_path) -> None:
    quota_path = tmp_path / "markdown_new_quota.json"
    monkeypatch.setattr(enrichment, "MARKDOWN_NEW_QUOTA_PATH", quota_path)
    monkeypatch.setattr(enrichment, "MARKDOWN_NEW_DAILY_LIMIT", 2)

    assert enrichment.reserve_markdown_new_request() is True
    enrichment.record_markdown_new_response(
        -1,
        status_code=200,
        raw_remaining_header=None,
        url="https://openai.com/news/",
    )
    state = enrichment.load_markdown_new_quota_state()

    assert state["requests_made"] == 1
    assert state["remaining"] == 1
    assert state["last_response"]["status_code"] == 200
    assert state["last_response"]["x_rate_limit_remaining"] is None

    enrichment.record_markdown_new_response(
        0,
        status_code=429,
        raw_remaining_header="0",
        url="https://openai.com/news/",
    )
    state = enrichment.load_markdown_new_quota_state()

    assert state["exhausted"] is True
    assert state["remaining"] == 0
    assert state["last_response"]["status_code"] == 429
    assert state["header_observations"][-1]["x_rate_limit_remaining"] == "0"
