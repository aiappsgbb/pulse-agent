"""Textual TUI panes and modals for Pulse Agent.

Panes (used as tab content):
  TodayPane     — interactive landing page (meetings + commitments)
  InboxPane     — unified actionable items (triage + digest, dismissed toggle)
  ProjectsPane  — per-engagement project YAML files with linked items
  ChatPane      — streaming chat with the agent via file IPC

Modals:
  ReplyModal    — review and send a drafted reply
  NoteModal     — add a note to an item
  QuestionModal — answer an ask_user question from the agent
  ProjectStatusModal  — change project status
  CommitmentModal     — mark commitments as done
"""

import json
from datetime import datetime, timedelta

import yaml

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Button, Input, Label, ListItem, ListView, RichLog, Static, TextArea

import re

from core.constants import DIGESTS_DIR, INTEL_DIR, PROJECTS_DIR, PULSE_HOME, TRANSCRIPT_STATUS_FILE
from tui.ipc import (
    add_note,
    archive_item,
    dismiss_item,
    load_dismissed_items,
    read_chat_stream_deltas,
    read_job_history,
    read_job_log,
    read_job_notification,
    read_pending_question,
    restore_item,
    send_chat_request,
    write_question_response,
    write_reply_job,
)

# ---------------------------------------------------------------------------
# Priority display helpers
# ---------------------------------------------------------------------------

PRIORITY_COLORS: dict[str, str] = {
    "urgent": "bold #FF3366",
    "high": "bold #FFB020",
    "medium": "#00D4FF",
    "low": "#5A6A80",
}

ORIGIN_COLORS: dict[str, str] = {
    "triage": "#FF44CC",
    "digest": "#00D4FF",
    "intel": "#AACC00",
}

STATUS_LABELS: dict[str, str] = {
    "dismissed": "SNOOZED",
    "archived": "ARCHIVED",
}

STATUS_COLORS: dict[str, str] = {
    "dismissed": "yellow",
    "archived": "dim",
}


def _priority_markup(priority: str, title: str, source: str = "", origin: str = "") -> str:
    p = priority.upper()
    color = PRIORITY_COLORS.get(priority.lower(), "white")
    src = f"  [dim]{source}[/dim]" if source else ""
    orig = f"  [{ORIGIN_COLORS.get(origin, 'dim')}]{origin}[/{ORIGIN_COLORS.get(origin, 'dim')}]" if origin else ""
    return f"[{color}][{p}][/{color}] {title}{src}{orig}"


def _age_str(iso_ts: str) -> str:
    """Human-readable age from ISO timestamp (e.g. '2h', '3d')."""
    try:
        dt = datetime.fromisoformat(iso_ts)
        delta = datetime.now() - dt
        if delta.days > 0:
            return f"{delta.days}d"
        hours = delta.seconds // 3600
        return f"{hours}h" if hours else "<1h"
    except Exception:
        return "?"


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------

def _load_triage_items() -> list[dict]:
    """Load items from the latest monitoring JSON."""
    files = sorted(PULSE_HOME.glob("monitoring-*.json"), reverse=True)
    if not files:
        return []
    try:
        data = json.loads(files[0].read_text(encoding="utf-8"))
        return data.get("items", [])
    except Exception:
        return []


def _load_digest_items() -> list[dict]:
    """Load items from the latest digest JSON."""
    if not DIGESTS_DIR.exists():
        return []
    files = sorted(DIGESTS_DIR.glob("*.json"), reverse=True)
    if not files:
        return []
    try:
        data = json.loads(files[0].read_text(encoding="utf-8"))
        return data.get("items", [])
    except Exception:
        return []


def _load_intel_items() -> list[dict]:
    """Load intel brief sections as inbox-compatible items.

    Parses the latest intel markdown and creates one inbox item per section
    (e.g., Moves & Announcements, Trends, Watch List). Each item contains
    the section's bullet points in its summary.
    """
    if not INTEL_DIR.exists():
        return []
    files = sorted(INTEL_DIR.glob("*.md"), reverse=True)
    if not files:
        return []
    try:
        text = files[0].read_text(encoding="utf-8")
        date = files[0].stem  # YYYY-MM-DD
        lines = text.strip().split("\n")
        sections: list[dict] = []
        current: dict | None = None
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("## "):
                current = {"title": stripped.lstrip("# ").strip(), "items": []}
                sections.append(current)
            elif current and (stripped.startswith("- ") or stripped.startswith("* ")):
                # Remove leading "- " or "* " prefix only (not all - and * chars)
                bullet_text = stripped[2:].strip() if len(stripped) > 2 else ""
                current["items"].append(bullet_text)

        result: list[dict] = []
        for section in sections:
            if not section["items"]:
                continue
            # Build readable summary from bullets
            summary_lines = []
            for bullet in section["items"]:
                # Convert **Name** to plain bold for display
                clean = re.sub(r"\*\*(.+?)\*\*", r"\1", bullet)
                summary_lines.append(f"- {clean}")
            result.append({
                "id": f"intel-{date}-{section['title'].lower().replace(' ', '-')}",
                "type": "intel",
                "priority": "low",
                "source": f"Intel Brief ({date})",
                "title": f"Intel: {section['title']}",
                "summary": "\n".join(summary_lines),
                "date": date,
                "status": "outstanding",
                "_origin": "intel",
            })
        return result
    except Exception:
        return []


def _load_digest_summary() -> dict | None:
    """Load latest digest summary for the Today briefing line."""
    if not DIGESTS_DIR.exists():
        return None
    files = sorted(DIGESTS_DIR.glob("*.json"), reverse=True)
    if not files:
        return None
    try:
        data = json.loads(files[0].read_text(encoding="utf-8"))
        items = data.get("items", [])
        outstanding = len([i for i in items if i.get("status") == "outstanding"])
        new = len([i for i in items if i.get("_origin") == "new" or i.get("is_new")])
        resolved = len([i for i in items if i.get("status") == "resolved"])
        return {
            "date": files[0].stem,
            "outstanding": outstanding,
            "new": new,
            "resolved": resolved,
            "total": len(items),
        }
    except Exception:
        return None


def _load_transcript_status() -> dict | None:
    """Load transcript collection status."""
    try:
        if TRANSCRIPT_STATUS_FILE.exists():
            data = json.loads(TRANSCRIPT_STATUS_FILE.read_text(encoding="utf-8"))
            return data
    except Exception:
        pass
    return None


def _load_projects() -> list[dict]:
    """Load all project YAML files."""
    if not PROJECTS_DIR.exists():
        return []
    projects = []
    for path in sorted(PROJECTS_DIR.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data["_id"] = path.stem
                projects.append(data)
        except Exception:
            pass
    return projects


# ---------------------------------------------------------------------------
# Today view data loaders
# ---------------------------------------------------------------------------

_CALENDAR_SCAN_FILE = PULSE_HOME / ".calendar-scan.json"

# Day-of-week abbreviations used in calendar date strings (e.g. "Monday, March 3, 2026")
_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}


def _parse_calendar_date(date_str: str) -> str | None:
    """Parse human-readable calendar date to ISO YYYY-MM-DD.

    Handles formats like:
      "Monday, March 3, 2026"
      "Thursday, February 20, 2026"
      "March 3, 2026"
    """
    import re
    # Try to find "Month Day, Year" anywhere in the string
    m = re.search(r"([A-Za-z]+)\s+(\d+),?\s*(\d{4})", date_str)
    if m:
        month_name = m.group(1).lower()
        day = int(m.group(2))
        year = int(m.group(3))
        month = _MONTHS.get(month_name)
        if month:
            return f"{year:04d}-{month:02d}-{day:02d}"
    return None


def _load_calendar_events() -> tuple[list[dict], str]:
    """Load persisted calendar scan events.

    Returns (events, scanned_at) where scanned_at is an ISO timestamp or "".
    """
    try:
        if _CALENDAR_SCAN_FILE.exists():
            data = json.loads(_CALENDAR_SCAN_FILE.read_text(encoding="utf-8"))
            if data.get("available"):
                return data.get("events", []), data.get("scanned_at", "")
    except Exception:
        pass
    return [], ""


def _filter_today_events(events: list[dict]) -> list[dict]:
    """Filter calendar events to today only, sorted by start_time."""
    today_iso = datetime.now().strftime("%Y-%m-%d")
    today_events = []
    for ev in events:
        if ev.get("is_declined"):
            continue
        date_str = ev.get("date", "")
        iso = _parse_calendar_date(date_str) if date_str else None
        if iso == today_iso:
            today_events.append(ev)
    # Sort by start_time (e.g. "9:00 AM", "10:30 AM")
    def _time_sort_key(ev):
        t = ev.get("start_time", "")
        try:
            return datetime.strptime(t, "%I:%M %p")
        except (ValueError, TypeError):
            return datetime.max
    today_events.sort(key=_time_sort_key)
    return today_events


