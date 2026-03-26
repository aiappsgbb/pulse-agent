"""Tests for sdk/hooks.py — session lifecycle hooks."""

import json
import time
from pathlib import Path
from unittest.mock import patch, PropertyMock

from sdk.hooks import (
    build_hooks,
    make_post_tool_use_hook,
    make_pre_tool_use_hook,
    make_error_occurred_hook,
    make_session_end_hook,
    _write_audit_entry,
    _ctx_session_id,
)


CTX = {"session_id": "test-session-123"}


# --- _ctx_session_id ---


def test_ctx_session_id_normal():
    assert _ctx_session_id({"session_id": "abc"}) == "abc"


def test_ctx_session_id_none():
    assert _ctx_session_id(None) == ""


def test_ctx_session_id_empty_dict():
    assert _ctx_session_id({}) == ""


def test_ctx_session_id_non_dict():
    assert _ctx_session_id("not a dict") == ""


# --- _write_audit_entry ---


def test_write_audit_entry_creates_jsonl(tmp_dir):
    with patch("sdk.hooks.LOGS_DIR", tmp_dir):
        _write_audit_entry({"type": "test", "data": "hello"})
    log_files = list(tmp_dir.glob("*.jsonl"))
    assert len(log_files) == 1
    entry = json.loads(log_files[0].read_text().strip())
    assert entry["type"] == "test"
    assert entry["data"] == "hello"


def test_write_audit_entry_appends(tmp_dir):
    with patch("sdk.hooks.LOGS_DIR", tmp_dir):
        _write_audit_entry({"type": "first"})
        _write_audit_entry({"type": "second"})
    log_files = list(tmp_dir.glob("*.jsonl"))
    lines = log_files[0].read_text().strip().split("\n")
    assert len(lines) == 2


def test_write_audit_entry_creates_missing_dir(tmp_dir):
    nested = tmp_dir / "deep" / "nested" / "logs"
    with patch("sdk.hooks.LOGS_DIR", nested):
        _write_audit_entry({"type": "test"})
    assert nested.exists()
    assert len(list(nested.glob("*.jsonl"))) == 1


def test_write_audit_entry_handles_unicode(tmp_dir):
    with patch("sdk.hooks.LOGS_DIR", tmp_dir):
        _write_audit_entry({"type": "test", "data": "Hello 世界 🎉"})
    entry = json.loads(list(tmp_dir.glob("*.jsonl"))[0].read_text(encoding="utf-8").strip())
    assert "世界" in entry["data"]
    assert "🎉" in entry["data"]


def test_write_audit_entry_survives_readonly_dir(tmp_dir):
    """If LOGS_DIR is unwritable, _write_audit_entry silently fails (no crash)."""
    bad_dir = tmp_dir / "nonexistent" / "readonly"
    # Patch mkdir to raise PermissionError
    with patch("sdk.hooks.LOGS_DIR", bad_dir), \
         patch.object(Path, "mkdir", side_effect=PermissionError("denied")):
        _write_audit_entry({"type": "should_not_crash"})
    # No exception raised — that's the test


# --- on_post_tool_use (audit trail) ---


def test_post_tool_use_logs_tool_call(tmp_dir):
    hook = make_post_tool_use_hook()
    with patch("sdk.hooks.LOGS_DIR", tmp_dir):
        hook({
            "timestamp": 1234567890,
            "cwd": "/tmp",
            "toolName": "write_output",
            "toolArgs": {"filename": "test.md", "content": "hello"},
            "toolResult": "Written to /tmp/test.md",
        }, CTX)
    log_files = list(tmp_dir.glob("*.jsonl"))
    assert len(log_files) == 1
    entry = json.loads(log_files[0].read_text().strip())
    assert entry["type"] == "tool_use"
    assert entry["tool"] == "write_output"
    assert "test.md" in entry["args"]
    assert "Written" in entry["result_preview"]
    assert entry["session_id"] == "test-session-123"


def test_post_tool_use_truncates_large_args(tmp_dir):
    hook = make_post_tool_use_hook()
    large_content = "x" * 10000
    with patch("sdk.hooks.LOGS_DIR", tmp_dir):
        hook({
            "timestamp": 0,
            "cwd": "/tmp",
            "toolName": "write_output",
            "toolArgs": {"content": large_content},
            "toolResult": large_content,
        }, CTX)
    entry = json.loads(list(tmp_dir.glob("*.jsonl"))[0].read_text().strip())
    assert len(entry["args"]) <= 600  # 500 + some dict formatting
    assert len(entry["result_preview"]) <= 1100  # 1000 + some dict formatting


