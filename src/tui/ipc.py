"""File-based IPC helpers for TUI ↔ daemon communication.

Daemon WRITES:
  .daemon-status.json   — uptime, queue size (every 60s)
  .chat-stream.jsonl    — streaming chat deltas
  .pending-question.json — ask_user questions

TUI WRITES:
  .chat-request.json    — chat prompt (fast-polled every 5s by daemon)
  .question-response.json — ask_user answers
"""

import asyncio
import json
import uuid
from datetime import datetime
from pathlib import Path

import yaml

from core.constants import PULSE_HOME, JOBS_DIR

# ---------------------------------------------------------------------------
# IPC file paths
# ---------------------------------------------------------------------------

STATUS_FILE = PULSE_HOME / ".daemon-status.json"
CHAT_REQUEST_FILE = PULSE_HOME / ".chat-request.json"
CHAT_STREAM_FILE = PULSE_HOME / ".chat-stream.jsonl"
PENDING_QUESTION_FILE = PULSE_HOME / ".pending-question.json"
QUESTION_RESPONSE_FILE = PULSE_HOME / ".question-response.json"
DIGEST_ACTIONS_FILE = PULSE_HOME / ".digest-actions.json"


# ---------------------------------------------------------------------------
# TUI reads: daemon status
# ---------------------------------------------------------------------------

def read_daemon_status() -> dict:
    """Read daemon status. Returns empty dict if unavailable."""
    try:
        if STATUS_FILE.exists():
            return json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


# ---------------------------------------------------------------------------
# TUI writes: chat request
# ---------------------------------------------------------------------------

def send_chat_request(prompt: str) -> str:
    """Write a chat request file. Returns the request_id."""
    request_id = str(uuid.uuid4())
    data = {
        "prompt": prompt,
        "request_id": request_id,
        "ts": datetime.now().isoformat(),
    }
    CHAT_REQUEST_FILE.write_text(json.dumps(data), encoding="utf-8")
    return request_id


def read_chat_stream_deltas(offset: int, request_id: str = "") -> tuple[str, bool, int]:
    """Read new chat stream content after byte offset.

    When request_id is provided, only deltas matching that request are returned.
    Stale deltas from previous requests are skipped.

    Returns (new_text, is_done, new_offset).
    """
    try:
        if not CHAT_STREAM_FILE.exists():
            return "", False, offset
        content = CHAT_STREAM_FILE.read_bytes()
        if len(content) <= offset:
            return "", False, offset
        new_content = content[offset:].decode("utf-8", errors="replace")
        new_offset = len(content)
        new_text = ""
        is_done = False
        for line in new_content.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                # Skip deltas from a different request
                if request_id and entry.get("request_id", "") and entry["request_id"] != request_id:
                    continue
                if entry.get("type") == "delta":
                    new_text += entry.get("text", "")
                elif entry.get("type") == "done":
                    is_done = True
            except json.JSONDecodeError:
                pass
        return new_text, is_done, new_offset
    except Exception:
        return "", False, offset


# ---------------------------------------------------------------------------
# Daemon writes: chat stream (daemon-side)
# ---------------------------------------------------------------------------

def write_chat_delta(text: str, request_id: str) -> None:
    """Append a streaming delta to .chat-stream.jsonl (daemon-side, sync)."""
    try:
        line = json.dumps({"type": "delta", "text": text, "request_id": request_id})
        with CHAT_STREAM_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def finish_chat_stream(request_id: str) -> None:
    """Append a done marker to .chat-stream.jsonl (daemon-side)."""
    try:
        line = json.dumps({"type": "done", "request_id": request_id})
        with CHAT_STREAM_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def clear_chat_stream() -> None:
    """Truncate .chat-stream.jsonl before a new chat (daemon-side)."""
    try:
        CHAT_STREAM_FILE.write_text("", encoding="utf-8")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# TUI writes: ask_user response
# ---------------------------------------------------------------------------

