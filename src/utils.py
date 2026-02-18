"""Shared utilities — logging, event handling, session context manager."""

import json
import logging
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from copilot import CopilotClient, Tool
from copilot.generated.session_events import SessionEventType

from session import build_session_config

# ---------------------------------------------------------------------------
# Structured logger
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).parent.parent
LOGS_DIR = PROJECT_ROOT / "logs"


class _JsonFormatter(logging.Formatter):
    """Emit structured JSON log lines."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "level": record.levelname,
            "module": record.module,
            "msg": record.getMessage(),
        }
        if hasattr(record, "run_id"):
            entry["run_id"] = record.run_id
        return json.dumps(entry, ensure_ascii=False)


def _safe_encode(text: str) -> str:
    """ASCII-safe encoding to avoid charmap errors on Windows."""
    return text.encode("ascii", "replace").decode("ascii")


def setup_logging(run_id: str | None = None) -> logging.Logger:
    """Configure the root Pulse logger.

    Returns a logger that writes:
    - Human-readable lines to stderr (for live terminal output)
    - JSON lines to logs/YYYY-MM-DD.jsonl (for audit/ops)
    """
    logger = logging.getLogger("pulse")
    if logger.handlers:
        return logger  # already configured

    logger.setLevel(logging.DEBUG)

    # Console handler — human-readable, INFO+
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(console)

    # File handler — structured JSON, DEBUG+
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")
    log_file = LOGS_DIR / f"{date_str}.jsonl"
    fh = logging.FileHandler(str(log_file), encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(_JsonFormatter())
    logger.addHandler(fh)

    # Attach run_id to all records via a filter
    if run_id:
        class _RunIdFilter(logging.Filter):
            def filter(self, record):
                record.run_id = run_id
                return True
        logger.addFilter(_RunIdFilter())

    return logger


def new_run_id() -> str:
    """Generate a short unique run ID for tracing."""
    return uuid.uuid4().hex[:8]


# ---------------------------------------------------------------------------
# Shared event logger (replaces duplicated _log_event across modules)
# ---------------------------------------------------------------------------

log = logging.getLogger("pulse")


def log_event(event) -> None:
    """Log a streaming GHCP SDK session event to the terminal."""
    event_type = getattr(event, "type", None)

    if event_type == SessionEventType.ASSISTANT_MESSAGE_DELTA:
        data = getattr(event, "data", None)
        if data and data.delta_content:
            text = _safe_encode(data.delta_content)
            print(text, end="", flush=True)

    elif event_type == SessionEventType.ASSISTANT_MESSAGE:
        print(flush=True)

    elif event_type == SessionEventType.TOOL_EXECUTION_START:
        data = getattr(event, "data", None)
        tool_name = data.tool_name if data and data.tool_name else "unknown"
        mcp = f" ({data.mcp_server_name})" if data and data.mcp_server_name else ""
        args = ""
        if data and hasattr(data, "arguments") and data.arguments:
            args = f" {_safe_encode(str(data.arguments)[:200])}"
        elif data and hasattr(data, "input") and data.input:
            args = f" {_safe_encode(str(data.input)[:200])}"
        print(_safe_encode(f"\n>> [TOOL] {tool_name}{mcp}{args}"), flush=True)

    elif event_type == SessionEventType.TOOL_EXECUTION_COMPLETE:
        data = getattr(event, "data", None)
        if data and data.result:
            preview = str(data.result)[:300]
            print(_safe_encode(f"<< [RESULT] {preview}"), flush=True)


# ---------------------------------------------------------------------------
# Session context manager (replaces repetitive create/on/destroy pattern)
# ---------------------------------------------------------------------------

@asynccontextmanager
async def agent_session(
    client: CopilotClient,
    config: dict,
    mode: str,
    tools: list[Tool] | None = None,
    timeout: int = 600,
    telegram_app=None,
    chat_id: int | None = None,
):
    """Async context manager for GHCP SDK sessions.

    Usage:
        async with agent_session(client, config, "digest", tools=get_tools()) as session:
            response = await session.send_and_wait({"prompt": prompt}, timeout=600)

    Handles session creation, event streaming, and cleanup automatically.
    """
    # Use shared browser CDP endpoint if available
    from browser import get_browser_manager
    mgr = get_browser_manager()
    cdp_endpoint = mgr.cdp_endpoint if mgr else None

    session_config = build_session_config(
        config, mode=mode, tools=tools,
        telegram_app=telegram_app, chat_id=chat_id,
        cdp_endpoint=cdp_endpoint,
    )
    session = await client.create_session(session_config)
    session.on(lambda event: log_event(event))

    try:
        yield session
    finally:
        await session.destroy()