def test_post_tool_use_handles_none_args(tmp_dir):
    hook = make_post_tool_use_hook()
    with patch("sdk.hooks.LOGS_DIR", tmp_dir):
        hook({
            "timestamp": 0,
            "cwd": "/tmp",
            "toolName": "list_schedules",
            "toolArgs": None,
            "toolResult": "No schedules",
        }, CTX)
    entry = json.loads(list(tmp_dir.glob("*.jsonl"))[0].read_text().strip())
    assert entry["tool"] == "list_schedules"
    assert entry["args"] == ""


def test_post_tool_use_handles_none_context(tmp_dir):
    """SDK might pass None context — hook must not crash."""
    hook = make_post_tool_use_hook()
    with patch("sdk.hooks.LOGS_DIR", tmp_dir):
        hook({
            "timestamp": 0,
            "cwd": "/tmp",
            "toolName": "test_tool",
            "toolArgs": {},
            "toolResult": "ok",
        }, None)
    entry = json.loads(list(tmp_dir.glob("*.jsonl"))[0].read_text().strip())
    assert entry["session_id"] == ""


def test_post_tool_use_survives_write_failure(tmp_dir):
    """If audit write fails, hook silently continues (no crash)."""
    hook = make_post_tool_use_hook()
    with patch("sdk.hooks._write_audit_entry", side_effect=Exception("disk full")):
        # Should not raise
        hook({
            "timestamp": 0,
            "cwd": "/tmp",
            "toolName": "test",
            "toolArgs": {},
            "toolResult": "ok",
        }, CTX)


# --- on_pre_tool_use (guardrails) ---


def test_pre_tool_use_allows_normal_write():
    hook = make_pre_tool_use_hook()
    result = hook({
        "timestamp": 0,
        "cwd": "/tmp",
        "toolName": "write_output",
        "toolArgs": {"filename": "digests/2026-02-24.md", "content": "ok"},
    }, CTX)
    assert result is None  # allowed


def test_pre_tool_use_blocks_write_path_traversal():
    hook = make_pre_tool_use_hook()
    with patch("sdk.hooks.log"):
        result = hook({
            "timestamp": 0,
            "cwd": "/tmp",
            "toolName": "write_output",
            "toolArgs": {"filename": "../../etc/passwd", "content": "pwned"},
        }, CTX)
    assert result is not None
    assert result["permissionDecision"] == "deny"
    assert ".." in result["permissionDecisionReason"]


def test_pre_tool_use_blocks_write_backslash_traversal():
    """Windows-style backslash path traversal must also be caught."""
    hook = make_pre_tool_use_hook()
    with patch("sdk.hooks.log"):
        result = hook({
            "timestamp": 0,
            "cwd": "C:\\Users\\test",
            "toolName": "write_output",
            "toolArgs": {"filename": "..\\..\\Windows\\system32\\evil.txt", "content": "pwned"},
        }, CTX)
    assert result is not None
    assert result["permissionDecision"] == "deny"


def test_pre_tool_use_blocks_project_path_traversal():
    hook = make_pre_tool_use_hook()
    with patch("sdk.hooks.log"):
        result = hook({
            "timestamp": 0,
            "cwd": "/tmp",
            "toolName": "update_project",
            "toolArgs": {"project_id": "../../evil"},
        }, CTX)
    assert result is not None
    assert result["permissionDecision"] == "deny"


def test_pre_tool_use_blocks_project_forward_slash():
    hook = make_pre_tool_use_hook()
    with patch("sdk.hooks.log"):
        result = hook({
            "timestamp": 0,
            "cwd": "/tmp",
            "toolName": "update_project",
            "toolArgs": {"project_id": "foo/bar"},
        }, CTX)
    assert result is not None
    assert result["permissionDecision"] == "deny"


def test_pre_tool_use_blocks_project_backslash():
    """Windows-style backslash in project ID must be blocked."""
    hook = make_pre_tool_use_hook()
    with patch("sdk.hooks.log"):
        result = hook({
            "timestamp": 0,
            "cwd": "/tmp",
            "toolName": "update_project",
            "toolArgs": {"project_id": "foo\\bar"},
        }, CTX)
    assert result is not None
    assert result["permissionDecision"] == "deny"


def test_pre_tool_use_allows_valid_project():
    hook = make_pre_tool_use_hook()
    result = hook({
        "timestamp": 0,
        "cwd": "/tmp",
        "toolName": "update_project",
        "toolArgs": {"project_id": "contoso-migration"},
    }, CTX)
    assert result is None  # allowed


