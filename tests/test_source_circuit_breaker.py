from datetime import datetime, timedelta, timezone

from app.jobs.pipeline import should_auto_disable_source, source_is_in_cooldown


def test_should_auto_disable_after_failure_threshold_and_time_window() -> None:
    now = datetime(2026, 3, 14, 12, 0, tzinfo=timezone.utc)
    stale_success = (now - timedelta(hours=13)).isoformat()
    assert should_auto_disable_source(6, stale_success, now=now) is True


def test_should_not_auto_disable_if_recent_success_even_with_failures() -> None:
    now = datetime(2026, 3, 14, 12, 0, tzinfo=timezone.utc)
    recent_success = (now - timedelta(hours=2)).isoformat()
    assert should_auto_disable_source(7, recent_success, now=now) is False


def test_source_cooldown_window_check() -> None:
    now = datetime(2026, 3, 14, 12, 0, tzinfo=timezone.utc)
    future_until = (now + timedelta(hours=3)).isoformat()
    past_until = (now - timedelta(minutes=10)).isoformat()
    assert source_is_in_cooldown(future_until, now=now) is True
    assert source_is_in_cooldown(past_until, now=now) is False