def _get_due_commitments(projects: list[dict], days_ahead: int = 7) -> list[dict]:
    """Get commitments due today or within days_ahead, from all projects.

    Returns list of {what, who, to, due, status, project_name, project_id, is_today}.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    cutoff = (datetime.now() + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
    result = []
    for p in projects:
        proj_name = p.get("project", p.get("_id", "?"))
        proj_id = p.get("_id", "")
        for c in p.get("commitments", []):
            status = c.get("status", "").lower()
            if status in ("done", "cancelled"):
                continue
            due_raw = c.get("due", "")
            if not due_raw:
                continue
            # YAML may parse dates as datetime.date — normalize to string
            due = str(due_raw) if not isinstance(due_raw, str) else due_raw
            # Include overdue (past) and upcoming (within window)
            if due <= cutoff:
                result.append({
                    "what": c.get("what", "?"),
                    "who": c.get("who", ""),
                    "to": c.get("to", ""),
                    "due": due,
                    "status": status,
                    "project_name": proj_name,
                    "project_id": proj_id,
                    "is_today": due == today,
                    "is_overdue": due < today and status != "overdue",
                })
    # Sort: overdue first, then today, then by date
    result.sort(key=lambda c: (
        0 if c["due"] < today else (1 if c["is_today"] else 2),
        c["due"],
    ))
    return result


def _match_meeting_to_project(event: dict, projects: list[dict]) -> dict | None:
    """Find the project linked to a calendar event by stakeholder/title matching."""
    title = event.get("title", "").lower()
    organizer = (event.get("organizer") or "").lower()
    for p in projects:
        stakeholder_names = [s.get("name", "").lower() for s in p.get("stakeholders", [])]
        proj_name = p.get("project", "").lower()
        if (
            any(sn in title for sn in stakeholder_names if sn)
            or any(sn in organizer for sn in stakeholder_names if sn)
            or proj_name in title
        ):
            return p
    return None


def _build_prep_hints(project: dict) -> str:
    """Build a Rich-markup prep hint string from a project's commitments."""
    commitments = project.get("commitments", [])
    overdue = sum(1 for c in commitments if c.get("status", "").lower() == "overdue")
    open_items = sum(1 for c in commitments if c.get("status", "").lower() == "open")
    if overdue:
        return f"[red]({overdue} overdue)[/red]"
    if open_items:
        return f"[yellow]({open_items} open)[/yellow]"
    return ""


def _load_today_items(
    projects: list[dict] | None = None,
) -> tuple[list[dict], int, int]:
    """Load today's meetings and due commitments as unified item dicts.

    Returns (items, meeting_count, commitment_count).
    Items sorted: meetings first (by time), then commitments (by urgency).
    """
    if projects is None:
        projects = _load_projects()

    items: list[dict] = []
    today_iso = datetime.now().strftime("%Y-%m-%d")

    # --- Meetings ---
    events, scanned_at = _load_calendar_events()
    today_events = _filter_today_events(events)
    for ev in today_events:
        linked = _match_meeting_to_project(ev, projects)
        prep = _build_prep_hints(linked) if linked else ""
        items.append({
            **ev,
            "_type": "meeting",
            "_linked_project": linked,
            "_linked_project_id": linked.get("_id", "") if linked else "",
            "_prep_hints": prep,
            "_scanned_at": scanned_at,
        })

    meeting_count = len(items)

    # --- Commitments ---
    due_items = _get_due_commitments(projects, days_ahead=7)
    for c in due_items:
        # Find the full project dict for this commitment
        linked = None
        for p in projects:
            if p.get("_id") == c.get("project_id"):
                linked = p
                break
        # Urgency tag + color
        if c["due"] < today_iso:
            tag, color = "OVERDUE", "red"
        elif c["is_today"]:
            tag, color = "DUE TODAY", "bold yellow"
        else:
            tag, color = f"DUE {c['due']}", "yellow"
        items.append({
            **c,
            "_type": "commitment",
            "_linked_project": linked,
            "_urgency_tag": tag,
            "_urgency_color": color,
        })

    commitment_count = len(items) - meeting_count
    return items, meeting_count, commitment_count


# ---------------------------------------------------------------------------
# Project sort modes
# ---------------------------------------------------------------------------

_STATUS_ORDER = {"active": 0, "blocked": 1, "on-hold": 2, "completed": 3}
_RISK_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}

PROJECT_SORT_MODES = ["urgency", "next_meeting", "status", "alphabetical"]
PROJECT_SORT_LABELS = {
    "urgency": "Urgency (overdue/risk)",
    "next_meeting": "Next meeting",
    "status": "Status",
    "alphabetical": "A-Z",
}


def _overdue_count(project: dict) -> int:
    return sum(
        1 for c in project.get("commitments", [])
        if c.get("status", "").lower() == "overdue"
    )


def _sort_projects(projects: list[dict], mode: str) -> list[dict]:
    """Sort projects by the given mode. Returns a new list."""
    if mode == "urgency":
        # Overdue count desc, then risk severity, then status, then alpha
        return sorted(projects, key=lambda p: (
            -_overdue_count(p),
            _RISK_ORDER.get(p.get("risk_level", "medium").lower(), 2),
            _STATUS_ORDER.get(p.get("status", "active").lower(), 0),
            p.get("project", p.get("_id", "")).lower(),
        ))
    elif mode == "next_meeting":
        # Soonest next_meeting first, nulls last
        def _meeting_key(p):
            nm = p.get("next_meeting", "")
            return (0, nm) if nm else (1, "")
        return sorted(projects, key=_meeting_key)
    elif mode == "status":
        # Status priority, then alpha within group
        return sorted(projects, key=lambda p: (
            _STATUS_ORDER.get(p.get("status", "active").lower(), 0),
            p.get("project", p.get("_id", "")).lower(),
        ))
    else:  # alphabetical
        return sorted(projects, key=lambda p: p.get("project", p.get("_id", "")).lower())


# ---------------------------------------------------------------------------
# Inbox data merging
# ---------------------------------------------------------------------------

_PRIORITY_ORDER = {"urgent": 0, "high": 1, "medium": 2, "low": 3}


def _inbox_sort_key(item: dict) -> tuple:
    """Sort: urgent first, then high; triage before digest before intel."""
    priority = item.get("priority", "medium").lower()
    origin = item.get("_origin", "digest")
    origin_order = {"triage": 0, "digest": 1, "intel": 2}.get(origin, 1)
    return (
        _PRIORITY_ORDER.get(priority, 2),
        origin_order,
        item.get("title", ""),
    )


def _load_inbox_items(include_dismissed: bool = False) -> tuple[list[dict], int]:
    """Merge triage + digest + intel items, dedup by ID, optionally include dismissed.

    Returns (items, dismissed_count).
    """
    triage_items = _load_triage_items()
    digest_items = _load_digest_items()
    intel_items = _load_intel_items()
    dismissed = load_dismissed_items()
    dismissed_ids = {d.get("item") for d in dismissed}

    # Tag items with origin
    for item in triage_items:
        item["_origin"] = "triage"
    for item in digest_items:
        item["_origin"] = "digest"
    # intel_items already tagged with _origin = "intel" by _load_intel_items

    # Merge: triage first (more recent), then digest, then intel. Dedup by ID.
    seen_ids: set[str] = set()
    active: list[dict] = []
    for item in triage_items:
        item_id = item.get("id", "")
        if item_id and item_id in dismissed_ids:
            continue
        if item_id:
            seen_ids.add(item_id)
        active.append(item)
    for item in digest_items:
        item_id = item.get("id", "")
        if item_id and (item_id in seen_ids or item_id in dismissed_ids):
            continue
        if item_id:
            seen_ids.add(item_id)
        active.append(item)
    for item in intel_items:
        item_id = item.get("id", "")
        if item_id and (item_id in seen_ids or item_id in dismissed_ids):
            continue
        if item_id:
            seen_ids.add(item_id)
        active.append(item)

    active.sort(key=_inbox_sort_key)

    dismissed_count = len(dismissed)

    if include_dismissed and dismissed:
        # Append dismissed items at the bottom
        for d in dismissed:
            d["_origin"] = "dismissed"
            d["_is_dismissed"] = True
            # Ensure basic fields for display
            d.setdefault("priority", "low")
            d.setdefault("title", d.get("item", "?"))
        active.extend(dismissed)

    return active, dismissed_count


# ---------------------------------------------------------------------------
# Modals
# ---------------------------------------------------------------------------

class HelpModal(ModalScreen):
    """Quick-reference keybindings modal."""

    BINDINGS = [
        Binding("escape", "dismiss_modal", "Close"),
        Binding("question_mark", "dismiss_modal", "Close"),
    ]

    def compose(self) -> ComposeResult:
        with Widget(id="help-dialog"):
            yield Static(
                "[bold]Pulse Agent — Keyboard Shortcuts[/bold]\n"
                "\n"
                "[bold cyan]Jobs[/bold cyan]\n"
                "  Ctrl+D  Queue digest        Ctrl+T  Queue triage\n"
                "  Ctrl+I  Queue intel          Ctrl+X  Queue transcripts\n"
                "\n"
                "[bold cyan]Navigation[/bold cyan]\n"
                "  Ctrl+L  Jump to Inbox        Ctrl+J  Jump to Jobs\n"
                "  Ctrl+R  Refresh all           Ctrl+H  Toggle dismissed\n"
                "  Ctrl+E  Clear chat            Ctrl+P  Command palette\n"
                "  Q       Quit\n"
                "\n"
                "[bold cyan]Today actions[/bold cyan]\n"
                "  C  Complete commitment       R  Queue research\n"
                "  D  Queue focused digest      N  Add note\n"
                "\n"
                "[bold cyan]Inbox actions[/bold cyan]\n"
                "  D  Snooze (1 day)            A  Archive (30 days)\n"
                "  R  Reply / Restore           N  Add note\n"
                "  M  Mark as read         Ctrl+M  Sweep all low-priority\n"
                "\n"
                "[bold cyan]Projects actions[/bold cyan]\n"
                "  S  Cycle sort mode           U  Update status\n"
                "  C  Complete commitment       R  Queue research\n"
                "  D  Queue focused digest      N  Add note\n"
                "\n"
                "[dim]Press Esc or ? to close[/dim]"
            )

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)