def test_pre_tool_use_allows_unrelated_tools():
    hook = make_pre_tool_use_hook()
    result = hook({
        "timestamp": 0,
        "cwd": "/tmp",
        "toolName": "search_local_files",
        "toolArgs": {"query": "test"},
    }, CTX)
    assert result is None


def test_pre_tool_use_handles_missing_args():
    hook = make_pre_tool_use_hook()
    result = hook({
        "timestamp": 0,
        "cwd": "/tmp",
        "toolName": "write_output",
        "toolArgs": None,
    }, CTX)
    assert result is None  # no args = nothing to block


def test_pre_tool_use_handles_none_context():
    """None context should not crash the hook."""
    hook = make_pre_tool_use_hook()
    result = hook({
        "timestamp": 0,
        "cwd": "/tmp",
        "toolName": "search_local_files",
        "toolArgs": {"query": "test"},
    }, None)
    assert result is None


def test_pre_tool_use_fails_open_on_exception():
    """If the hook itself crashes internally, it should allow the tool (fail open)."""
    hook = make_pre_tool_use_hook()
    # Pass a non-dict input_data to trigger an exception in .get()
    result = hook("not a dict", CTX)
    assert result is None  # fail open


# --- on_error_occurred ---


def test_error_occurred_logs_to_audit(tmp_dir):
    hook = make_error_occurred_hook()
    with patch("sdk.hooks.LOGS_DIR", tmp_dir), patch("sdk.hooks.log"):
        hook({
            "timestamp": 0,
            "cwd": "/tmp",
            "error": "WorkIQ connection failed",
            "errorContext": "tool_execution",
            "recoverable": True,
        }, CTX)
    entry = json.loads(list(tmp_dir.glob("*.jsonl"))[0].read_text().strip())
    assert entry["type"] == "error"
    assert "WorkIQ" in entry["error"]
    assert entry["error_context"] == "tool_execution"
    assert entry["recoverable"] is True


def test_error_occurred_retries_recoverable_tool_error(tmp_dir):
    hook = make_error_occurred_hook()
    with patch("sdk.hooks.LOGS_DIR", tmp_dir), patch("sdk.hooks.log"):
        result = hook({
            "timestamp": 0,
            "cwd": "/tmp",
            "error": "Timeout",
            "errorContext": "tool_execution",
            "recoverable": True,
        }, CTX)
    assert result is not None
    assert result["errorHandling"] == "retry"
    assert result["retryCount"] == 1


def test_error_occurred_no_retry_for_model_error(tmp_dir):
    hook = make_error_occurred_hook()
    with patch("sdk.hooks.LOGS_DIR", tmp_dir), patch("sdk.hooks.log"):
        result = hook({
            "timestamp": 0,
            "cwd": "/tmp",
            "error": "Rate limited",
            "errorContext": "model_call",
            "recoverable": True,
        }, CTX)
    assert result is None  # no retry — let SDK handle model errors


def test_error_occurred_no_retry_for_unrecoverable(tmp_dir):
    hook = make_error_occurred_hook()
    with patch("sdk.hooks.LOGS_DIR", tmp_dir), patch("sdk.hooks.log"):
        result = hook({
            "timestamp": 0,
            "cwd": "/tmp",
            "error": "Fatal crash",
            "errorContext": "tool_execution",
            "recoverable": False,
        }, CTX)
    assert result is None


def test_error_occurred_handles_none_context(tmp_dir):
    hook = make_error_occurred_hook()
    with patch("sdk.hooks.LOGS_DIR", tmp_dir), patch("sdk.hooks.log"):
        result = hook({
            "timestamp": 0,
            "cwd": "/tmp",
            "error": "some error",
            "errorContext": "system",
            "recoverable": False,
        }, None)
    # Should not crash, and entry should have empty session_id
    entry = json.loads(list(tmp_dir.glob("*.jsonl"))[0].read_text().strip())
    assert entry["session_id"] == ""
    assert result is None


def test_error_occurred_truncates_long_error(tmp_dir):
    hook = make_error_occurred_hook()
    long_error = "E" * 5000
    with patch("sdk.hooks.LOGS_DIR", tmp_dir), patch("sdk.hooks.log"):
        hook({
            "timestamp": 0,
            "cwd": "/tmp",
            "error": long_error,
            "errorContext": "tool_execution",
            "recoverable": False,
        }, CTX)
    entry = json.loads(list(tmp_dir.glob("*.jsonl"))[0].read_text().strip())
    assert len(entry["error"]) <= 1000


