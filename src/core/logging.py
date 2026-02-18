"""Structured logging, event streaming, and session context manager."""

import json
import logging
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from core.constants import LOGS_DIR


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


def safe_encode(text: str) -> str:
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


log = logging.getLogger("pulse")


def log_event(event) -> None:
    """Log a streaming GHCP SDK session event to the terminal."""
    from copilot.generated.session_events import SessionEventType

    event_type = getattr(event, "type", None)

    if event_type == SessionEventType.ASSISTANT_MESSAGE_DELTA:
        data = getattr(event, "data", None)
        if data and data.delta_content:
            text = safe_encode(data.delta_content)
            print(text, end="", flush=True)

    elif event_type == SessionEventType.ASSISTANT_MESSAGE:
        print(flush=True)

    elif event_type == SessionEventType.TOOL_EXECUTION_START:
        data = getattr(event, "data", None)
        tool_name = data.tool_name if data and data.tool_name else "unknown"
        mcp = f" ({data.mcp_server_name})" if data and data.mcp_server_name else ""
        args = ""
        if data and hasattr(data, "arguments") and data.arguments:
            args = f" {safe_encode(str(data.arguments)[:200])}"
        elif data and hasattr(data, "input") and data.input:
            args = f" {safe_encode(str(data.input)[:200])}"
        print(safe_encode(f"\n>> [TOOL] {tool_name}{mcp}{args}"), flush=True)

    elif event_type == SessionEventType.TOOL_EXECUTION_COMPLETE:
        data = getattr(event, "data", None)
        if data and data.result:
            preview = str(data.result)[:300]
            print(safe_encode(f"<< [RESULT] {preview}"), flush=True)
