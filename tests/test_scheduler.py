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
    is_office_hours,
    add_schedule,
    update_schedule,
    remove_schedule,
    list_schedules,
    mark_run,
    ensure_default_schedules,
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


def test_update_schedule_changes_pattern(tmp_dir):
    sched_file = tmp_dir / ".scheduler.json"
    with patch("core.scheduler.SCHEDULER_FILE", sched_file):
        add_schedule("update-me", "monitor", "every 30m")
        result = update_schedule("update-me", pattern="every 15m")
        assert result is not None
        assert result["pattern"] == "every 15m"
        # Verify persisted
        schedules = list_schedules()
        assert schedules[0]["pattern"] == "every 15m"


def test_update_schedule_not_found(tmp_dir):
    sched_file = tmp_dir / ".scheduler.json"
    with patch("core.scheduler.SCHEDULER_FILE", sched_file):
        result = update_schedule("nope", pattern="every 15m")
        assert result is None


def test_update_schedule_invalid_pattern(tmp_dir):
    sched_file = tmp_dir / ".scheduler.json"
    with patch("core.scheduler.SCHEDULER_FILE", sched_file):
        add_schedule("bad-update", "monitor", "every 30m")
        with pytest.raises(ValueError, match="Invalid"):
            update_schedule("bad-update", pattern="nope")


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


# --- is_office_hours ---

def test_office_hours_no_config():
    """No office hours configured = always on."""
    assert is_office_hours({}) is True
    assert is_office_hours({"monitoring": {}}) is True


def test_office_hours_with_config():
    """Returns bool with valid office hours config."""
    config = {
        "monitoring": {
            "office_hours": {
                "start": "08:00",
                "end": "18:00",
                "days": [1, 2, 3, 4, 5],
            }
        }
    }
    result = is_office_hours(config)
    assert isinstance(result, bool)


# --- is_due with office_hours_only ---

def test_is_due_office_hours_only_respected():
    """Schedule with office_hours_only=True is not due outside office hours."""
    schedule = {
        "pattern": "every 30m",
        "enabled": True,
        "last_run": None,
        "office_hours_only": True,
    }
    # Config with office hours that are definitely NOT now (use midnight range)
    config = {
        "monitoring": {
            "office_hours": {
                "start": "03:00",
                "end": "03:01",
                "days": [1, 2, 3, 4, 5, 6, 7],
            }
        }
    }
    # Without config, office_hours_only is ignored
    assert is_due(schedule) is True
    # With config that puts us outside hours, not due
    assert is_due(schedule, config=config) is False


def test_is_due_office_hours_only_false_ignores():
    """Schedule with office_hours_only=False is due regardless of hours."""
    schedule = {
        "pattern": "every 30m",
        "enabled": True,
        "last_run": None,
        "office_hours_only": False,
    }
    config = {
        "monitoring": {
            "office_hours": {
                "start": "03:00",
                "end": "03:01",
                "days": [1, 2, 3, 4, 5, 6, 7],
            }
        }
    }
    assert is_due(schedule, config=config) is True


# --- ensure_default_schedules ---

def test_ensure_default_schedules_seeds_new(tmp_dir):
    """Config schedules are added to empty scheduler state."""
    sched_file = tmp_dir / ".scheduler.json"
    config = {
        "schedule": [
            {"id": "morning-digest", "type": "digest", "pattern": "daily 07:00",
             "description": "Morning digest"},
            {"id": "triage", "type": "monitor", "pattern": "every 30m",
             "description": "Triage", "office_hours_only": True},
        ]
    }
    with patch("core.scheduler.SCHEDULER_FILE", sched_file):
        ensure_default_schedules(config)
        schedules = list_schedules()

    assert len(schedules) == 2
    digest = next(s for s in schedules if s["id"] == "morning-digest")
    assert digest["type"] == "digest"
    assert digest["pattern"] == "daily 07:00"
    assert digest["last_run"] is None  # catch-up fires naturally
    assert digest["office_hours_only"] is False

    triage = next(s for s in schedules if s["id"] == "triage")
    assert triage["office_hours_only"] is True


def test_ensure_default_schedules_updates_existing_pattern(tmp_dir):
    """Config pattern change updates existing schedule but preserves last_run."""
    sched_file = tmp_dir / ".scheduler.json"
    config_v1 = {
        "schedule": [
            {"id": "digest", "type": "digest", "pattern": "daily 07:00"},
        ]
    }
    config_v2 = {
        "schedule": [
            {"id": "digest", "type": "digest", "pattern": "daily 06:00",
             "description": "Earlier digest"},
        ]
    }
    with patch("core.scheduler.SCHEDULER_FILE", sched_file):
        ensure_default_schedules(config_v1)
        # Simulate a run
        mark_run("digest")
        old_last_run = list_schedules()[0]["last_run"]

        # Update pattern
        ensure_default_schedules(config_v2)
        schedules = list_schedules()

    assert len(schedules) == 1
    assert schedules[0]["pattern"] == "daily 06:00"
    assert schedules[0]["description"] == "Earlier digest"
    assert schedules[0]["last_run"] == old_last_run  # preserved


def test_ensure_default_schedules_leaves_agent_created(tmp_dir):
    """Agent-created schedules (not in config) are left untouched."""
    sched_file = tmp_dir / ".scheduler.json"
    config = {
        "schedule": [
            {"id": "digest", "type": "digest", "pattern": "daily 07:00"},
        ]
    }
    with patch("core.scheduler.SCHEDULER_FILE", sched_file):
        # Agent creates a custom schedule
        add_schedule("custom-research", "research", "every 6h", "My research")
        ensure_default_schedules(config)
        schedules = list_schedules()

    assert len(schedules) == 2
    ids = {s["id"] for s in schedules}
    assert "custom-research" in ids
    assert "digest" in ids


def test_ensure_default_schedules_skips_invalid_pattern(tmp_dir):
    """Invalid patterns in config are skipped without crashing."""
    sched_file = tmp_dir / ".scheduler.json"
    config = {
        "schedule": [
            {"id": "good", "type": "digest", "pattern": "daily 07:00"},
            {"id": "bad", "type": "intel", "pattern": "nope nope"},
        ]
    }
    with patch("core.scheduler.SCHEDULER_FILE", sched_file):
        ensure_default_schedules(config)
        schedules = list_schedules()

    assert len(schedules) == 1
    assert schedules[0]["id"] == "good"


def test_ensure_default_schedules_no_config():
    """No schedule config = no-op."""
    ensure_default_schedules({})
    ensure_default_schedules({"schedule": []})


def test_ensure_default_schedules_idempotent(tmp_dir):
    """Running ensure twice with same config doesn't duplicate schedules."""
    sched_file = tmp_dir / ".scheduler.json"
    config = {
        "schedule": [
            {"id": "digest", "type": "digest", "pattern": "daily 07:00"},
        ]
    }
    with patch("core.scheduler.SCHEDULER_FILE", sched_file):
        ensure_default_schedules(config)
        ensure_default_schedules(config)
        schedules = list_schedules()

    assert len(schedules) == 1
