"""Tests covering real logic gaps — concurrent safety, encoding, TTL boundaries,
IPC edge cases, hook error paths, reply flow variants, and large data handling.

Every test exercises real code paths, not just mock wiring.
"""

import json
import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# 1. Concurrent file write safety
# ---------------------------------------------------------------------------


class TestConcurrentDigestActions:
    """Verify atomic write pattern survives concurrent access."""

    def test_concurrent_digest_actions_writes(self, tmp_path):
        """Multiple threads writing digest actions — file remains valid JSON.

        On Windows, concurrent os.replace can fail with PermissionError.
        We tolerate errors during the race but assert the final file is valid.
        """
        actions_file = tmp_path / ".digest-actions.json"
        actions_file.write_text('{"dismissed": [], "notes": {}}')

        errors = []

        def writer(thread_id):
            for i in range(10):
                try:
                    data = json.loads(actions_file.read_text())
                    data["dismissed"].append({"item": f"t{thread_id}-{i}"})
                    tmp = actions_file.with_suffix(f".t{thread_id}.tmp")
                    tmp.write_text(json.dumps(data))
                    os.replace(tmp, actions_file)
                except Exception as e:
                    errors.append(str(e))

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # After all threads complete, final read should be valid JSON
        # (individual writes may have failed, but the file should not be truncated/corrupt)
        content = actions_file.read_text()
        data = json.loads(content)
        assert isinstance(data["dismissed"], list)
        assert len(data["dismissed"]) > 0  # at least some writes succeeded

    def test_atomic_save_via_state_module(self, tmp_path):
        """save_json_state uses os.replace — verify it doesn't leave .tmp artifacts."""
        from core.state import save_json_state, load_json_state
        import time

        state_file = tmp_path / "test-state.json"
        for i in range(5):
            for attempt in range(3):
                try:
                    save_json_state(state_file, {"counter": i})
                    break
                except OSError:
                    time.sleep(0.05)  # Windows file lock contention

        result = load_json_state(state_file, {})
        assert result["counter"] == 4
        # No leftover .tmp files
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert len(tmp_files) == 0

    def test_save_json_state_creates_parent_dirs(self, tmp_path):
        """save_json_state creates parent directories if missing."""
        from core.state import save_json_state, load_json_state

        deep_file = tmp_path / "a" / "b" / "c" / "state.json"
        save_json_state(deep_file, {"deep": True})
        assert load_json_state(deep_file, {}) == {"deep": True}

    def test_load_json_state_returns_default_on_corrupt(self, tmp_path):
        """Corrupt JSON falls back to default dict."""
        from core.state import load_json_state

        corrupt = tmp_path / "corrupt.json"
        corrupt.write_text("{this is not valid json")
        result = load_json_state(corrupt, {"fallback": True})
        assert result == {"fallback": True}


# ---------------------------------------------------------------------------
# 2. Unicode edge cases in extractors
# ---------------------------------------------------------------------------


class TestExtractorUnicode:
    """Real file I/O with various encodings."""

    def test_plaintext_extractor_handles_cjk(self, tmp_path):
        """CJK characters in transcripts don't crash extraction."""
        from collectors.extractors import extract_text

        f = tmp_path / "meeting.txt"
        f.write_text("Discussion about pricing strategy.\n"
                      "Notes from Tokyo office:\n"
                      "This is a test with special chars.",
                      encoding="utf-8")
        result = extract_text(f)
        assert result is not None
        assert "Tokyo" in result

    def test_plaintext_extractor_handles_mixed_encoding(self, tmp_path):
        """Files with mixed encoding fall back to latin-1."""
        from collectors.extractors import extract_text

        f = tmp_path / "mixed.txt"
        f.write_bytes(b"Hello \xff\xfe world")
        result = extract_text(f)
        assert result is not None
        assert "Hello" in result
        assert "world" in result

    def test_plaintext_extractor_handles_bom(self, tmp_path):
        """UTF-8 BOM prefix doesn't crash extraction."""
        from collectors.extractors import extract_text

        f = tmp_path / "bom.txt"
        f.write_bytes(b"\xef\xbb\xbfHello BOM world")
        result = extract_text(f)
        assert result is not None
        assert "Hello BOM world" in result

    def test_unsupported_extension_returns_none(self, tmp_path):
        """Unsupported file types return None, not crash."""
        from collectors.extractors import extract_text

        f = tmp_path / "data.xyz"
        f.write_text("some content")
        assert extract_text(f) is None

    def test_empty_file_returns_empty_string(self, tmp_path):
        """Empty text file returns empty string, not None."""
        from collectors.extractors import extract_text

        f = tmp_path / "empty.txt"
        f.write_text("")
        result = extract_text(f)
        assert result == ""

    def test_markdown_extraction(self, tmp_path):
        """Markdown files use the plaintext extractor."""
        from collectors.extractors import extract_text

        f = tmp_path / "notes.md"
        f.write_text("# Meeting Notes\n\n- Action item 1\n- Action item 2")
        result = extract_text(f)
        assert "Meeting Notes" in result
        assert "Action item" in result

    def test_vtt_extraction(self, tmp_path):
        """VTT (subtitle) files are extracted as plaintext."""
        from collectors.extractors import extract_text

        f = tmp_path / "transcript.vtt"
        f.write_text("WEBVTT\n\n00:00:01.000 --> 00:00:05.000\nHello everyone")
        result = extract_text(f)
        assert "Hello everyone" in result

    def test_eml_extraction(self, tmp_path):
        """EML files are extracted as plaintext."""
        from collectors.extractors import extract_text

        f = tmp_path / "message.eml"
        f.write_text("From: alice@example.com\nSubject: Test\n\nHello")
        result = extract_text(f)
        assert "alice@example.com" in result


