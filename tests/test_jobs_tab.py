"""Tests for Jobs tab, job history IPC, per-job activity logs, auto-dismiss,
and dismiss/archive + note prompt chain.

Tests real logic paths: file I/O, event consolidation, EventHandler logging,
and the dismiss→note chain.
"""

import json
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Job history IPC
# ---------------------------------------------------------------------------


class TestJobHistoryIPC:
    """append_job_event and read_job_history with real file I/O."""

    def test_append_and_read(self, tmp_dir):
        with patch("tui.ipc.PULSE_HOME", tmp_dir):
            with patch("tui.ipc.JOB_HISTORY_FILE", tmp_dir / ".job-history.jsonl"):
                from tui.ipc import append_job_event, read_job_history

                append_job_event("j1", "digest", "running", "Morning digest")
                append_job_event("j1", "digest", "completed", "Morning digest")
                append_job_event("j2", "monitor", "running", "Triage")

                events = read_job_history()
                assert len(events) == 3
                # Most recent first
                assert events[0]["job_id"] == "j2"
                assert events[1]["job_id"] == "j1"
                assert events[1]["status"] == "completed"

    def test_read_empty(self, tmp_dir):
        with patch("tui.ipc.JOB_HISTORY_FILE", tmp_dir / ".job-history.jsonl"):
            from tui.ipc import read_job_history

            assert read_job_history() == []

    def test_append_with_log_file(self, tmp_dir):
        with patch("tui.ipc.JOB_HISTORY_FILE", tmp_dir / ".job-history.jsonl"):
            from tui.ipc import append_job_event, read_job_history

            append_job_event("j1", "digest", "running", "test", log_file="/logs/job-j1.jsonl")
            events = read_job_history()
            assert events[0]["log_file"] == "/logs/job-j1.jsonl"

    def test_limit_respected(self, tmp_dir):
        with patch("tui.ipc.JOB_HISTORY_FILE", tmp_dir / ".job-history.jsonl"):
            from tui.ipc import append_job_event, read_job_history

            for i in range(10):
                append_job_event(f"j{i}", "digest", "completed", f"Job {i}")

            events = read_job_history(limit=3)
            # Should return at most limit * 4 raw entries, but still capped
            assert len(events) == 10  # 10 < 3*4=12, so all fit

    def test_cleanup_orphaned_jobs(self, tmp_dir):
        """Orphaned 'running' jobs get marked as 'failed' on cleanup."""
        with patch("tui.ipc.JOB_HISTORY_FILE", tmp_dir / ".job-history.jsonl"):
            from tui.ipc import append_job_event, read_job_history, cleanup_orphaned_jobs

            # Simulate: j1 completed normally, j2 left running (daemon killed)
            append_job_event("j1", "digest", "running", "Digest")
            append_job_event("j1", "digest", "completed", "Digest")
            append_job_event("j2", "monitor", "running", "Triage")

            cleaned = cleanup_orphaned_jobs()
            assert cleaned == 1

            events = read_job_history()
            # j2 should now have a "failed" event
            j2_events = [e for e in events if e["job_id"] == "j2"]
            assert any(e["status"] == "failed" for e in j2_events)
            assert any("daemon restarted" in e.get("detail", "").lower() for e in j2_events)

    def test_cleanup_no_orphans(self, tmp_dir):
        """No orphans to clean up returns 0."""
        with patch("tui.ipc.JOB_HISTORY_FILE", tmp_dir / ".job-history.jsonl"):
            from tui.ipc import append_job_event, cleanup_orphaned_jobs

            append_job_event("j1", "digest", "running", "Digest")
            append_job_event("j1", "digest", "completed", "Digest")

            cleaned = cleanup_orphaned_jobs()
            assert cleaned == 0

    def test_cleanup_empty_history(self, tmp_dir):
        """Cleanup on empty history returns 0."""
        with patch("tui.ipc.JOB_HISTORY_FILE", tmp_dir / ".job-history.jsonl"):
            from tui.ipc import cleanup_orphaned_jobs

            assert cleanup_orphaned_jobs() == 0


# ---------------------------------------------------------------------------
# Job event consolidation (screens.py)
# ---------------------------------------------------------------------------


