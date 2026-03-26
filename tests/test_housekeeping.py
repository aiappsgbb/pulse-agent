"""Tests for daily housekeeping — file pruning and state cleanup."""

import json
import time
from pathlib import Path

import pytest

from core.housekeeping import (
    run_housekeeping,
    _delete_old_files,
    _truncate_jsonl,
    _prune_state_file,
    _age_days,
    DEFAULT_RETENTION,
)


# --- Helpers ---

def _make_old_file(path: Path, days_old: int):
    """Create a file and backdate its mtime."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("test", encoding="utf-8")
    old_time = time.time() - (days_old * 86400)
    import os
    os.utime(str(path), (old_time, old_time))


def _make_recent_file(path: Path):
    """Create a file with current mtime."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("test", encoding="utf-8")


# --- _age_days ---

def test_age_days_recent_file(tmp_path):
    f = tmp_path / "recent.txt"
    f.write_text("hi")
    assert _age_days(f) < 0.01  # less than ~15 minutes


def test_age_days_old_file(tmp_path):
    f = tmp_path / "old.txt"
    _make_old_file(f, 10)
    assert 9.5 < _age_days(f) < 10.5


def test_age_days_missing_file(tmp_path):
    f = tmp_path / "missing.txt"
    assert _age_days(f) == 0


# --- _delete_old_files ---

def test_delete_old_files_removes_old(tmp_path):
    _make_old_file(tmp_path / "monitoring-2026-01-01.json", 10)
    _make_old_file(tmp_path / "monitoring-2026-01-02.json", 5)
    _make_recent_file(tmp_path / "monitoring-2026-03-19.json")

    deleted = _delete_old_files(tmp_path, "monitoring-*.json", max_age_days=3)
    assert deleted == 2
    assert (tmp_path / "monitoring-2026-03-19.json").exists()
    assert not (tmp_path / "monitoring-2026-01-01.json").exists()


def test_delete_old_files_keeps_recent(tmp_path):
    _make_recent_file(tmp_path / "test.json")
    deleted = _delete_old_files(tmp_path, "*.json", max_age_days=1)
    assert deleted == 0
    assert (tmp_path / "test.json").exists()


def test_delete_old_files_empty_dir(tmp_path):
    deleted = _delete_old_files(tmp_path, "*.json", max_age_days=1)
    assert deleted == 0


def test_delete_old_files_nonexistent_dir(tmp_path):
    deleted = _delete_old_files(tmp_path / "nope", "*.json", max_age_days=1)
    assert deleted == 0


# --- _truncate_jsonl ---

def test_truncate_jsonl_removes_old_entries(tmp_path):
    path = tmp_path / "history.jsonl"
    lines = [
        json.dumps({"ts": "2026-01-01T00:00:00", "type": "old"}),
        json.dumps({"ts": "2026-01-15T00:00:00", "type": "old2"}),
        json.dumps({"ts": "2026-03-19T12:00:00", "type": "recent"}),
    ]
    path.write_text("\n".join(lines), encoding="utf-8")

    removed = _truncate_jsonl(path, max_age_days=30)
    assert removed >= 2  # old entries removed
    remaining = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(remaining) == 1
    assert "recent" in remaining[0]


def test_truncate_jsonl_keeps_all_recent(tmp_path):
    path = tmp_path / "history.jsonl"
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    lines = [
        json.dumps({"ts": now, "type": "a"}),
        json.dumps({"ts": now, "type": "b"}),
    ]
    path.write_text("\n".join(lines), encoding="utf-8")

    removed = _truncate_jsonl(path, max_age_days=1)
    assert removed == 0


def test_truncate_jsonl_missing_file(tmp_path):
    removed = _truncate_jsonl(tmp_path / "nope.jsonl", max_age_days=1)
    assert removed == 0


def test_truncate_jsonl_keeps_malformed_lines(tmp_path):
    path = tmp_path / "history.jsonl"
    path.write_text("not json\n{\"ts\": \"2020-01-01\", \"x\": 1}\n", encoding="utf-8")
    removed = _truncate_jsonl(path, max_age_days=1)
    remaining = path.read_text(encoding="utf-8").strip().splitlines()
    assert "not json" in remaining[0]  # malformed line kept


# --- _prune_state_file ---

def test_prune_state_file_removes_old(tmp_path):
    path = tmp_path / "state.json"
    data = {
        "old_item": {"processed_at": "2025-01-01T00:00:00", "path": "/old"},
        "recent_item": {"processed_at": "2026-03-19T00:00:00", "path": "/new"},
        "no_timestamp": {"path": "/kept"},  # no ts → kept
    }
    path.write_text(json.dumps(data), encoding="utf-8")

    removed = _prune_state_file(path, max_age_days=30)
    assert removed == 1
    result = json.loads(path.read_text(encoding="utf-8"))
    assert "old_item" not in result
    assert "recent_item" in result
    assert "no_timestamp" in result


def test_prune_state_file_missing(tmp_path):
    removed = _prune_state_file(tmp_path / "nope.json", max_age_days=30)
    assert removed == 0


# --- run_housekeeping (integration) ---

