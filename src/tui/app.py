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

import logging
import threading

from textual import events

log = logging.getLogger(__name__)
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.theme import Theme
from textual.widget import Widget
from textual.widgets import Footer, Header, Input, Static, TabbedContent, TabPane, TextArea

from textual.screen import ModalScreen


# ---------------------------------------------------------------------------
# Custom Pulse brand theme — cyan/green/amber on deep dark
# ---------------------------------------------------------------------------

PULSE_THEME = Theme(
    name="pulse-dark",
    primary="#00D4FF",       # Cyan — borders, tabs, focus
    secondary="#00CC88",     # Green — success, agent messages
    accent="#FFB020",        # Amber — alerts, attention
    warning="#FFB020",       # Amber
    error="#FF3366",         # Hot pink — errors, urgent
    success="#00CC88",       # Green
    foreground="#B8C4D8",    # Cool light gray
    background="#0A0E14",    # Near-black (deep space)
    surface="#0F1923",       # Slightly lighter for content areas
    panel="#162233",         # Panel borders/backgrounds
    dark=True,
    variables={
        "footer-key-foreground": "#00D4FF",
        "footer-description-foreground": "#5A6A80",
        "footer-background": "#0A0E14",
        "input-cursor-background": "#00D4FF",
        "block-cursor-background": "#00D4FF",
        "scrollbar": "#1A3040",
        "scrollbar-hover": "#00D4FF 40%",
    },
)

