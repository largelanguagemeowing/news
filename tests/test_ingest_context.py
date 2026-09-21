"""The ingest seam carries only real adapters.

ingest_stage used to thread ~13 callables, most with exactly one
implementation — pure utilities the caller imported and passed through. Those
are now imported at module scope inside stages_ingest, and the interface
crosses only the adapters with real second implementations: the enrichment
adapter (no-op vs chain), the cooldown policy, and the incident client,
bundled in IngestContext.

Tests lock the seam shape (context bundle, thin signature, utilities bound
at module scope) and prove the enrichment and cooldown adapters actually
cross the interface end-to-end against an in-memory database with a stubbed
feed fetch.
"""

from __future__ import annotations

import inspect
import sqlite3
from dataclasses import FrozenInstanceError, fields, is_dataclass

import pytest

import app.utils as app_utils
from app.config import SourceConfig
from app.db import init_db
from app.jobs import pipeline, stages_ingest


@pytest.fixture()
def conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    yield conn
    conn.close()


def _source() -> SourceConfig:
    return SourceConfig(
        source_id="test-source",
        name="Test Source",
        feed_url="https://example.com/feed.xml",
        default_category="general",
        enabled=True,
    )


def _insert_source(conn) -> None:
    conn.execute(
        "INSERT INTO sources (source_id, name, feed_url, default_category, enabled)"
        " VALUES (?, ?, ?, ?, ?)",
        ("test-source", "Test Source", "https://example.com/feed.xml", "general", 1),
    )
    conn.commit()


class FakeIssueClient:
    def __init__(self) -> None:
        self.created: list[tuple] = []
        self.comments: list[tuple] = []
        self.closed: list[int] = []

    def create_issue(self, title: str, body: str, labels: list[str]):
        self.created.append((title, body, labels))
        return 1

    def add_comment(self, issue_number: int, body: str) -> None:
        self.comments.append((issue_number, body))

    def close_issue(self, issue_number: int) -> None:
        self.closed.append(issue_number)


class _FakeResponse:
    def __init__(self, content: bytes) -> None:
        self.status_code = 200
        self.headers: dict[str, str] = {}
        self.content = content

    def raise_for_status(self) -> None:
        return None


_RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Test Feed</title>
    <item>
      <title>Hello World</title>
      <link>https://example.com/post/1</link>
      <description>Summary body</description>
    </item>
  </channel>
