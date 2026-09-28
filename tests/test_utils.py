from app.utils import canonicalize_url, normalize_text, pair_similarity, resolve_feed_link, simhash64


def test_canonicalize_url_removes_tracking_params() -> None:
    url = "https://example.com/path/?utm_source=x&id=123&ref=abc"
    assert canonicalize_url(url) == "https://example.com/path?id=123"


def test_normalize_text_collapses_noise() -> None:
    assert normalize_text("  Hello,   WORLD!!! ") == "hello world"


def test_pair_similarity_prefers_near_duplicates() -> None:
    a = "OpenAI launches GPT-5 model release"
    b = "GPT-5 model release announced by OpenAI"
    c = "Quarterly market report about interest rates"
    score_near = pair_similarity(a, b, simhash64(a), simhash64(b))
    score_far = pair_similarity(a, c, simhash64(a), simhash64(c))
    assert score_near > 0.8
    assert score_far < 0.8



def test_resolve_feed_link_relative_against_feed_url() -> None:
    # sakana.ai-style root-relative href with a feed-file URL as base
    assert (
        resolve_feed_link("/sail/", "https://sakana.ai/feed.xml")
        == "https://sakana.ai/sail/"
    )


def test_resolve_feed_link_prefers_feed_page_link() -> None:
    assert (
        resolve_feed_link("/a/b", "https://x.com/feed.xml", "https://x.com/blog/")
        == "https://x.com/a/b"
    )


def test_resolve_feed_link_passthrough_absolute_and_empty() -> None:
    assert (
        resolve_feed_link("https://example.com/p", "https://sakana.ai/feed.xml")
        == "https://example.com/p"
    )
    assert resolve_feed_link("", "https://sakana.ai/feed.xml") == ""
    assert resolve_feed_link(None, "https://sakana.ai/feed.xml") == ""
