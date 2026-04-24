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
from daemon.worker import _write_guardian_response, _requeue_with_delay, _is_transient_error


# --- _is_transient_error ---

def test_session_not_found_is_transient():
    """'Session not found' errors should trigger automatic retry with fresh session."""
    error = "JSON-RPC Error -32603: Request session.send failed with message: Session not found: ae16145a-1a5c-4a72-80b1-b7a0ee8f9b57"
    assert _is_transient_error(error) is True


def test_known_transient_errors():
    """All known transient patterns are recognized."""
    assert _is_transient_error("fetch failed") is True
    assert _is_transient_error("Something went wrong") is True
    assert _is_transient_error("Request timed out") is True
    assert _is_transient_error("ECONNREFUSED 127.0.0.1:3000") is True
    assert _is_transient_error("ProxyResponseError: 502") is True


def test_non_transient_errors():
    """Non-transient errors should not trigger retry."""
    assert _is_transient_error("Invalid tool arguments") is False
    assert _is_transient_error("Permission denied") is False


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


# --- _write_guardian_response ---


def test_write_guardian_response_writes_yaml(tmp_dir):
    """Response YAML is written to reply_to path with correct fields."""
    reply_dir = tmp_dir / "reply-jobs"
    reply_dir.mkdir()

    config = {"user": {"name": "Beta User", "alias": "beta"}}
    job = {
        "task": "original question?",
        "project_id": "some-project",
        "reply_to": str(reply_dir),
        "request_id": "abc12345",
    }
    parsed = {
        "status": "answered",
        "result": "Some answer.",
        "sources": ["transcripts/x.md"],
    }
    _write_guardian_response(config, job, parsed)

    files = list(reply_dir.glob("*.yaml"))
    assert len(files) == 1
    data = yaml.safe_load(files[0].read_text())
    assert data["type"] == "agent_response"
    assert data["kind"] == "response"
    assert data["status"] == "answered"
    assert data["project_id"] == "some-project"
    assert data["request_id"] == "abc12345"
    assert data["from"] == "Beta User"
    assert data["result"] == "Some answer."
    assert data["sources"] == ["transcripts/x.md"]
    assert data["original_task"] == "original question?"


def test_write_guardian_response_empty_reply_to_no_crash(tmp_dir, caplog):
    """No crash and no file written when neither reply_to nor sender-alias resolve."""
    _write_guardian_response(
        {"user": {"name": "X", "alias": "x"}, "team": []},
        {"task": "q", "reply_to": "", "request_id": "r"},
        {"status": "no_context"},
    )
    assert not list(tmp_dir.glob("**/*.yaml"))
    assert any(
        "cannot resolve reply destination" in rec.message.lower()
        for rec in caplog.records
    )


def test_write_guardian_response_prefers_receiver_team_config(tmp_dir, monkeypatch):
    """Cross-machine reply_to is ignored when the receiver has the sender in its team.

    The sender's reply_to points at a path local to the sender's machine
    (``C:\\Users\\<them>\\...``) which is not accessible on the receiver.
    The receiver must resolve the write destination from its own team config
    (``agent_path`` or ``PULSE_TEAM_DIR/{alias}/jobs/pending``), not trust the
    sender-provided path.
    """
    # Simulate sender's unreachable path
    unreachable = Path("C:/Users/ghost-user-does-not-exist/OneDrive/Pulse-Team/ricchi/jobs/pending")

    # Receiver's view of sender's shared folder (OneDrive shortcut wherever it landed)
    receiver_view = tmp_dir / "shortcut-from-ricchi"
    receiver_view.mkdir()

    config = {
        "user": {"name": "Artur", "alias": "artur"},
        "team": [{"name": "Riccardo", "alias": "ricchi", "agent_path": str(receiver_view)}],
    }
    job = {
        "task": "What do you know about X?",
        "project_id": "proj-a",
        "from": "Riccardo",
        "from_alias": "ricchi",
        "reply_to": str(unreachable),
        "request_id": "req-xyz",
    }
    parsed = {"status": "answered", "result": "Answer text.", "sources": []}

    _write_guardian_response(config, job, parsed)

    # Did NOT write to the sender-local (unreachable) path
    assert not unreachable.exists()
    # DID write to the receiver's local shortcut view
    files = list((receiver_view / "pending").glob("*.yaml"))
    assert len(files) == 1
    data = yaml.safe_load(files[0].read_text())
    assert data["status"] == "answered"
    assert data["request_id"] == "req-xyz"


def test_write_guardian_response_convention_fallback(tmp_dir):
    """Without agent_path, falls back to PULSE_TEAM_DIR/{sender_alias}/jobs/pending."""
    team_dir = tmp_dir / "Pulse-Team"
    (team_dir / "ricchi" / "jobs" / "pending").mkdir(parents=True)

    with patch("core.constants.PULSE_TEAM_DIR", team_dir):
        config = {
            "user": {"name": "Artur", "alias": "artur"},
            "team": [{"name": "Riccardo", "alias": "ricchi"}],
        }
        job = {
            "task": "q",
            "from_alias": "ricchi",
            "reply_to": "C:/Users/ghost/whatever",
            "request_id": "req-1",
        }
        _write_guardian_response(config, job, {"status": "no_context", "result": "", "sources": []})

    files = list((team_dir / "ricchi" / "jobs" / "pending").glob("*.yaml"))
    assert len(files) == 1


