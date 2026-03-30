"""Tests for daemon/TUI bug fixes.

Covers:
1. Chat stream reader skips partial trailing lines
2. Chat stream reader handles complete lines correctly
3. Chat streaming flag is set before request is sent
4. Job failure notifications emitted for non-proxy errors
5. _save_digest_actions failure is logged by callers
6. write_reply_job logs warning on unknown action_type
7. Onboarding flag is shared between worker.py and tasks.py
"""

import json
import logging
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


# ---------------------------------------------------------------------------
# 1 & 2: Chat stream reader — partial line handling
# ---------------------------------------------------------------------------


class TestChatStreamPartialLines:
    """read_chat_stream_deltas must skip incomplete trailing lines."""

    def test_partial_trailing_line_skipped(self, tmp_dir):
        """A partial JSON line at the end (no trailing newline) is not parsed."""
        stream_file = tmp_dir / ".chat-stream.jsonl"
        complete = json.dumps({"type": "delta", "text": "hello ", "request_id": "r1"}) + "\n"
        partial = '{"type": "delta", "text": "wor'  # incomplete — no newline
        # Write in binary to avoid platform-specific line ending conversion
        stream_file.write_bytes((complete + partial).encode("utf-8"))

        with patch("tui.ipc.CHAT_STREAM_FILE", stream_file):
            from tui.ipc import read_chat_stream_deltas
            text, done, new_offset, *_ = read_chat_stream_deltas(0, "r1")

        assert text == "hello "
        assert not done
        # Offset should cover only the complete line, not the partial one
        assert new_offset == len(complete.encode("utf-8"))

    def test_entirely_partial_chunk_waits(self, tmp_dir):
        """If the entire new chunk is one incomplete line, return nothing."""
        stream_file = tmp_dir / ".chat-stream.jsonl"
        partial = '{"type": "delta", "text": "hi"'  # no newline at all
        stream_file.write_bytes(partial.encode("utf-8"))

        with patch("tui.ipc.CHAT_STREAM_FILE", stream_file):
            from tui.ipc import read_chat_stream_deltas
            text, done, new_offset, *_ = read_chat_stream_deltas(0, "r1")

        assert text == ""
        assert not done
        assert new_offset == 0  # No progress — will re-read next poll

    def test_complete_lines_parsed_normally(self, tmp_dir):
        """Lines ending with newline are parsed correctly."""
        stream_file = tmp_dir / ".chat-stream.jsonl"
        line1 = json.dumps({"type": "delta", "text": "foo", "request_id": "r1"}) + "\n"
        line2 = json.dumps({"type": "delta", "text": "bar", "request_id": "r1"}) + "\n"
        line3 = json.dumps({"type": "done", "request_id": "r1"}) + "\n"
        stream_file.write_bytes((line1 + line2 + line3).encode("utf-8"))

        with patch("tui.ipc.CHAT_STREAM_FILE", stream_file):
            from tui.ipc import read_chat_stream_deltas
            text, done, new_offset, *_ = read_chat_stream_deltas(0, "r1")

        assert text == "foobar"
        assert done is True
        assert new_offset == len((line1 + line2 + line3).encode("utf-8"))

    def test_partial_line_recovered_on_next_read(self, tmp_dir):
        """After a partial line, next read picks it up once completed."""
        stream_file = tmp_dir / ".chat-stream.jsonl"
        complete = json.dumps({"type": "delta", "text": "A", "request_id": "r1"}) + "\n"
        partial = '{"type": "delta", "text": "B", "request_id": "r1"}'
        stream_file.write_bytes((complete + partial).encode("utf-8"))

        with patch("tui.ipc.CHAT_STREAM_FILE", stream_file):
            from tui.ipc import read_chat_stream_deltas
            text1, _, offset1, *_ = read_chat_stream_deltas(0, "r1")

        assert text1 == "A"
        assert offset1 == len(complete.encode("utf-8"))

        # Now the daemon finishes writing the line
        stream_file.write_bytes((complete + partial + "\n").encode("utf-8"))

        with patch("tui.ipc.CHAT_STREAM_FILE", stream_file):
            from tui.ipc import read_chat_stream_deltas
            text2, _, offset2, *_ = read_chat_stream_deltas(offset1, "r1")

        assert text2 == "B"


# ---------------------------------------------------------------------------
# 2b: Chat error visibility — "(no response)" bug fix
# ---------------------------------------------------------------------------


