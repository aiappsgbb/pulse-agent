"""Session hooks for GHCP SDK — audit trail, guardrails, error handling, metrics.

Hooks are lifecycle callbacks invoked by the SDK at key points:
- on_pre_tool_use: before a tool executes (can block or modify)
- on_post_tool_use: after a tool completes (automatic audit trail)
- on_error_occurred: when the session hits an error (structured logging + recovery)
- on_session_end: when a session finishes (duration + metrics)

IMPORTANT: Hooks must NEVER raise exceptions — a crashing hook would disrupt
the SDK session. All hooks wrap their logic in try/except as a safety net.
The SDK also catches hook exceptions silently, but we add our own layer.
"""

import json
import sys
import time
from datetime import datetime

from core.constants import LOGS_DIR
from core.logging import log, safe_encode


def _write_audit_entry(entry: dict) -> None:
    """Append an entry to the daily JSONL audit log.

    Silently swallows errors — audit logging must never crash the session.
    """
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now().strftime("%Y-%m-%d")
        log_file = LOGS_DIR / f"{date_str}.jsonl"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[AUDIT ERROR] {e}", file=sys.stderr)


def _ctx_session_id(context) -> str:
    """Safely extract session_id from context (may be None or missing key)."""
    if context and isinstance(context, dict):
        return context.get("session_id", "")
    return ""


# --- Hook factories ---
# Each returns a callable(input_data, context) matching the SDK hook signature.
# Using factories (closures) so hooks can capture mode and start_time.


def make_post_tool_use_hook():
    """Automatic audit trail — logs every tool call to JSONL."""

    def hook(input_data, context):
        try:
            tool_name = input_data.get("toolName", "unknown")
            tool_args = input_data.get("toolArgs")
            tool_result = input_data.get("toolResult")

            # Truncate for audit log — keep it manageable but show full errors
            args_str = str(tool_args)[:500] if tool_args else ""
            result_str = str(tool_result)[:1000] if tool_result else ""

            _write_audit_entry({
                "timestamp": datetime.now().isoformat(),
                "type": "tool_use",
                "tool": tool_name,
                "args": args_str,
                "result_preview": result_str,
                "session_id": _ctx_session_id(context),
            })
        except Exception as e:
            print(f"[AUDIT ERROR] {e}", file=sys.stderr)

    return hook


def make_pre_tool_use_hook():
    """Write path guardrails — defense-in-depth for file-writing tools."""

    def hook(input_data, context):
        try:
            tool_name = input_data.get("toolName", "")
            tool_args = input_data.get("toolArgs") or {}

            # Guardrail: block path traversal in write_output
            if tool_name == "write_output":
                filename = str(tool_args.get("filename", ""))
                if ".." in filename:
                    log.warning(f"  Hook blocked write_output path traversal: {filename}")
                    return {
                        "permissionDecision": "deny",
                        "permissionDecisionReason": f"Path traversal blocked: '{filename}' contains '..'",
                    }

            # Guardrail: block invalid project IDs
            if tool_name == "update_project":
                project_id = str(tool_args.get("project_id", ""))
                if ".." in project_id or "/" in project_id or "\\" in project_id:
                    log.warning(f"  Hook blocked update_project invalid ID: {project_id}")
                    return {
                        "permissionDecision": "deny",
                        "permissionDecisionReason": f"Invalid project ID: '{project_id}'",
                    }

            return None  # allow
        except Exception as e:
            print(f"[AUDIT ERROR] {e}", file=sys.stderr)
            return None  # fail open — don't block tools on hook errors

    return hook


def make_error_occurred_hook():
    """Structured error logging + auto-retry for recoverable tool errors."""

    def hook(input_data, context):
        try:
            error = input_data.get("error", "Unknown error")
            error_context = input_data.get("errorContext", "unknown")
            recoverable = input_data.get("recoverable", False)

            _write_audit_entry({
                "timestamp": datetime.now().isoformat(),
                "type": "error",
                "error": str(error)[:1000],
                "error_context": error_context,
                "recoverable": recoverable,
                "session_id": _ctx_session_id(context),
            })

            log.warning(
                f"  Session error [{error_context}]: "
                f"{safe_encode(str(error)[:200])} (recoverable={recoverable})"
            )

            # Auto-retry recoverable tool execution errors (once)
            if recoverable and error_context == "tool_execution":
                return {"errorHandling": "retry", "retryCount": 1}

            return None
        except Exception as e:
            print(f"[AUDIT ERROR] {e}", file=sys.stderr)
            return None  # never crash the session

    return hook


def make_session_end_hook(mode: str, start_time: float):
    """Session metrics — logs duration, end reason, mode."""

    def hook(input_data, context):
        try:
            reason = input_data.get("reason", "unknown")
            duration = time.time() - start_time

            entry = {
                "timestamp": datetime.now().isoformat(),
                "type": "session_end",
                "mode": mode,
                "reason": reason,
                "duration_seconds": round(duration, 1),
                "session_id": _ctx_session_id(context),
            }

            error = input_data.get("error")
            if error:
                entry["error"] = str(error)[:500]

            _write_audit_entry(entry)
            log.info(f"  Session ended: mode={mode}, reason={reason}, duration={duration:.1f}s")
        except Exception as e:
            print(f"[AUDIT ERROR] {e}", file=sys.stderr)

    return hook


def build_hooks(mode: str) -> dict:
    """Build the SessionHooks dict for a given mode."""
    return {
        "on_pre_tool_use": make_pre_tool_use_hook(),
        "on_post_tool_use": make_post_tool_use_hook(),
        "on_error_occurred": make_error_occurred_hook(),
        "on_session_end": make_session_end_hook(mode, time.time()),
    }
