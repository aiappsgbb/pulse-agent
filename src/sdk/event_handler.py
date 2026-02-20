"""Event handler for GHCP SDK session events — dispatch table pattern.

Replaces ad-hoc lambda event handlers with a structured, extensible handler.
Tracks completion state (done, final_text, error) so callers can use
session.send() + handler.done.wait() instead of send_and_wait().
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from core.logging import log, safe_encode


class EventHandler:
    """Dispatch session events to handlers. Tracks completion for non-blocking sends."""

    _dispatch: dict | None = None

    def __init__(self, on_delta: Callable[[str], None] | None = None) -> None:
        self.on_delta = on_delta
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
        print(flush=True)

    def _handle_idle(self, event: Any) -> None:
        log.debug("SESSION_IDLE — agent done")
        self.done.set()

    def _handle_error(self, event: Any) -> None:
        data = getattr(event, "data", None)
        self.error = str(data) if data else "Unknown session error"
        log.error(f"SESSION_ERROR: {self.error}")
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

    def _handle_tool_complete(self, event: Any) -> None:
        data = getattr(event, "data", None)
        if data and data.result:
            preview = str(data.result)[:300]
            print(safe_encode(f"<< [RESULT] {preview}"), flush=True)
