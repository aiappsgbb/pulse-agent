"""Tests for daemon/ modules — heartbeat utilities, sync, worker helpers."""

import asyncio
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from daemon.heartbeat import parse_interval
from core.scheduler import is_office_hours
from daemon.worker import _write_agent_response, _requeue_with_delay


# --- parse_interval ---

def test_parse_minutes():
    assert parse_interval("30m") == 1800
    assert parse_interval("5m") == 300
    assert parse_interval("1m") == 60


def test_parse_hours():
    assert parse_interval("1h") == 3600
    assert parse_interval("2h") == 7200


def test_parse_seconds():
    assert parse_interval("10s") == 10
    assert parse_interval("90s") == 90


def test_parse_bare_number():
    assert parse_interval("120") == 120


def test_parse_with_whitespace():
    assert parse_interval("  30m  ") == 1800


def test_parse_invalid_defaults():
    assert parse_interval("bogus") == 1800


def test_parse_case_insensitive():
    assert parse_interval("30M") == 1800
    assert parse_interval("1H") == 3600


# --- is_office_hours ---

def test_office_hours_no_config():
    """No office hours configured = always on."""
    assert is_office_hours({}) is True
    assert is_office_hours({"monitoring": {}}) is True


def test_office_hours_with_config():
    """Just verify it doesn't crash with a valid config."""
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


# --- _write_agent_response ---


def test_write_agent_response_creates_yaml(tmp_dir):
    """Response YAML is written to reply_to path with correct fields."""
    reply_dir = tmp_dir / "reply-jobs"
    reply_dir.mkdir()

    config = {"user": {"name": "Esther Barthel"}}
    job = {
        "type": "agent_request",
        "task": "What about Vodafone?",
        "from": "Artur Zielinski",
        "reply_to": str(reply_dir),
        "request_id": "abc-12345678",
    }

    _write_agent_response(config, job, "Vodafone deal is progressing well.")

    yaml_files = list(reply_dir.glob("*.yaml"))
    assert len(yaml_files) == 1
    data = yaml.safe_load(yaml_files[0].read_text())
    assert data["type"] == "agent_response"
    assert data["kind"] == "response"
    assert data["request_id"] == "abc-12345678"
    assert data["from"] == "Esther Barthel"
    assert "Vodafone" in data["result"]
    assert data["original_task"] == "What about Vodafone?"


def test_write_agent_response_no_reply_to(tmp_dir):
    """No crash and no file written when reply_to is empty."""
    config = {"user": {"name": "Esther"}}
    job = {"type": "agent_request", "task": "Test", "reply_to": ""}

    _write_agent_response(config, job, "Result")
    # Nothing should be written anywhere
    assert not list(tmp_dir.glob("**/*.yaml"))


def test_write_agent_response_creates_reply_dir(tmp_dir):
    """reply_to directory is created if it does not exist."""
    reply_dir = tmp_dir / "new-reply-dir"
    config = {"user": {"name": "Esther"}}
    job = {
        "type": "agent_request",
        "task": "Test",
        "reply_to": str(reply_dir),
        "request_id": "xyz-987",
    }

    _write_agent_response(config, job, "Answer here.")
    assert reply_dir.exists()
    assert len(list(reply_dir.glob("*.yaml"))) == 1


# --- _requeue_with_delay ---


def test_requeue_writes_yaml(tmp_dir):
    """_requeue_with_delay writes a YAML job file with correct retry fields."""
    from core.constants import JOBS_DIR
    with patch("daemon.worker.JOBS_DIR", tmp_dir / "jobs"):
        job = {"type": "digest", "task": "Morning digest"}
        _requeue_with_delay(job, retry_count=1)

    pending_files = list((tmp_dir / "jobs" / "pending").glob("*.yaml"))
    assert len(pending_files) == 1
    data = yaml.safe_load(pending_files[0].read_text())
    assert data["type"] == "digest"
    assert data["_retry_count"] == 1
    assert data["_retry_reason"] == "ProxyResponseError"
    assert "_retry_after" in data


def test_requeue_strips_internal_fields(tmp_dir):
    """_requeue_with_delay strips _ prefixed fields from the original job."""
    with patch("daemon.worker.JOBS_DIR", tmp_dir / "jobs"):
        job = {
            "type": "monitor",
            "_schedule_id": "triage",
            "_chat_id": 123,
            "_file": "/some/file.yaml",
        }
        _requeue_with_delay(job, retry_count=2)

    pending_files = list((tmp_dir / "jobs" / "pending").glob("*.yaml"))
    data = yaml.safe_load(pending_files[0].read_text())
    assert "_schedule_id" not in data
    assert "_chat_id" not in data
    assert "_file" not in data
    assert data["_retry_count"] == 2


def test_requeue_retry_after_is_future(tmp_dir):
    """retry_after timestamp is in the future."""
    from datetime import datetime
    with patch("daemon.worker.JOBS_DIR", tmp_dir / "jobs"):
        _requeue_with_delay({"type": "intel"}, retry_count=1, delay_seconds=300)

    pending_files = list((tmp_dir / "jobs" / "pending").glob("*.yaml"))
    data = yaml.safe_load(pending_files[0].read_text())
    retry_after = datetime.fromisoformat(data["_retry_after"])
    assert retry_after > datetime.now()


def test_load_pending_tasks_skips_future_retry(tmp_dir):
    """load_pending_tasks skips retry jobs whose retry_after is in the future."""
    from datetime import datetime, timedelta
    from core.config import load_pending_tasks

    pending_dir = tmp_dir / "pending"
    pending_dir.mkdir(parents=True)

    future = (datetime.now() + timedelta(minutes=10)).isoformat()
    job_data = {"type": "digest", "_retry_after": future}
    (pending_dir / "retry-digest.yaml").write_text(
        yaml.dump(job_data), encoding="utf-8"
    )

    with patch("core.config.JOBS_DIR", tmp_dir):
        tasks = load_pending_tasks()

    assert tasks == []


def test_load_pending_tasks_includes_past_retry(tmp_dir):
    """load_pending_tasks includes retry jobs whose retry_after has passed."""
    from datetime import datetime, timedelta
    from core.config import load_pending_tasks

    pending_dir = tmp_dir / "pending"
    pending_dir.mkdir(parents=True)

    past = (datetime.now() - timedelta(minutes=1)).isoformat()
    job_data = {"type": "monitor", "_retry_after": past}
    (pending_dir / "retry-monitor.yaml").write_text(
        yaml.dump(job_data), encoding="utf-8"
    )

    with patch("core.config.JOBS_DIR", tmp_dir):
        tasks = load_pending_tasks()

    assert len(tasks) == 1
    assert tasks[0]["type"] == "monitor"


def test_load_pending_tasks_normal_jobs_unaffected(tmp_dir):
    """load_pending_tasks picks up normal jobs (no _retry_after) as before."""
    from core.config import load_pending_tasks

    pending_dir = tmp_dir / "pending"
    pending_dir.mkdir(parents=True)

    (pending_dir / "research.yaml").write_text(
        yaml.dump({"type": "research", "task": "Market analysis"}), encoding="utf-8"
    )

    with patch("core.config.JOBS_DIR", tmp_dir):
        tasks = load_pending_tasks()

    assert len(tasks) == 1
    assert tasks[0]["type"] == "research"
