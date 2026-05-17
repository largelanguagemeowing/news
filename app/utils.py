from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from rapidfuzz import fuzz


TRACKING_PREFIXES = ("utm_", "fbclid", "gclid", "mc_", "ref")
WHITESPACE_RE = re.compile(r"\s+")
NON_WORD_RE = re.compile(r"[^\w\s]")
DATE_PREFIX_RE = re.compile(r"^(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},\s+\d{4}\s*")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_text(text: str) -> str:
    if not text:
        return ""
    text = text.lower().strip()
    text = NON_WORD_RE.sub(" ", text)
    return WHITESPACE_RE.sub(" ", text).strip()


def canonicalize_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url.strip())
    netloc = parsed.netloc.lower()
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    params = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=False)
        if not any(k.lower().startswith(prefix) for prefix in TRACKING_PREFIXES)
    ]
    query = urlencode(sorted(params))
    return urlunparse((parsed.scheme.lower(), netloc, path, "", query, ""))


def sha1_hexdigest(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()


def simhash64(text: str) -> int:
    """Simple 64-bit simhash to avoid extra heavy deps."""
    tokens = normalize_text(text).split()
    if not tokens:
        return 0
    bits = [0] * 64
    for token in tokens:
        h = int(hashlib.sha1(token.encode("utf-8")).hexdigest()[:16], 16)
        for i in range(64):
            bits[i] += 1 if (h >> i) & 1 else -1
    out = 0
    for i, score in enumerate(bits):
        if score > 0:
            out |= 1 << i
    return out


def hamming_similarity(left: int, right: int) -> float:
    xor = left ^ right
    distance = xor.bit_count()
    return 1.0 - (distance / 64.0)


def pair_similarity(
    title_a: str,
    title_b: str,
    simhash_a: int,
    simhash_b: int,
) -> float:
    title_score = fuzz.token_set_ratio(title_a or "", title_b or "") / 100.0
    body_score = hamming_similarity(simhash_a, simhash_b)
    return (0.65 * title_score) + (0.35 * body_score)


def to_json(value: dict | list) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"))


def clean_title(title: str) -> str:
    """Remove date prefix from titles like 'May 14, 2026Policy...'"""
    if not title:
        return title
    return DATE_PREFIX_RE.sub("", title)