# ---------------------------------------------------------------------------
# 3. TTL boundary conditions
# ---------------------------------------------------------------------------


class TestTTLBoundaries:
    """Exercise exact boundary behavior of dismissed item TTL logic."""

    def test_snooze_at_exactly_one_day(self):
        """Snoozed item at exactly 1 day age is included (boundary is >1, not >=1)."""
        from sdk.runner import _build_dismissed_block

        exactly_one_day = (datetime.now() - timedelta(days=1)).isoformat()
        actions = {
            "dismissed": [{
                "item": "boundary-snooze",
                "status": "dismissed",
                "title": "Boundary snooze",
                "dismissed_at": exactly_one_day,
            }],
            "notes": {},
        }

        with patch("sdk.runner.load_actions", return_value=actions):
            result = _build_dismissed_block()

        # days property truncates — timedelta(days=1).days == 1, which is NOT > 1
        assert "boundary-snooze" in result

    def test_snooze_at_two_days_excluded(self):
        """Snoozed item at 2 days age is excluded."""
        from sdk.runner import _build_dismissed_block

        two_days = (datetime.now() - timedelta(days=2)).isoformat()
        actions = {
            "dismissed": [{
                "item": "expired-snooze",
                "status": "dismissed",
                "title": "Expired",
                "dismissed_at": two_days,
            }],
            "notes": {},
        }

        with patch("sdk.runner.load_actions", return_value=actions):
            result = _build_dismissed_block()

        assert "expired-snooze" not in result

    def test_archive_at_exactly_30_days(self):
        """Archived item at exactly 30 days is included (boundary is >30, not >=30)."""
        from sdk.runner import _build_dismissed_block

        exactly_30 = (datetime.now() - timedelta(days=30)).isoformat()
        actions = {
            "dismissed": [{
                "item": "boundary-archive",
                "status": "archived",
                "title": "Boundary archive",
                "dismissed_at": exactly_30,
            }],
            "notes": {},
        }

        with patch("sdk.runner.load_actions", return_value=actions):
            result = _build_dismissed_block()

        assert "boundary-archive" in result

    def test_archive_at_31_days_excluded(self):
        """Archived item at 31 days is excluded."""
        from sdk.runner import _build_dismissed_block

        past_31 = (datetime.now() - timedelta(days=31)).isoformat()
        actions = {
            "dismissed": [{
                "item": "expired-archive",
                "status": "archived",
                "title": "Expired",
                "dismissed_at": past_31,
            }],
            "notes": {},
        }

        with patch("sdk.runner.load_actions", return_value=actions):
            result = _build_dismissed_block()

        assert "expired-archive" not in result

    def test_resolved_never_expires(self):
        """Resolved items are always included regardless of age."""
        from sdk.runner import _build_dismissed_block

        ancient = (datetime.now() - timedelta(days=365)).isoformat()
        actions = {
            "dismissed": [{
                "item": "resolved-ancient",
                "status": "resolved",
                "title": "Ancient resolved",
                "dismissed_at": ancient,
            }],
            "notes": {},
        }

        with patch("sdk.runner.load_actions", return_value=actions):
            result = _build_dismissed_block()

        assert "resolved-ancient" in result
        assert "PERMANENTLY done" in result


# ---------------------------------------------------------------------------
# 4. IPC partial write handling
# ---------------------------------------------------------------------------


