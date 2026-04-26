"""Tests for SDK fixes — hooks stderr logging, tool save error handling, timeout constants."""

import json
import sys
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from sdk.hooks import (
    _write_audit_entry,
    make_post_tool_use_hook,
    make_pre_tool_use_hook,
    make_error_occurred_hook,
    make_session_end_hook,
)
from sdk.tools import dismiss_item, add_note


CTX = {"session_id": "test-session-123"}


class _BadDict(dict):
    """Dict subclass that raises on .get() — used to trigger hook error paths.

    Hooks check isinstance(input_data, dict) first, so a MagicMock (not a dict)
    would skip the .get() branch entirely and never trigger the error.
    """
    def __init__(self, error):
        super().__init__()
        self._error = error

    def get(self, key, default=None):
        raise self._error


# ---------------------------------------------------------------------------
# Fix 1: Hooks log to stderr on failure instead of silent pass
# ---------------------------------------------------------------------------


class TestHooksStderrLogging:
    """All hook except blocks must print to stderr, not silently pass."""

    def test_write_audit_entry_logs_stderr(self, tmp_dir, capsys):
        """_write_audit_entry prints to stderr when writing fails."""
        with patch("sdk.hooks.LOGS_DIR", tmp_dir), \
             patch("builtins.open", side_effect=OSError("mock write error")):
            _write_audit_entry({"type": "test"})
        captured = capsys.readouterr()
        assert "[AUDIT ERROR]" in captured.err
        assert "mock write error" in captured.err

    def test_post_tool_use_hook_logs_stderr(self, capsys):
        """post_tool_use hook logs to stderr when it fails internally."""
        hook = make_post_tool_use_hook()
        bad_input = _BadDict(RuntimeError("mock failure"))
        hook(bad_input, CTX)
        captured = capsys.readouterr()
        assert "[AUDIT ERROR]" in captured.err
        assert "mock failure" in captured.err

    def test_pre_tool_use_hook_logs_stderr(self, capsys):
        """pre_tool_use hook logs to stderr and returns None (fail-open) on error."""
        hook = make_pre_tool_use_hook()
        bad_input = _BadDict(RuntimeError("pre-tool boom"))
        result = hook(bad_input, CTX)
        assert result is None  # fail open
        captured = capsys.readouterr()
        assert "[AUDIT ERROR]" in captured.err
        assert "pre-tool boom" in captured.err

    def test_error_occurred_hook_logs_stderr(self, capsys):
        """error_occurred hook logs to stderr and returns None on internal failure."""
        hook = make_error_occurred_hook()
        bad_input = _BadDict(RuntimeError("error-hook boom"))
        result = hook(bad_input, CTX)
        assert result is None
        captured = capsys.readouterr()
        assert "[AUDIT ERROR]" in captured.err
        assert "error-hook boom" in captured.err

    def test_session_end_hook_logs_stderr(self, capsys):
        """session_end hook logs to stderr on internal failure."""
        hook = make_session_end_hook("test-mode", time.time())
        bad_input = _BadDict(RuntimeError("session-end boom"))
        hook(bad_input, CTX)
        captured = capsys.readouterr()
        assert "[AUDIT ERROR]" in captured.err
        assert "session-end boom" in captured.err


# ---------------------------------------------------------------------------
# Fix 5: dismiss_item / add_note return error when save fails
# ---------------------------------------------------------------------------