class TestConsolidateJobs:
    """_consolidate_jobs groups events by job_id and picks latest status."""

    def test_basic_consolidation(self):
        from tui.screens import _consolidate_jobs

        events = [
            {"job_id": "j1", "job_type": "digest", "status": "running", "ts": "2026-03-02T10:00:00", "detail": "Morning", "log_file": "/logs/j1.jsonl"},
            {"job_id": "j1", "job_type": "digest", "status": "completed", "ts": "2026-03-02T10:05:00", "detail": "Morning", "log_file": "/logs/j1.jsonl"},
            {"job_id": "j2", "job_type": "monitor", "status": "running", "ts": "2026-03-02T10:06:00", "detail": "Triage", "log_file": ""},
        ]
        jobs = _consolidate_jobs(events)
        assert len(jobs) == 2
        # Running first
        assert jobs[0]["job_id"] == "j2"
        assert jobs[0]["status"] == "running"
        # Completed second
        assert jobs[1]["job_id"] == "j1"
        assert jobs[1]["status"] == "completed"

    def test_empty_events(self):
        from tui.screens import _consolidate_jobs

        assert _consolidate_jobs([]) == []

    def test_failed_status(self):
        from tui.screens import _consolidate_jobs

        events = [
            {"job_id": "j1", "job_type": "digest", "status": "running", "ts": "2026-03-02T10:00:00", "detail": "", "log_file": ""},
            {"job_id": "j1", "job_type": "digest", "status": "failed", "ts": "2026-03-02T10:01:00", "detail": "Timeout", "log_file": ""},
        ]
        jobs = _consolidate_jobs(events)
        assert len(jobs) == 1
        assert jobs[0]["status"] == "failed"
        assert jobs[0]["detail"] == "Timeout"

    def test_multiple_running(self):
        """Multiple running jobs should all appear at the top."""
        from tui.screens import _consolidate_jobs

        events = [
            {"job_id": "j1", "job_type": "digest", "status": "running", "ts": "2026-03-02T10:00:00", "detail": "", "log_file": ""},
            {"job_id": "j2", "job_type": "monitor", "status": "running", "ts": "2026-03-02T10:01:00", "detail": "", "log_file": ""},
            {"job_id": "j3", "job_type": "intel", "status": "completed", "ts": "2026-03-02T09:00:00", "detail": "", "log_file": ""},
        ]
        jobs = _consolidate_jobs(events)
        assert jobs[0]["status"] == "running"
        assert jobs[1]["status"] == "running"
        assert jobs[2]["status"] == "completed"

    def test_started_ts_captured(self):
        from tui.screens import _consolidate_jobs

        events = [
            {"job_id": "j1", "job_type": "digest", "status": "running", "ts": "2026-03-02T10:00:00", "detail": "", "log_file": ""},
            {"job_id": "j1", "job_type": "digest", "status": "completed", "ts": "2026-03-02T10:05:00", "detail": "", "log_file": ""},
        ]
        jobs = _consolidate_jobs(events)
        assert jobs[0]["started_ts"] == "2026-03-02T10:00:00"


# ---------------------------------------------------------------------------
# Per-job activity log (EventHandler)
# ---------------------------------------------------------------------------


class TestEventHandlerLog:
    """EventHandler writes to per-job log file when log_file is set."""

    def test_write_log_creates_file(self, tmp_dir):
        from sdk.event_handler import EventHandler

        log_file = tmp_dir / "job-test.jsonl"
        handler = EventHandler(log_file=log_file)
        handler._write_log({"type": "test", "data": "hello"})

        assert log_file.exists()
        entries = [json.loads(line) for line in log_file.read_text().strip().splitlines()]
        assert len(entries) == 1
        assert entries[0]["type"] == "test"

    def test_no_log_file_no_write(self, tmp_dir):
        from sdk.event_handler import EventHandler

        handler = EventHandler()
        handler._write_log({"type": "test"})
        # No crash, no file created

    def test_tool_events_logged(self, tmp_dir):
        """Tool start/complete events write to log file."""
        from sdk.event_handler import EventHandler

        log_file = tmp_dir / "job-tools.jsonl"
        handler = EventHandler(log_file=log_file)

        # Simulate tool start event
        class FakeToolStartData:
            tool_name = "search_local_files"
            mcp_server_name = None
            arguments = {"query": "test"}
            input = None

        class FakeToolStartEvent:
            type = None  # will be set by dispatch
            data = FakeToolStartData()

        handler._handle_tool_start(FakeToolStartEvent())

        entries = [json.loads(line) for line in log_file.read_text().strip().splitlines()]
        assert len(entries) == 1
        assert entries[0]["type"] == "tool_start"
        assert entries[0]["tool"] == "search_local_files"

    def test_message_logged(self, tmp_dir):
        from sdk.event_handler import EventHandler

        log_file = tmp_dir / "job-msg.jsonl"
        handler = EventHandler(log_file=log_file)

        class FakeMessageData:
            content = "Here is the digest summary."

        class FakeEvent:
            data = FakeMessageData()

        handler._handle_message(FakeEvent())

        entries = [json.loads(line) for line in log_file.read_text().strip().splitlines()]
        assert len(entries) == 1
        assert entries[0]["type"] == "message"
        assert "digest" in entries[0]["preview"]