def read_pending_question() -> dict | None:
    """Read .pending-question.json if it exists."""
    try:
        if PENDING_QUESTION_FILE.exists():
            return json.loads(PENDING_QUESTION_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


def write_question_response(session_id: str, answer: str) -> None:
    """Write the user's answer to .question-response.json (TUI-side)."""
    try:
        data = {
            "answer": answer,
            "session_id": session_id,
            "ts": datetime.now().isoformat(),
        }
        QUESTION_RESPONSE_FILE.write_text(json.dumps(data), encoding="utf-8")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Daemon writes: pending question + reads response (daemon-side)
# ---------------------------------------------------------------------------

def write_pending_question(question: str, session_id: str) -> None:
    """Write .pending-question.json (daemon-side)."""
    try:
        data = {
            "question": question,
            "session_id": session_id,
            "ts": datetime.now().isoformat(),
        }
        PENDING_QUESTION_FILE.write_text(json.dumps(data), encoding="utf-8")
    except Exception:
        pass


def read_question_response(session_id: str) -> str | None:
    """Read .question-response.json for matching session_id (daemon-side)."""
    try:
        if QUESTION_RESPONSE_FILE.exists():
            data = json.loads(QUESTION_RESPONSE_FILE.read_text(encoding="utf-8"))
            if data.get("session_id") == session_id:
                return data.get("answer", "")
    except Exception:
        pass
    return None


def clear_question_files() -> None:
    """Remove both question IPC files after response received (daemon-side)."""
    for f in (PENDING_QUESTION_FILE, QUESTION_RESPONSE_FILE):
        try:
            f.unlink(missing_ok=True)
        except Exception:
            pass


def make_user_input_handler_file():
    """Create a file-based user input handler (replaces Telegram ask_user relay).

    Writes .pending-question.json; polls .question-response.json every 2s.
    Timeout 120s → returns "no".
    """
    async def handler(request, context):
        question = request.get("question", "")
        session_id = str(uuid.uuid4())
        write_pending_question(question, session_id)

        for _ in range(60):  # 120s at 2s intervals
            await asyncio.sleep(2)
            answer = read_question_response(session_id)
            if answer is not None:
                clear_question_files()
                return {"answer": answer, "wasFreeform": True}

        clear_question_files()
        return {"answer": "no", "wasFreeform": True}

    return handler


# ---------------------------------------------------------------------------
# TUI writes: job queue
# ---------------------------------------------------------------------------

def queue_onboarding_chat() -> None:
    """Queue a chat job flagged as onboarding.

    The worker detects ``_onboarding: true`` and loads the onboarding trigger
    prompt so the agent walks the user through setup.
    """
    try:
        pending_dir = JOBS_DIR / "pending"
        pending_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%dT%H%M%S")
        file_path = pending_dir / f"{ts}-onboarding-tui.yaml"
        data = {
            "type": "chat",
            "prompt": "Let's set up my agent",
            "_onboarding": True,
            "_from_tui": True,
            "_request_id": str(uuid.uuid4()),
            "_source": "tui",
        }
        file_path.write_text(yaml.dump(data, default_flow_style=False), encoding="utf-8")
    except Exception:
        pass


def queue_job(job_type: str) -> None:
    """Write a minimal job YAML to JOBS_DIR/pending/."""
    try:
        pending_dir = JOBS_DIR / "pending"
        pending_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%dT%H%M%S")
        file_path = pending_dir / f"{ts}-{job_type}-tui.yaml"
        data = {"type": job_type, "_source": "tui"}
        file_path.write_text(yaml.dump(data, default_flow_style=False), encoding="utf-8")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# TUI writes: dismiss / note / reply
# ---------------------------------------------------------------------------

def _load_digest_actions() -> dict:
    try:
        if DIGEST_ACTIONS_FILE.exists():
            return json.loads(DIGEST_ACTIONS_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"dismissed": [], "notes": {}}


def _save_digest_actions(actions: dict) -> bool:
    """Save digest actions. Returns True on success, False on failure."""
    try:
        DIGEST_ACTIONS_FILE.write_text(json.dumps(actions, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False


def dismiss_item(
    item_id: str,
    reason: str = "",
    title: str = "",
    source: str = "",
) -> None:
    """Snooze an item (1-day suppress). Comes back tomorrow if still relevant."""
    actions = _load_digest_actions()
    existing_ids = {d.get("item") for d in actions.get("dismissed", [])}
    if item_id not in existing_ids:
        actions.setdefault("dismissed", []).append({
            "item": item_id,
            "title": title,
            "source": source,
            "dismissed_at": datetime.now().isoformat(),
            "reason": reason,
            "status": "dismissed",
        })
        _save_digest_actions(actions)


def archive_item(
    item_id: str,
    title: str = "",
    source: str = "",
) -> None:
    """Permanently archive an item (30-day TTL).

    Works on both already-dismissed items (updates status) and active items
    (creates a new archived entry directly).
    """
    actions = _load_digest_actions()
    found = False
    for d in actions.get("dismissed", []):
        if d.get("item") == item_id:
            d["status"] = "archived"
            d["archived_at"] = datetime.now().isoformat()
            found = True
            break
    if not found:
        actions.setdefault("dismissed", []).append({
            "item": item_id,
            "title": title,
            "source": source,
            "dismissed_at": datetime.now().isoformat(),
            "archived_at": datetime.now().isoformat(),
            "status": "archived",
        })
    _save_digest_actions(actions)


def restore_item(item_id: str) -> None:
    """Remove an item from the dismissed list (un-dismiss)."""
    actions = _load_digest_actions()
    actions["dismissed"] = [
        d for d in actions.get("dismissed", []) if d.get("item") != item_id
    ]
    _save_digest_actions(actions)


def load_dismissed_items() -> list[dict]:
    """Load all dismissed/archived entries for the Dismissed tab."""
    actions = _load_digest_actions()
    return actions.get("dismissed", [])


def add_note(item_id: str, note: str) -> None:
    """Add a note to an item in .digest-actions.json."""
    actions = _load_digest_actions()
    actions.setdefault("notes", {})[item_id] = {
        "note": note,
        "added_at": datetime.now().isoformat(),
    }
    _save_digest_actions(actions)


def write_reply_job(item: dict, draft: str) -> bool:
    """Queue a reply job (teams_send or email_reply) to JOBS_DIR/pending/.

    Returns True on success, False on failure.
    """
    suggested = item.get("suggested_actions", [])
    if not suggested:
        return False

    action = suggested[0]
    action_type = action.get("action_type", "")

    if action_type in ("teams_reply", "teams_send"):
        job: dict = {
            "type": "teams_send",
            "message": draft,
            "_source": "tui",
        }
        if action.get("chat_name"):
            job["chat_name"] = action["chat_name"]
        elif action.get("recipient"):
            job["recipient"] = action["recipient"]
        elif item.get("source") == "teams":
            # Fall back to title as chat name hint
            job["chat_name"] = item.get("title", "")[:50]
    elif action_type == "email_reply":
        job = {
            "type": "email_reply",
            "message": draft,
            "search_query": action.get("search_query", item.get("title", "")),
            "_source": "tui",
        }
    else:
        return False

    try:
        pending_dir = JOBS_DIR / "pending"
        pending_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%dT%H%M%S")
        file_path = pending_dir / f"{ts}-reply-tui.yaml"
        file_path.write_text(yaml.dump(job, default_flow_style=False), encoding="utf-8")
        return True
    except Exception:
        return False