from tui.ipc import queue_job, read_daemon_status, read_pending_question
from tui.screens import (
    ChatPane,
    HelpModal,
    InboxPane,
    JobsPane,
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
        from datetime import datetime
        status = read_daemon_status()
        if status:
            # Check if status file is stale (daemon died without cleanup)
            updated_at = status.get("updated_at", "")
            try:
                last_update = datetime.fromisoformat(updated_at)
                age_s = (datetime.now() - last_update).total_seconds()
            except (ValueError, TypeError):
                age_s = 999
            if age_s > 120:  # >2 min stale = daemon is dead
                self.update(
                    " Daemon: offline — start with: python src/pulse.py"
                )
                return
            uptime_s = status.get("uptime_s", 0)
            h, m = divmod(uptime_s // 60, 60)
            uptime_str = f"{h}h{m:02d}m" if h else f"{m}m"
            queue_size = status.get("queue_size", 0)

            # Show current running job (if any)
            cur_job = status.get("current_job")
            job_part = ""
            if cur_job:
                started = status.get("current_job_started", "")
                try:
                    started_dt = datetime.fromisoformat(started)
                    elapsed_s = int((datetime.now() - started_dt).total_seconds())
                    em, es = divmod(elapsed_s, 60)
                    elapsed_str = f"{em}m" if em else f"{es}s"
                    job_part = f"  |  Running: {cur_job} ({elapsed_str})"
                except (ValueError, TypeError):
                    job_part = f"  |  Running: {cur_job}"

            self.update(
                f" Daemon: up {uptime_str}  |  Queue: {queue_size}{job_part}"
            )
        else:
            self.update(
                " Daemon: offline — start with: python src/pulse.py"
            )


class PulseApp(App):
    """Pulse Agent interactive terminal dashboard."""

    CSS_PATH = "styles.tcss"

    TITLE = "Pulse Agent"

    # Set by the entry point when onboarding is needed
    needs_onboarding: bool = False

    BINDINGS = [
        # Job triggers (hidden from footer — shown in help modal)
        Binding("ctrl+d", "trigger_digest", "Digest", show=False),
        Binding("ctrl+t", "trigger_triage", "Triage", show=False),
        Binding("ctrl+i", "trigger_intel", "Intel", show=False),
        Binding("ctrl+x", "trigger_transcripts", "Transcripts", show=False),
        Binding("ctrl+l", "view_latest_inbox", "Latest", show=False),
        Binding("ctrl+r", "refresh_all", "Refresh", show=False),
        Binding("ctrl+h", "toggle_dismissed", "Dismissed", show=False),
        Binding("ctrl+e", "clear_chat", "Clear Chat", show=False),
        Binding("ctrl+j", "view_jobs", "Jobs", show=False),
        # Item actions
        Binding("d", "item_dismiss", "Dismiss", show=False),
        Binding("r", "item_reply_or_restore", "Reply/Restore", show=False),
        Binding("n", "item_note", "Note", show=False),
        Binding("a", "item_archive", "Archive", show=False),
        # Always visible
        Binding("question_mark", "show_help", "? Help", show=True),
        Binding("ctrl+p", "command_palette", "Palette", show=True),
        Binding("q", "quit", "Quit", show=True),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(id="tabs"):
            with TabPane("Inbox", id="tab-inbox"):
                yield InboxPane(id="inbox-pane")
            with TabPane("Projects", id="tab-projects"):
                yield ProjectsPane(id="projects-pane")
            with TabPane("Jobs", id="tab-jobs"):
                yield JobsPane(id="jobs-pane")
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
        # Apply Pulse brand theme
        self.register_theme(PULSE_THEME)
        self.theme = "pulse-dark"

        self._prev_item_count = len(self.query_one(InboxPane)._items)
        self._update_tab_labels()
        # Periodic background tasks
        self.set_interval(30, self._auto_refresh_panes)
        self.set_interval(5, self._update_status_bar)
        self.set_interval(2, self._check_pending_question)

        # First-run onboarding — switch to Chat and auto-queue setup prompt
        if self.needs_onboarding:
            self._trigger_onboarding()

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
            self.query_one(JobsPane).load_data()
            self._update_tab_labels()
            new_count = len(inbox._items)
            if new_count > self._prev_item_count:
                _play_alert()
                self.bell()
            self._prev_item_count = new_count
        except Exception:
            log.debug("Failed to refresh panes", exc_info=True)

    def _update_tab_labels(self) -> None:
        """Update tab labels with item/project counts."""
        try:
            tabs = self.query_one(TabbedContent)
            inbox = self.query_one(InboxPane)
            active_count = sum(1 for i in inbox._items if not i.get("_is_dismissed"))
            dismissed = inbox._dismissed_count

            if dismissed and not inbox._show_dismissed:
                label = f"Inbox ({active_count} + {dismissed} hidden)"
            elif active_count:
                label = f"Inbox ({active_count})"
            else:
                label = "Inbox"
            tabs.get_tab("tab-inbox").label = label

            proj_count = len(self.query_one(ProjectsPane)._projects)
            tabs.get_tab("tab-projects").label = (
                f"Projects ({proj_count})" if proj_count else "Projects"
            )

            jobs_pane = self.query_one(JobsPane)
            active_jobs = jobs_pane.get_active_count()
            total_jobs = len(jobs_pane._jobs)
            if active_jobs:
                tabs.get_tab("tab-jobs").label = f"Jobs ({active_jobs} active)"
            elif total_jobs:
                tabs.get_tab("tab-jobs").label = f"Jobs ({total_jobs})"
            else:
                tabs.get_tab("tab-jobs").label = "Jobs"
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
                        # Delete file BEFORE pushing modal to prevent re-triggering on next tick
                        try:
                            from tui.ipc import PENDING_QUESTION_FILE
                            PENDING_QUESTION_FILE.unlink(missing_ok=True)
                        except Exception:
                            pass
                        self.push_screen(QuestionModal(question, session_id))
        except Exception:
            pass

    # -------------------------------------------------------------------------
    # Job trigger actions
    # -------------------------------------------------------------------------

    def _trigger_onboarding(self) -> None:
        """Switch to Chat tab for onboarding.

        No job is auto-queued — the user's first message naturally triggers
        onboarding because the worker detects is_first_run(config) and injects
        the onboarding prompt when the chat session is fresh.
        """
        tabs = self.query_one(TabbedContent)
        tabs.active = "tab-chat"
        self.notify(
            "Welcome to Pulse Agent! Type anything in Chat to start setup.",
            title="First Run",
            timeout=8,
        )
        self.needs_onboarding = False

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
        self._update_tab_labels()

    def action_view_jobs(self) -> None:
        tabs = self.query_one(TabbedContent)
        tabs.active = "tab-jobs"
        self.query_one(JobsPane).load_data()

    def action_refresh_all(self) -> None:
        self._auto_refresh_panes()
        self.notify("All panes refreshed")

    def action_toggle_dismissed(self) -> None:
        self.query_one(InboxPane).toggle_dismissed()
        self._update_tab_labels()

    def action_show_help(self) -> None:
        """Show keybindings help modal (?)."""
        if not any(isinstance(s, HelpModal) for s in self.screen_stack):
            self.push_screen(HelpModal())

    def action_clear_chat(self) -> None:
        """Clear chat log (Ctrl+E)."""
        tabs = self.query_one(TabbedContent)
        if tabs.active == "tab-chat":
            self.query_one(ChatPane).clear_chat()
            self.notify("Chat cleared")

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
        if pane:
            pane.archive_selected()

    def _get_active_item_pane(self) -> InboxPane | None:
        """Return InboxPane if inbox tab is active."""
        tabs = self.query_one(TabbedContent)
        if tabs.active == "tab-inbox":
            return self.query_one(InboxPane)
        return None
