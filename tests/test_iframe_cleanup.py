from app.jobs.pipeline import replace_iframes_with_markdown_links


def test_replace_iframes_with_markdown_links_preserves_src() -> None:
    html = '<p>Intro</p><iframe src="https://www.youtube.com/embed/abc123" title="YouTube video player"></iframe><p>Outro</p>'
    cleaned = replace_iframes_with_markdown_links(html)
    assert "https://www.youtube.com/embed/abc123" in cleaned
    assert "[iframe: YouTube video player]" in cleaned
    assert "<iframe" not in cleaned


def test_replace_iframes_with_markdown_links_noop_without_iframe() -> None:
    html = "<p>Hello world</p>"
    cleaned = replace_iframes_with_markdown_links(html)
    assert cleaned == html
