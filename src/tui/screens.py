"""Textual TUI panes and modals for Pulse Agent.

Panes (used as tab content):
  TriagePane    — latest monitoring JSON items with D/R/N actions
  DigestPane    — latest digest JSON items with D/R/N actions
  ProjectsPane  — per-engagement project YAML files
  ChatPane      — streaming chat with the agent via file IPC

Modals:
  ReplyModal    — review and send a drafted reply
  NoteModal     — add a note to an item
  QuestionModal — answer an ask_user question from the agent
"""

import json

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
    dismiss_item,
    read_chat_stream_deltas,
    read_pending_question,
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


def _priority_markup(priority: str, title: str, source: str = "") -> str:
    p = priority.upper()
    color = PRIORITY_COLORS.get(priority.lower(), "white")
    src = f"  [dim]{source}[/dim]" if source else ""
    return f"[{color}][{p}][/{color}] {title}{src}"


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
# Modals
# ---------------------------------------------------------------------------

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

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-send":
            self.action_send_reply()
        else:
            self.dismiss(None)

    def action_send_reply(self) -> None:
        text_area = self.query_one(TextArea)
        draft = text_area.text
        if draft.strip():
            write_reply_job(self._item, draft)
        self.dismiss("sent")

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
# Base item pane (shared by Triage and Digest)
# ---------------------------------------------------------------------------

class ItemPane(Widget):
    """Base widget for triage/digest item views.

    Subclasses override _load_items() to provide data.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._items: list[dict] = []
        self._selected_idx: int = 0

    def _load_items(self) -> list[dict]:
        """Override in subclass to load items from disk."""
        return []

    def compose(self) -> ComposeResult:
        yield ListView()
        with VerticalScroll(classes="detail-container"):
            yield Static("Select an item to view details")

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
            lv.append(ListItem(Label("[dim]No items[/dim]")))
            return
        for item in self._items:
            priority = item.get("priority", "?")
            title = item.get("title", "?")[:55]
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
                    # Show first 120 chars of draft
                    preview = draft[:120] + ("..." if len(draft) > 120 else "")
                    lines.append(f"  [dim]{preview}[/dim]")

        lines += ["", "[dim]  d = dismiss   r = reply   n = note[/dim]"]
        detail.update("\n".join(lines))

    def get_selected_item(self) -> dict | None:
        if 0 <= self._selected_idx < len(self._items):
            return self._items[self._selected_idx]
        return None

    def dismiss_selected(self) -> None:
        item = self.get_selected_item()
        if item:
            dismiss_item(item.get("id", ""), "")
            self._items.pop(self._selected_idx)
            self._selected_idx = max(0, self._selected_idx - 1)
            self._refresh_list()
            self.notify("Item dismissed")

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

    def _on_note_result(self, result) -> None:
        if result == "saved":
            self.notify("Note saved")


# ---------------------------------------------------------------------------
# Concrete item panes
# ---------------------------------------------------------------------------

class TriagePane(ItemPane):
    """Triage items from the latest monitoring JSON."""

    def _load_items(self) -> list[dict]:
        return _load_triage_items()


class DigestPane(ItemPane):
    """Digest items from the latest digest JSON."""

    def _load_items(self) -> list[dict]:
        return _load_digest_items()


# ---------------------------------------------------------------------------
# Projects pane
# ---------------------------------------------------------------------------

class ProjectsPane(Widget):
    """Projects list from PULSE_HOME/projects/*.yaml."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._projects: list[dict] = []
        self._selected_idx: int = 0

    def compose(self) -> ComposeResult:
        yield ListView()
        with VerticalScroll(classes="detail-container"):
            yield Static("Select a project to view details")

    def on_mount(self) -> None:
        self.load_data()

    def load_data(self) -> None:
        self._projects = _load_projects()
        self._refresh_list()

    def _refresh_list(self) -> None:
        lv = self.query_one(ListView)
        lv.clear()
        if not self._projects:
            lv.append(ListItem(Label("[dim]No project files found[/dim]")))
            return

        STATUS_COLORS = {
            "active": "green", "blocked": "red",
            "on-hold": "yellow", "completed": "dim",
        }
        RISK_COLORS = {
            "critical": "bold red", "high": "yellow",
            "medium": "cyan", "low": "green",
        }

        for p in self._projects:
            name = p.get("project", p.get("_id", "?"))[:40]
            status = p.get("status", "active")
            risk = p.get("risk_level", "medium")
            sc = STATUS_COLORS.get(status, "white")
            rc = RISK_COLORS.get(risk, "white")
            text = f"[{sc}]{status}[/{sc}]  [{rc}]{risk}[/{rc}]  {name}"
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

        stakeholders = project.get("stakeholders", [])
        if stakeholders:
            lines += ["", "[bold]Stakeholders:[/bold]"]
            for s in stakeholders:
                name = s.get("name", "?")
                role = s.get("role", "")
                lines.append(f"  {name}" + (f" ({role})" if role else ""))

        commitments = project.get("commitments", [])
        if commitments:
            lines += ["", "[bold]Commitments:[/bold]"]
            for c in commitments:
                c_status = c.get("status", "open").upper()
                what = c.get("what", "?")
                due = c.get("due", "")
                color = "red" if c_status == "OVERDUE" else ("yellow" if c_status == "OPEN" else "dim")
                lines.append(
                    f"  [{color}][{c_status}][/{color}] {what}"
                    + (f"  (due: {due})" if due else "")
                )

        next_mtg = project.get("next_meeting", "")
        if next_mtg:
            lines += ["", f"Next meeting: [cyan]{next_mtg}[/cyan]"]

        key_dates = project.get("key_dates", [])
        if key_dates:
            lines += ["", "[bold]Key dates:[/bold]"]
            for kd in key_dates[:5]:
                lines.append(f"  {kd.get('date', '?')} — {kd.get('event', '?')}")

        detail.update("\n".join(lines))


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
        chat_log.write("[dim]Requires daemon running: python src/main.py[/dim]")
        chat_log.write("")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        prompt = event.value.strip()
        if not prompt or self._streaming:
            return

        input_widget = self.query_one(Input)
        input_widget.value = ""

        chat_log = self.query_one(RichLog)
        chat_log.write(f"[bold cyan]You:[/bold cyan] {prompt}")

        # Send to daemon via file IPC
        self._current_request_id = send_chat_request(prompt)
        self._stream_offset = 0
        self._response_buf = ""
        self._response_started = False
        self._wait_ticks = 0
        self._streaming = True

    def _poll_stream(self) -> None:
        if not self._streaming:
            return

        new_text, is_done, new_offset = read_chat_stream_deltas(self._stream_offset)
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
