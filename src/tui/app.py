"""Pulse Agent TUI — main Textual application.

Provides a 3-tab interactive dashboard:
  Inbox     — unified actionable items (triage + digest + dismissed toggle)
  Projects  — per-engagement project memory with linked items
  Chat      — streaming chat with the agent

Key bindings:
  ctrl+d/t/i/x  — queue digest/triage/intel/transcript jobs
  ctrl+l        — jump to Inbox tab and reload
  ctrl+r        — force refresh all panes
  ctrl+h        — toggle show/hide dismissed items in Inbox
  d / r / n     — dismiss / reply (or restore) / note
  a             — archive (on dismissed items in Inbox)
  q             — quit
"""

import threading

from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widget import Widget
from textual.widgets import Footer, Header, Input, Static, TabbedContent, TabPane, TextArea

from textual.screen import ModalScreen

from tui.ipc import queue_job, read_daemon_status, read_pending_question
from tui.screens import (
    ChatPane,
    InboxPane,
    ProjectsPane,
    QuestionModal,
)


def _play_alert() -> None:
    """Play a retro 3-tone ascending chime in a background thread (non-blocking)."""
    def _beep():
        try:
            import winsound
            winsound.Beep(660, 80)   # E5
            winsound.Beep(880, 80)   # A5
            winsound.Beep(1320, 120) # E6
        except Exception:
            pass
    threading.Thread(target=_beep, daemon=True).start()


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
                f"  |  ^D digest  ^T triage  ^I intel  ^H dismissed  q quit"
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
        Binding("ctrl+l", "view_latest_inbox", "Latest", show=True),
        Binding("ctrl+r", "refresh_all", "Refresh", show=True),
        Binding("ctrl+h", "toggle_dismissed", "Dismissed", show=True),
        # Item actions
        Binding("d", "item_dismiss", "Dismiss", show=False),
        Binding("r", "item_reply_or_restore", "Reply/Restore", show=False),
        Binding("n", "item_note", "Note", show=False),
        Binding("a", "item_archive", "Archive", show=False),
        Binding("q", "quit", "Quit", show=True),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(id="tabs"):
            with TabPane("Inbox", id="tab-inbox"):
                yield InboxPane(id="inbox-pane")
            with TabPane("Projects", id="tab-projects"):
                yield ProjectsPane(id="projects-pane")
            with TabPane("Chat", id="tab-chat"):
                yield ChatPane(id="chat-pane")
        yield StatusBar(id="status-bar")
        yield Footer()

    async def on_event(self, event: events.Event) -> None:
        """Intercept space key for Input/TextArea widgets.

        Works around a Textual + Windows Terminal issue where the space
        key event is lost during the normal dispatch/binding chain.
        We catch it here *before* Textual's own ``on_event`` routing and
        insert the character directly into the focused widget.
        """
        if (
            isinstance(event, events.Key)
            and event.key == "space"
            and not event.is_forwarded
        ):
            focused = self.focused
            if isinstance(focused, Input):
                sel = focused.selection
                if sel.is_empty:
                    focused.insert_text_at_cursor(" ")
                else:
                    focused.replace(" ", *sel)
                event.stop()
                event.prevent_default()
                return
            if isinstance(focused, TextArea):
                focused.insert(" ")
                event.stop()
                event.prevent_default()
                return
        await super().on_event(event)

    def on_mount(self) -> None:
        self._prev_item_count = len(self.query_one(InboxPane)._items)
        # Periodic background tasks
        self.set_interval(30, self._auto_refresh_panes)
        self.set_interval(5, self._update_status_bar)
        self.set_interval(2, self._check_pending_question)

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        """Auto-focus chat input when switching to the Chat tab."""
        try:
            if self.query_one(TabbedContent).active == "tab-chat":
                self.query_one("#chat-input").focus()
        except Exception:
            pass

    # -------------------------------------------------------------------------
    # Periodic callbacks
    # -------------------------------------------------------------------------

    def _is_modal_open(self) -> bool:
        """Return True if a ModalScreen is currently displayed."""
        return any(isinstance(s, ModalScreen) for s in self.screen_stack[1:])

    def _auto_refresh_panes(self) -> None:
        """Reload all data panes every 30s. Play alert on new items.

        Skips refresh when a modal is open to avoid focus interference.
        """
        if self._is_modal_open():
            return
        try:
            inbox = self.query_one(InboxPane)
            inbox.load_data()
            self.query_one(ProjectsPane).load_data()
            new_count = len(inbox._items)
            if new_count > self._prev_item_count:
                _play_alert()
                self.bell()
            self._prev_item_count = new_count
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

    def action_view_latest_inbox(self) -> None:
        tabs = self.query_one(TabbedContent)
        tabs.active = "tab-inbox"
        self.query_one(InboxPane).load_data()

    def action_refresh_all(self) -> None:
        self._auto_refresh_panes()
        self.notify("All panes refreshed")

    def action_toggle_dismissed(self) -> None:
        self.query_one(InboxPane).toggle_dismissed()

    # -------------------------------------------------------------------------
    # Item actions (delegate to active pane)
    # -------------------------------------------------------------------------

    def _input_is_focused(self) -> bool:
        """Return True if an Input or TextArea widget currently has focus."""
        return isinstance(self.focused, (Input, TextArea))

    def action_item_dismiss(self) -> None:
        if self._input_is_focused():
            return
        pane = self._get_active_item_pane()
        if pane:
            pane.dismiss_selected()

    def action_item_reply_or_restore(self) -> None:
        """Reply on active items, Restore on dismissed items."""
        if self._input_is_focused():
            return
        pane = self._get_active_item_pane()
        if pane and pane.is_dismissed_selected():
            pane.restore_selected()
        elif pane:
            pane.reply_selected()

    def action_item_note(self) -> None:
        if self._input_is_focused():
            return
        pane = self._get_active_item_pane()
        if pane:
            pane.note_selected()

    def action_item_archive(self) -> None:
        if self._input_is_focused():
            return
        pane = self._get_active_item_pane()
        if pane and pane.is_dismissed_selected():
            pane.archive_selected()

    def _get_active_item_pane(self) -> InboxPane | None:
        """Return InboxPane if inbox tab is active."""
        tabs = self.query_one(TabbedContent)
        if tabs.active == "tab-inbox":
            return self.query_one(InboxPane)
        return None