class ReplyModal(ModalScreen):
    """Modal for reviewing and sending a reply draft."""

    BINDINGS = [
        Binding("ctrl+s", "send_reply", "Send"),
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, item: dict, **kwargs):
        super().__init__(**kwargs)
        self._item = item
        suggested = item.get("suggested_actions", [])
        self._draft = suggested[0].get("draft", "") if suggested else ""

    def compose(self) -> ComposeResult:
        with Widget(id="reply-dialog"):
            yield Label("Review and send reply", id="reply-title")
            yield Label(f"Re: {self._item.get('title', '')[:80]}", id="reply-subject")
            yield TextArea(self._draft, id="reply-text")
            with Horizontal(id="reply-buttons"):
                yield Button("Send (Ctrl+S)", id="btn-send", variant="primary")
                yield Button("Cancel (Esc)", id="btn-cancel")

    def on_mount(self) -> None:
        self.call_after_refresh(self._focus_editor)

    def _focus_editor(self) -> None:
        try:
            self.query_one(TextArea).focus()
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-send":
            self.action_send_reply()
        else:
            self.dismiss(None)

    def action_send_reply(self) -> None:
        text_area = self.query_one(TextArea)
        draft = text_area.text
        if draft.strip():
            if write_reply_job(self._item, draft):
                # Auto-dismiss: item is handled, remove from inbox
                item_id = self._item.get("id", "")
                if item_id:
                    archive_item(
                        item_id,
                        title=self._item.get("title", ""),
                        source=self._item.get("source", ""),
                    )
                self.dismiss("sent")
            else:
                self.dismiss("error")
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class NoteModal(ModalScreen):
    """Modal for adding a note to an item.

    When allow_empty=True (used after dismiss/archive), pressing Enter with
    no text skips the note instead of doing nothing. This enables the
    "dismiss + optional note" workflow.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, item_id: str, allow_empty: bool = False, title: str = "Add note", **kwargs):
        super().__init__(**kwargs)
        self._item_id = item_id
        self._allow_empty = allow_empty
        self._title = title

    def compose(self) -> ComposeResult:
        with Widget(id="note-dialog"):
            yield Label(self._title, id="note-title")
            placeholder = "Enter note (Enter to skip)..." if self._allow_empty else "Enter note text..."
            yield Input(placeholder=placeholder, id="note-input")
            with Horizontal(id="note-buttons"):
                label = "Save / Skip (Enter)" if self._allow_empty else "Save (Enter)"
                yield Button(label, id="btn-save", variant="primary")
                yield Button("Cancel (Esc)", id="btn-cancel")

    def on_mount(self) -> None:
        self.call_after_refresh(self._focus_input)

    def _focus_input(self) -> None:
        try:
            self.query_one(Input).focus()
        except Exception:
            pass

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._save()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-save":
            self._save()
        else:
            self.dismiss(None)

    def _save(self) -> None:
        note_text = self.query_one(Input).value.strip()
        if note_text:
            add_note(self._item_id, note_text)
            self.dismiss("saved")
        elif self._allow_empty:
            self.dismiss("skipped")  # Empty note on dismiss/archive — still counts as done
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class QuestionModal(ModalScreen):
    """Modal for answering an ask_user question from the agent."""

    BINDINGS = [
        Binding("escape", "skip", "Skip (no)"),
    ]

    def __init__(self, question: str, session_id: str, **kwargs):
        super().__init__(**kwargs)
        self._question = question
        self._session_id = session_id

    def compose(self) -> ComposeResult:
        with Widget(id="question-dialog"):
            yield Label("Agent needs your input", id="question-title")
            yield Static(self._question, id="question-text")
            yield Input(placeholder="Your answer...", id="question-input")
            with Horizontal(id="question-buttons"):
                yield Button("Answer (Enter)", id="btn-answer", variant="primary")
                yield Button("Skip / No (Esc)", id="btn-skip")

    def on_mount(self) -> None:
        self.call_after_refresh(self._focus_input)

    def _focus_input(self) -> None:
        try:
            self.query_one(Input).focus()
        except Exception:
            pass

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._answer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-answer":
            self._answer()
        else:
            write_question_response(self._session_id, "no")
            self.dismiss(None)

    def _answer(self) -> None:
        answer = self.query_one(Input).value.strip()
        write_question_response(self._session_id, answer or "no")
        self.dismiss(answer)

    def action_skip(self) -> None:
        write_question_response(self._session_id, "no")
        self.dismiss(None)


class ProjectStatusModal(ModalScreen):
    """Modal for changing a project's status."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, project: dict, **kwargs):
        super().__init__(**kwargs)
        self._project = project

    def compose(self) -> ComposeResult:
        name = self._project.get("project", "?")
        current = self._project.get("status", "active")
        with Widget(id="status-dialog"):
            yield Label(f"Update status: {name}", id="status-title")
            yield Label(f"Current: [bold]{current}[/bold]", id="status-current")
            with Horizontal(id="status-buttons"):
                for s in ("active", "blocked", "on-hold", "completed"):
                    variant = "primary" if s == current else "default"
                    yield Button(s.capitalize(), id=f"btn-{s}", variant=variant)
            yield Button("Cancel (Esc)", id="btn-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "btn-cancel":
            self.dismiss(None)
        elif bid.startswith("btn-"):
            new_status = bid[4:]  # strip "btn-"
            if new_status in ("active", "blocked", "on-hold", "completed"):
                self.dismiss(new_status)

    def action_cancel(self) -> None:
        self.dismiss(None)


class CommitmentModal(ModalScreen):
    """Modal for marking commitments as done."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, project: dict, **kwargs):
        super().__init__(**kwargs)
        self._project = project
        # Only show open/overdue commitments
        self._actionable = [
            c for c in project.get("commitments", [])
            if c.get("status", "").lower() in ("open", "overdue")
        ]

    def compose(self) -> ComposeResult:
        name = self._project.get("project", "?")
        with Widget(id="commitment-dialog"):
            yield Label(f"Complete commitments: {name}", id="commitment-title")
            if not self._actionable:
                yield Label("[dim]No open or overdue commitments[/dim]")
            else:
                yield Label("[dim]Select a commitment to mark as done:[/dim]")
                for i, c in enumerate(self._actionable):
                    what = c.get("what", "?")
                    due = c.get("due", "")
                    status = c.get("status", "open").upper()
                    color = "red" if status == "OVERDUE" else "yellow"
                    label = f"[{color}][{status}][/{color}] {what}"
                    if due:
                        label += f"  (due: {due})"
                    yield Button(label, id=f"btn-c-{i}")
            yield Button("Cancel (Esc)", id="btn-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "btn-cancel":
            self.dismiss(None)
        elif bid.startswith("btn-c-"):
            try:
                idx = int(bid[6:])
                if 0 <= idx < len(self._actionable):
                    self.dismiss(idx)
            except ValueError:
                pass

    def action_cancel(self) -> None:
        self.dismiss(None)


# ---------------------------------------------------------------------------
# Base item pane (shared logic)
# ---------------------------------------------------------------------------

class ItemPane(Widget):
    """Base widget for item views with list + detail layout.

    Subclasses override _load_items() to provide data.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._items: list[dict] = []
        self._selected_idx: int = 0

    def _load_items(self) -> list[dict]:
        """Override in subclass to load items from disk."""
        return []

    def _title_width(self) -> int:
        """Max character width for titles in list items."""
        try:
            w = self.size.width
            return max(25, w - 30) if w > 30 else 55
        except Exception:
            return 55

    def compose(self) -> ComposeResult:
        yield ListView()
        with VerticalScroll(classes="detail-container"):
            yield Static("[dim]Select an item to view details[/dim]")

    def on_mount(self) -> None:
        self.load_data()

    def load_data(self) -> None:
        """Reload items from disk and refresh the list."""
        self._items = self._load_items()
        self._refresh_list()

    def _refresh_list(self) -> None:
        lv = self.query_one(ListView)
        lv.clear()
        if not self._items:
            lv.append(ListItem(Label("[dim]No items — press ^T triage or ^D digest to fetch[/dim]")))
            return
        tw = self._title_width()
        for item in self._items:
            priority = item.get("priority", "?")
            title = item.get("title", "?")[:tw]
            source = item.get("source", "")
            text = _priority_markup(priority, title, source)
            lv.append(ListItem(Label(text)))

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.item is None:
            return
        idx = event.list_view.index
        if idx is not None and 0 <= idx < len(self._items):
            self._selected_idx = idx
            self._show_detail(self._items[idx])

    def _show_detail(self, item: dict) -> None:
        detail = self.query_one(Static)
        priority = item.get("priority", "?").upper()
        color = PRIORITY_COLORS.get(item.get("priority", "").lower(), "white")

        lines = [
            f"[{color}][{priority}][/{color}] [bold]{item.get('title', '')}[/bold]",
            f"Source: {item.get('source', '?')}  |  Date: {item.get('date', '?')}",
            "",
            item.get("summary", ""),
        ]

        suggested = item.get("suggested_actions", [])
        if suggested:
            lines += ["", "[bold]Suggested actions:[/bold]"]
            for a in suggested:
                label = a.get("label", a.get("action_type", "?"))
                draft = a.get("draft", "")
                lines.append(f"  [{label}]")
                if draft:
                    preview = draft[:120] + ("..." if len(draft) > 120 else "")
                    lines.append(f"  [dim]{preview}[/dim]")

        detail.update("\n".join(lines))

    def get_selected_item(self) -> dict | None:
        if 0 <= self._selected_idx < len(self._items):
            return self._items[self._selected_idx]
        return None

    def dismiss_selected(self) -> None:
        item = self.get_selected_item()
        if item:
            dismiss_item(
                item.get("id", ""),
                reason="",
                title=item.get("title", ""),
                source=item.get("source", ""),
            )
            self._items.pop(self._selected_idx)
            self._selected_idx = max(0, self._selected_idx - 1)
            self._refresh_list()
            self.notify("Item snoozed (comes back tomorrow if still relevant)")

    def reply_selected(self) -> None:
        item = self.get_selected_item()
        if item and item.get("suggested_actions"):
            self.app.push_screen(ReplyModal(item), self._on_reply_result)
        elif item:
            self.notify("No suggested reply for this item", severity="warning")

    def note_selected(self) -> None:
        item = self.get_selected_item()
        if item:
            self.app.push_screen(NoteModal(item.get("id", "")), self._on_note_result)

    def _on_reply_result(self, result) -> None:
        if result == "sent":
            # Remove item from list (auto-archived by ReplyModal)
            if 0 <= self._selected_idx < len(self._items):
                self._items.pop(self._selected_idx)
                self._selected_idx = max(0, self._selected_idx - 1)
                self._refresh_list()
            self.notify("Reply queued — check Jobs tab for status")
        elif result == "error":
            self.notify("Failed to queue reply — check PULSE_HOME/jobs/", severity="error")

    def _on_note_result(self, result) -> None:
        if result == "saved":
            self.notify("Note saved")


# ---------------------------------------------------------------------------
# Inbox pane (replaces Triage + Digest + Dismissed)
# ---------------------------------------------------------------------------

class InboxPane(ItemPane):
    """Unified inbox: triage + digest items, dismissed toggle (Ctrl+H)."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._show_dismissed: bool = False
        self._dismissed_count: int = 0
        self._list_to_item: list[int] = []  # Maps ListView index -> _items index (-1 for separator)

    def compose(self) -> ComposeResult:
        yield ListView()
        with VerticalScroll(classes="detail-container"):
            yield Static("[dim]Select an item to view details[/dim]")

    def _load_items(self) -> list[dict]:
        items, self._dismissed_count = _load_inbox_items(
            include_dismissed=self._show_dismissed,
        )
        return items

    def load_data(self) -> None:
        """Reload items from disk and refresh list."""
        self._items = self._load_items()
        self._refresh_list()

    def _refresh_list(self) -> None:
        lv = self.query_one(ListView)
        lv.clear()
        self._list_to_item = []
        tw = self._title_width()

        if not self._items:
            lv.append(ListItem(Label(
                "[dim]Inbox empty — press ^T to run triage or ^D for digest[/dim]"
            )))
            self._list_to_item.append(-1)
            return

        separator_added = False
        for i, item in enumerate(self._items):
            # Insert visual separator before first dismissed item
            if item.get("_is_dismissed") and not separator_added:
                lv.append(ListItem(Label(
                    "[dim]──── Dismissed ─────────────────────────────────[/dim]"
                )))
                self._list_to_item.append(-1)
                separator_added = True

            if item.get("_is_dismissed"):
                status = item.get("status", "archived")
                label = STATUS_LABELS.get(status, "ARCHIVED")
                color = STATUS_COLORS.get(status, "dim")
                title = (item.get("title") or item.get("item", "?"))[:tw]
                age = _age_str(item.get("dismissed_at", ""))
                text = f"[{color}][{label}][/{color}] {title}  [dim]({age} ago)[/dim]"
            else:
                priority = item.get("priority", "?")
                title = item.get("title", "?")[:tw]
                source = item.get("source", "")
                origin = item.get("_origin", "")
                project = item.get("project", "")
                proj_tag = f"  [cyan][{project}][/cyan]" if project else ""
                text = _priority_markup(priority, title, source, origin) + proj_tag

            lv.append(ListItem(Label(text)))
            self._list_to_item.append(i)

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        """Override to handle separator items in the ListView index mapping."""
        if event.item is None:
            return
        idx = event.list_view.index
        if idx is not None and 0 <= idx < len(self._list_to_item):
            item_idx = self._list_to_item[idx]
            if item_idx >= 0 and item_idx < len(self._items):
                self._selected_idx = item_idx
                self._show_detail(self._items[item_idx])

    def _show_detail(self, item: dict) -> None:
        if item.get("_is_dismissed"):
            self._show_dismissed_detail(item)
        else:
            self._show_active_detail(item)

    def _show_active_detail(self, item: dict) -> None:
        detail = self.query_one(Static)
        priority = item.get("priority", "?").upper()
        color = PRIORITY_COLORS.get(item.get("priority", "").lower(), "white")
        origin = item.get("_origin", "")
        origin_tag = f"  [{ORIGIN_COLORS.get(origin, 'dim')}]{origin}[/{ORIGIN_COLORS.get(origin, 'dim')}]" if origin else ""

        lines = [
            f"[{color}][{priority}][/{color}]{origin_tag} [bold]{item.get('title', '')}[/bold]",
            f"Source: {item.get('source', '?')}  |  Date: {item.get('date', '?')}",
        ]

        project = item.get("project", "")
        if project:
            lines.append(f"Project: [cyan]{project}[/cyan]")

        lines += ["", item.get("summary", "")]

        suggested = item.get("suggested_actions", [])
        if suggested:
            lines += ["", "[bold]Suggested actions:[/bold]"]
            for a in suggested:
                label = a.get("label", a.get("action_type", "?"))
                draft = a.get("draft", "")
                lines.append(f"  [{label}]")
                if draft:
                    preview = draft[:120] + ("..." if len(draft) > 120 else "")
                    lines.append(f"  [dim]{preview}[/dim]")

        detail.update("\n".join(lines))

    def _show_dismissed_detail(self, item: dict) -> None:
        detail = self.query_one(Static)
        status = item.get("status", "archived")
        label = STATUS_LABELS.get(status, "ARCHIVED")
        color = STATUS_COLORS.get(status, "dim")
        title = item.get("title") or item.get("item", "?")
        age = _age_str(item.get("dismissed_at", ""))

        lines = [
            f"[{color}][{label}][/{color}] [bold]{title}[/bold]",
            f"Source: {item.get('source', '?')}  |  Dismissed: {age} ago",
        ]
        reason = item.get("reason", "")
        if reason:
            lines.append(f"Reason: {reason}")

        if status == "dismissed":
            lines += [
                "",
                "[yellow]Snoozed for today — comes back tomorrow if still relevant.[/yellow]",
            ]
        else:
            lines += [
                "",
                "[dim]Archived (30-day expiry).[/dim]",
            ]
        detail.update("\n".join(lines))

    def toggle_dismissed(self) -> None:
        """Toggle showing/hiding dismissed items."""
        self._show_dismissed = not self._show_dismissed
        self.load_data()
        if self._show_dismissed:
            self.notify(f"Showing {self._dismissed_count} dismissed items")
        else:
            hidden = self._dismissed_count
            self.notify(f"Hiding {hidden} dismissed items" if hidden else "No dismissed items")

    def dismiss_selected(self) -> None:
        item = self.get_selected_item()
        if not item or item.get("_is_dismissed"):
            return
        item_id = item.get("id", "")
        dismiss_item(
            item_id,
            reason="",
            title=item.get("title", ""),
            source=item.get("source", ""),
        )
        self._items.pop(self._selected_idx)
        self._selected_idx = max(0, self._selected_idx - 1)
        self._refresh_list()
        self.notify("Item snoozed — add a note? (Enter to skip)")
        # Chain NoteModal for optional note
        if item_id:
            self.app.push_screen(
                NoteModal(item_id, allow_empty=True, title="Add note (optional)"),
                self._on_dismiss_note_result,
            )

    def _on_dismiss_note_result(self, result) -> None:
        if result == "saved":
            self.notify("Note saved")

    def archive_selected(self) -> None:
        """Archive an item (works on both active and dismissed items)."""
        item = self.get_selected_item()
        if not item:
            return
        if item.get("_is_dismissed"):
            item_id = item.get("item", "")
            archive_item(item_id)
            item["status"] = "archived"
            self._refresh_list()
        else:
            item_id = item.get("id", "")
            archive_item(
                item_id,
                title=item.get("title", ""),
                source=item.get("source", ""),
            )
            self._items.pop(self._selected_idx)
            self._selected_idx = max(0, self._selected_idx - 1)
            self._refresh_list()
        self.notify("Item archived — add a note? (Enter to skip)")
        # Chain NoteModal for optional note
        if item_id:
            self.app.push_screen(
                NoteModal(item_id, allow_empty=True, title="Add note (optional)"),
                self._on_dismiss_note_result,
            )

    def mark_read_selected(self) -> None:
        """Mark the selected item as read in its source app (Teams/Outlook)."""
        item = self.get_selected_item()
        if not item or item.get("_is_dismissed"):
            return
        source = item.get("source", "")
        if not source:
            self.notify("Cannot determine source for this item", severity="warning")
            return
        from tui.ipc import queue_mark_read_job
        if queue_mark_read_job(item):
            # Also archive it in the TUI
            item_id = item.get("id", "")
            archive_item(
                item_id,
                title=item.get("title", ""),
                source=source,
            )
            self._items.pop(self._selected_idx)
            self._selected_idx = max(0, self._selected_idx - 1)
            self._refresh_list()
            title = item.get("title", source)[:40]
            self.notify(f"Marking as read: {title}")
        else:
            self.notify("Only Teams and Email items can be marked as read", severity="warning")

    def restore_selected(self) -> None:
        """Restore a dismissed item (only works on dismissed items)."""
        item = self.get_selected_item()
        if item and item.get("_is_dismissed"):
            restore_item(item.get("item", ""))
            self._items.pop(self._selected_idx)
            self._selected_idx = max(0, self._selected_idx - 1)
            self._refresh_list()
            self.notify("Item restored — will appear in next triage/digest")

    def is_dismissed_selected(self) -> bool:
        """Check if the currently selected item is a dismissed item."""
        item = self.get_selected_item()
        return bool(item and item.get("_is_dismissed"))


# ---------------------------------------------------------------------------
# Today pane (interactive landing page — meetings + commitments)
# ---------------------------------------------------------------------------


class TodayPane(Widget):
    """Today landing page: meetings timeline + due commitments.

    Shows today's calendar events and commitments due today/upcoming,
    enriched with linked project context and prep hints.

    Actions:
      C — complete a commitment (mark done in project YAML)
      R — queue research job for linked project
      D — queue focused digest for linked project
      N — add note to commitment
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._items: list[dict] = []
        self._list_to_item: list[int] = []  # ListView index -> _items index (-1 = separator)
        self._selected_idx: int = 0
        self._meeting_count: int = 0
        self._commitment_count: int = 0
        self._projects: list[dict] = []
        self._digest_summary: dict | None = None
        self._transcript_status: dict | None = None

    def compose(self) -> ComposeResult:
        yield Static("", id="today-header")
        yield ListView()
        with VerticalScroll(classes="detail-container"):
            yield Static("[dim]Select a meeting or commitment to view details[/dim]")

    def on_mount(self) -> None:
        self.load_data()

    def load_data(self) -> None:
        """Reload calendar events + commitments + briefing from disk."""
        self._projects = _load_projects()
        self._items, self._meeting_count, self._commitment_count = _load_today_items(self._projects)
        self._digest_summary = _load_digest_summary()
        self._transcript_status = _load_transcript_status()
        self._refresh_header()
        self._refresh_list()

    def _refresh_header(self) -> None:
        try:
            header = self.query_one("#today-header", Static)
            now = datetime.now()
            today_str = f"{now.strftime('%A')}, {now.strftime('%B')} {now.day}"
            header.update(f"[bold cyan]Today -- {today_str}[/bold cyan]")
        except Exception:
            pass

    def _title_width(self) -> int:
        try:
            w = self.size.width
            return max(25, w - 30) if w > 30 else 55
        except Exception:
            return 55

    def _refresh_list(self) -> None:
        lv = self.query_one(ListView)
        lv.clear()
        self._list_to_item = []
        tw = self._title_width()
        today_iso = datetime.now().strftime("%Y-%m-%d")

        # Compact briefing: digest summary + transcript status (1-2 lines)
        self._render_briefing(lv)

        if not self._items:
            lv.append(ListItem(Label("[dim]No meetings or commitments for today[/dim]")))
            self._list_to_item.append(-1)
            return

        current_section = None
        for i, item in enumerate(self._items):
            # Determine section for this item
            if item["_type"] == "meeting":
                section = "meetings"
            elif item.get("due", "") < today_iso:
                section = "overdue"
            elif item.get("is_today"):
                section = "due_today"
            else:
                section = "upcoming"

            # Insert section header when section changes
            if section != current_section:
                header = {
                    "meetings": "[bold cyan]Meetings[/bold cyan]",
                    "overdue": "[bold red]Overdue[/bold red]",
                    "due_today": "[bold yellow]Due Today[/bold yellow]",
                    "upcoming": "[dim]Upcoming (7 days)[/dim]",
                }.get(section, "")
                if header:
                    lv.append(ListItem(Label(header)))
                    self._list_to_item.append(-1)
                current_section = section

            if item["_type"] == "meeting":
                text = self._fmt_meeting(item, tw)
            else:
                text = self._fmt_commitment(item, tw)

            lv.append(ListItem(Label(text)))
            self._list_to_item.append(i)

    def _render_briefing(self, lv: ListView) -> None:
        """Render compact digest + transcript status at top of Today."""
        parts: list[str] = []
        ds = self._digest_summary
        if ds:
            date = ds.get("date", "?")
            outstanding = ds.get("outstanding", 0)
            new = ds.get("new", 0)
            resolved = ds.get("resolved", 0)
            parts.append(
                f"[cyan]Digest[/cyan] ({date}) {outstanding} outstanding"
                + (f", {new} new" if new else "")
                + (f", [green]{resolved} resolved[/green]" if resolved else "")
            )
        ts = self._transcript_status
        if ts:
            collected = ts.get("collected", 0)
            success = ts.get("success", False)
            try:
                dt = datetime.fromisoformat(ts.get("timestamp", ""))
                hours_ago = int((datetime.now() - dt).total_seconds() / 3600)
                age = f"{hours_ago}h ago" if hours_ago > 0 else "just now"
            except Exception:
                age = "?"
            if success:
                color = "#00CC88" if collected > 0 else "dim"
                parts.append(f"[{color}]Transcripts[/{color}]: {collected} ({age})")
            else:
                parts.append(f"[red]Transcripts: failed ({age})[/red]")
        if parts:
            lv.append(ListItem(Label("[bold cyan]Briefing[/bold cyan]")))
            self._list_to_item.append(-1)
            for part in parts:
                lv.append(ListItem(Label(f"  {part}")))
                self._list_to_item.append(-1)

    def _fmt_meeting(self, item: dict, tw: int) -> str:
        start = item.get("start_time", "?")
        end = item.get("end_time", "")
        title = item.get("title", "?")[:tw]
        time_str = f"{start}-{end}" if end else start
        teams_tag = " [cyan][Teams][/cyan]" if item.get("is_teams") else ""
        org = item.get("organizer", "")
        org_tag = f"  [dim]({org})[/dim]" if org else ""
        prep = item.get("_prep_hints", "")
        prep_tag = f"  {prep}" if prep else ""
        return f"[bold]{time_str}[/bold]  {title}{teams_tag}{org_tag}{prep_tag}"

    def _fmt_commitment(self, item: dict, tw: int) -> str:
        tag = item.get("_urgency_tag", "DUE")
        color = item.get("_urgency_color", "yellow")
        what = item.get("what", "?")[:tw]
        proj = item.get("project_name", "")
        proj_tag = f"  [dim]{proj}[/dim]" if proj else ""
        return f"[{color}][{tag}][/{color}] {what}{proj_tag}"

    # -- Selection / detail panel --

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.item is None:
            return
        idx = event.list_view.index
        if idx is not None and 0 <= idx < len(self._list_to_item):
            item_idx = self._list_to_item[idx]
            if 0 <= item_idx < len(self._items):
                self._selected_idx = item_idx
                self._show_detail(self._items[item_idx])

    def get_selected_item(self) -> dict | None:
        if 0 <= self._selected_idx < len(self._items):
            return self._items[self._selected_idx]
        return None

    def _show_detail(self, item: dict) -> None:
        if item["_type"] == "meeting":
            self._show_meeting_detail(item)
        else:
            self._show_commitment_detail(item)

    def _show_meeting_detail(self, item: dict) -> None:
        try:
            detail = self.query_one(".detail-container Static", Static)
        except Exception:
            return
        start = item.get("start_time", "?")
        end = item.get("end_time", "")
        time_str = f"{start} - {end}" if end else start

        lines = [
            f"[bold]{item.get('title', '?')}[/bold]",
            f"Time: {time_str}",
            f"Organizer: {item.get('organizer', '?')}",
        ]
        if item.get("is_teams"):
            lines.append("[cyan]Teams meeting[/cyan]")
        if item.get("is_recurring"):
            lines.append("[dim]Recurring[/dim]")

        project = item.get("_linked_project")
        if project:
            proj_name = project.get("project", "?")
            lines += ["", f"[bold cyan]Project: {proj_name}[/bold cyan]"]
            commitments = project.get("commitments", [])
            overdue = [c for c in commitments if c.get("status", "").lower() == "overdue"]
            open_items = [c for c in commitments if c.get("status", "").lower() == "open"]
            if overdue:
                lines.append(f"[bold red]{len(overdue)} overdue commitments:[/bold red]")
                for c in overdue[:4]:
                    lines.append(f"  [red]- {c.get('what', '?')[:50]}[/red]")
            if open_items:
                lines.append(f"[yellow]{len(open_items)} open commitments:[/yellow]")
                for c in open_items[:4]:
                    due = c.get("due", "")
                    due_tag = f"  (due: {due})" if due else ""
                    lines.append(f"  [yellow]- {c.get('what', '?')[:45]}{due_tag}[/yellow]")
            stakeholders = project.get("stakeholders", [])
            if stakeholders:
                lines += ["", "[bold]Stakeholders:[/bold]"]
                for s in stakeholders[:5]:
                    role = f" ({s['role']})" if s.get("role") else ""
                    lines.append(f"  {s.get('name', '?')}{role}")

        scanned_at = item.get("_scanned_at", "")
        if scanned_at:
            try:
                dt = datetime.fromisoformat(scanned_at)
                mins = int((datetime.now() - dt).total_seconds() / 60)
                age = f"scanned {mins}m ago" if mins > 1 else "just scanned"
                lines.append(f"\n[dim]Calendar {age}[/dim]")
            except Exception:
                pass

        lines += ["", "[dim]R=research  D=digest[/dim]"]
        detail.update("\n".join(lines))

    def _show_commitment_detail(self, item: dict) -> None:
        try:
            detail = self.query_one(".detail-container Static", Static)
        except Exception:
            return
        tag = item.get("_urgency_tag", "DUE")
        color = item.get("_urgency_color", "yellow")

        lines = [
            f"[{color}][{tag}][/{color}] [bold]{item.get('what', '?')}[/bold]",
            f"Project: [cyan]{item.get('project_name', '?')}[/cyan]",
            f"Due: {item.get('due', '?')}",
        ]
        who = item.get("who", "")
        to = item.get("to", "")
        if who:
            lines.append(f"Who: {who}")
        if to:
            lines.append(f"To: {to}")
        source = item.get("source", "")
        if source:
            lines.append(f"Source: [dim]{source}[/dim]")

        # Related inbox items for this project
        project_id = item.get("project_id", "")
        if project_id:
            try:
                digest_items = _load_digest_items()
                linked = [
                    d for d in digest_items
                    if isinstance(d.get("project", ""), str)
                    and d["project"].lower() == project_id.lower()
                ][:5]
                if linked:
                    lines += ["", f"[bold cyan]Related inbox ({len(linked)}):[/bold cyan]"]
                    for d in linked:
                        p_color = PRIORITY_COLORS.get(d.get("priority", "").lower(), "white")
                        lines.append(f"  [{p_color}]{d.get('title', '?')[:50]}[/{p_color}]")
            except Exception:
                pass

        lines += ["", "[dim]C=done  R=research  D=digest  N=note[/dim]"]
        detail.update("\n".join(lines))

    # -- Actions --

    def complete_commitment_selected(self) -> None:
        """Mark selected commitment as done in the project YAML."""
        item = self.get_selected_item()
        if not item or item["_type"] != "commitment":
            self.notify("Select a commitment to complete", severity="warning")
            return
        project = item.get("_linked_project")
        project_id = item.get("project_id", "")
        if not project or not project_id:
            return
        # Match by what + due
        for c in project.get("commitments", []):
            if c.get("what") == item.get("what") and str(c.get("due", "")) == item.get("due", ""):
                c["status"] = "done"
                break
        project["updated_at"] = datetime.now().isoformat()
        what = item.get("what", "?")[:40]
        if _save_project_yaml(project_id, project):
            self.notify(f"Done: {what}")
            self.load_data()
        else:
            self.notify("Failed to save project", severity="error")

    def research_selected(self) -> None:
        """Queue research job for the linked project."""
        from tui.ipc import queue_job
        item = self.get_selected_item()
        if not item:
            return
        project = item.get("_linked_project")
        if project:
            name = project.get("project", project.get("_id", "?"))
            queue_job("research", context=f"Deep research on project: {name}")
            self.notify(f"Research queued: {name}")
        else:
            self.notify("No linked project", severity="warning")

    def digest_selected(self) -> None:
        """Queue focused digest for the linked project."""
        from tui.ipc import queue_job
        item = self.get_selected_item()
        if not item:
            return
        project = item.get("_linked_project")
        if project:
            name = project.get("project", project.get("_id", "?"))
            queue_job("digest", context=f"Focused digest for project: {name}")
            self.notify(f"Digest queued: {name}")
        else:
            self.notify("No linked project", severity="warning")

    def note_selected(self) -> None:
        """Add note to commitment via NoteModal."""
        item = self.get_selected_item()
        if not item:
            return
        if item["_type"] == "commitment":
            what = item.get("what", "?")[:30]
            project_id = item.get("project_id", "")
            self.app.push_screen(
                NoteModal(project_id, title=f"Note: {what}"),
                self._on_note_result,
            )
        else:
            title = item.get("title", "?")[:30]
            self.app.push_screen(
                NoteModal("", title=f"Note: {title}"),
                self._on_note_result,
            )

    def _on_note_result(self, result) -> None:
        if result == "saved":
            self.notify("Note saved")


# ---------------------------------------------------------------------------
# Projects pane (enhanced with linked items + commitment highlights)
# ---------------------------------------------------------------------------

class ProjectsPane(Widget):
    """Projects list with sort modes and project actions.

    Sort modes (cycle with S key):
      urgency      — overdue count desc, risk severity, status
      next_meeting — soonest upcoming meeting first
      status       — active > blocked > on-hold > completed
      alphabetical — A-Z by project name

    Actions:
      S — cycle sort mode
      U — update project status
      C — complete a commitment
      R — queue research job for this project
      D — queue focused digest for this project
      N — add note to project
    """

    PROJECT_STATUS_COLORS: dict[str, str] = {
        "active": "#00CC88", "blocked": "#FF3366",
        "on-hold": "#FFB020", "completed": "#5A6A80",
    }
    RISK_COLORS: dict[str, str] = {
        "critical": "bold #FF3366", "high": "#FFB020",
        "medium": "#00D4FF", "low": "#00CC88",
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._projects: list[dict] = []
        self._selected_idx: int = 0
        self._sort_mode: str = "urgency"  # default: most actionable first

    def _title_width(self) -> int:
        """Max character width for project names in list items."""
        try:
            w = self.size.width
            return max(20, w - 35) if w > 35 else 40
        except Exception:
            return 40

    def compose(self) -> ComposeResult:
        yield Static("", id="sort-indicator")
        yield ListView()
        with VerticalScroll(classes="detail-container"):
            yield Static("[dim]Select a project to view details\n\n"
                         "Projects are auto-discovered from\n"
                         "meetings, emails, and digest cycles.\n\n"
                         "Actions: S=sort  U=status  C=done  R=research  D=digest  N=note[/dim]")

    def on_mount(self) -> None:
        self.load_data()

    def load_data(self) -> None:
        raw = _load_projects()
        self._projects = _sort_projects(raw, self._sort_mode)
        self._refresh_list()

    def _refresh_list(self) -> None:
        # Update sort indicator
        try:
            indicator = self.query_one("#sort-indicator", Static)
            label = PROJECT_SORT_LABELS.get(self._sort_mode, self._sort_mode)
            indicator.update(f"[dim]Sort: {label}  (S to cycle)[/dim]")
        except Exception:
            pass

        lv = self.query_one(ListView)
        lv.clear()
        if not self._projects:
            lv.append(ListItem(Label(
                "[dim]No projects yet — run ^D digest to discover engagements[/dim]"
            )))
            return

        tw = self._title_width()
        for p in self._projects:
            name = p.get("project", p.get("_id", "?"))[:tw]
            status = p.get("status", "active")
            risk = p.get("risk_level", "medium")
            sc = self.PROJECT_STATUS_COLORS.get(status, "white")
            rc = self.RISK_COLORS.get(risk, "white")

            overdue = _overdue_count(p)
            overdue_badge = f"  [bold red]({overdue} overdue)[/bold red]" if overdue else ""
            text = f"[{sc}]{status}[/{sc}]  [{rc}]{risk}[/{rc}]  {name}{overdue_badge}"
            lv.append(ListItem(Label(text)))

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.item is None:
            return
        idx = event.list_view.index
        if idx is not None and 0 <= idx < len(self._projects):
            self._selected_idx = idx
            self._show_detail(self._projects[idx])

    def get_selected_project(self) -> dict | None:
        if 0 <= self._selected_idx < len(self._projects):
            return self._projects[self._selected_idx]
        return None

    # -- Sort cycling --

    def cycle_sort(self) -> None:
        """Cycle to the next sort mode and re-sort."""
        idx = PROJECT_SORT_MODES.index(self._sort_mode)
        self._sort_mode = PROJECT_SORT_MODES[(idx + 1) % len(PROJECT_SORT_MODES)]
        self._projects = _sort_projects(self._projects, self._sort_mode)
        self._refresh_list()
        label = PROJECT_SORT_LABELS.get(self._sort_mode, self._sort_mode)
        self.notify(f"Sort: {label}")

    # -- Project actions --

    def update_status_selected(self) -> None:
        """Open status modal for selected project."""
        project = self.get_selected_project()
        if project:
            self.app.push_screen(ProjectStatusModal(project), self._on_status_result)

    def _on_status_result(self, result) -> None:
        if result is None:
            return
        project = self.get_selected_project()
        if not project:
            return
        project_id = project.get("_id", "")
        if not project_id:
            return
        # Write updated status to YAML
        old_status = project.get("status", "active")
        project["status"] = result
        project["updated_at"] = datetime.now().isoformat()
        if _save_project_yaml(project_id, project):
            self.notify(f"Status: {old_status} -> {result}")
            self.load_data()
        else:
            self.notify("Failed to save project", severity="error")

    def complete_commitment_selected(self) -> None:
        """Open commitment completion modal for selected project."""
        project = self.get_selected_project()
        if project:
            actionable = [
                c for c in project.get("commitments", [])
                if c.get("status", "").lower() in ("open", "overdue")
            ]
            if not actionable:
                self.notify("No open or overdue commitments", severity="warning")
                return
            self.app.push_screen(CommitmentModal(project), self._on_commitment_result)

    def _on_commitment_result(self, result) -> None:
        if result is None:
            return
        project = self.get_selected_project()
        if not project:
            return
        project_id = project.get("_id", "")
        if not project_id:
            return
        # Find the actionable commitment by index
        actionable = [
            c for c in project.get("commitments", [])
            if c.get("status", "").lower() in ("open", "overdue")
        ]
        if not (0 <= result < len(actionable)):
            return
        target = actionable[result]
        # Update the matching commitment in the full list
        for c in project.get("commitments", []):
            if c is target:
                c["status"] = "done"
                break
        project["updated_at"] = datetime.now().isoformat()
        what = target.get("what", "?")[:40]
        if _save_project_yaml(project_id, project):
            self.notify(f"Done: {what}")
            self.load_data()
            self._show_detail(project)
        else:
            self.notify("Failed to save project", severity="error")

    def research_selected(self) -> None:
        """Queue a research job focused on the selected project."""
        from tui.ipc import queue_job
        project = self.get_selected_project()
        if project:
            name = project.get("project", project.get("_id", "?"))
            queue_job("research", context=f"Deep research on project: {name}")
            self.notify(f"Research queued: {name}")

    def digest_selected(self) -> None:
        """Queue a digest job focused on the selected project."""
        from tui.ipc import queue_job
        project = self.get_selected_project()
        if project:
            name = project.get("project", project.get("_id", "?"))
            queue_job("digest", context=f"Focused digest for project: {name}")
            self.notify(f"Digest queued: {name}")

    def note_selected(self) -> None:
        """Add a note to the selected project."""
        project = self.get_selected_project()
        if project:
            name = project.get("project", "?")[:30]
            self.app.push_screen(
                NoteModal(project.get("_id", ""), title=f"Note: {name}"),
                self._on_note_result,
            )

    def _on_note_result(self, result) -> None:
        if result == "saved":
            self.notify("Note saved to project")

    # -- Detail rendering --

    def _show_detail(self, project: dict) -> None:
        detail = self.query_one(".detail-container Static", Static)

        lines = [
            f"[bold]{project.get('project', '?')}[/bold]",
            f"Status: {project.get('status', '?')}  |  Risk: {project.get('risk_level', '?')}",
            "",
            project.get("summary", ""),
            "",
            "[dim]Actions: U=status  C=done  R=research  D=digest  N=note[/dim]",
        ]

        # Commitments — overdue first, then open, highlighted
        commitments = project.get("commitments", [])
        if commitments:
            overdue = [c for c in commitments if c.get("status", "").lower() == "overdue"]
            upcoming = [c for c in commitments if c.get("status", "").lower() == "open"]
            done = [c for c in commitments if c.get("status", "").lower() in ("done", "cancelled")]

            if overdue:
                lines += ["", "[bold red]Overdue commitments:[/bold red]"]
                for c in overdue:
                    what = c.get("what", "?")
                    due = c.get("due", "")
                    who = c.get("who", "")
                    to = c.get("to", "")
                    line = f"  [red][OVERDUE][/red] {what}"
                    if due:
                        line += f"  (due: {due})"
                    if who:
                        line += f"  — {who}"
                    if to:
                        line += f" to {to}"
                    lines.append(line)

            if upcoming:
                lines += ["", "[bold yellow]Open commitments:[/bold yellow]"]
                for c in upcoming:
                    what = c.get("what", "?")
                    due = c.get("due", "")
                    who = c.get("who", "")
                    color = "yellow"
                    line = f"  [{color}][OPEN][/{color}] {what}"
                    if due:
                        line += f"  (due: {due})"
                    if who:
                        line += f"  — {who}"
                    lines.append(line)

            if done:
                lines += ["", "[dim]Completed/cancelled:[/dim]"]
                for c in done[:3]:  # Show max 3
                    what = c.get("what", "?")
                    lines.append(f"  [dim][{c.get('status', '?').upper()}] {what}[/dim]")

        # Linked inbox items (digest items referencing this project)
        project_id = project.get("_id", "")
        if project_id:
            linked = self._get_linked_items(project_id)
            if linked:
                lines += ["", f"[bold cyan]Linked inbox items ({len(linked)}):[/bold cyan]"]
                for item in linked[:5]:  # Show max 5
                    p_color = PRIORITY_COLORS.get(item.get("priority", "").lower(), "white")
                    title = item.get("title", "?")[:50]
                    lines.append(f"  [{p_color}][{item.get('priority', '?').upper()}][/{p_color}] {title}")

        # Stakeholders
        stakeholders = project.get("stakeholders", [])
        if stakeholders:
            lines += ["", "[bold]Stakeholders:[/bold]"]
            for s in stakeholders:
                name = s.get("name", "?")
                role = s.get("role", "")
                lines.append(f"  {name}" + (f" ({role})" if role else ""))

        # Next meeting + key dates
        next_mtg = project.get("next_meeting", "")
        if next_mtg:
            lines += ["", f"Next meeting: [cyan]{next_mtg}[/cyan]"]

        key_dates = project.get("key_dates", [])
        if key_dates:
            lines += ["", "[bold]Key dates:[/bold]"]
            for kd in key_dates[:5]:
                lines.append(f"  {kd.get('date', '?')} — {kd.get('event', '?')}")

        detail.update("\n".join(lines))

    def _get_linked_items(self, project_id: str) -> list[dict]:
        """Find digest items that reference this project."""
        digest_items = _load_digest_items()
        linked = []
        for item in digest_items:
            item_project = item.get("project", "")
            if isinstance(item_project, str) and item_project.lower() == project_id.lower():
                linked.append(item)
        return linked


def _save_project_yaml(project_id: str, project: dict) -> bool:
    """Save project data back to YAML file. Returns True on success."""
    import logging
    log = logging.getLogger(__name__)
    try:
        path = PROJECTS_DIR / f"{project_id}.yaml"
        # Remove internal fields before saving
        data = {k: v for k, v in project.items() if not k.startswith("_")}
        path.write_text(yaml.dump(data, default_flow_style=False, allow_unicode=True), encoding="utf-8")
        return True
    except Exception:
        log.debug("Failed to save project %s", project_id, exc_info=True)
        return False


# ---------------------------------------------------------------------------
# Jobs pane (real-time job visibility + activity logs)
# ---------------------------------------------------------------------------

JOB_STATUS_COLORS: dict[str, str] = {
    "running": "bold #00D4FF",
    "completed": "#00CC88",
    "failed": "bold #FF3366",
    "queued": "#FFB020",
}


def _consolidate_jobs(events: list[dict]) -> list[dict]:
    """Consolidate raw job events into one entry per job (latest status wins).

    Returns list of job dicts sorted: running first, then by timestamp desc.
    """
    jobs: dict[str, dict] = {}
    for ev in events:
        jid = ev.get("job_id", "")
        if not jid:
            continue
        existing = jobs.get(jid)
        if existing is None:
            jobs[jid] = {
                "job_id": jid,
                "job_type": ev.get("job_type", "?"),
                "status": ev.get("status", "?"),
                "detail": ev.get("detail", ""),
                "log_file": ev.get("log_file", ""),
                "ts": ev.get("ts", ""),
                "started_ts": ev.get("ts", "") if ev.get("status") == "running" else "",
            }
        else:
            # Update with later event
            existing["status"] = ev.get("status", existing["status"])
            existing["detail"] = ev.get("detail") or existing["detail"]
            existing["log_file"] = ev.get("log_file") or existing["log_file"]
            existing["ts"] = ev.get("ts", existing["ts"])
            if ev.get("status") == "running":
                existing["started_ts"] = ev.get("ts", "")

    result = list(jobs.values())
    # Sort: running first, then most recent
    running = [j for j in result if j["status"] == "running"]
    others = [j for j in result if j["status"] != "running"]
    others.sort(key=lambda j: j.get("ts", ""), reverse=True)
    return running + others


class JobsPane(Widget):
    """Jobs tab showing all job history with activity logs."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._jobs: list[dict] = []
        self._selected_idx: int = 0

    def compose(self) -> ComposeResult:
        yield ListView()
        with VerticalScroll(classes="detail-container"):
            yield Static("[dim]Select a job to view its activity log[/dim]", id="job-detail")

    def on_mount(self) -> None:
        self.load_data()
        self.set_interval(3, self._auto_refresh)

    def _auto_refresh(self) -> None:
        """Auto-refresh only while a job is running — skip if idle."""
        has_running = any(j["status"] == "running" for j in self._jobs)
        if not has_running:
            return
        self.load_data()
        sel = self._get_selected_job()
        if sel and sel["status"] == "running":
            self._show_detail(sel)

    def load_data(self) -> None:
        events = read_job_history(limit=200)
        self._jobs = _consolidate_jobs(events)
        self._refresh_list()

    def _refresh_list(self) -> None:
        lv = self.query_one(ListView)
        lv.clear()
        if not self._jobs:
            lv.append(ListItem(Label(
                "[dim]No jobs yet — press ^T triage or ^D digest to queue one[/dim]"
            )))
            return

        for job in self._jobs:
            status = job["status"]
            color = JOB_STATUS_COLORS.get(status, "white")
            job_type = job["job_type"]
            detail = job.get("detail", "")[:40]

            # Duration for running/completed
            duration = ""
            started = job.get("started_ts", "")
            if started and status == "running":
                try:
                    dt = datetime.fromisoformat(started)
                    elapsed = int((datetime.now() - dt).total_seconds())
                    m, s = divmod(elapsed, 60)
                    duration = f" ({m}m{s:02d}s)" if m else f" ({s}s)"
                except Exception:
                    pass
            elif status in ("completed", "failed"):
                age = _age_str(job.get("ts", ""))
                duration = f" ({age} ago)"

            text = f"[{color}]{status.upper():>9}[/{color}]  {job_type}{duration}"
            if detail and detail != job_type:
                text += f"  [dim]{detail}[/dim]"
            lv.append(ListItem(Label(text)))

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.item is None:
            return
        idx = event.list_view.index
        if idx is not None and 0 <= idx < len(self._jobs):
            self._selected_idx = idx
            self._show_detail(self._jobs[idx])

    def _get_selected_job(self) -> dict | None:
        if 0 <= self._selected_idx < len(self._jobs):
            return self._jobs[self._selected_idx]
        return None

    def _show_detail(self, job: dict) -> None:
        detail_widget = self.query_one("#job-detail", Static)
        status = job["status"]
        color = JOB_STATUS_COLORS.get(status, "white")

        lines = [
            f"[{color}]{status.upper()}[/{color}] [bold]{job['job_type']}[/bold]",
            f"Job ID: {job['job_id']}",
        ]
        if job.get("started_ts"):
            lines.append(f"Started: {job['started_ts']}")
        if job.get("detail") and job["detail"] != job["job_type"]:
            lines.append(f"Detail: {job['detail']}")

        # Load activity log
        log_file = job.get("log_file", "")
        if log_file:
            log_entries = read_job_log(log_file)
            if log_entries:
                lines.append("")
                lines.append("[bold cyan]Activity log:[/bold cyan]")
                for entry in log_entries:
                    ts = entry.get("ts", "")
                    # Format timestamp as HH:MM:SS
                    try:
                        t = datetime.fromisoformat(ts)
                        ts_short = t.strftime("%H:%M:%S")
                    except Exception:
                        ts_short = ts[:8] if ts else ""

                    etype = entry.get("type", "")
                    if etype == "tool_start":
                        tool = entry.get("tool", "?")
                        mcp = f" ({entry['mcp']})" if entry.get("mcp") else ""
                        args = entry.get("args", "")
                        args_preview = f" {args[:80]}" if args else ""
                        lines.append(f"  [cyan]{ts_short}[/cyan] >> [bold]{tool}[/bold]{mcp}{args_preview}")
                    elif etype == "tool_result":
                        result = entry.get("result", "")[:120]
                        lines.append(f"  [dim]{ts_short} << {result}[/dim]")
                    elif etype == "message":
                        preview = entry.get("preview", "")[:200]
                        lines.append(f"  [green]{ts_short}[/green] Agent: {preview}")
                    elif etype == "error":
                        err = entry.get("error", "")[:200]
                        lines.append(f"  [red]{ts_short} ERROR: {err}[/red]")
                    elif etype == "idle":
                        lines.append(f"  [dim]{ts_short} Session complete[/dim]")
            else:
                lines.append("")
                if status == "running":
                    lines.append("[dim]Waiting for activity...[/dim]")
                else:
                    lines.append("[dim]No activity log available[/dim]")
        else:
            lines.append("")
            lines.append("[dim]No activity log for this job type[/dim]")

        detail_widget.update("\n".join(lines))

    def get_running_count(self) -> int:
        return sum(1 for j in self._jobs if j["status"] == "running")

    def get_pending_count(self) -> int:
        return sum(1 for j in self._jobs if j["status"] == "queued")

    def get_active_count(self) -> int:
        return self.get_running_count() + self.get_pending_count()


# ---------------------------------------------------------------------------
# Chat pane
# ---------------------------------------------------------------------------

class ChatPane(Widget):
    """Chat tab with streaming response support via file IPC."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._streaming = False
        self._stream_offset = 0
        self._current_request_id = ""
        self._response_buf = ""
        self._response_started = False
        self._wait_ticks = 0

    def compose(self) -> ComposeResult:
        yield RichLog(id="chat-log", wrap=True, markup=True, highlight=False)
        yield Input(
            placeholder="Ask anything... (Enter to send, daemon must be running)",
            id="chat-input",
        )

    def on_mount(self) -> None:
        self.set_interval(1, self._poll_stream)
        chat_log = self.query_one(RichLog)
        chat_log.write("[bold cyan]Pulse Chat[/bold cyan] — type a message and press Enter")
        chat_log.write("[dim]Requires daemon running: python src/pulse.py[/dim]")
        chat_log.write("")
        # Auto-focus the input so the user can type immediately
        self.call_after_refresh(self._focus_input)

    def _focus_input(self) -> None:
        try:
            self.query_one(Input).focus()
        except Exception:
            pass

    def on_input_submitted(self, event: Input.Submitted) -> None:
        prompt = event.value.strip()
        if not prompt or self._streaming:
            return

        input_widget = self.query_one(Input)
        input_widget.value = ""

        chat_log = self.query_one(RichLog)
        chat_log.write(f"[bold cyan]You:[/bold cyan] {prompt}")

        # Clear stale stream BEFORE sending so we never read old responses
        from tui.ipc import clear_chat_stream
        clear_chat_stream()

        # Send to daemon via file IPC
        self._current_request_id = send_chat_request(prompt)
        self._stream_offset = 0
        self._response_buf = ""
        self._response_started = False
        self._wait_ticks = 0
        self._streaming = True

    def _poll_stream(self) -> None:
        # Always check for job completion notifications (even when not streaming)
        notif = read_job_notification()
        if notif:
            chat_log = self.query_one(RichLog)
            summary = notif.get("summary", "Job complete")
            job_type = notif.get("job_type", "")
            safe_summary = summary.encode("ascii", "replace").decode("ascii")
            chat_log.write(f"[bold yellow]{job_type.capitalize()} complete:[/bold yellow] {safe_summary}")
            chat_log.write("")
            # Refresh inbox/projects since new data arrived
            try:
                self.app.action_refresh_all()
            except Exception:
                pass

        if not self._streaming:
            return

        new_text, is_done, new_offset = read_chat_stream_deltas(self._stream_offset, self._current_request_id)
        self._stream_offset = new_offset
        chat_log = self.query_one(RichLog)

        if new_text:
            self._wait_ticks = 0
            self._response_buf += new_text

            # Write complete lines as they arrive
            while "\n" in self._response_buf:
                line, self._response_buf = self._response_buf.split("\n", 1)
                if not self._response_started:
                    chat_log.write("[bold green]Agent:[/bold green]")
                    self._response_started = True
                # Encode to avoid charmap errors on Windows
                safe_line = line.encode("ascii", "replace").decode("ascii")
                chat_log.write(safe_line)
        elif not is_done:
            self._wait_ticks += 1
            if self._wait_ticks == 12:
                chat_log.write("[dim]Processing... (start daemon if stuck)[/dim]")
            elif self._wait_ticks >= 90:  # 90s timeout
                chat_log.write("[dim red]Request timed out — is the daemon running?[/dim red]")
                chat_log.write("")
                self._streaming = False
                return

        if is_done:
            # Flush remaining buffer
            if self._response_buf.strip():
                if not self._response_started:
                    chat_log.write("[bold green]Agent:[/bold green]")
                safe_buf = self._response_buf.encode("ascii", "replace").decode("ascii")
                chat_log.write(safe_buf)
                self._response_buf = ""
            elif not self._response_started:
                chat_log.write("[dim]Agent: (no response)[/dim]")
            chat_log.write("")  # blank separator
            self._streaming = False

    def clear_chat(self) -> None:
        self.query_one(RichLog).clear()
        self._streaming = False
        self._stream_offset = 0
        self._response_buf = ""
        self._response_started = False