def test_write_guardian_response_creates_missing_reply_dir(tmp_dir):
    """reply_to directory is created if it does not exist."""
    reply_to = tmp_dir / "new" / "deep"  # does not exist yet
    _write_guardian_response(
        {"user": {"name": "B", "alias": "b"}},
        {"task": "q", "project_id": "p", "reply_to": str(reply_to), "request_id": "abc123"},
        {"status": "no_context"},
    )
    assert reply_to.exists()
    assert len(list(reply_to.glob("*.yaml"))) == 1


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

    with patch("core.config.JOBS_DIR", tmp_dir), \
         patch("core.config.load_config", return_value={}):
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

    with patch("core.config.JOBS_DIR", tmp_dir), \
         patch("core.config.load_config", return_value={}):
        tasks = load_pending_tasks()

    assert len(tasks) == 1
    assert tasks[0]["type"] == "monitor"


# --- inter-agent inbox bridge ---


def test_load_pending_tasks_picks_up_team_inbox(tmp_dir):
    """A YAML in PULSE_TEAM_DIR/{my_alias}/jobs/pending/ must be enqueued alongside local jobs."""
    from core.config import load_pending_tasks

    local_pending = tmp_dir / "local" / "pending"
    local_pending.mkdir(parents=True)
    (local_pending / "local-digest.yaml").write_text(
        yaml.dump({"type": "digest"}), encoding="utf-8"
    )

    team_root = tmp_dir / "team"
    inbox = team_root / "artur" / "jobs" / "pending"
    inbox.mkdir(parents=True)
    (inbox / "from-riccardo.yaml").write_text(
        yaml.dump({
            "type": "agent_request",
            "kind": "question",
            "task": "got context on X?",
            "from": "Riccardo",
            "from_alias": "riccardo",
        }),
        encoding="utf-8",
    )

    fake_config = {"user": {"alias": "artur"}}
    with patch("core.config.JOBS_DIR", tmp_dir / "local"), \
         patch("core.config.PULSE_TEAM_DIR", team_root), \
         patch("core.config.load_config", return_value=fake_config):
        tasks = load_pending_tasks()

    types = sorted(t["type"] for t in tasks)
    assert types == ["agent_request", "digest"]
    team_task = next(t for t in tasks if t["type"] == "agent_request")
    assert team_task["from_alias"] == "riccardo"
    assert "team" in team_task["_file"] and "artur" in team_task["_file"]


def test_load_pending_tasks_no_alias_skips_team_inbox(tmp_dir):
    """Solo users with no user.alias never scan the team inbox — defensive no-op."""
    from core.config import load_pending_tasks

    (tmp_dir / "local" / "pending").mkdir(parents=True)

    team_root = tmp_dir / "team"
    inbox = team_root / "someone-else" / "jobs" / "pending"
    inbox.mkdir(parents=True)
    (inbox / "stale.yaml").write_text(
        yaml.dump({"type": "agent_request", "from": "ghost"}), encoding="utf-8"
    )

    with patch("core.config.JOBS_DIR", tmp_dir / "local"), \
         patch("core.config.PULSE_TEAM_DIR", team_root), \
         patch("core.config.load_config", return_value={"user": {}}):
        tasks = load_pending_tasks()

    assert tasks == []


def test_mark_task_completed_moves_within_team_inbox(tmp_dir):
    """Completion must move the file into the sibling completed/ of its ORIGIN folder.

    Previously mark_task_completed always used JOBS_DIR/completed, which meant
    team-inbox requests would cross-dir move into the local PULSE_HOME instead
    of being archived in the shared Pulse-Team folder.
    """
    from core.config import mark_task_completed

    team_pending = tmp_dir / "team" / "artur" / "jobs" / "pending"
    team_pending.mkdir(parents=True)
    src = team_pending / "req-abc.yaml"
    src.write_text(yaml.dump({"type": "agent_request"}), encoding="utf-8")

    mark_task_completed({"_file": str(src)})

    assert not src.exists()
    expected = tmp_dir / "team" / "artur" / "jobs" / "completed" / "req-abc.yaml"
    assert expected.exists()


def test_mark_task_completed_moves_within_local_queue(tmp_dir):
    """Local queue completion still lands in JOBS_DIR/completed — existing behavior preserved."""
    from core.config import mark_task_completed

    local_pending = tmp_dir / "jobs" / "pending"
    local_pending.mkdir(parents=True)
    src = local_pending / "digest.yaml"
    src.write_text(yaml.dump({"type": "digest"}), encoding="utf-8")

    mark_task_completed({"_file": str(src)})

    assert not src.exists()
    assert (tmp_dir / "jobs" / "completed" / "digest.yaml").exists()


