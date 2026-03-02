"""Textual TUI panes and modals for Pulse Agent.

Panes (used as tab content):
  InboxPane     — unified actionable items (triage + digest, dismissed toggle)
  ProjectsPane  — per-engagement project YAML files with linked items
  ChatPane      — streaming chat with the agent via file IPC

Modals:
  ReplyModal    — review and send a drafted reply
  NoteModal     — add a note to an item
  QuestionModal — answer an ask_user question from the agent
"""

import json
from datetime import datetime

import yaml

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Button, Input, Label, ListItem, ListView, RichLog, Static, TextArea

from core.constants import DIGESTS_DIR, PROJECTS_DIR, PULSE_HOME
from tui.ipc import (
    add_note,
    archive_item,
    dismiss_item,
    load_dismissed_items,
    read_chat_stream_deltas,
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
    "urgent": "bold red",
    "high": "bold yellow",
    "medium": "cyan",
    "low": "dim white",
}

ORIGIN_COLORS: dict[str, str] = {
    "triage": "magenta",
    "digest": "blue",
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
# Inbox data merging
# ---------------------------------------------------------------------------

_PRIORITY_ORDER = {"urgent": 0, "high": 1, "medium": 2, "low": 3}


def _inbox_sort_key(item: dict) -> tuple:
    """Sort: urgent first, then high, then triage before digest."""
    priority = item.get("priority", "medium").lower()
    origin = item.get("_origin", "digest")
    return (
        _PRIORITY_ORDER.get(priority, 2),
        0 if origin == "triage" else 1,
        item.get("title", ""),
    )


def _load_inbox_items(include_dismissed: bool = False) -> tuple[list[dict], int]:
    """Merge triage + digest items, dedup by ID, optionally include dismissed.

    Returns (items, dismissed_count).
    """
    triage_items = _load_triage_items()
    digest_items = _load_digest_items()
    dismissed = load_dismissed_items()
    dismissed_ids = {d.get("item") for d in dismissed}

    # Tag items with origin
    for item in triage_items:
        item["_origin"] = "triage"
    for item in digest_items:
        item["_origin"] = "digest"

    # Merge: triage first (more recent), then digest. Dedup by ID.
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
                "  Ctrl+L  Jump to Inbox        Ctrl+R  Refresh all\n"
                "  Ctrl+H  Toggle dismissed      Ctrl+E  Clear chat\n"
                "  Ctrl+P  Command palette       Q       Quit\n"
                "\n"
                "[bold cyan]Inbox actions[/bold cyan]\n"
                "  D  Snooze (1 day)            A  Archive (30 days)\n"
                "  R  Reply / Restore           N  Add note\n"
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
                self.dismiss("sent")
            else:
                self.dismiss("error")
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class NoteModal(ModalScreen):
    """Modal for adding a note to an item."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, item_id: str, **kwargs):
        super().__init__(**kwargs)
        self._item_id = item_id

    def compose(self) -> ComposeResult:
        with Widget(id="note-dialog"):
            yield Label("Add note", id="note-title")
            yield Input(placeholder="Enter note text...", id="note-input")
            with Horizontal(id="note-buttons"):
                yield Button("Save (Enter)", id="btn-save", variant="primary")
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
        self.dismiss("saved" if note_text else None)

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
            self.notify("Reply job queued — daemon will send it")
        elif result == "error":
            self.notify("Failed to queue reply — check PULSE_HOME/jobs/", severity="error")

    def _on_note_result(self, result) -> None:
        if result == "saved":
            self.notify("Note saved")


# ---------------------------------------------------------------------------
# Inbox pane (replaces Triage + Digest + Dismissed)
# ---------------------------------------------------------------------------

class InboxPane(ItemPane):
    """Unified inbox: triage + digest items, with dismissed toggle (Ctrl+H)."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._show_dismissed: bool = False
        self._dismissed_count: int = 0
        self._list_to_item: list[int] = []  # Maps ListView index -> _items index (-1 for separator)

    def _load_items(self) -> list[dict]:
        items, self._dismissed_count = _load_inbox_items(
            include_dismissed=self._show_dismissed,
        )
        return items

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

    def archive_selected(self) -> None:
        """Archive an item (works on both active and dismissed items)."""
        item = self.get_selected_item()
        if not item:
            return
        if item.get("_is_dismissed"):
            archive_item(item.get("item", ""))
            item["status"] = "archived"
            self._refresh_list()
        else:
            archive_item(
                item.get("id", ""),
                title=item.get("title", ""),
                source=item.get("source", ""),
            )
            self._items.pop(self._selected_idx)
            self._selected_idx = max(0, self._selected_idx - 1)
            self._refresh_list()
        self.notify("Item archived (30-day suppress)")

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
# Projects pane (enhanced with linked items + commitment highlights)
# ---------------------------------------------------------------------------

class ProjectsPane(Widget):
    """Projects list from PULSE_HOME/projects/*.yaml with linked items."""

    PROJECT_STATUS_COLORS: dict[str, str] = {
        "active": "green", "blocked": "red",
        "on-hold": "yellow", "completed": "dim",
    }
    RISK_COLORS: dict[str, str] = {
        "critical": "bold red", "high": "yellow",
        "medium": "cyan", "low": "green",
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._projects: list[dict] = []
        self._selected_idx: int = 0

    def _title_width(self) -> int:
        """Max character width for project names in list items."""
        try:
            w = self.size.width
            return max(20, w - 35) if w > 35 else 40
        except Exception:
            return 40

    def compose(self) -> ComposeResult:
        yield ListView()
        with VerticalScroll(classes="detail-container"):
            yield Static("[dim]Select a project to view details\n\n"
                         "Projects are auto-discovered from\n"
                         "meetings, emails, and digest cycles.[/dim]")

    def on_mount(self) -> None:
        self.load_data()

    def load_data(self) -> None:
        self._projects = _load_projects()
        self._refresh_list()

    def _refresh_list(self) -> None:
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

            # Count overdue commitments for badge
            overdue = sum(
                1 for c in p.get("commitments", [])
                if c.get("status", "").lower() == "overdue"
            )
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

    def _show_detail(self, project: dict) -> None:
        detail = self.query_one(Static)

        lines = [
            f"[bold]{project.get('project', '?')}[/bold]",
            f"Status: {project.get('status', '?')}  |  Risk: {project.get('risk_level', '?')}",
            "",
            project.get("summary", ""),
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