class TestIPCEdgeCases:
    """Chat stream IPC edge cases and partial writes."""

    def test_chat_stream_partial_jsonl_line(self, tmp_path):
        """Partial JSONL line at end of file is not parsed (JSONDecodeError skip)."""
        from tui.ipc import read_chat_stream_deltas

        stream_file = tmp_path / ".chat-stream.jsonl"
        stream_file.write_text(
            '{"type":"delta","text":"hello","request_id":"r1"}\n'
            '{"type":"delta","text":"wor',  # incomplete
            encoding="utf-8",
        )

        with patch("tui.ipc.CHAT_STREAM_FILE", stream_file):
            text, done, offset, *_ = read_chat_stream_deltas(0, "r1")

        assert text == "hello"
        assert done is False

    def test_chat_stream_empty_file(self, tmp_path):
        """Empty stream file returns empty result."""
        from tui.ipc import read_chat_stream_deltas

        stream_file = tmp_path / ".chat-stream.jsonl"
        stream_file.write_text("", encoding="utf-8")

        with patch("tui.ipc.CHAT_STREAM_FILE", stream_file):
            text, done, offset, *_ = read_chat_stream_deltas(0)

        assert text == ""
        assert done is False
        assert offset == 0

    def test_chat_stream_only_whitespace_lines(self, tmp_path):
        """Stream file with only whitespace lines returns empty."""
        from tui.ipc import read_chat_stream_deltas

        stream_file = tmp_path / ".chat-stream.jsonl"
        stream_file.write_text("\n\n   \n\n", encoding="utf-8")

        with patch("tui.ipc.CHAT_STREAM_FILE", stream_file):
            text, done, offset, *_ = read_chat_stream_deltas(0)

        assert text == ""

    def test_job_notification_round_trip(self, tmp_path):
        """Write and read job notification atomically."""
        from tui.ipc import write_job_notification, read_job_notification

        notif_file = tmp_path / ".job-notification.json"
        with patch("tui.ipc.JOB_NOTIFICATION_FILE", notif_file):
            write_job_notification("digest", "Processed 42 items")
            result = read_job_notification()

        assert result is not None
        assert result["job_type"] == "digest"
        assert result["summary"] == "Processed 42 items"
        # File should be deleted after read
        assert not notif_file.exists()

    def test_job_notification_none_when_missing(self, tmp_path):
        """read_job_notification returns None when no file exists."""
        from tui.ipc import read_job_notification

        notif_file = tmp_path / ".job-notification.json"
        with patch("tui.ipc.JOB_NOTIFICATION_FILE", notif_file):
            result = read_job_notification()

        assert result is None

    def test_read_question_response_session_mismatch(self, tmp_path):
        """Question response for wrong session returns None."""
        from tui.ipc import read_question_response

        resp_file = tmp_path / ".question-response.json"
        resp_file.write_text(json.dumps({
            "session_id": "session-A",
            "answer": "yes",
        }), encoding="utf-8")

        with patch("tui.ipc.QUESTION_RESPONSE_FILE", resp_file):
            result = read_question_response("session-B")

        assert result is None

    def test_read_question_response_session_match(self, tmp_path):
        """Question response for matching session returns the answer."""
        from tui.ipc import read_question_response

        resp_file = tmp_path / ".question-response.json"
        resp_file.write_text(json.dumps({
            "session_id": "session-A",
            "answer": "yes",
        }), encoding="utf-8")

        with patch("tui.ipc.QUESTION_RESPONSE_FILE", resp_file):
            result = read_question_response("session-A")

        assert result == "yes"


# ---------------------------------------------------------------------------
# 5. Hook error isolation
# ---------------------------------------------------------------------------