def test_error_occurred_survives_internal_failure(tmp_dir):
    """If audit write fails inside the hook, it should not crash."""
    hook = make_error_occurred_hook()
    with patch("sdk.hooks._write_audit_entry", side_effect=Exception("boom")), \
         patch("sdk.hooks.log"):
        # Should not raise
        result = hook({
            "timestamp": 0,
            "cwd": "/tmp",
            "error": "test",
            "errorContext": "system",
            "recoverable": False,
        }, CTX)
    assert result is None


# --- on_session_end ---


def test_session_end_logs_metrics(tmp_dir):
    start = time.time() - 10.5  # pretend session took 10.5s
    hook = make_session_end_hook("digest", start)
    with patch("sdk.hooks.LOGS_DIR", tmp_dir), patch("sdk.hooks.log"):
        hook({
            "timestamp": 0,
            "cwd": "/tmp",
            "reason": "complete",
        }, CTX)
    entry = json.loads(list(tmp_dir.glob("*.jsonl"))[0].read_text().strip())
    assert entry["type"] == "session_end"
    assert entry["mode"] == "digest"
    assert entry["reason"] == "complete"
    assert entry["duration_seconds"] >= 10.0
    assert entry["session_id"] == "test-session-123"


def test_session_end_includes_error(tmp_dir):
    hook = make_session_end_hook("monitor", time.time())
    with patch("sdk.hooks.LOGS_DIR", tmp_dir), patch("sdk.hooks.log"):
        hook({
            "timestamp": 0,
            "cwd": "/tmp",
            "reason": "error",
            "error": "Session crashed",
        }, CTX)
    entry = json.loads(list(tmp_dir.glob("*.jsonl"))[0].read_text().strip())
    assert entry["reason"] == "error"
    assert "crashed" in entry["error"]


def test_session_end_no_error_field_when_complete(tmp_dir):
    hook = make_session_end_hook("chat", time.time())
    with patch("sdk.hooks.LOGS_DIR", tmp_dir), patch("sdk.hooks.log"):
        hook({
            "timestamp": 0,
            "cwd": "/tmp",
            "reason": "complete",
        }, CTX)
    entry = json.loads(list(tmp_dir.glob("*.jsonl"))[0].read_text().strip())
    assert "error" not in entry


def test_session_end_handles_none_context(tmp_dir):
    hook = make_session_end_hook("intel", time.time())
    with patch("sdk.hooks.LOGS_DIR", tmp_dir), patch("sdk.hooks.log"):
        hook({
            "timestamp": 0,
            "cwd": "/tmp",
            "reason": "complete",
        }, None)
    entry = json.loads(list(tmp_dir.glob("*.jsonl"))[0].read_text().strip())
    assert entry["session_id"] == ""
    assert entry["mode"] == "intel"


def test_session_end_truncates_long_error(tmp_dir):
    hook = make_session_end_hook("research", time.time())
    long_error = "Z" * 3000
    with patch("sdk.hooks.LOGS_DIR", tmp_dir), patch("sdk.hooks.log"):
        hook({
            "timestamp": 0,
            "cwd": "/tmp",
            "reason": "error",
            "error": long_error,
        }, CTX)
    entry = json.loads(list(tmp_dir.glob("*.jsonl"))[0].read_text().strip())
    assert len(entry["error"]) <= 500


# --- build_hooks ---


def test_build_hooks_returns_all_four():
    hooks = build_hooks("digest")
    assert "on_pre_tool_use" in hooks
    assert "on_post_tool_use" in hooks
    assert "on_error_occurred" in hooks
    assert "on_session_end" in hooks
    assert callable(hooks["on_pre_tool_use"])
    assert callable(hooks["on_post_tool_use"])
    assert callable(hooks["on_error_occurred"])
    assert callable(hooks["on_session_end"])


def test_build_hooks_session_end_captures_mode(tmp_dir):
    """Session end hook should capture the mode passed to build_hooks."""
    hooks = build_hooks("research")
    with patch("sdk.hooks.LOGS_DIR", tmp_dir), patch("sdk.hooks.log"):
        hooks["on_session_end"]({
            "timestamp": 0,
            "cwd": "/tmp",
            "reason": "complete",
        }, CTX)
    entry = json.loads(list(tmp_dir.glob("*.jsonl"))[0].read_text().strip())
    assert entry["mode"] == "research"


def test_build_hooks_each_mode_gets_independent_hooks():
    """Hooks for different modes should be independent closures."""
    hooks_a = build_hooks("digest")
    hooks_b = build_hooks("monitor")
    # They're different function objects (not shared)
    assert hooks_a["on_session_end"] is not hooks_b["on_session_end"]
    assert hooks_a["on_pre_tool_use"] is not hooks_b["on_pre_tool_use"]
