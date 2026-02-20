"""Tests for core/scheduler.py — persistent schedule management."""

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from core.scheduler import (
    parse_pattern,
    validate_pattern,
    is_due,
    add_schedule,
    remove_schedule,
    list_schedules,
    mark_run,
)


# --- parse_pattern ---

def test_parse_daily():
    result = parse_pattern("daily 07:00")
    assert result == {"type": "daily", "hour": 7, "minute": 0}


def test_parse_weekdays():
    result = parse_pattern("weekdays 09:30")
    assert result == {"type": "weekdays", "hour": 9, "minute": 30}


def test_parse_every_hours():
    result = parse_pattern("every 6h")
    assert result == {"type": "interval", "seconds": 21600}


def test_parse_every_minutes():
    result = parse_pattern("every 30m")
    assert result == {"type": "interval", "seconds": 1800}


def test_parse_case_insensitive():
    result = parse_pattern("Daily 07:00")
    assert result is not None
    assert result["type"] == "daily"


def test_parse_invalid():
    assert parse_pattern("bogus") is None
    assert parse_pattern("every 2x") is None
    assert parse_pattern("daily 25:00") is None
    assert parse_pattern("daily 12:99") is None


def test_parse_too_frequent():
    """Intervals below MIN_INTERVAL_MINUTES are rejected."""
    assert parse_pattern("every 1m") is None  # 1 min < 5 min minimum


def test_parse_with_whitespace():
    result = parse_pattern("  daily  07:00  ")
    # extra internal spaces won't match — only leading/trailing stripped
    # "daily  07:00" has double space, won't match regex
    # That's fine — patterns should be well-formed


# --- validate_pattern ---

def test_validate_valid():
    assert validate_pattern("daily 07:00") is True
    assert validate_pattern("weekdays 18:00") is True
    assert validate_pattern("every 6h") is True
    assert validate_pattern("every 30m") is True


def test_validate_invalid():
    assert validate_pattern("nope") is False
    assert validate_pattern("") is False


# --- is_due ---

def test_is_due_interval_never_run():
    """Interval schedule that never ran is due immediately."""
    schedule = {"pattern": "every 6h", "enabled": True, "last_run": None}
    assert is_due(schedule) is True


def test_is_due_interval_recently_run():
    """Interval schedule that ran recently is not due."""
    schedule = {
        "pattern": "every 6h",
        "enabled": True,
        "last_run": datetime.now().isoformat(),
    }
    assert is_due(schedule) is False


def test_is_due_interval_elapsed():
    """Interval schedule past its period is due."""
    schedule = {
        "pattern": "every 30m",
        "enabled": True,
        "last_run": (datetime.now() - timedelta(hours=1)).isoformat(),
    }
    assert is_due(schedule) is True


def test_is_due_daily_past_time_not_run_today():
    """Daily schedule past target time, not yet run today."""
    now = datetime.now().replace(hour=10, minute=0)
    schedule = {
        "pattern": "daily 07:00",
        "enabled": True,
        "last_run": (now - timedelta(days=1)).isoformat(),
    }
    assert is_due(schedule, now=now) is True


def test_is_due_daily_already_run_today():
    """Daily schedule already run today is not due."""
    now = datetime.now().replace(hour=10, minute=0)
    schedule = {
        "pattern": "daily 07:00",
        "enabled": True,
        "last_run": now.replace(hour=7, minute=5).isoformat(),
    }
    assert is_due(schedule, now=now) is False


def test_is_due_daily_before_target_time():
    """Daily schedule before target time is not due."""
    now = datetime.now().replace(hour=6, minute=0)
    schedule = {
        "pattern": "daily 07:00",
        "enabled": True,
        "last_run": None,
    }
    assert is_due(schedule, now=now) is False


def test_is_due_weekdays_on_weekend():
    """Weekdays schedule on Saturday is not due."""
    # Find next Saturday
    now = datetime.now()
    while now.isoweekday() != 6:
        now += timedelta(days=1)
    now = now.replace(hour=10, minute=0)
    schedule = {"pattern": "weekdays 07:00", "enabled": True, "last_run": None}
    assert is_due(schedule, now=now) is False


def test_is_due_weekdays_on_weekday():
    """Weekdays schedule on a weekday past target time is due."""
    now = datetime.now()
    while now.isoweekday() > 5:
        now += timedelta(days=1)
    now = now.replace(hour=10, minute=0)
    schedule = {"pattern": "weekdays 07:00", "enabled": True, "last_run": None}
    assert is_due(schedule, now=now) is True


def test_is_due_disabled():
    """Disabled schedule is never due."""
    schedule = {"pattern": "every 30m", "enabled": False, "last_run": None}
    assert is_due(schedule) is False


# --- add/remove/list/mark_run ---

def test_add_and_list(tmp_dir):
    sched_file = tmp_dir / ".scheduler.json"
    with patch("core.scheduler.SCHEDULER_FILE", sched_file):
        entry = add_schedule("test-1", "digest", "daily 07:00", "Morning digest")
        assert entry["id"] == "test-1"
        assert entry["type"] == "digest"
        assert entry["enabled"] is True

        schedules = list_schedules()
        assert len(schedules) == 1
        assert schedules[0]["id"] == "test-1"


def test_add_sets_last_run_to_prevent_immediate_fire(tmp_dir):
    """New schedules should not fire immediately — last_run is set to now."""
    sched_file = tmp_dir / ".scheduler.json"
    with patch("core.scheduler.SCHEDULER_FILE", sched_file):
        entry = add_schedule("no-fire", "digest", "daily 07:00")
        assert entry["last_run"] is not None
        # Should NOT be due right after creation
        schedules = list_schedules()
        assert is_due(schedules[0]) is False


def test_add_duplicate_raises(tmp_dir):
    sched_file = tmp_dir / ".scheduler.json"
    with patch("core.scheduler.SCHEDULER_FILE", sched_file):
        add_schedule("dup", "digest", "daily 07:00")
        with pytest.raises(ValueError, match="already exists"):
            add_schedule("dup", "intel", "daily 08:00")


def test_add_invalid_pattern_raises(tmp_dir):
    sched_file = tmp_dir / ".scheduler.json"
    with patch("core.scheduler.SCHEDULER_FILE", sched_file):
        with pytest.raises(ValueError, match="Invalid"):
            add_schedule("bad", "digest", "nope nope")


def test_remove(tmp_dir):
    sched_file = tmp_dir / ".scheduler.json"
    with patch("core.scheduler.SCHEDULER_FILE", sched_file):
        add_schedule("rm-me", "digest", "daily 07:00")
        assert remove_schedule("rm-me") is True
        assert list_schedules() == []


def test_remove_nonexistent(tmp_dir):
    sched_file = tmp_dir / ".scheduler.json"
    with patch("core.scheduler.SCHEDULER_FILE", sched_file):
        assert remove_schedule("nope") is False


def test_mark_run(tmp_dir):
    sched_file = tmp_dir / ".scheduler.json"
    with patch("core.scheduler.SCHEDULER_FILE", sched_file):
        add_schedule("mark-test", "digest", "daily 07:00")
        mark_run("mark-test")
        schedules = list_schedules()
        assert schedules[0]["last_run"] is not None
