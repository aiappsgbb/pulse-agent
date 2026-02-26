"""Pulse Agent TUI — main Textual application.

Provides a 4-tab interactive dashboard:
  Triage    — latest triage items with dismiss/reply/note actions
  Digest    — morning digest items
  Projects  — per-engagement project memory
  Chat      — streaming chat with the agent

Key bindings:
  ctrl+d/t/i/x  — queue digest/triage/intel/transcript jobs
  ctrl+l        — jump to Digest tab and reload
  ctrl+r        — force refresh all panes
  d / r / n     — dismiss / reply / note (on Triage and Digest tabs)
  q             — quit
"""

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widget import Widget
from textual.widgets import Footer, Header, Static, TabbedContent, TabPane

from tui.ipc import queue_job, read_daemon_status, read_pending_question
from tui.screens import (
    ChatPane,
    DigestPane,
    ProjectsPane,
    QuestionModal,
    TriagePane,
)


class StatusBar(Static):
    """Bottom status bar showing daemon uptime, queue size, and last update."""

    DEFAULT_CSS = """
    StatusBar {
        height: 1;
        background: $panel;
        color: $text-muted;
        padding: 0 1;
    }
    """

    def update_status(self) -> None:
        status = read_daemon_status()
        if status:
            uptime_s = status.get("uptime_s", 0)
            h, m = divmod(uptime_s // 60, 60)
            uptime_str = f"{h}h{m:02d}m" if h else f"{m}m"
            queue_size = status.get("queue_size", 0)
            updated = status.get("updated_at", "")[:16].replace("T", " ")
            self.update(
                f" Daemon: up {uptime_str}  |  Queue: {queue_size}  |  Updated: {updated}"
                f"  |  ^D digest  ^T triage  ^I intel  ^X transcripts  ^L latest  q quit"
            )
        else:
            self.update(
                " Daemon: offline — start with: python src/main.py"
                "  |  ^R refresh  q quit"
            )


class PulseApp(App):
    """Pulse Agent interactive terminal dashboard."""

    CSS_PATH = "styles.tcss"

    TITLE = "Pulse Agent"

    BINDINGS = [
        Binding("ctrl+d", "trigger_digest", "Digest", show=True),
        Binding("ctrl+t", "trigger_triage", "Triage", show=True),
        Binding("ctrl+i", "trigger_intel", "Intel", show=True),
        Binding("ctrl+x", "trigger_transcripts", "Transcripts", show=True),
        Binding("ctrl+l", "view_latest_digest", "Latest Digest", show=True),
        Binding("ctrl+r", "refresh_all", "Refresh", show=True),
        # Item actions — active on Triage and Digest tabs
        Binding("d", "item_dismiss", "Dismiss", show=False),
        Binding("r", "item_reply", "Reply", show=False),
        Binding("n", "item_note", "Note", show=False),
        Binding("q", "quit", "Quit", show=True),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(id="tabs"):
            with TabPane("Triage", id="tab-triage"):
                yield TriagePane(id="triage-pane")
            with TabPane("Digest", id="tab-digest"):
                yield DigestPane(id="digest-pane")
            with TabPane("Projects", id="tab-projects"):
                yield ProjectsPane(id="projects-pane")
            with TabPane("Chat", id="tab-chat"):
                yield ChatPane(id="chat-pane")
        yield StatusBar(id="status-bar")
        yield Footer()

    def on_mount(self) -> None:
        # Periodic background tasks
        self.set_interval(30, self._auto_refresh_panes)
        self.set_interval(5, self._update_status_bar)
        self.set_interval(2, self._check_pending_question)

    # -------------------------------------------------------------------------
    # Periodic callbacks
    # -------------------------------------------------------------------------

    def _auto_refresh_panes(self) -> None:
        """Reload all data panes every 30s."""
        try:
            self.query_one(TriagePane).load_data()
            self.query_one(DigestPane).load_data()
            self.query_one(ProjectsPane).load_data()
        except Exception:
            pass

    def _update_status_bar(self) -> None:
        try:
            self.query_one(StatusBar).update_status()
        except Exception:
            pass

    def _check_pending_question(self) -> None:
        """Check for a pending ask_user question and show modal."""
        try:
            q = read_pending_question()
            if q:
                question = q.get("question", "")
                session_id = q.get("session_id", "")
                if question and session_id:
                    # Only show if no QuestionModal already open
                    if not any(isinstance(s, QuestionModal) for s in self.screen_stack):
                        self.push_screen(QuestionModal(question, session_id))
        except Exception:
            pass

    # -------------------------------------------------------------------------
    # Job trigger actions
    # -------------------------------------------------------------------------

    def action_trigger_digest(self) -> None:
        queue_job("digest")
        self.notify("Digest job queued — daemon will pick it up within 60s")

    def action_trigger_triage(self) -> None:
        queue_job("monitor")
        self.notify("Triage job queued")

    def action_trigger_intel(self) -> None:
        queue_job("intel")
        self.notify("Intel job queued")

    def action_trigger_transcripts(self) -> None:
        queue_job("transcripts")
        self.notify("Transcript collection queued")

    def action_view_latest_digest(self) -> None:
        tabs = self.query_one(TabbedContent)
        tabs.active = "tab-digest"
        self.query_one(DigestPane).load_data()

    def action_refresh_all(self) -> None:
        self._auto_refresh_panes()
        self.notify("All panes refreshed")

    # -------------------------------------------------------------------------
    # Item actions (delegate to active pane)
    # -------------------------------------------------------------------------

    def action_item_dismiss(self) -> None:
        pane = self._get_active_item_pane()
        if pane:
            pane.dismiss_selected()

    def action_item_reply(self) -> None:
        pane = self._get_active_item_pane()
        if pane:
            pane.reply_selected()

    def action_item_note(self) -> None:
        pane = self._get_active_item_pane()
        if pane:
            pane.note_selected()

    def _get_active_item_pane(self) -> TriagePane | DigestPane | None:
        """Return TriagePane or DigestPane if that tab is currently active."""
        tabs = self.query_one(TabbedContent)
        if tabs.active == "tab-triage":
            return self.query_one(TriagePane)
        elif tabs.active == "tab-digest":
            return self.query_one(DigestPane)
        return None
