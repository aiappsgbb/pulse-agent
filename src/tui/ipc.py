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
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path

import yaml

from core.constants import PULSE_HOME, JOBS_DIR, LOGS_DIR

log = logging.getLogger(__name__)

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
        log.debug("Failed to read daemon status", exc_info=True)
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
        log.debug("Failed to write chat delta", exc_info=True)


def finish_chat_stream(request_id: str) -> None:
    """Append a done marker to .chat-stream.jsonl (daemon-side)."""
    try:
        line = json.dumps({"type": "done", "request_id": request_id})
        with CHAT_STREAM_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        log.debug("Failed to finish chat stream", exc_info=True)


def clear_chat_stream() -> None:
    """Truncate .chat-stream.jsonl before a new chat (daemon-side)."""
    try:
        CHAT_STREAM_FILE.write_text("", encoding="utf-8")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Daemon writes: job completion notifications (shown in Chat tab)
# ---------------------------------------------------------------------------

JOB_NOTIFICATION_FILE = PULSE_HOME / ".job-notification.json"


def write_job_notification(job_type: str, summary: str) -> None:
    """Write a job completion notification for the Chat pane to pick up."""
    try:
        data = {
            "job_type": job_type,
            "summary": summary,
            "timestamp": datetime.now().isoformat(),
        }
        JOB_NOTIFICATION_FILE.write_text(json.dumps(data), encoding="utf-8")
    except Exception:
        log.debug("Failed to write job notification", exc_info=True)


def read_job_notification() -> dict | None:
    """Read and delete job notification. Returns None if no notification."""
    try:
        if JOB_NOTIFICATION_FILE.exists():
            data = json.loads(JOB_NOTIFICATION_FILE.read_text(encoding="utf-8"))
            JOB_NOTIFICATION_FILE.unlink(missing_ok=True)
            return data
    except Exception:
        log.debug("Failed to read job notification", exc_info=True)
    return None


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
        log.debug("Failed to write question response", exc_info=True)


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
        log.debug("Failed to write pending question", exc_info=True)


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
        uid = uuid.uuid4().hex[:8]
        file_path = pending_dir / f"{ts}-onboarding-tui-{uid}.yaml"
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
        log.debug("Failed to queue onboarding chat", exc_info=True)


def queue_job(job_type: str, context: str = "") -> None:
    """Write a minimal job YAML to JOBS_DIR/pending/.

    The optional *context* string is included in the YAML and injected into
    the trigger prompt as additional instructions (e.g. project-focused digest).
    """
    try:
        pending_dir = JOBS_DIR / "pending"
        pending_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%dT%H%M%S")
        uid = uuid.uuid4().hex[:8]
        file_path = pending_dir / f"{ts}-{job_type}-tui-{uid}.yaml"
        data: dict = {"type": job_type, "_source": "tui"}
        if context:
            data["context"] = context
        file_path.write_text(yaml.dump(data, default_flow_style=False), encoding="utf-8")
    except Exception:
        log.debug("Failed to queue job %s", job_type, exc_info=True)


# ---------------------------------------------------------------------------
# TUI writes: dismiss / note / reply
# ---------------------------------------------------------------------------

def _load_digest_actions() -> dict:
    try:
        if DIGEST_ACTIONS_FILE.exists():
            return json.loads(DIGEST_ACTIONS_FILE.read_text(encoding="utf-8"))
    except Exception:
        log.debug("Failed to load digest actions", exc_info=True)
    return {"dismissed": [], "notes": {}}


def _save_digest_actions(actions: dict) -> bool:
    """Save digest actions atomically (write-to-tmp-then-rename).

    Both TUI and daemon read/write this file — atomic rename prevents corruption.
    """
    try:
        tmp_path = DIGEST_ACTIONS_FILE.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(actions, indent=2), encoding="utf-8")
        os.replace(tmp_path, DIGEST_ACTIONS_FILE)
        return True
    except Exception:
        log.debug("Failed to save digest actions", exc_info=True)
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


# ---------------------------------------------------------------------------
# Job history (append-only JSONL for Jobs tab)
# ---------------------------------------------------------------------------

JOB_HISTORY_FILE = PULSE_HOME / ".job-history.jsonl"


_JOB_HISTORY_MAX_LINES = 2000  # ~500 jobs worth of events


def append_job_event(
    job_id: str,
    job_type: str,
    status: str,
    detail: str = "",
    log_file: str = "",
) -> None:
    """Append a job lifecycle event to .job-history.jsonl (daemon-side).

    Status values: queued, running, completed, failed.
    Rotates the file when it exceeds _JOB_HISTORY_MAX_LINES.
    """
    try:
        entry = {
            "ts": datetime.now().isoformat(),
            "job_id": job_id,
            "job_type": job_type,
            "status": status,
            "detail": detail,
        }
        if log_file:
            entry["log_file"] = log_file
        with JOB_HISTORY_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")

        # Rotate: keep last N lines when file grows too large
        _maybe_rotate_job_history()
    except Exception:
        pass