# --- write_daemon_status_loop: queue_size includes pending files ---


async def test_status_queue_size_includes_pending_files(tmp_dir):
    """queue_size counts both in-memory queue items and pending YAML files on disk."""
    import json
    from unittest.mock import patch

    pending_dir = tmp_dir / "jobs" / "pending"
    pending_dir.mkdir(parents=True)

    # 2 pending files on disk
    (pending_dir / "digest-job.yaml").write_text("type: digest\n", encoding="utf-8")
    (pending_dir / "intel-job.yml").write_text("type: intel\n", encoding="utf-8")

    # 1 item in memory queue
    q = asyncio.PriorityQueue()
    q.put_nowait((1, 0, {"type": "monitor"}))

    shutdown = asyncio.Event()
    shutdown.set()  # Stop immediately after first write

    status_file = tmp_dir / ".daemon-status.json"
    boot_time = datetime.now()

    with patch("daemon.tasks.PULSE_HOME", tmp_dir), \
         patch("daemon.tasks.JOBS_DIR", tmp_dir / "jobs"):
        from daemon.tasks import write_daemon_status_loop
        await write_daemon_status_loop(q, boot_time, shutdown)

    status = json.loads(status_file.read_text(encoding="utf-8"))
    assert status["queue_size"] == 3  # 1 in-memory + 2 on disk


async def test_status_queue_size_no_pending_dir(tmp_dir):
    """queue_size works when pending/ dir doesn't exist."""
    import json
    from unittest.mock import patch

    q = asyncio.PriorityQueue()
    shutdown = asyncio.Event()
    shutdown.set()

    status_file = tmp_dir / ".daemon-status.json"
    boot_time = datetime.now()

    with patch("daemon.tasks.PULSE_HOME", tmp_dir), \
         patch("daemon.tasks.JOBS_DIR", tmp_dir / "jobs"):
        from daemon.tasks import write_daemon_status_loop
        await write_daemon_status_loop(q, boot_time, shutdown)

    status = json.loads(status_file.read_text(encoding="utf-8"))
    assert status["queue_size"] == 0


async def test_status_queue_size_ignores_non_yaml(tmp_dir):
    """Only .yaml/.yml files counted, not .json or other files."""
    import json
    from unittest.mock import patch

    pending_dir = tmp_dir / "jobs" / "pending"
    pending_dir.mkdir(parents=True)
    (pending_dir / "digest.yaml").write_text("type: digest\n", encoding="utf-8")
    (pending_dir / "notes.txt").write_text("not a job\n", encoding="utf-8")
    (pending_dir / "state.json").write_text("{}\n", encoding="utf-8")

    q = asyncio.PriorityQueue()
    shutdown = asyncio.Event()
    shutdown.set()

    status_file = tmp_dir / ".daemon-status.json"
    boot_time = datetime.now()

    with patch("daemon.tasks.PULSE_HOME", tmp_dir), \
         patch("daemon.tasks.JOBS_DIR", tmp_dir / "jobs"):
        from daemon.tasks import write_daemon_status_loop
        await write_daemon_status_loop(q, boot_time, shutdown)

    status = json.loads(status_file.read_text(encoding="utf-8"))
    assert status["queue_size"] == 1  # Only the .yaml file


async def test_status_includes_current_job(tmp_dir):
    """active_workers info is included in status when a job is running."""
    import json
    from unittest.mock import patch

    q = asyncio.PriorityQueue()
    shutdown = asyncio.Event()
    shutdown.set()

    status_file = tmp_dir / ".daemon-status.json"
    boot_time = datetime.now()

    with patch("daemon.tasks.PULSE_HOME", tmp_dir), \
         patch("daemon.tasks.JOBS_DIR", tmp_dir / "jobs"), \
         patch("daemon.tasks.active_workers", {0: {"type": "digest", "started": "2026-03-02T10:00:00", "job_id": "test"}}):
        from daemon.tasks import write_daemon_status_loop
        await write_daemon_status_loop(q, boot_time, shutdown)

    status = json.loads(status_file.read_text(encoding="utf-8"))
    assert status["current_job"] == "digest"
    assert status["current_job_started"] == "2026-03-02T10:00:00"
    assert len(status["active_workers"]) == 1
    assert status["active_workers"][0]["job_type"] == "digest"


def test_load_pending_tasks_normal_jobs_unaffected(tmp_dir):
    """load_pending_tasks picks up normal jobs (no _retry_after) as before."""
    from core.config import load_pending_tasks

    pending_dir = tmp_dir / "pending"
    pending_dir.mkdir(parents=True)

    (pending_dir / "research.yaml").write_text(
        yaml.dump({"type": "research", "task": "Market analysis"}), encoding="utf-8"
    )

    with patch("core.config.JOBS_DIR", tmp_dir), \
         patch("core.config.load_config", return_value={}):
        tasks = load_pending_tasks()

    assert len(tasks) == 1
    assert tasks[0]["type"] == "research"
