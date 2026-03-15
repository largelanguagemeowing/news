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


def test_extract_youtube_schema_description() -> None:
    html = '''
    <html><head>
    <script type="application/ld+json">
    {"@context":"https://schema.org","@type":"VideoObject","description":"Detailed video summary here."}
    </script>
    </head></html>
    '''
    assert pipeline.extract_youtube_schema_description(html) == "Detailed video summary here."