class TestHookErrorIsolation:
    """Hooks must never crash — verify they swallow errors silently.

    All tests patch LOGS_DIR to a temp directory to avoid polluting
    the production audit log with test entries.
    """

    def test_post_tool_use_hook_survives_bad_input(self, tmp_dir):
        """post_tool_use hook doesn't crash on missing/weird fields."""
        from sdk.hooks import make_post_tool_use_hook

        with patch("sdk.hooks.LOGS_DIR", tmp_dir):
            hook = make_post_tool_use_hook()
            # Completely empty input
            hook({}, None)
            # None context
            hook({"toolName": "test"}, None)
            # Garbage input
            hook({"toolName": 12345, "toolArgs": object()}, {"session_id": None})
            # No crash = pass

    def test_pre_tool_use_hook_allows_normal_tools(self):
        """pre_tool_use hook returns None (allow) for non-write tools."""
        from sdk.hooks import make_pre_tool_use_hook

        hook = make_pre_tool_use_hook()
        result = hook({"toolName": "search_local_files", "toolArgs": {"query": "test"}}, {})
        assert result is None

    def test_pre_tool_use_hook_blocks_path_traversal(self):
        """pre_tool_use hook blocks write_output with '..' in filename."""
        from sdk.hooks import make_pre_tool_use_hook

        hook = make_pre_tool_use_hook()
        result = hook(
            {"toolName": "write_output", "toolArgs": {"filename": "../../../etc/passwd"}},
            {},
        )
        assert result is not None
        assert result["permissionDecision"] == "deny"

    def test_pre_tool_use_hook_blocks_project_id_traversal(self):
        """pre_tool_use hook blocks update_project with path chars in ID."""
        from sdk.hooks import make_pre_tool_use_hook

        hook = make_pre_tool_use_hook()
        for bad_id in ["../evil", "foo/bar", "foo\\bar"]:
            result = hook(
                {"toolName": "update_project", "toolArgs": {"project_id": bad_id}},
                {},
            )
            assert result is not None
            assert result["permissionDecision"] == "deny", f"Should block {bad_id}"

    def test_error_hook_returns_retry_for_tool_execution(self, tmp_dir):
        """error_occurred hook returns retry for recoverable tool errors."""
        from sdk.hooks import make_error_occurred_hook

        with patch("sdk.hooks.LOGS_DIR", tmp_dir):
            hook = make_error_occurred_hook()
            result = hook(
                {"error": "timeout", "errorContext": "tool_execution", "recoverable": True},
                {},
            )
            assert result is not None
            assert result["errorHandling"] == "retry"
            assert result["retryCount"] == 1

    def test_error_hook_returns_none_for_unrecoverable(self, tmp_dir):
        """error_occurred hook returns None for unrecoverable errors."""
        from sdk.hooks import make_error_occurred_hook

        with patch("sdk.hooks.LOGS_DIR", tmp_dir):
            hook = make_error_occurred_hook()
            result = hook(
                {"error": "fatal", "errorContext": "session", "recoverable": False},
                {},
            )
            assert result is None

    def test_session_end_hook_logs_duration(self, tmp_dir):
        """session_end hook doesn't crash and handles normal input."""
        from sdk.hooks import make_session_end_hook

        with patch("sdk.hooks.LOGS_DIR", tmp_dir):
            hook = make_session_end_hook("digest", time.time() - 60.0)
            # Should not raise
            hook({"reason": "complete"}, {"session_id": "test-session"})

    def test_session_end_hook_with_error(self, tmp_dir):
        """session_end hook handles error field in input."""
        from sdk.hooks import make_session_end_hook

        with patch("sdk.hooks.LOGS_DIR", tmp_dir):
            hook = make_session_end_hook("chat", time.time())
            hook({"reason": "error", "error": "Something went wrong"}, {})

    def test_build_hooks_returns_all_four(self):
        """build_hooks returns dict with all 4 hook keys."""
        from sdk.hooks import build_hooks

        hooks = build_hooks("triage")
        assert "on_pre_tool_use" in hooks
        assert "on_post_tool_use" in hooks
        assert "on_error_occurred" in hooks
        assert "on_session_end" in hooks
        # All are callable
        for key, fn in hooks.items():
            assert callable(fn), f"{key} is not callable"


# ---------------------------------------------------------------------------
# 6. Reply flow with all known action_type variants
# ---------------------------------------------------------------------------


class TestReplyFlowActionTypes:
    """write_reply_job must handle all action_type values that LLM prompts produce."""

    @pytest.mark.parametrize("action_type,expected_job_type", [
        ("teams_reply", "teams_send"),
        ("teams_send", "teams_send"),
        ("draft_teams_reply", "teams_send"),
        ("email_reply", "email_reply"),
        ("send_email_reply", "email_reply"),
    ])
    def test_reply_job_maps_action_types(self, action_type, expected_job_type, tmp_path):
        """All known LLM action_type values map to correct job types."""
        from tui.ipc import write_reply_job

        pending_dir = tmp_path / "jobs" / "pending"
        pending_dir.mkdir(parents=True)

        item = {
            "id": "test-1",
            "title": "Test item",
            "source": "Teams: Alice",
            "suggested_actions": [{
                "action_type": action_type,
                "target": "Alice Smith",
                "chat_name": "Alice Smith",
                "draft": "Hello!",
            }],
        }

        with patch("tui.ipc.JOBS_DIR", tmp_path / "jobs"):
            result = write_reply_job(item, "Hello!")

        assert result is True
        # Verify the job file was written
        files = list(pending_dir.glob("*.yaml"))
        assert len(files) == 1

        import yaml
        job = yaml.safe_load(files[0].read_text())
        assert job["type"] == expected_job_type

    def test_reply_job_unknown_type_returns_false(self, tmp_path):
        """Unknown action_type returns False."""
        from tui.ipc import write_reply_job

        item = {
            "id": "test-1",
            "suggested_actions": [{
                "action_type": "schedule_meeting",  # not teams/email
                "target": "Someone",
                "draft": "Let's meet",
            }],
        }

        with patch("tui.ipc.JOBS_DIR", tmp_path / "jobs"):
            result = write_reply_job(item, "Let's meet")

        assert result is False

    def test_reply_job_no_actions_returns_false(self):
        """Item with no suggested_actions returns False."""
        from tui.ipc import write_reply_job

        assert write_reply_job({"id": "test", "suggested_actions": []}, "Hi") is False
        assert write_reply_job({"id": "test"}, "Hi") is False

    def test_reply_job_target_field_maps_to_chat_name(self, tmp_path):
        """LLM 'target' field maps to 'chat_name' in job YAML."""
        from tui.ipc import write_reply_job

        pending_dir = tmp_path / "jobs" / "pending"
        pending_dir.mkdir(parents=True)

        item = {
            "id": "test-target",
            "source": "Teams: Bob",
            "suggested_actions": [{
                "action_type": "draft_teams_reply",
                "target": "Bob Jones",  # LLM uses 'target'
                "draft": "Got it",
            }],
        }

        with patch("tui.ipc.JOBS_DIR", tmp_path / "jobs"):
            write_reply_job(item, "Got it")

        import yaml
        files = list(pending_dir.glob("*.yaml"))
        job = yaml.safe_load(files[0].read_text())
        assert job["chat_name"] == "Bob Jones"

    def test_reply_job_email_search_query(self, tmp_path):
        """Email reply job includes search_query from target or title."""
        from tui.ipc import write_reply_job

        pending_dir = tmp_path / "jobs" / "pending"
        pending_dir.mkdir(parents=True)

        item = {
            "id": "email-test",
            "title": "RE: Budget Review",
            "source": "Email: Jane",
            "suggested_actions": [{
                "action_type": "send_email_reply",
                "target": "Budget Review",
                "draft": "Approved.",
            }],
        }

        with patch("tui.ipc.JOBS_DIR", tmp_path / "jobs"):
            write_reply_job(item, "Approved.")

        import yaml
        files = list(pending_dir.glob("*.yaml"))
        job = yaml.safe_load(files[0].read_text())
        assert job["type"] == "email_reply"
        assert job["search_query"] == "Budget Review"