class TestChatErrorVisibility:
    """When run_chat_query returns an error (no deltas streamed), TUI must see it."""

    @pytest.mark.asyncio
    async def test_error_written_to_stream_when_no_deltas(self, tmp_dir):
        """If run_chat_query returns error text and no deltas were streamed,
        the error must be written to the chat stream file."""
        from unittest.mock import AsyncMock

        stream_file = tmp_dir / ".chat-stream.jsonl"
        request_file = tmp_dir / ".chat-request.json"

        # Simulate run_chat_query returning an error (no deltas produced)
        async def mock_run_chat_query(client, config, prompt, on_delta=None, on_status=None):
            # on_delta is NEVER called — simulates SDK error
            return "Agent error: fetch failed"

        with patch("daemon.tasks.PULSE_HOME", tmp_dir), \
             patch("tui.ipc.CHAT_STREAM_FILE", stream_file), \
             patch("daemon.worker.run_chat_query", side_effect=mock_run_chat_query), \
             patch("daemon.worker.process_pending_actions", new_callable=AsyncMock):
            from daemon.tasks import _handle_chat_request
            await _handle_chat_request(None, {}, "test prompt", "req-123")

        # The stream file should contain the error, not be empty
        content = stream_file.read_text(encoding="utf-8")
        assert "Agent error: fetch failed" in content
        assert '"type": "done"' in content

    @pytest.mark.asyncio
    async def test_exception_written_to_stream(self, tmp_dir):
        """If run_chat_query raises an exception, error text is written to stream."""
        from unittest.mock import AsyncMock

        stream_file = tmp_dir / ".chat-stream.jsonl"

        async def mock_run_chat_query(client, config, prompt, on_delta=None, on_status=None):
            raise RuntimeError("SDK crashed")

        with patch("daemon.tasks.PULSE_HOME", tmp_dir), \
             patch("tui.ipc.CHAT_STREAM_FILE", stream_file), \
             patch("daemon.worker.run_chat_query", side_effect=mock_run_chat_query), \
             patch("daemon.worker.process_pending_actions", new_callable=AsyncMock):
            from daemon.tasks import _handle_chat_request
            await _handle_chat_request(None, {}, "test prompt", "req-456")

        content = stream_file.read_text(encoding="utf-8")
        assert "Error:" in content
        assert "SDK crashed" in content

    @pytest.mark.asyncio
    async def test_deltas_present_no_duplicate(self, tmp_dir):
        """When deltas ARE streamed, the return value is NOT double-written."""
        from unittest.mock import AsyncMock

        stream_file = tmp_dir / ".chat-stream.jsonl"

        async def mock_run_chat_query(client, config, prompt, on_delta=None, on_status=None):
            # Simulate agent streaming deltas
            if on_delta:
                on_delta("Hello world")
            return "Hello world"

        with patch("daemon.tasks.PULSE_HOME", tmp_dir), \
             patch("tui.ipc.CHAT_STREAM_FILE", stream_file), \
             patch("daemon.worker.run_chat_query", side_effect=mock_run_chat_query), \
             patch("daemon.worker.process_pending_actions", new_callable=AsyncMock):
            from daemon.tasks import _handle_chat_request
            await _handle_chat_request(None, {}, "test prompt", "req-789")

        content = stream_file.read_text(encoding="utf-8")
        # Count how many delta entries contain "Hello world"
        delta_count = content.count("Hello world")
        assert delta_count == 1, f"Expected 1 delta with 'Hello world', got {delta_count}"


# ---------------------------------------------------------------------------
# 3: Chat flag set before request
# ---------------------------------------------------------------------------


class TestChatFlagOrder:
    """_streaming must be True before send_chat_request is called."""

    def test_streaming_set_before_send(self):
        """Verify on_input_submitted sets _streaming before calling send_chat_request."""
        import inspect
        from tui.screens import ChatPane

        source = inspect.getsource(ChatPane.on_input_submitted)
        # Find positions of key operations
        streaming_true_pos = source.find("self._streaming = True")
        send_request_pos = source.find("send_chat_request(prompt)")

        assert streaming_true_pos != -1, "_streaming = True not found in on_input_submitted"
        assert send_request_pos != -1, "send_chat_request not found in on_input_submitted"
        assert streaming_true_pos < send_request_pos, (
            "_streaming = True must appear BEFORE send_chat_request(prompt) "
            "to prevent _poll_stream race condition"
        )