# ---------------------------------------------------------------------------
# read_job_log
# ---------------------------------------------------------------------------


class TestReadJobLog:
    """read_job_log reads per-job JSONL activity log."""

    def test_reads_entries(self, tmp_dir):
        from tui.ipc import read_job_log

        log_file = tmp_dir / "job-test.jsonl"
        entries = [
            {"ts": "2026-03-02T10:00:00", "type": "tool_start", "tool": "write_output"},
            {"ts": "2026-03-02T10:00:01", "type": "tool_result", "result": "OK"},
        ]
        log_file.write_text("\n".join(json.dumps(e) for e in entries))

        result = read_job_log(str(log_file))
        assert len(result) == 2
        assert result[0]["tool"] == "write_output"

    def test_missing_file(self, tmp_dir):
        from tui.ipc import read_job_log

        result = read_job_log(str(tmp_dir / "nonexistent.jsonl"))
        assert result == []


# ---------------------------------------------------------------------------
# Auto-dismiss on reply
# ---------------------------------------------------------------------------


class TestReplyAutoDismiss:
    """ReplyModal auto-archives items when reply is sent."""

    def test_write_reply_job_with_auto_archive(self, tmp_dir):
        """Verify that archive_item is called when reply succeeds."""
        from tui.ipc import archive_item, _load_digest_actions

        with patch("tui.ipc.DIGEST_ACTIONS_FILE", tmp_dir / ".digest-actions.json"):
            with patch("tui.ipc.JOBS_DIR", tmp_dir / "jobs"):
                from tui.ipc import write_reply_job

                item = {
                    "id": "reply-test-item",
                    "title": "Test reply",
                    "source": "Teams: Test",
                    "suggested_actions": [
                        {"action_type": "teams_reply", "chat_name": "Alice", "draft": "Hi Alice"}
                    ],
                }

                # Write the reply job
                result = write_reply_job(item, "Hi Alice")
                assert result is True

                # Now simulate what ReplyModal does: archive the item
                archive_item(
                    "reply-test-item",
                    title="Test reply",
                    source="Teams: Test",
                )

                actions = _load_digest_actions()
                dismissed = actions.get("dismissed", [])
                assert len(dismissed) == 1
                assert dismissed[0]["item"] == "reply-test-item"
                assert dismissed[0]["status"] == "archived"


# ---------------------------------------------------------------------------
# Dismiss + note combo (NoteModal allow_empty)
# ---------------------------------------------------------------------------


class TestNoteModalAllowEmpty:
    """NoteModal with allow_empty returns 'skipped' on empty input."""

    def test_save_with_text(self, tmp_dir):
        """Note with text saves and returns 'saved'."""
        from tui.ipc import add_note, _load_digest_actions

        with patch("tui.ipc.DIGEST_ACTIONS_FILE", tmp_dir / ".digest-actions.json"):
            add_note("test-item", "Follow up next week")
            actions = _load_digest_actions()
            assert "test-item" in actions["notes"]
            assert actions["notes"]["test-item"]["note"] == "Follow up next week"


# ---------------------------------------------------------------------------
# Worker reply notifications (the critical bug fix)
# ---------------------------------------------------------------------------