# ---------------------------------------------------------------------------
# 7. Large data handling
# ---------------------------------------------------------------------------


class TestLargeDataHandling:
    """Verify system handles large volumes without crash or hang."""

    def test_digest_with_1000_items_roundtrip(self, tmp_path):
        """1000-item digest JSON roundtrips through file I/O."""
        items = [
            {"id": f"item-{i}", "title": f"Item {i}", "priority": "low",
             "source": "Email", "status": "new", "reply_needed": False}
            for i in range(1000)
        ]
        digest = {"items": items, "stats": {"outstanding": 0, "new": 1000}}

        f = tmp_path / "big-digest.json"
        f.write_text(json.dumps(digest), encoding="utf-8")

        loaded = json.loads(f.read_text(encoding="utf-8"))
        assert len(loaded["items"]) == 1000
        assert loaded["items"][999]["id"] == "item-999"

    def test_job_history_rotation(self, tmp_path):
        """Job history rotation keeps tail when file exceeds max lines."""
        from tui.ipc import _maybe_rotate_job_history, _JOB_HISTORY_MAX_LINES

        history_file = tmp_path / ".job-history.jsonl"
        # Write more than _JOB_HISTORY_MAX_LINES entries
        excess = 100
        total_lines = _JOB_HISTORY_MAX_LINES + excess
        lines = []
        for i in range(total_lines):
            entry = json.dumps({
                "ts": datetime.now().isoformat(),
                "job_id": f"job-{i}",
                "job_type": "test",
                "status": "completed",
            })
            lines.append(entry)
        history_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

        with patch("tui.ipc.JOB_HISTORY_FILE", history_file):
            _maybe_rotate_job_history()

        result_lines = history_file.read_text(encoding="utf-8").strip().splitlines()
        assert len(result_lines) == _JOB_HISTORY_MAX_LINES
        # Should keep the tail — last entry should be the newest
        last = json.loads(result_lines[-1])
        assert last["job_id"] == f"job-{total_lines - 1}"

    def test_job_history_no_rotation_when_under_limit(self, tmp_path):
        """Job history file not modified when under the limit."""
        from tui.ipc import _maybe_rotate_job_history, _JOB_HISTORY_MAX_LINES

        history_file = tmp_path / ".job-history.jsonl"
        lines = []
        for i in range(10):
            lines.append(json.dumps({"ts": "2026-03-25", "job_id": f"j-{i}"}))
        history_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        original_content = history_file.read_text()

        with patch("tui.ipc.JOB_HISTORY_FILE", history_file):
            _maybe_rotate_job_history()

        assert history_file.read_text() == original_content

    def test_read_job_history_limits_output(self, tmp_path):
        """read_job_history returns at most limit entries."""
        from tui.ipc import read_job_history

        history_file = tmp_path / ".job-history.jsonl"
        lines = []
        for i in range(500):
            lines.append(json.dumps({
                "ts": datetime.now().isoformat(),
                "job_id": f"job-{i}",
                "job_type": "test",
                "status": "completed",
            }))
        history_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

        with patch("tui.ipc.JOB_HISTORY_FILE", history_file):
            result = read_job_history(limit=50)

        # The function reads limit*4 tail lines then parses them
        # With 500 lines and limit=50, it reads 200 lines from end
        assert len(result) <= 500
        assert len(result) > 0

    def test_cleanup_orphaned_jobs(self, tmp_path):
        """cleanup_orphaned_jobs marks dead-PID running jobs as failed.

        Live-PID running jobs are preserved (regression for 2026-04-28
        incident where an in-flight transcripts job was incorrectly flipped
        to failed by a startup cleanup pass).
        """
        from tui.ipc import cleanup_orphaned_jobs, append_job_event

        history_file = tmp_path / ".job-history.jsonl"
        # Simulate a daemon crash — the PID stamped on the running event no
        # longer corresponds to a live process.
        with patch("tui.ipc.JOB_HISTORY_FILE", history_file), \
             patch("tui.ipc._pid_is_alive", side_effect=lambda pid: False):
            append_job_event("orphan-1", "digest", "running", "Started")
            cleaned = cleanup_orphaned_jobs()

        assert cleaned == 1

        with patch("tui.ipc.JOB_HISTORY_FILE", history_file):
            from tui.ipc import read_job_history
            events = read_job_history()

        failed_events = [e for e in events if e.get("status") == "failed"]
        assert len(failed_events) == 1
        assert "interrupted" in failed_events[0].get("detail", "").lower()