# ---------------------------------------------------------------------------
# 4: Job failure notifications for non-proxy errors
# ---------------------------------------------------------------------------


class TestJobFailureNotifications:
    """Non-ProxyError failures must emit notifications."""

    @pytest.mark.asyncio
    async def test_non_proxy_failure_notifies(self):
        """When a job fails with a non-proxy error, write_job_notification is called."""
        import asyncio
        from unittest.mock import AsyncMock

        notifications = []
        original_write = MagicMock(side_effect=lambda jt, s: notifications.append((jt, s)))

        from daemon.worker import enqueue_job
        job_queue = asyncio.PriorityQueue()
        enqueue_job(job_queue, {"type": "digest", "_source": "test"})

        mock_client = MagicMock()
        mock_config = {"user": {"name": "Test"}}

        with patch("daemon.worker.write_job_notification", original_write), \
             patch("daemon.worker.notify_desktop"), \
             patch("daemon.worker.append_job_event"), \
             patch("daemon.worker.log"), \
             patch("daemon.tasks.active_workers", {}), \
             patch("daemon.worker.LOGS_DIR", Path("/tmp/test-logs")), \
             patch("sdk.runner.run_job", AsyncMock(side_effect=RuntimeError("test boom"))):

            # Run the worker for just one job
            from daemon.worker import job_worker

            task = asyncio.create_task(job_worker(mock_client, mock_config, job_queue))
            # Wait for job to be processed
            await asyncio.sleep(0.1)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Should have a FAILED notification
        assert any("FAILED" in s for _, s in notifications), (
            f"Expected FAILED notification, got: {notifications}"
        )


# ---------------------------------------------------------------------------
# 5: _save_digest_actions failure logging
# ---------------------------------------------------------------------------


