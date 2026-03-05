"""Event handler for GHCP SDK session events — dispatch table pattern.

Replaces ad-hoc lambda event handlers with a structured, extensible handler.
Tracks completion state (done, final_text, error) so callers can use
session.send() + handler.done.wait() instead of send_and_wait().
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from core.logging import log, safe_encode


class EventHandler:
    """Dispatch session events to handlers. Tracks completion for non-blocking sends."""

    _dispatch: dict | None = None

    def __init__(
        self,
        on_delta: Callable[[str], None] | None = None,
        log_file: str | Path | None = None,
    ) -> None:
        self.on_delta = on_delta
        self.log_file = Path(log_file) if log_file else None
        self.final_text: str | None = None
        self.error: str | None = None
        self.done = asyncio.Event()

    @classmethod
    def _get_dispatch(cls) -> dict:
        """Build dispatch table lazily (avoids import-time copilot SDK dependency)."""
        if cls._dispatch is None:
            from copilot.generated.session_events import SessionEventType
            cls._dispatch = {
                SessionEventType.ASSISTANT_MESSAGE_DELTA: cls._handle_delta,
                SessionEventType.ASSISTANT_MESSAGE: cls._handle_message,
                SessionEventType.SESSION_IDLE: cls._handle_idle,
                SessionEventType.SESSION_ERROR: cls._handle_error,
                SessionEventType.TOOL_EXECUTION_START: cls._handle_tool_start,
                SessionEventType.TOOL_EXECUTION_COMPLETE: cls._handle_tool_complete,
            }
        return cls._dispatch

    def __call__(self, event: Any) -> None:
        dispatch = self._get_dispatch()
        handler = dispatch.get(event.type)
        if handler:
            handler(self, event)

    def _write_log(self, entry: dict) -> None:
        """Append an entry to the per-job activity log (if configured)."""
        if not self.log_file:
            return
        try:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
            with self.log_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=str, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _handle_delta(self, event: Any) -> None:
        data = getattr(event, "data", None)
        if data and data.delta_content:
            text = safe_encode(data.delta_content)
            print(text, end="", flush=True)
            if self.on_delta:
                self.on_delta(data.delta_content)

    def _handle_message(self, event: Any) -> None:
        data = getattr(event, "data", None)
        if data and hasattr(data, "content") and data.content:
            self.final_text = data.content
            self._write_log({
                "ts": datetime.now().isoformat(),
                "type": "message",
                "preview": data.content[:500],
            })
        print(flush=True)

    def _handle_idle(self, event: Any) -> None:
        log.debug("SESSION_IDLE — agent done")
        self._write_log({"ts": datetime.now().isoformat(), "type": "idle"})
        self.done.set()

    def _handle_error(self, event: Any) -> None:
        data = getattr(event, "data", None)
        self.error = str(data) if data else "Unknown session error"
        log.error(f"SESSION_ERROR: {self.error}")
        self._write_log({
            "ts": datetime.now().isoformat(),
            "type": "error",
            "error": self.error[:500],
        })
        self.done.set()

    def _handle_tool_start(self, event: Any) -> None:
        data = getattr(event, "data", None)
        tool_name = data.tool_name if data and data.tool_name else "unknown"
        mcp = f" ({data.mcp_server_name})" if data and data.mcp_server_name else ""
        args = ""
        if data and hasattr(data, "arguments") and data.arguments:
            args = f" {safe_encode(str(data.arguments)[:200])}"
        elif data and hasattr(data, "input") and data.input:
            args = f" {safe_encode(str(data.input)[:200])}"
        print(safe_encode(f"\n>> [TOOL] {tool_name}{mcp}{args}"), flush=True)
        self._write_log({
            "ts": datetime.now().isoformat(),
            "type": "tool_start",
            "tool": tool_name,
            "mcp": data.mcp_server_name if data and data.mcp_server_name else "",
            "args": args.strip()[:300],
        })

    def _handle_tool_complete(self, event: Any) -> None:
        data = getattr(event, "data", None)
        if data and data.result:
            preview = str(data.result)[:300]
            print(safe_encode(f"<< [RESULT] {preview}"), flush=True)
            self._write_log({
                "ts": datetime.now().isoformat(),
                "type": "tool_result",
                "tool": data.tool_name if hasattr(data, "tool_name") and data.tool_name else "",
                "result": preview,
            })