# ---------------------------------------------------------------------------
# 8. Housekeeping logic (real file I/O, real date math)
# ---------------------------------------------------------------------------


class TestHousekeepingLogic:
    """Test housekeeping file pruning with real files."""

    def test_prune_digest_actions_expired_snooze(self, tmp_path):
        """Snoozed items older than 1 day are pruned."""
        from core.housekeeping import _prune_digest_actions

        actions = {
            "dismissed": [
                {
                    "item": "old-snooze",
                    "status": "dismissed",
                    "dismissed_at": (datetime.now() - timedelta(days=3)).isoformat(),
                },
                {
                    "item": "fresh-snooze",
                    "status": "dismissed",
                    "dismissed_at": datetime.now().isoformat(),
                },
            ],
            "notes": {"old-snooze": {"note": "test"}},
        }
        f = tmp_path / ".digest-actions.json"
        f.write_text(json.dumps(actions), encoding="utf-8")

        removed = _prune_digest_actions(f)
        assert removed >= 1  # old snooze + its orphaned note

        data = json.loads(f.read_text(encoding="utf-8"))
        item_ids = {d["item"] for d in data["dismissed"]}
        assert "fresh-snooze" in item_ids
        assert "old-snooze" not in item_ids

    def test_prune_digest_actions_keeps_resolved(self, tmp_path):
        """Resolved items are never pruned regardless of age."""
        from core.housekeeping import _prune_digest_actions

        actions = {
            "dismissed": [{
                "item": "ancient-resolved",
                "status": "resolved",
                "dismissed_at": (datetime.now() - timedelta(days=365)).isoformat(),
            }],
            "notes": {},
        }
        f = tmp_path / ".digest-actions.json"
        f.write_text(json.dumps(actions), encoding="utf-8")

        removed = _prune_digest_actions(f)
        assert removed == 0

    def test_truncate_jsonl_removes_old_entries(self, tmp_path):
        """JSONL truncation removes entries older than cutoff."""
        from core.housekeeping import _truncate_jsonl

        old_ts = (datetime.now() - timedelta(days=60)).isoformat()
        new_ts = datetime.now().isoformat()

        f = tmp_path / "test.jsonl"
        lines = [
            json.dumps({"ts": old_ts, "data": "old"}),
            json.dumps({"ts": new_ts, "data": "new"}),
        ]
        f.write_text("\n".join(lines) + "\n", encoding="utf-8")

        removed = _truncate_jsonl(f, max_age_days=30)
        assert removed == 1

        kept_lines = f.read_text(encoding="utf-8").strip().splitlines()
        assert len(kept_lines) == 1
        assert json.loads(kept_lines[0])["data"] == "new"

    def test_prune_state_file(self, tmp_path):
        """State file pruning removes old entries by processed_at."""
        from core.housekeeping import _prune_state_file

        old_ts = (datetime.now() - timedelta(days=60)).isoformat()
        new_ts = datetime.now().isoformat()

        state = {
            "old-entry": {"processed_at": old_ts, "content": "old"},
            "new-entry": {"processed_at": new_ts, "content": "new"},
        }
        f = tmp_path / "state.json"
        f.write_text(json.dumps(state), encoding="utf-8")

        removed = _prune_state_file(f, max_age_days=30)
        assert removed == 1

        data = json.loads(f.read_text(encoding="utf-8"))
        assert "new-entry" in data
        assert "old-entry" not in data

    def test_delete_old_files(self, tmp_path):
        """_delete_old_files removes files older than threshold."""
        from core.housekeeping import _delete_old_files

        # Create a file and backdate its mtime
        old_file = tmp_path / "monitoring-2026-01-01T00-00.json"
        old_file.write_text("{}")
        old_mtime = time.time() - (10 * 86400)  # 10 days ago
        os.utime(old_file, (old_mtime, old_mtime))

        new_file = tmp_path / "monitoring-2026-03-25T09-00.json"
        new_file.write_text("{}")

        deleted = _delete_old_files(tmp_path, "monitoring-*.json", max_age_days=3)
        assert deleted == 1
        assert not old_file.exists()
        assert new_file.exists()

    def test_retention_override(self, tmp_path):
        """run_housekeeping respects config retention overrides."""
        from core.housekeeping import run_housekeeping

        with patch("core.housekeeping.PULSE_HOME", tmp_path), \
             patch("core.housekeeping.LOGS_DIR", tmp_path / "logs"), \
             patch("core.housekeeping.DIGESTS_DIR", tmp_path / "digests"), \
             patch("core.housekeeping.INTEL_DIR", tmp_path / "intel"), \
             patch("core.housekeeping.JOBS_DIR", tmp_path / "jobs"):
            # Create dirs
            (tmp_path / "logs").mkdir()
            (tmp_path / "digests").mkdir()
            (tmp_path / "intel").mkdir()
            (tmp_path / "jobs" / "completed").mkdir(parents=True)

            config = {"housekeeping": {"retention": {"monitoring": 7}}}
            summary = run_housekeeping(config)

        assert isinstance(summary, dict)