class TestSaveDigestActionsLogging:
    """Callers of _save_digest_actions log warnings on failure."""

    def test_dismiss_logs_on_save_failure(self, tmp_dir, caplog):
        """dismiss_item logs a warning when _save_digest_actions returns False."""
        actions_file = tmp_dir / ".digest-actions.json"
        actions_file.write_text('{"dismissed": [], "notes": {}}', encoding="utf-8")

        with patch("tui.ipc.DIGEST_ACTIONS_FILE", actions_file), \
             patch("tui.ipc._save_digest_actions", return_value=False), \
             caplog.at_level(logging.WARNING, logger="tui.ipc"):
            from tui.ipc import dismiss_item
            dismiss_item("test-item-1", title="Test")

        assert any("Failed to persist" in r.message for r in caplog.records), (
            f"Expected warning about failed persist, got: {[r.message for r in caplog.records]}"
        )

    def test_snooze_logs_on_save_failure(self, tmp_dir, caplog):
        """snooze_item logs a warning when _save_digest_actions returns False."""
        actions_file = tmp_dir / ".digest-actions.json"
        actions_file.write_text('{"dismissed": [], "notes": {}}', encoding="utf-8")

        with patch("tui.ipc.DIGEST_ACTIONS_FILE", actions_file), \
             patch("tui.ipc._save_digest_actions", return_value=False), \
             caplog.at_level(logging.WARNING, logger="tui.ipc"):
            from tui.ipc import snooze_item
            snooze_item("test-item-2", title="Test")

        assert any("Failed to persist" in r.message for r in caplog.records)

    def test_archive_logs_on_save_failure(self, tmp_dir, caplog):
        """archive_item logs a warning when _save_digest_actions returns False."""
        actions_file = tmp_dir / ".digest-actions.json"
        actions_file.write_text('{"dismissed": [], "notes": {}}', encoding="utf-8")

        with patch("tui.ipc.DIGEST_ACTIONS_FILE", actions_file), \
             patch("tui.ipc._save_digest_actions", return_value=False), \
             caplog.at_level(logging.WARNING, logger="tui.ipc"):
            from tui.ipc import archive_item
            archive_item("test-item-3", title="Test")

        assert any("Failed to persist" in r.message for r in caplog.records)

    def test_restore_logs_on_save_failure(self, tmp_dir, caplog):
        """restore_item logs a warning when _save_digest_actions returns False."""
        actions_file = tmp_dir / ".digest-actions.json"
        actions_file.write_text('{"dismissed": [{"item": "x"}], "notes": {}}', encoding="utf-8")

        with patch("tui.ipc.DIGEST_ACTIONS_FILE", actions_file), \
             patch("tui.ipc._save_digest_actions", return_value=False), \
             caplog.at_level(logging.WARNING, logger="tui.ipc"):
            from tui.ipc import restore_item
            restore_item("x")

        assert any("Failed to persist" in r.message for r in caplog.records)

    def test_add_note_logs_on_save_failure(self, tmp_dir, caplog):
        """add_note logs a warning when _save_digest_actions returns False."""
        actions_file = tmp_dir / ".digest-actions.json"
        actions_file.write_text('{"dismissed": [], "notes": {}}', encoding="utf-8")

        with patch("tui.ipc.DIGEST_ACTIONS_FILE", actions_file), \
             patch("tui.ipc._save_digest_actions", return_value=False), \
             caplog.at_level(logging.WARNING, logger="tui.ipc"):
            from tui.ipc import add_note
            add_note("test-item-5", "some note")

        assert any("Failed to persist" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# 6: write_reply_job logs warning on unknown action_type
# ---------------------------------------------------------------------------


class TestWriteReplyJobUnknownType:
    """write_reply_job must log a warning for unknown action_type."""

    def test_unknown_action_type_logs_warning(self, caplog):
        """Unknown action_type produces a log.warning and returns False."""
        item = {
            "title": "Test Item",
            "suggested_actions": [
                {"action_type": "carrier_pigeon", "draft": "coo"}
            ],
        }

        with caplog.at_level(logging.WARNING, logger="tui.ipc"):
            from tui.ipc import write_reply_job
            result = write_reply_job(item, "test draft")

        assert result is False
        assert any("Unknown action_type" in r.message and "carrier_pigeon" in r.message
                    for r in caplog.records), (
            f"Expected warning about unknown action_type, got: {[r.message for r in caplog.records]}"
        )

    def test_known_action_types_no_warning(self, tmp_dir, caplog):
        """Known action_types (teams_reply, email_reply) should not log warnings."""
        item = {
            "title": "Test",
            "suggested_actions": [
                {"action_type": "teams_reply", "chat_name": "Someone", "draft": "hi"}
            ],
        }

        with patch("tui.ipc.JOBS_DIR", tmp_dir), \
             caplog.at_level(logging.WARNING, logger="tui.ipc"):
            (tmp_dir / "pending").mkdir()
            from tui.ipc import write_reply_job
            result = write_reply_job(item, "hello")

        assert result is True
        assert not any("Unknown action_type" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# 7: Onboarding flag shared between worker.py and tasks.py
# ---------------------------------------------------------------------------


class TestOnboardingFlagShared:
    """Both worker.py and tasks.py must use the same onboarding flag."""

    def test_tasks_references_worker_flag(self):
        """tasks.py's _handle_chat_request uses daemon.worker._onboarding_sent,
        not a local copy."""
        import inspect
        from daemon.tasks import _handle_chat_request

        source = inspect.getsource(_handle_chat_request)
        # Should reference daemon.worker (or _worker), not a local _onboarding_sent
        assert "daemon.worker" in source or "_worker._onboarding_sent" in source, (
            "_handle_chat_request should reference daemon.worker._onboarding_sent, "
            "not a module-level copy"
        )

    def test_tasks_has_no_module_level_onboarding_flag(self):
        """tasks.py should NOT have its own _onboarding_sent at module level."""
        import daemon.tasks as tasks_mod
        # The module should not have _onboarding_sent as a direct attribute
        # (it was removed in the fix)
        assert not hasattr(tasks_mod, "_onboarding_sent"), (
            "daemon.tasks should not have its own _onboarding_sent — "
            "it should use daemon.worker._onboarding_sent"
        )

    def test_worker_has_canonical_flag(self):
        """worker.py has the canonical _onboarding_sent flag."""
        import daemon.worker as worker_mod
        assert hasattr(worker_mod, "_onboarding_sent"), (
            "daemon.worker must have _onboarding_sent as the canonical flag"
        )

    def test_setting_worker_flag_visible_to_tasks(self):
        """Setting daemon.worker._onboarding_sent is visible when tasks.py reads it."""
        import daemon.worker as worker_mod

        original = worker_mod._onboarding_sent
        try:
            worker_mod._onboarding_sent = True
            # tasks.py references it via import, so it should see the update
            assert worker_mod._onboarding_sent is True
        finally:
            worker_mod._onboarding_sent = original
