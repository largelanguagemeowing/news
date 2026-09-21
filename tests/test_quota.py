"""The daily-quota seam: one DailyQuota machine, two instances.

The markdown.new and compress.new daily-limit bookkeeping used to be two
near-identical copies (load/save/exhausted/reserve) differing only in the
state file path. Tests here cross DailyQuota with an isolated tmp_path file —
no real data/status files are touched — and the flavour-specific record
functions through their module instances pointed at tmp files.
"""

from __future__ import annotations

import json

from app.jobs import enrichment
from app.jobs.quota import DailyQuota


def _quota(tmp_path, limit: int = 3, name: str = "test") -> DailyQuota:
    return DailyQuota(
        name=name,
        path=tmp_path / "quota.json",
        daily_limit=limit,
        extra_state=lambda: {"counter": 0},
    )


def test_fresh_state_when_no_file_yet(tmp_path) -> None:
    quota = _quota(tmp_path, limit=100)
    exhausted, state = quota.exhausted()
    assert exhausted is False
    assert state["date"] is not None
    assert state["limit"] == 100
    assert state["remaining"] == 100
    assert state["requests_made"] == 0
    assert state["exhausted"] is False


def test_reserve_tracks_requests_until_exhausted(tmp_path) -> None:
    quota = _quota(tmp_path, limit=3)
    assert quota.reserve() is True
    assert quota.reserve() is True
    assert quota.reserve() is True
    assert quota.reserve() is False  # limit reached

    exhausted, state = quota.exhausted()
    assert exhausted is True
    assert state["requests_made"] == 3
    assert state["remaining"] == 0


def test_reserve_does_not_write_when_exhausted(tmp_path) -> None:
    quota = _quota(tmp_path, limit=1)
    assert quota.reserve() is True
    exhausted, state = quota.exhausted()
    assert exhausted is True
    assert quota.reserve() is False
    # state unchanged after the failed reserve
    exhausted, state = quota.exhausted()
    assert state["requests_made"] == 1


def test_load_resets_stale_dates(tmp_path) -> None:
    quota = _quota(tmp_path, limit=5)
    tmp_path.joinpath("quota.json").write_text(
        json.dumps(
            {
                "date": "2000-01-01",
                "requests_made": 4,
                "limit": 5,
                "remaining": 1,
                "exhausted": False,
                "updated_at": "2000-01-01T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    state = quota.load()
    assert state["date"] != "2000-01-01"
    assert state["requests_made"] == 0
    assert state["remaining"] == 5


def test_load_resets_corrupt_files(tmp_path) -> None:
    quota = _quota(tmp_path, limit=5)
    tmp_path.joinpath("quota.json").write_text("{not json", encoding="utf-8")
    exhausted, state = quota.exhausted()
    assert exhausted is False
    assert state["requests_made"] == 0


def test_state_persists_across_instances(tmp_path) -> None:
    first = _quota(tmp_path, limit=2)
    assert first.reserve() is True
    second = _quota(tmp_path, limit=2)
    exhausted, state = second.exhausted()
    assert state["requests_made"] == 1
    assert second.reserve() is True
    assert second.reserve() is False


def test_exhausted_honours_file_limit_even_with_remaining(tmp_path) -> None:
    quota = _quota(tmp_path, limit=10)
    tmp_path.joinpath("quota.json").write_text(
        json.dumps(
            {
                "date": quota.load()["date"],
                "requests_made": 10,
                "limit": 10,
                "remaining": 5,
                "exhausted": False,
                "updated_at": "2026-01-01T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    exhausted, state = quota.exhausted()
    assert exhausted is True  # requests_made >= limit wins


def test_extra_state_fields_present_and_fresh_per_call(tmp_path) -> None:
    quota = _quota(tmp_path)
    first = quota.new_state()
    assert first["counter"] == 0
    # mutating one fresh state must not leak into the next
    first["counter"] = 99
    assert quota.new_state()["counter"] == 0


def test_machines_are_instances_of_the_same_class() -> None:
    assert isinstance(enrichment.markdown_new_quota, DailyQuota)
    assert isinstance(enrichment.compress_new_quota, DailyQuota)
    assert type(enrichment.markdown_new_quota) is type(enrichment.compress_new_quota)
    assert enrichment.markdown_new_quota.name == "markdown.new"
    assert enrichment.compress_new_quota.name == "compress.new"


def test_record_markdown_new_response_observes_header(monkeypatch, tmp_path) -> None:
    quota = _quota(tmp_path, limit=100)
    monkeypatch.setattr(enrichment, "markdown_new_quota", quota)
    enrichment.record_markdown_new_response(
        80,
        status_code=200,
        raw_remaining_header="80",
        url="https://example.com/post",
    )
    state = quota.load()
    assert state["remaining"] == 80
    assert state["requests_made"] == 20
    assert state["last_response"]["parsed_remaining"] == 80
    assert state["last_response"]["url"] == "https://example.com/post"
    assert len(state["header_observations"]) == 1
    assert state["exhausted"] is False


def test_record_markdown_new_response_429_exhausts(monkeypatch, tmp_path) -> None:
    quota = _quota(tmp_path, limit=100)
    monkeypatch.setattr(enrichment, "markdown_new_quota", quota)
    enrichment.record_markdown_new_response(0, status_code=429)
    exhausted, state = quota.exhausted()
    assert exhausted is True
    assert state["remaining"] == 0


def test_record_compress_new_response_counts_success_and_failure(monkeypatch, tmp_path) -> None:
    quota = _quota(tmp_path, limit=100)
    monkeypatch.setattr(enrichment, "compress_new_quota", quota)
    enrichment.record_compress_new_response(True)
    enrichment.record_compress_new_response(False, "boom")
    state = quota.load()
    assert state["total_successes"] == 1
    assert state["total_failures"] == 1
    assert state["consecutive_failures"] == 1
    assert state["last_success"] is not None
    assert state["last_failure"] is not None
    enrichment.record_compress_new_response(True)
    state = quota.load()
    assert state["consecutive_failures"] == 0
    assert state["total_successes"] == 2