"""Tests for TTL/staleness logic and file-based IPC correctness.

These test the real date math, carry-forward expiry, dismissed item TTL,
and the chat streaming IPC protocol — all with real file I/O, no mocks
of the logic under test.
"""

import json
import time
import threading
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Carry-forward staleness (digest pipeline)
# ---------------------------------------------------------------------------


class TestCarryForwardStaleness:
    """_build_carry_forward drops items older than MAX_CARRY_FORWARD_DAYS."""

    def test_fresh_items_kept(self):
        """Items within the staleness window are kept."""
        from sdk.runner import _build_carry_forward

        today = datetime.now().strftime("%Y-%m-%d")
        prev = {
            "items": [
                {"id": "fresh-1", "title": "Recent item", "date": today},
                {"id": "fresh-2", "title": "Yesterday item",
                 "date": (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")},
            ]
        }
        result = _build_carry_forward(prev)
        assert "fresh-1" in result
        assert "fresh-2" in result

    def test_stale_items_dropped(self):
        """Items older than MAX_CARRY_FORWARD_DAYS are dropped."""
        from sdk.runner import _build_carry_forward, MAX_CARRY_FORWARD_DAYS

        old_date = (datetime.now() - timedelta(days=MAX_CARRY_FORWARD_DAYS + 1)).strftime("%Y-%m-%d")
        prev = {
            "items": [
                {"id": "stale-1", "title": "Old item", "date": old_date},
            ]
        }
        result = _build_carry_forward(prev)
        assert "stale-1" not in result
        assert "Auto-dropped" in result

    def test_exactly_at_boundary(self):
        """Items exactly at MAX_CARRY_FORWARD_DAYS are dropped (>= boundary)."""
        from sdk.runner import _build_carry_forward, MAX_CARRY_FORWARD_DAYS

        boundary_date = (datetime.now() - timedelta(days=MAX_CARRY_FORWARD_DAYS)).strftime("%Y-%m-%d")
        prev = {
            "items": [
                {"id": "boundary", "title": "Boundary item", "date": boundary_date},
            ]
        }
        result = _build_carry_forward(prev)
        assert "Boundary item" not in result
        assert "Auto-dropped" in result

    def test_one_day_past_boundary_dropped(self):
        """Items one day past boundary are dropped."""
        from sdk.runner import _build_carry_forward, MAX_CARRY_FORWARD_DAYS

        past = (datetime.now() - timedelta(days=MAX_CARRY_FORWARD_DAYS + 1)).strftime("%Y-%m-%d")
        prev = {"items": [{"id": "gone", "title": "Gone", "date": past}]}
        result = _build_carry_forward(prev)
        assert "gone" not in result

    def test_mixed_fresh_and_stale(self):
        """Fresh items kept, stale dropped, with correct count."""
        from sdk.runner import _build_carry_forward, MAX_CARRY_FORWARD_DAYS

        today = datetime.now().strftime("%Y-%m-%d")
        old = (datetime.now() - timedelta(days=MAX_CARRY_FORWARD_DAYS + 5)).strftime("%Y-%m-%d")

        prev = {
            "items": [
                {"id": "keep-1", "title": "Keep me", "date": today},
                {"id": "drop-1", "title": "Drop me", "date": old},
                {"id": "drop-2", "title": "Drop me too", "date": old},
            ]
        }
        result = _build_carry_forward(prev)
        assert "keep-1" in result
        assert "drop-1" not in result
        assert "drop-2" not in result
        assert "Auto-dropped 2 items" in result

    def test_no_date_field_kept(self):
        """Items with no date field are kept (age defaults to 0)."""
        from sdk.runner import _build_carry_forward

        prev = {"items": [{"id": "no-date", "title": "No date"}]}
        result = _build_carry_forward(prev)
        assert "no-date" in result

    def test_invalid_date_kept(self):
        """Items with unparseable date are kept."""
        from sdk.runner import _build_carry_forward

        prev = {"items": [{"id": "bad-date", "title": "Bad date", "date": "not-a-date"}]}
        result = _build_carry_forward(prev)
        assert "bad-date" in result

    def test_none_input(self):
        """None input returns empty string."""
        from sdk.runner import _build_carry_forward

        assert _build_carry_forward(None) == ""

    def test_empty_items(self):
        """Empty items list returns empty string."""
        from sdk.runner import _build_carry_forward

        assert _build_carry_forward({"items": []}) == ""


# ---------------------------------------------------------------------------
# Dismissed item TTL (dual: snooze=1 day, archive=30 days)
# ---------------------------------------------------------------------------


class TestDismissedTTL:
    """_build_dismissed_block correctly expires snoozed and archived items."""

    def _make_dismissed_actions(self, entries: list[dict]) -> dict:
        return {"dismissed": entries, "notes": {}}

    def test_fresh_snooze_included(self, tmp_dir):
        """Snoozed item from today is included."""
        from sdk.runner import _build_dismissed_block
        from tui.ipc import DIGEST_ACTIONS_FILE

        actions = self._make_dismissed_actions([{
            "item": "snooze-1",
            "status": "dismissed",
            "title": "Fresh snooze",
            "dismissed_at": datetime.now().isoformat(),
        }])
        actions_file = tmp_dir / ".digest-actions.json"
        actions_file.write_text(json.dumps(actions), encoding="utf-8")

        with patch("sdk.runner.load_actions", return_value=actions):
            result = _build_dismissed_block()

        assert "snooze-1" in result
        assert "Snoozed today" in result

    def test_expired_snooze_excluded(self, tmp_dir):
        """Snoozed item from 2 days ago is excluded (1-day TTL)."""
        from sdk.runner import _build_dismissed_block

        actions = self._make_dismissed_actions([{
            "item": "old-snooze",
            "status": "dismissed",
            "title": "Old snooze",
            "dismissed_at": (datetime.now() - timedelta(days=2)).isoformat(),
        }])

        with patch("sdk.runner.load_actions", return_value=actions):
            result = _build_dismissed_block()

        assert "old-snooze" not in result

    def test_fresh_archive_included(self, tmp_dir):
        """Archived item from 10 days ago is included (30-day TTL)."""
        from sdk.runner import _build_dismissed_block

        actions = self._make_dismissed_actions([{
            "item": "archive-1",
            "status": "archived",
            "title": "Recent archive",
            "dismissed_at": (datetime.now() - timedelta(days=10)).isoformat(),
        }])

        with patch("sdk.runner.load_actions", return_value=actions):
            result = _build_dismissed_block()

        assert "archive-1" in result
        assert "Archived" in result

    def test_expired_archive_excluded(self, tmp_dir):
        """Archived item from 31 days ago is excluded (30-day TTL)."""
        from sdk.runner import _build_dismissed_block

        actions = self._make_dismissed_actions([{
            "item": "old-archive",
            "status": "archived",
            "title": "Old archive",
            "dismissed_at": (datetime.now() - timedelta(days=31)).isoformat(),
        }])

        with patch("sdk.runner.load_actions", return_value=actions):
            result = _build_dismissed_block()

        assert "old-archive" not in result

    def test_legacy_entry_treated_as_archive(self, tmp_dir):
        """Entry without status field is treated as archived (backward compat)."""
        from sdk.runner import _build_dismissed_block

        actions = self._make_dismissed_actions([{
            "item": "legacy-1",
            "title": "Legacy",
            "dismissed_at": (datetime.now() - timedelta(days=5)).isoformat(),
        }])

        with patch("sdk.runner.load_actions", return_value=actions):
            result = _build_dismissed_block()

        assert "legacy-1" in result  # Within 30-day window

    def test_mixed_ttl(self, tmp_dir):
        """Mix of fresh/expired snoozes and archives."""
        from sdk.runner import _build_dismissed_block

        now = datetime.now()
        actions = self._make_dismissed_actions([
            {"item": "fresh-snooze", "status": "dismissed",
             "title": "FS", "dismissed_at": now.isoformat()},
            {"item": "expired-snooze", "status": "dismissed",
             "title": "ES", "dismissed_at": (now - timedelta(days=2)).isoformat()},
            {"item": "fresh-archive", "status": "archived",
             "title": "FA", "dismissed_at": (now - timedelta(days=15)).isoformat()},
            {"item": "expired-archive", "status": "archived",
             "title": "EA", "dismissed_at": (now - timedelta(days=35)).isoformat()},
        ])

        with patch("sdk.runner.load_actions", return_value=actions):
            result = _build_dismissed_block()

        assert "fresh-snooze" in result
        assert "expired-snooze" not in result
        assert "fresh-archive" in result
        assert "expired-archive" not in result

    def test_empty_dismissed_returns_empty(self, tmp_dir):
        """No dismissed items returns empty string."""
        from sdk.runner import _build_dismissed_block

        with patch("sdk.runner.load_actions", return_value={"dismissed": [], "notes": {}}):
            result = _build_dismissed_block()

        assert result == ""

    def test_invalid_datetime_handled(self, tmp_dir):
        """Invalid dismissed_at doesn't crash."""
        from sdk.runner import _build_dismissed_block

        actions = self._make_dismissed_actions([{
            "item": "bad-date",
            "status": "dismissed",
            "title": "Bad date",
            "dismissed_at": "not-a-date",
        }])

        with patch("sdk.runner.load_actions", return_value=actions):
            result = _build_dismissed_block()

        # Should not crash — item treated as age=0 (fresh)
        assert "bad-date" in result


# ---------------------------------------------------------------------------
# Chat IPC: streaming deltas, request/response protocol
# ---------------------------------------------------------------------------


class TestChatIPC:
    """File-based IPC between daemon (writer) and TUI (reader)."""

    def test_write_then_read_deltas(self, tmp_dir):
        """Basic write → read cycle works."""
        from tui.ipc import write_chat_delta, read_chat_stream_deltas, clear_chat_stream

        stream_file = tmp_dir / ".chat-stream.jsonl"
        with patch("tui.ipc.CHAT_STREAM_FILE", stream_file):
            clear_chat_stream()
            write_chat_delta("Hello ", "req-1")
            write_chat_delta("world!", "req-1")

            text, done, offset, *_ = read_chat_stream_deltas(0, "req-1")

        assert text == "Hello world!"
        assert done is False
        assert offset > 0

    def test_done_marker_detected(self, tmp_dir):
        """'done' marker signals completion."""
        from tui.ipc import write_chat_delta, finish_chat_stream, read_chat_stream_deltas, clear_chat_stream

        stream_file = tmp_dir / ".chat-stream.jsonl"
        with patch("tui.ipc.CHAT_STREAM_FILE", stream_file):
            clear_chat_stream()
            write_chat_delta("Result text", "req-1")
            finish_chat_stream("req-1")

            text, done, _, *_ = read_chat_stream_deltas(0, "req-1")

        assert text == "Result text"
        assert done is True

    def test_incremental_reading(self, tmp_dir):
        """Reads only new content after offset."""
        from tui.ipc import write_chat_delta, read_chat_stream_deltas, clear_chat_stream

        stream_file = tmp_dir / ".chat-stream.jsonl"
        with patch("tui.ipc.CHAT_STREAM_FILE", stream_file):
            clear_chat_stream()
            write_chat_delta("First ", "req-1")

            text1, _, offset1, *_ = read_chat_stream_deltas(0, "req-1")
            assert text1 == "First "

            write_chat_delta("Second", "req-1")
            text2, _, offset2, *_ = read_chat_stream_deltas(offset1, "req-1")
            assert text2 == "Second"
            assert offset2 > offset1

    def test_request_id_filtering(self, tmp_dir):
        """Deltas from different requests are filtered out."""
        from tui.ipc import write_chat_delta, read_chat_stream_deltas, clear_chat_stream

        stream_file = tmp_dir / ".chat-stream.jsonl"
        with patch("tui.ipc.CHAT_STREAM_FILE", stream_file):
            clear_chat_stream()
            write_chat_delta("Old response", "req-OLD")
            write_chat_delta("New response", "req-NEW")

            # Read only req-NEW
            text, _, _, *_ = read_chat_stream_deltas(0, "req-NEW")

        assert text == "New response"
        assert "Old response" not in text

    def test_clear_stream_resets(self, tmp_dir):
        """clear_chat_stream empties the file."""
        from tui.ipc import write_chat_delta, clear_chat_stream, read_chat_stream_deltas

        stream_file = tmp_dir / ".chat-stream.jsonl"
        with patch("tui.ipc.CHAT_STREAM_FILE", stream_file):
            write_chat_delta("Old data", "req-1")
            clear_chat_stream()

            text, done, offset, *_ = read_chat_stream_deltas(0, "req-1")

        assert text == ""
        assert done is False
        assert offset == 0

    def test_nonexistent_stream_file(self, tmp_dir):
        """Reading from nonexistent file returns empty."""
        from tui.ipc import read_chat_stream_deltas

        stream_file = tmp_dir / "nonexistent.jsonl"
        with patch("tui.ipc.CHAT_STREAM_FILE", stream_file):
            text, done, offset, *_ = read_chat_stream_deltas(0)

        assert text == ""
        assert done is False
        assert offset == 0

    def test_send_chat_request(self, tmp_dir):
        """send_chat_request writes a valid JSON file."""
        from tui.ipc import send_chat_request

        request_file = tmp_dir / ".chat-request.json"
        with patch("tui.ipc.CHAT_REQUEST_FILE", request_file):
            request_id = send_chat_request("What's the status of Vodafone?")

        assert request_file.exists()
        data = json.loads(request_file.read_text(encoding="utf-8"))
        assert data["prompt"] == "What's the status of Vodafone?"
        assert data["request_id"] == request_id
        assert "ts" in data

    def test_concurrent_write_read(self, tmp_dir):
        """Concurrent writes and reads don't corrupt the stream."""
        from tui.ipc import write_chat_delta, read_chat_stream_deltas, clear_chat_stream

        stream_file = tmp_dir / ".chat-stream.jsonl"
        errors = []

        def writer():
            try:
                with patch("tui.ipc.CHAT_STREAM_FILE", stream_file):
                    for i in range(50):
                        write_chat_delta(f"chunk-{i} ", "req-concurrent")
                        time.sleep(0.001)
            except Exception as e:
                errors.append(f"Writer: {e}")

        def reader():
            try:
                offset = 0
                collected = ""
                with patch("tui.ipc.CHAT_STREAM_FILE", stream_file):
                    for _ in range(100):
                        text, _, offset, *_ = read_chat_stream_deltas(offset, "req-concurrent")
                        collected += text
                        time.sleep(0.001)
                    # Verify we got valid data (no corruption)
                    if collected:
                        assert "chunk-" in collected
            except Exception as e:
                errors.append(f"Reader: {e}")

        with patch("tui.ipc.CHAT_STREAM_FILE", stream_file):
            clear_chat_stream()

        t_writer = threading.Thread(target=writer)
        t_reader = threading.Thread(target=reader)
        t_writer.start()
        t_reader.start()
        t_writer.join(timeout=10)
        t_reader.join(timeout=10)

        assert not errors, f"Concurrent IPC errors: {errors}"

    def test_unicode_in_stream(self, tmp_dir):
        """Unicode content survives write→read cycle."""
        from tui.ipc import write_chat_delta, read_chat_stream_deltas, clear_chat_stream

        stream_file = tmp_dir / ".chat-stream.jsonl"
        with patch("tui.ipc.CHAT_STREAM_FILE", stream_file):
            clear_chat_stream()
            write_chat_delta("Meeting with Sch\u00f6nbrunn \u2014 \u2705 done", "req-1")

            text, _, _, *_ = read_chat_stream_deltas(0, "req-1")

        assert "\u00f6" in text  # ö
        assert "\u2014" in text  # —
        assert "\u2705" in text  # ✅

    def test_malformed_jsonl_line_skipped(self, tmp_dir):
        """Malformed JSONL lines are skipped, not crash."""
        from tui.ipc import read_chat_stream_deltas

        stream_file = tmp_dir / ".chat-stream.jsonl"
        stream_file.write_text(
            '{"type":"delta","text":"good","request_id":"r1"}\n'
            'THIS IS NOT JSON\n'
            '{"type":"delta","text":" data","request_id":"r1"}\n',
            encoding="utf-8",
        )

        with patch("tui.ipc.CHAT_STREAM_FILE", stream_file):
            text, _, _, *_ = read_chat_stream_deltas(0, "r1")

        assert text == "good data"


# ---------------------------------------------------------------------------
# Inbox deduplication (triage + digest merge)
# ---------------------------------------------------------------------------


class TestInboxDedup:
    """_load_inbox_items correctly deduplicates triage and digest items."""

    def test_triage_wins_over_digest(self, tmp_dir):
        """When same ID appears in both triage and digest, triage version wins."""
        from tui.screens import _load_inbox_items

        triage = {
            "items": [{"id": "dup-1", "title": "Triage version", "priority": "urgent",
                        "source": "Teams: Alice"}],
        }
        digest = {
            "items": [{"id": "dup-1", "title": "Digest version", "priority": "medium",
                        "source": "Teams: Alice"}],
        }

        (tmp_dir / "monitoring-2026-03-02T09-00.json").write_text(
            json.dumps(triage), encoding="utf-8"
        )
        digests_dir = tmp_dir / "digests"
        digests_dir.mkdir()
        (digests_dir / "2026-03-02.json").write_text(
            json.dumps(digest), encoding="utf-8"
        )

        with patch("tui.screens.PULSE_HOME", tmp_dir), \
             patch("tui.screens.DIGESTS_DIR", digests_dir), \
             patch("tui.ipc.DIGEST_ACTIONS_FILE", tmp_dir / ".digest-actions.json"):
            items, _ = _load_inbox_items()

        # Only one instance of dup-1
        dup_items = [i for i in items if i.get("id") == "dup-1"]
        assert len(dup_items) == 1
        assert dup_items[0]["title"] == "Triage version"

    def test_dismissed_items_excluded(self, tmp_dir):
        """Dismissed items are excluded from active list."""
        from tui.screens import _load_inbox_items
        from tui.ipc import dismiss_item

        triage = {
            "items": [
                {"id": "active-1", "title": "Active", "priority": "high", "source": "Teams"},
                {"id": "dismissed-1", "title": "Dismissed", "priority": "low", "source": "Teams"},
            ],
        }

        (tmp_dir / "monitoring-2026-03-02T09-00.json").write_text(
            json.dumps(triage), encoding="utf-8"
        )

        actions_file = tmp_dir / ".digest-actions.json"
        with patch("tui.ipc.DIGEST_ACTIONS_FILE", actions_file):
            dismiss_item("dismissed-1", title="Dismissed")

        with patch("tui.screens.PULSE_HOME", tmp_dir), \
             patch("tui.screens.DIGESTS_DIR", tmp_dir / "digests"), \
             patch("tui.ipc.DIGEST_ACTIONS_FILE", actions_file):
            items, dismissed_count = _load_inbox_items()

        active_ids = {i["id"] for i in items}
        assert "active-1" in active_ids
        assert "dismissed-1" not in active_ids
        assert dismissed_count >= 1

    def test_unique_items_from_both_sources(self, tmp_dir):
        """Unique items from triage and digest both appear."""
        from tui.screens import _load_inbox_items

        triage = {
            "items": [{"id": "triage-only", "title": "Triage", "priority": "high",
                        "source": "Teams"}],
        }
        digest = {
            "items": [{"id": "digest-only", "title": "Digest", "priority": "medium",
                        "source": "Email"}],
        }

        (tmp_dir / "monitoring-2026-03-02T09-00.json").write_text(
            json.dumps(triage), encoding="utf-8"
        )
        digests_dir = tmp_dir / "digests"
        digests_dir.mkdir()
        (digests_dir / "2026-03-02.json").write_text(
            json.dumps(digest), encoding="utf-8"
        )

        with patch("tui.screens.PULSE_HOME", tmp_dir), \
             patch("tui.screens.DIGESTS_DIR", digests_dir), \
             patch("tui.ipc.DIGEST_ACTIONS_FILE", tmp_dir / ".digest-actions.json"):
            items, _ = _load_inbox_items()

        ids = {i["id"] for i in items}
        assert "triage-only" in ids
        assert "digest-only" in ids
