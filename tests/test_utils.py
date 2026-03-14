from app.utils import canonicalize_url, normalize_text, pair_similarity, simhash64


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