def _maybe_rotate_job_history() -> None:
    """Trim .job-history.jsonl to last _JOB_HISTORY_MAX_LINES lines."""
    try:
        if not JOB_HISTORY_FILE.exists():
            return
        lines = JOB_HISTORY_FILE.read_text(encoding="utf-8").splitlines()
        if len(lines) <= _JOB_HISTORY_MAX_LINES:
            return
        # Keep the tail
        trimmed = lines[-_JOB_HISTORY_MAX_LINES:]
        JOB_HISTORY_FILE.write_text("\n".join(trimmed) + "\n", encoding="utf-8")
    except Exception:
        pass


def read_job_history(limit: int = 200) -> list[dict]:
    """Read recent job history events in chronological order.

    Returns raw events for _consolidate_jobs to merge. Chronological order
    is critical — consolidation uses last-write-wins, so newest events must
    come last.
    """
    try:
        if not JOB_HISTORY_FILE.exists():
            return []
        lines = JOB_HISTORY_FILE.read_text(encoding="utf-8").strip().splitlines()
        # Only parse the tail — 4 events per job typical (queued/running/completed/failed)
        tail = lines[-(limit * 4):] if len(lines) > limit * 4 else lines
        entries = []
        for line in tail:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        return entries  # chronological — DO NOT reverse
    except Exception:
        return []


def cleanup_orphaned_jobs() -> int:
    """Mark any 'running' jobs as 'failed' if they have no terminal event.

    Called on daemon startup to clean up jobs that were running when the
    previous daemon instance was killed. Returns the number of jobs cleaned up.
    """
    try:
        if not JOB_HISTORY_FILE.exists():
            return 0

        lines = JOB_HISTORY_FILE.read_text(encoding="utf-8").strip().splitlines()
        entries = []
        for line in lines:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                pass

        # Find jobs whose latest status is "running" — they're orphaned
        latest_status: dict[str, dict] = {}
        for e in entries:
            jid = e.get("job_id", "")
            if jid:
                latest_status[jid] = e

        cleaned = 0
        for jid, event in latest_status.items():
            if event.get("status") == "running":
                append_job_event(
                    jid,
                    event.get("job_type", "unknown"),
                    "failed",
                    "Daemon restarted — job interrupted",
                    log_file=event.get("log_file", ""),
                )
                cleaned += 1

        return cleaned
    except Exception:
        return 0


def read_job_log(log_file: str) -> list[dict]:
    """Read a per-job activity log (tool calls, messages).

    Returns list of log entries for display in Jobs tab detail panel.
    """
    try:
        path = Path(log_file)
        if not path.exists():
            return []
        entries = []
        for line in path.read_text(encoding="utf-8").strip().splitlines():
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        return entries
    except Exception:
        return []


def write_reply_job(item: dict, draft: str) -> bool:
    """Queue a reply job (teams_send or email_reply) to JOBS_DIR/pending/.

    Returns True on success, False on failure.
    """
    suggested = item.get("suggested_actions", [])
    if not suggested:
        return False

    action = suggested[0]
    action_type = action.get("action_type", "")

    # Accept both LLM prompt values (draft_teams_reply, send_email_reply)
    # and internal values (teams_reply, teams_send, email_reply)
    teams_types = ("teams_reply", "teams_send", "draft_teams_reply")
    email_types = ("email_reply", "send_email_reply")

    if action_type in teams_types:
        job: dict = {
            "type": "teams_send",
            "message": draft,
            "_source": "tui",
        }
        # LLM outputs "target", internal uses "chat_name"/"recipient"
        if action.get("chat_name"):
            job["chat_name"] = action["chat_name"]
        elif action.get("recipient"):
            job["recipient"] = action["recipient"]
        elif action.get("target"):
            job["chat_name"] = action["target"]
        elif item.get("source", "").lower().startswith("teams"):
            # Fall back to title as chat name hint
            job["chat_name"] = item.get("title", "")[:50]
    elif action_type in email_types:
        job = {
            "type": "email_reply",
            "message": draft,
            "search_query": action.get("search_query", action.get("target", item.get("title", ""))),
            "_source": "tui",
        }
    else:
        return False

    try:
        pending_dir = JOBS_DIR / "pending"
        pending_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%dT%H%M%S")
        uid = uuid.uuid4().hex[:8]
        file_path = pending_dir / f"{ts}-reply-tui-{uid}.yaml"
        file_path.write_text(yaml.dump(job, default_flow_style=False), encoding="utf-8")
        return True
    except Exception:
        log.exception("write_reply_job failed to write YAML to %s", JOBS_DIR / "pending")
        return False
