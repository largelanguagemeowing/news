import sqlite3

from app.jobs.classifier import classify_article, extract_article_tags, generate_weak_labels


def test_classify_article_returns_evidence() -> None:
    result = classify_article(
        "New GPU cluster improves model serving",
        "The data center deployment reduces inference latency for large language models.",
        "general",
    )

    assert result.label == "infrastructure"
    assert result.confidence >= 0.7
    assert "gpu" in result.evidence["infrastructure"]


def test_classify_article_uses_word_boundaries() -> None:
    result = classify_article(
        "A browser sends a user-agent header",
        "The post discusses HTTP request metadata and browser compatibility.",
        "general",
    )

    assert result.label == "general"


def test_extract_article_tags_includes_video_for_youtube_sources() -> None:
    tags = extract_article_tags(
        "Model release walkthrough",
        "Transcript from a YouTube video about the API.",
        "matt-wolfe",
        {"matt-wolfe"},
    )

    assert "video" in tags
    assert "models" in tags


def test_generate_weak_labels_from_enriched_body() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE sources (
          source_id TEXT PRIMARY KEY,
          default_category TEXT NOT NULL
        );
        CREATE TABLE articles (
          article_id INTEGER PRIMARY KEY,
          title TEXT NOT NULL,
          body TEXT NOT NULL,
          url TEXT NOT NULL,
          source_id TEXT NOT NULL,
          extraction_method TEXT NOT NULL,
          published_at TEXT NOT NULL
        );
        INSERT INTO sources VALUES ('example', 'general');
        INSERT INTO articles VALUES (
          1,
          'Policy memo',
          'The government introduced a new AI regulation and compliance framework.',
          'https://example.com/policy',
          'example',
          'trafilatura',
          '2026-01-01T00:00:00+00:00'
        );
        """
    )

    labels = generate_weak_labels(conn)

    assert labels[0]["label"] == "policy"
    assert labels[0]["body_chars"] > 0
    assert labels[0]["evidence"]["policy"]