def test_run_housekeeping_full(tmp_path, monkeypatch):
    """Full housekeeping run with various old files."""
    # Monkeypatch PULSE_HOME and directory constants
    monkeypatch.setattr("core.housekeeping.PULSE_HOME", tmp_path)
    monkeypatch.setattr("core.housekeeping.LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr("core.housekeeping.DIGESTS_DIR", tmp_path / "digests")
    monkeypatch.setattr("core.housekeeping.INTEL_DIR", tmp_path / "intel")
    monkeypatch.setattr("core.housekeeping.JOBS_DIR", tmp_path / "jobs")

    # Create old monitoring reports
    _make_old_file(tmp_path / "monitoring-2026-01-01T10-00.json", 10)
    _make_old_file(tmp_path / "monitoring-2026-01-01T10-00.md", 10)
    _make_recent_file(tmp_path / "monitoring-2026-03-19T10-00.json")

    # Create old digests
    _make_old_file(tmp_path / "digests" / "2025-12-01.json", 60)
    _make_old_file(tmp_path / "digests" / "2025-12-01.md", 60)
    _make_recent_file(tmp_path / "digests" / "2026-03-19.json")

    # Create old logs
    _make_old_file(tmp_path / "logs" / "2026-01-01.jsonl", 15)
    _make_old_file(tmp_path / "logs" / "job-abc123.jsonl", 10)
    _make_recent_file(tmp_path / "logs" / "2026-03-19.jsonl")

    # Create old completed jobs
    _make_old_file(tmp_path / "jobs" / "completed" / "old-job.yaml", 10)
    _make_recent_file(tmp_path / "jobs" / "completed" / "new-job.yaml")

    # Create old intel
    _make_old_file(tmp_path / "intel" / "2026-01-01.md", 30)

    result = run_housekeeping()
    assert result["monitoring_json"] == 1
    assert result["monitoring_md"] == 1
    assert result["digests_json"] == 1
    assert result["digests_md"] == 1
    assert result["daily_logs"] == 1
    assert result["job_logs"] == 1
    assert result["completed_jobs"] == 1
    assert result["intel"] == 1

    # Recent files still exist
    assert (tmp_path / "monitoring-2026-03-19T10-00.json").exists()
    assert (tmp_path / "digests" / "2026-03-19.json").exists()
    assert (tmp_path / "logs" / "2026-03-19.jsonl").exists()
    assert (tmp_path / "jobs" / "completed" / "new-job.yaml").exists()


def test_run_housekeeping_config_overrides(tmp_path, monkeypatch):
    """Config can override default retention periods."""
    monkeypatch.setattr("core.housekeeping.PULSE_HOME", tmp_path)
    monkeypatch.setattr("core.housekeeping.LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr("core.housekeeping.DIGESTS_DIR", tmp_path / "digests")
    monkeypatch.setattr("core.housekeeping.INTEL_DIR", tmp_path / "intel")
    monkeypatch.setattr("core.housekeeping.JOBS_DIR", tmp_path / "jobs")

    # 2-day old monitoring report — default retention is 3 days (would keep)
    _make_old_file(tmp_path / "monitoring-old.json", 2)

    # With default config: kept
    result = run_housekeeping()
    assert result["monitoring_json"] == 0

    # With override: retention=1 day → deleted
    config = {"housekeeping": {"retention": {"monitoring": 1}}}
    result = run_housekeeping(config)
    assert result["monitoring_json"] == 1


def test_run_housekeeping_empty_pulse_home(tmp_path, monkeypatch):
    """No files to clean up → all zeros."""
    monkeypatch.setattr("core.housekeeping.PULSE_HOME", tmp_path)
    monkeypatch.setattr("core.housekeeping.LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr("core.housekeeping.DIGESTS_DIR", tmp_path / "digests")
    monkeypatch.setattr("core.housekeeping.INTEL_DIR", tmp_path / "intel")
    monkeypatch.setattr("core.housekeeping.JOBS_DIR", tmp_path / "jobs")

    result = run_housekeeping()
    assert all(v == 0 for v in result.values())


# --- Browser idle watcher ---

def test_browser_manager_idle_tracking():
    """BrowserManager tracks last-used time and reports idle seconds."""
    import time as _time
    from core.browser import BrowserManager

    mgr = BrowserManager(user_data_dir="/tmp/test-profile")
    assert mgr.idle_seconds < 1

    # Simulate time passing
    mgr._last_used = _time.monotonic() - 150
    assert mgr.idle_seconds >= 149


def test_browser_manager_touch_resets_idle():
    """touch() resets the idle timer."""
    import time as _time
    from core.browser import BrowserManager

    mgr = BrowserManager(user_data_dir="/tmp/test-profile")
    mgr._last_used = _time.monotonic() - 300  # 5 minutes ago
    assert mgr.idle_seconds >= 299

    mgr.touch()
    assert mgr.idle_seconds < 1


def test_ensure_browser_returns_none_on_failure():
    """ensure_browser returns None when browser fails to start."""
    import asyncio
    from unittest.mock import patch, AsyncMock
    from core.browser import ensure_browser

    with patch("core.browser.BrowserManager") as MockMgr:
        instance = MockMgr.return_value
        instance.start = AsyncMock(side_effect=Exception("no Edge"))
        result = asyncio.get_event_loop().run_until_complete(ensure_browser())
    assert result is None


def test_default_retention_values():
    """Verify sensible defaults."""
    assert DEFAULT_RETENTION["monitoring"] == 3
    assert DEFAULT_RETENTION["digests"] == 30
    assert DEFAULT_RETENTION["logs"] == 7
    assert DEFAULT_RETENTION["job_logs"] == 3
    assert DEFAULT_RETENTION["completed_jobs"] == 3