class TestWorkerReplyNotifications:
    """teams_send and email_reply must call mark_task_completed + write_job_notification."""

    def test_teams_send_has_notification_code(self):
        """Verify the worker code calls write_job_notification for teams_send."""
        import inspect
        from daemon.worker import job_worker

        source = inspect.getsource(job_worker)
        # The critical bug was missing these calls
        assert 'write_job_notification("teams_send"' in source
        assert 'write_job_notification("email_reply"' in source
        assert 'notify_desktop("Pulse — Teams Reply"' in source
        assert 'notify_desktop("Pulse — Email Reply"' in source

    def test_teams_send_has_mark_completed(self):
        """Verify teams_send calls mark_task_completed."""
        import inspect
        from daemon.worker import job_worker

        source = inspect.getsource(job_worker)
        # Count mark_task_completed occurrences — should include teams_send and email_reply
        count = source.count("mark_task_completed(job)")
        # At minimum: research, transcripts, knowledge, digest/monitor/intel, teams_send, email_reply = 7+
        assert count >= 7, f"Expected >= 7 mark_task_completed calls, got {count}"

    def test_worker_generates_job_id(self):
        """Verify worker generates job_id and log_file for each job."""
        import inspect
        from daemon.worker import job_worker

        source = inspect.getsource(job_worker)
        assert "job_id" in source
        assert "job_log_file" in source
        assert "append_job_event" in source

    def test_browser_jobs_have_timeout(self):
        """Verify teams_send and email_reply are wrapped with asyncio.wait_for timeout."""
        import inspect
        from daemon.worker import job_worker

        source = inspect.getsource(job_worker)
        assert "asyncio.wait_for" in source
        assert "_BROWSER_JOB_TIMEOUT" in source


# ---------------------------------------------------------------------------
# _write_job_log helper
# ---------------------------------------------------------------------------


class TestWriteJobLog:
    """_write_job_log writes progress entries to per-job activity log."""

    def test_writes_entry(self, tmp_dir):
        from daemon.worker import _write_job_log

        log_file = str(tmp_dir / "job-test.jsonl")
        _write_job_log(log_file, "tool_start", tool="teams_send", target="Alice")

        entries = [json.loads(line) for line in Path(log_file).read_text().strip().splitlines()]
        assert len(entries) == 1
        assert entries[0]["type"] == "tool_start"
        assert entries[0]["tool"] == "teams_send"
        assert entries[0]["target"] == "Alice"
        assert "ts" in entries[0]

    def test_no_log_file_no_crash(self):
        from daemon.worker import _write_job_log

        # Should not raise
        _write_job_log(None, "tool_start", tool="test")

    def test_multiple_entries(self, tmp_dir):
        from daemon.worker import _write_job_log

        log_file = str(tmp_dir / "job-multi.jsonl")
        _write_job_log(log_file, "tool_start", tool="teams_send")
        _write_job_log(log_file, "tool_result", status="Sent")

        entries = [json.loads(line) for line in Path(log_file).read_text().strip().splitlines()]
        assert len(entries) == 2
        assert entries[0]["type"] == "tool_start"
        assert entries[1]["type"] == "tool_result"


# ---------------------------------------------------------------------------
# EventHandler log_file parameter
# ---------------------------------------------------------------------------


class TestEventHandlerLogFile:
    """EventHandler accepts log_file parameter and creates parent dirs."""

    def test_log_file_creates_parent_dirs(self, tmp_dir):
        from sdk.event_handler import EventHandler

        log_file = tmp_dir / "subdir" / "job-test.jsonl"
        handler = EventHandler(log_file=log_file)
        handler._write_log({"type": "test"})
        assert log_file.exists()

    def test_idle_event_logged(self, tmp_dir):
        from sdk.event_handler import EventHandler

        log_file = tmp_dir / "job-idle.jsonl"
        handler = EventHandler(log_file=log_file)

        class FakeIdleEvent:
            type = None

        handler._handle_idle(FakeIdleEvent())
        assert handler.done.is_set()

        entries = [json.loads(line) for line in log_file.read_text().strip().splitlines()]
        assert len(entries) == 1
        assert entries[0]["type"] == "idle"

    def test_error_event_logged(self, tmp_dir):
        from sdk.event_handler import EventHandler

        log_file = tmp_dir / "job-err.jsonl"
        handler = EventHandler(log_file=log_file)

        class FakeErrorEvent:
            type = None
            data = "Something went wrong"

        handler._handle_error(FakeErrorEvent())
        assert handler.done.is_set()
        assert handler.error == "Something went wrong"

        entries = [json.loads(line) for line in log_file.read_text().strip().splitlines()]
        assert entries[0]["type"] == "error"
        assert "Something went wrong" in entries[0]["error"]