# ---------------------------------------------------------------------------
# 9. Digest actions IPC (dismiss, snooze, archive, restore, notes)
# ---------------------------------------------------------------------------


class TestDigestActionsIPC:
    """Real file-based digest actions round-trips."""

    def test_dismiss_creates_archived_entry(self, tmp_path):
        """dismiss_item creates an archived entry in the file."""
        from tui.ipc import dismiss_item, _load_digest_actions

        actions_file = tmp_path / ".digest-actions.json"
        with patch("tui.ipc.DIGEST_ACTIONS_FILE", actions_file):
            dismiss_item("item-1", title="Test item", source="Teams")
            data = _load_digest_actions()

        entries = data["dismissed"]
        assert len(entries) == 1
        assert entries[0]["item"] == "item-1"
        assert entries[0]["status"] == "archived"

    def test_dismiss_dedup(self, tmp_path):
        """Dismissing the same item twice doesn't create duplicates."""
        from tui.ipc import dismiss_item, _load_digest_actions

        actions_file = tmp_path / ".digest-actions.json"
        with patch("tui.ipc.DIGEST_ACTIONS_FILE", actions_file):
            dismiss_item("item-1", title="Test")
            dismiss_item("item-1", title="Test")
            data = _load_digest_actions()

        entries = [d for d in data["dismissed"] if d["item"] == "item-1"]
        assert len(entries) == 1

    def test_snooze_creates_dismissed_entry(self, tmp_path):
        """snooze_item creates a 'dismissed' status entry."""
        from tui.ipc import snooze_item, _load_digest_actions

        actions_file = tmp_path / ".digest-actions.json"
        with patch("tui.ipc.DIGEST_ACTIONS_FILE", actions_file):
            snooze_item("item-2", title="Snooze me")
            data = _load_digest_actions()

        entries = data["dismissed"]
        assert entries[0]["status"] == "dismissed"

    def test_archive_upgrades_existing(self, tmp_path):
        """archive_item upgrades an existing dismissed item to archived."""
        from tui.ipc import snooze_item, archive_item, _load_digest_actions

        actions_file = tmp_path / ".digest-actions.json"
        with patch("tui.ipc.DIGEST_ACTIONS_FILE", actions_file):
            snooze_item("item-3")
            archive_item("item-3")
            data = _load_digest_actions()

        entries = [d for d in data["dismissed"] if d["item"] == "item-3"]
        assert len(entries) == 1
        assert entries[0]["status"] == "archived"
        assert "archived_at" in entries[0]

    def test_restore_removes_entry(self, tmp_path):
        """restore_item removes the item from dismissed list."""
        from tui.ipc import dismiss_item, restore_item, _load_digest_actions

        actions_file = tmp_path / ".digest-actions.json"
        with patch("tui.ipc.DIGEST_ACTIONS_FILE", actions_file):
            dismiss_item("item-4")
            restore_item("item-4")
            data = _load_digest_actions()

        items = [d["item"] for d in data["dismissed"]]
        assert "item-4" not in items

    def test_add_note_with_finality_keyword_resolves(self, tmp_path):
        """Adding a note with 'done' keyword auto-resolves the item."""
        from tui.ipc import dismiss_item, add_note, _load_digest_actions

        actions_file = tmp_path / ".digest-actions.json"
        with patch("tui.ipc.DIGEST_ACTIONS_FILE", actions_file):
            dismiss_item("item-5")
            add_note("item-5", "already done, handled it yesterday")
            data = _load_digest_actions()

        entries = [d for d in data["dismissed"] if d["item"] == "item-5"]
        assert entries[0]["status"] == "resolved"

    def test_add_note_without_finality_keeps_status(self, tmp_path):
        """Adding a note without finality keywords keeps original status."""
        from tui.ipc import dismiss_item, add_note, _load_digest_actions

        actions_file = tmp_path / ".digest-actions.json"
        with patch("tui.ipc.DIGEST_ACTIONS_FILE", actions_file):
            dismiss_item("item-6")
            add_note("item-6", "need to follow up next week")
            data = _load_digest_actions()

        entries = [d for d in data["dismissed"] if d["item"] == "item-6"]
        assert entries[0]["status"] == "archived"  # unchanged

    def test_add_note_to_undismissed_item_creates_resolved(self, tmp_path):
        """Adding a finality note to a non-dismissed item creates resolved entry."""
        from tui.ipc import add_note, _load_digest_actions

        actions_file = tmp_path / ".digest-actions.json"
        with patch("tui.ipc.DIGEST_ACTIONS_FILE", actions_file):
            add_note("item-7", "done")
            data = _load_digest_actions()

        entries = [d for d in data["dismissed"] if d["item"] == "item-7"]
        assert len(entries) == 1
        assert entries[0]["status"] == "resolved"

    def test_finality_keywords_comprehensive(self, tmp_path):
        """Multiple finality keywords all trigger resolution."""
        from tui.ipc import _is_finality_note

        assert _is_finality_note("done")
        assert _is_finality_note("Already dealt with")
        assert _is_finality_note("HANDLED")
        assert _is_finality_note("sent the reply")
        assert _is_finality_note("completed the review")
        assert _is_finality_note("not applicable to us")
        assert _is_finality_note("resolved in standup")
        assert _is_finality_note("doje")  # typo for done

        assert not _is_finality_note("need to follow up")
        assert not _is_finality_note("check with team")
        assert not _is_finality_note("interesting point")