</rss>
"""


# --- seam shape -------------------------------------------------------------


def test_ingest_context_bundles_exactly_the_real_adapters() -> None:
    assert is_dataclass(stages_ingest.IngestContext)
    field_names = {f.name for f in fields(stages_ingest.IngestContext)}
    assert field_names == {
        "issue_client",
        "enrich_article_content",
        "source_is_in_cooldown",
        "should_auto_disable_source",
    }


def test_ingest_context_is_frozen() -> None:
    ctx = stages_ingest.IngestContext(
        issue_client=FakeIssueClient(),
        enrich_article_content=lambda *a: ("body", "rss", []),
        source_is_in_cooldown=lambda *a: False,
        should_auto_disable_source=lambda *a: False,
    )
    with pytest.raises(FrozenInstanceError):
        ctx.issue_client = None  # type: ignore[misc]


def test_ingest_stage_signature_is_thin() -> None:
    params = list(inspect.signature(stages_ingest.ingest_stage).parameters)
    # 20 parameters before (4 positional + 16 keyword); now one context object.
    assert params == [
        "conn",
        "run_id",
        "sources",
        "ctx",
        "defuddle_enabled",
        "source_fail_threshold",
        "source_auto_disable_cooldown_hours",
        "slow_source_latency_ms",
    ]


def test_pure_utils_are_imported_not_threaded() -> None:
    # The single-implementation utilities resolve at module scope, so tests
    # can still swap them by monkeypatching the module attribute and the
    # fetch call site no longer has to import and bind them.
    assert stages_ingest.utc_now_iso is app_utils.utc_now_iso
    assert stages_ingest.canonicalize_url is app_utils.canonicalize_url
    assert stages_ingest.normalize_text is app_utils.normalize_text
    assert stages_ingest.sha1_hexdigest is app_utils.sha1_hexdigest
    assert stages_ingest.simhash64 is app_utils.simhash64
    assert stages_ingest.iso is pipeline.iso
    assert stages_ingest.parse_date is pipeline.parse_date
    assert stages_ingest.parse_date_inferred is pipeline.parse_date_inferred
    assert stages_ingest.get_source_timeout_seconds is pipeline.get_source_timeout_seconds


# --- the enrichment adapter crosses the interface ---------------------------


def test_enrichment_adapter_is_crossed_by_ingest(monkeypatch, conn) -> None:
    _insert_source(conn)
    monkeypatch.setattr(stages_ingest.requests, "get", lambda *a, **k: _FakeResponse(_RSS))

    calls: list[tuple] = []

    def fake_enrich(url: str, source_id: str, title: str, body: str):
        calls.append((url, source_id, title, body))
        return body.upper(), "jina", [{"method": "jina", "status": "success"}]

    ctx = stages_ingest.IngestContext(
        issue_client=FakeIssueClient(),
        enrich_article_content=fake_enrich,
        source_is_in_cooldown=lambda *a: False,
        should_auto_disable_source=lambda *a: False,
    )
    result = stages_ingest.ingest_stage(
        conn,
        "run-1",
        [_source()],
        ctx,
        defuddle_enabled=False,
        source_fail_threshold=3,
        source_auto_disable_cooldown_hours=24,
        slow_source_latency_ms=5000,
    )

    assert result["inserted_articles"] == 1
    # The adapter saw the entry with (url, source_id, title, body).
    assert calls == [("https://example.com/post/1", "test-source", "Hello World", "Summary body")]
    # Its return value drove the article row.
    row = conn.execute(
        "SELECT extraction_method, body FROM articles WHERE url = ?",
        ("https://example.com/post/1",),
    ).fetchone()
    assert row["extraction_method"] == "jina"
    assert row["body"] == "SUMMARY BODY"


def test_enrichment_adapter_swapped_for_real_chain_shape(monkeypatch, conn) -> None:
    # The no-op fetch adapter returns (body, "rss", []) — the shape the real
    # chain shares (body, method, attempts). Both must parse identically.
    _insert_source(conn)
    monkeypatch.setattr(stages_ingest.requests, "get", lambda *a, **k: _FakeResponse(_RSS))

    ctx = stages_ingest.IngestContext(
        issue_client=FakeIssueClient(),
        enrich_article_content=lambda url, source_id, title, body: (body, "rss", []),
        source_is_in_cooldown=lambda *a: False,
        should_auto_disable_source=lambda *a: False,
    )
    result = stages_ingest.ingest_stage(
        conn,
        "run-1",
        [_source()],
        ctx,
        defuddle_enabled=False,
        source_fail_threshold=3,
        source_auto_disable_cooldown_hours=24,
        slow_source_latency_ms=5000,
    )

    assert result["inserted_articles"] == 1
    row = conn.execute(
        "SELECT extraction_method FROM articles WHERE url = ?",
        ("https://example.com/post/1",),
    ).fetchone()
    assert row["extraction_method"] == "rss"


# --- the cooldown policy crosses the interface ------------------------------


def test_cooldown_policy_skips_source_via_context(conn) -> None:
    _insert_source(conn)
    conn.execute(
        "INSERT INTO source_health (source_id, auto_disabled_until)"
        " VALUES (?, ?)",
        ("test-source", "2099-01-01T00:00:00+00:00"),
    )
    conn.commit()

    policy_seen: list[tuple] = []

    def fake_cooldown(auto_disabled_until: str | None, now):
        policy_seen.append((auto_disabled_until, now))
        return True

    ctx = stages_ingest.IngestContext(
        issue_client=FakeIssueClient(),
        enrich_article_content=lambda *a: ("body", "rss", []),
        source_is_in_cooldown=fake_cooldown,
        should_auto_disable_source=lambda *a: False,
    )
    result = stages_ingest.ingest_stage(
        conn,
        "run-1",
        [_source()],
        ctx,
        defuddle_enabled=False,
        source_fail_threshold=3,
        source_auto_disable_cooldown_hours=24,
        slow_source_latency_ms=5000,
    )

    assert result["skipped_sources"] == 1
    assert result["inserted_articles"] == 0
    assert len(policy_seen) == 1
    assert policy_seen[0][0] == "2099-01-01T00:00:00+00:00"
    check = conn.execute(
        "SELECT status FROM source_checks WHERE source_id = ?", ("test-source",)
    ).fetchone()
    assert check["status"] == "skipped"