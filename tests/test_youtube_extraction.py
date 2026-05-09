from app.jobs import pipeline


def test_get_youtube_video_id_from_watch_url() -> None:
    assert pipeline.get_youtube_video_id("https://www.youtube.com/watch?v=abc123") == "abc123"


def test_get_youtube_video_id_from_shorts_url() -> None:
    assert pipeline.get_youtube_video_id("https://www.youtube.com/shorts/xyz789") == "xyz789"


def test_get_youtube_embed_url() -> None:
    assert pipeline.get_youtube_embed_url("https://youtu.be/abc123") == "https://www.youtube.com/embed/abc123"


def test_build_youtube_body() -> None:
    body = pipeline.build_youtube_body(
        {
            "description": "A useful video description.",
            "author": "Channel Name",
            "embed_url": "https://www.youtube.com/embed/abc123",
        },
        "",
    )
    assert "A useful video description." in body
    assert "author: Channel Name" in body
    assert "video: https://www.youtube.com/embed/abc123" in body


def test_build_youtube_transcript_body() -> None:
    body = pipeline.build_youtube_transcript_body(
        {
            "title": "Video title",
            "author": "Channel Name",
            "embed_url": "https://www.youtube.com/embed/abc123",
        },
        "Transcript text from the video.",
    )
    assert body.startswith("Transcript text from the video.")
    assert "title: Video title" in body
    assert "author: Channel Name" in body
    assert "video: https://www.youtube.com/embed/abc123" in body


def test_youtube_enrichment_prefers_transcript(monkeypatch) -> None:
    monkeypatch.setattr(
        pipeline,
        "extract_youtube_metadata",
        lambda *_args: {
            "title": "Video title",
            "author": "Channel Name",
            "description": "Fallback description.",
            "embed_url": "https://www.youtube.com/embed/abc123",
            "video_id": "abc123",
        },
    )
    monkeypatch.setattr(pipeline, "fetch_youtube_transcript", lambda _video_id: "Transcript " * 80)

    body, method, _remaining, _rate_limited = pipeline.enrich_with_policy(
        "https://www.youtube.com/watch?v=abc123",
        "matt-wolfe",
        "Video title",
        "Fallback description.",
    )

    assert method == "youtube_transcript"
    assert body.startswith("Transcript")
    assert "Fallback description." not in body


def test_youtube_enrichment_falls_back_to_description(monkeypatch) -> None:
    monkeypatch.setattr(
        pipeline,
        "extract_youtube_metadata",
        lambda *_args: {
            "title": "Video title",
            "author": "Channel Name",
            "description": "Fallback description.",
            "embed_url": "https://www.youtube.com/embed/abc123",
            "video_id": "abc123",
        },
    )
    monkeypatch.setattr(pipeline, "fetch_youtube_transcript", lambda _video_id: None)

    body, method, _remaining, _rate_limited = pipeline.enrich_with_policy(
        "https://www.youtube.com/watch?v=abc123",
        "matt-wolfe",
        "Video title",
        "Fallback description.",
    )

    assert method == "youtube"
    assert "Fallback description." in body


def test_extract_youtube_schema_description() -> None:
    html = '''
    <html><head>
    <script type="application/ld+json">
    {"@context":"https://schema.org","@type":"VideoObject","description":"Detailed video summary here."}
    </script>
    </head></html>
    '''
    assert pipeline.extract_youtube_schema_description(html) == "Detailed video summary here."