class TestToolSaveErrorHandling:
    """Tools that write state must surface save failures to the LLM."""

    @pytest.mark.asyncio
    async def test_dismiss_item_returns_error_on_save_failure(self, tmp_dir):
        """dismiss_item returns ERROR message when _save_actions raises OSError."""
        actions_file = tmp_dir / ".digest-actions.json"
        with patch("sdk.tools.ACTIONS_FILE", actions_file), \
             patch("sdk.tools.save_json_state", side_effect=OSError("disk full")):
            result = await dismiss_item.handler(
                {"arguments": {"item": "test-item", "reason": "test"}}
            )
        text = result["textResultForLlm"]
        assert "ERROR" in text
        assert "disk full" in text

    @pytest.mark.asyncio
    async def test_dismiss_item_succeeds_normally(self, tmp_dir):
        """dismiss_item returns Archived on success (regression check)."""
        actions_file = tmp_dir / ".digest-actions.json"
        with patch("sdk.tools.ACTIONS_FILE", actions_file):
            result = await dismiss_item.handler(
                {"arguments": {"item": "test-item", "reason": "done"}}
            )
        assert "Archived" in result["textResultForLlm"]

    @pytest.mark.asyncio
    async def test_add_note_returns_error_on_save_failure(self, tmp_dir):
        """add_note returns ERROR message when _save_actions raises OSError."""
        actions_file = tmp_dir / ".digest-actions.json"
        with patch("sdk.tools.ACTIONS_FILE", actions_file), \
             patch("sdk.tools.save_json_state", side_effect=OSError("permission denied")):
            result = await add_note.handler(
                {"arguments": {"item": "test-item", "note": "test note"}}
            )
        text = result["textResultForLlm"]
        assert "ERROR" in text
        assert "permission denied" in text

    @pytest.mark.asyncio
    async def test_add_note_succeeds_normally(self, tmp_dir):
        """add_note returns success on normal save (regression check)."""
        actions_file = tmp_dir / ".digest-actions.json"
        with patch("sdk.tools.ACTIONS_FILE", actions_file):
            result = await add_note.handler(
                {"arguments": {"item": "test-item", "note": "remember this"}}
            )
        assert "Note added" in result["textResultForLlm"]


# ---------------------------------------------------------------------------
# Fix 4: Timeout constants — no bare 1800/3600 in runner.py
# ---------------------------------------------------------------------------


class TestTimeoutConstants:
    """Verify timeout magic numbers are replaced by named constants."""

    def test_no_bare_timeout_magic_numbers_in_runner(self):
        """runner.py should use _TIMEOUT_DEFAULT / _TIMEOUT_RESEARCH, not bare literals."""
        import sdk.runner as runner_mod
        source = Path(runner_mod.__file__).read_text(encoding="utf-8")

        # The constants themselves will contain 1800/3600 — exclude those lines
        lines = source.splitlines()
        for i, line in enumerate(lines):
            stripped = line.strip()
            # Skip constant definitions and comments and the hours_ago calculation
            if stripped.startswith("_TIMEOUT_") or stripped.startswith("#"):
                continue
            # 3600 appears in hours_ago calculation (seconds / 3600) — that's fine
            if "/ 3600" in stripped or "* 3600" in stripped:
                continue
            # No bare timeout usage of 1800 or 3600 should remain
            for magic in ["= 1800", "=1800", "timeout=1800", "timeout=3600", "= 3600", "=3600"]:
                assert magic not in stripped, (
                    f"Bare timeout magic number found on line {i+1}: {stripped}"
                )

    def test_timeout_constants_exist(self):
        """The named constants must exist with expected values."""
        from sdk.runner import _TIMEOUT_DEFAULT, _TIMEOUT_RESEARCH
        assert _TIMEOUT_DEFAULT == 1800
        assert _TIMEOUT_RESEARCH == 3600


# ---------------------------------------------------------------------------
# Fix 3: stat() caching — structural test
# ---------------------------------------------------------------------------


class TestStatCaching:
    """Verify the stat() result is cached in the file listing loop."""

    def test_no_triple_stat_in_file_listing(self):
        """The file listing loop should not call f.stat() more than once per file."""
        import sdk.runner as runner_mod
        source = Path(runner_mod.__file__).read_text(encoding="utf-8")

        # Find the block between "for f in sorted" and the next "if files:"
        in_block = False
        stat_calls = 0
        for line in source.splitlines():
            if "for f in sorted(directory.rglob" in line:
                in_block = True
                # The sort key lambda also calls stat — that's a separate call
                # we're checking the body of the loop
                continue
            if in_block and "if files:" in line:
                break
            if in_block and "f.stat()" in line:
                stat_calls += 1

        # Should be exactly 1 cached call: st = f.stat()
        assert stat_calls == 1, (
            f"Expected 1 stat() call in file listing loop body, found {stat_calls}"
        )