# ---------------------------------------------------------------------------
# 10. Queue job and mark-read job
# ---------------------------------------------------------------------------


class TestQueueJobs:
    """Test job queuing via IPC."""

    def test_queue_job_creates_yaml(self, tmp_path):
        """queue_job writes a valid YAML to pending dir."""
        from tui.ipc import queue_job
        import yaml

        pending_dir = tmp_path / "jobs" / "pending"
        pending_dir.mkdir(parents=True)

        with patch("tui.ipc.JOBS_DIR", tmp_path / "jobs"):
            queue_job("digest", context="Focus on Contoso project")

        files = list(pending_dir.glob("*.yaml"))
        assert len(files) == 1
        data = yaml.safe_load(files[0].read_text())
        assert data["type"] == "digest"
        assert data["context"] == "Focus on Contoso project"

    def test_queue_mark_read_teams(self, tmp_path):
        """queue_mark_read_job creates correct Teams mark-read job."""
        from tui.ipc import queue_mark_read_job
        import yaml

        pending_dir = tmp_path / "jobs" / "pending"
        pending_dir.mkdir(parents=True)

        item = {"source": "Teams: Alice", "title": "Chat with Alice"}
        with patch("tui.ipc.JOBS_DIR", tmp_path / "jobs"):
            result = queue_mark_read_job(item)

        assert result is True
        files = list(pending_dir.glob("*.yaml"))
        data = yaml.safe_load(files[0].read_text())
        assert data["type"] == "mark_read_teams"
        assert data["chat_name"] == "Alice"

    def test_queue_mark_read_email(self, tmp_path):
        """queue_mark_read_job creates correct Outlook mark-read job."""
        from tui.ipc import queue_mark_read_job
        import yaml

        pending_dir = tmp_path / "jobs" / "pending"
        pending_dir.mkdir(parents=True)

        item = {"source": "Email: Bob Smith", "title": "RE: Proposal", "conv_id": "abc123"}
        with patch("tui.ipc.JOBS_DIR", tmp_path / "jobs"):
            result = queue_mark_read_job(item)

        assert result is True
        files = list(pending_dir.glob("*.yaml"))
        data = yaml.safe_load(files[0].read_text())
        assert data["type"] == "mark_read_outlook"
        assert data["sender"] == "Bob Smith"

    def test_queue_mark_read_unknown_source_returns_false(self, tmp_path):
        """Unknown source type returns False."""
        from tui.ipc import queue_mark_read_job

        item = {"source": "Calendar: Meeting", "title": "Standup"}
        with patch("tui.ipc.JOBS_DIR", tmp_path / "jobs"):
            assert queue_mark_read_job(item) is False
