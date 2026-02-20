"""Telegram bot interface for Pulse Agent.

Conversational interface — just talk to it naturally:
  "what's new?"
  "did I miss anything in meetings yesterday?"
  "analyze parloa versus 11labs"

Slash commands for direct actions:
  /digest — run a full digest
  /triage — inbox triage
  /intel — external intel brief
  /transcripts — collect meeting transcripts
  /status — show agent status
  /latest — show latest digest

Chat history is managed by the GHCP SDK agent itself (reads/writes Pulse/chat-history.md).
"""

import asyncio
import html
import json
import re
import time
from datetime import datetime
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, ContextTypes, filters

from core.constants import OUTPUT_DIR
from core.logging import log
from tg.confirmations import has_pending_confirmation, resolve_confirmation
from tg.pii_filter import scrub as scrub_pii


def md_to_telegram_html(md_text: str) -> str:
    """Convert Markdown to Telegram-safe HTML.

    Handles the subset the agent actually produces: bold, italic,
    inline code, code blocks, and headers.  Everything else passes
    through as escaped plain text.
    """
    text = html.escape(md_text)

    # Code blocks (``` ... ```) -> <pre>
    text = re.sub(r"```(?:\w*)\n?(.*?)```", r"<pre>\1</pre>", text, flags=re.S)
    # Inline code (`...`) -> <code>
    text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)
    # Bold (**...**) -> <b>
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    # Italic (*...*) -> <i>  (only single *, after bold is already replaced)
    text = re.sub(r"\*(.+?)\*", r"<i>\1</i>", text)
    # Markdown headers (## Foo) -> bold line
    text = re.sub(r"^#{1,6}\s+(.+)$", r"<b>\1</b>", text, flags=re.M)

    return text


def split_message(text: str, max_len: int = 4000) -> list[str]:
    """Split text into chunks, preferring newline then space boundaries.

    Ported from OctoClaw's message_processor.split_message — avoids breaking
    mid-word or mid-sentence.
    """
    if len(text) <= max_len:
        return [text]
    chunks: list[str] = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        # Try splitting at last newline before limit
        split_at = text.rfind("\n", 0, max_len)
        if split_at < max_len // 2:
            # Fall back to last space
            split_at = text.rfind(" ", 0, max_len)
        if split_at < max_len // 2:
            # Hard cut as last resort
            split_at = max_len
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip()
    return chunks


class StreamingReply:
    """Progressively edits a Telegram message as LLM deltas arrive.

    Sends an initial placeholder, then throttled edits (~1/sec) as text
    accumulates. Final edit sends the complete HTML-formatted response.
    """

    EDIT_INTERVAL = 1.0  # seconds between edits
    MIN_CHARS_FOR_EDIT = 40  # don't edit for tiny increments

    def __init__(self, bot, chat_id: int):
        self._bot = bot
        self._chat_id = chat_id
        self._message_id: int | None = None
        self._buffer: list[str] = []
        self._last_edit = 0.0
        self._total_len = 0

    async def start(self):
        """Send the initial placeholder message."""
        msg = await self._bot.send_message(chat_id=self._chat_id, text="...")
        self._message_id = msg.message_id

    def on_delta(self, chunk: str):
        """Called for each text delta — accumulates and schedules edits."""
        self._buffer.append(chunk)
        self._total_len += len(chunk)

        now = time.monotonic()
        if (now - self._last_edit) >= self.EDIT_INTERVAL and self._total_len >= self.MIN_CHARS_FOR_EDIT:
            asyncio.get_event_loop().create_task(self._do_edit())

    async def _do_edit(self):
        """Edit the message with accumulated text so far."""
        if not self._message_id:
            return
        text = scrub_pii("".join(self._buffer))
        # Truncate preview to avoid Telegram limits (4096 chars)
        if len(text) > 3900:
            text = text[:3900] + "\n\n..."
        try:
            await self._bot.edit_message_text(
                chat_id=self._chat_id,
                message_id=self._message_id,
                text=text,
            )
            self._last_edit = time.monotonic()
        except Exception:
            pass  # Telegram may reject edits with same content

    async def finish(self):
        """Final edit with complete HTML-formatted text. Returns the full text."""
        full_text = scrub_pii("".join(self._buffer))
        if not self._message_id or not full_text:
            return full_text

        formatted = md_to_telegram_html(full_text)
        chunks = split_message(formatted)

        # Edit the first message with final formatted content
        try:
            await self._bot.edit_message_text(
                chat_id=self._chat_id,
                message_id=self._message_id,
                text=chunks[0],
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            # Fallback to plain text if HTML parsing fails
            try:
                await self._bot.edit_message_text(
                    chat_id=self._chat_id,
                    message_id=self._message_id,
                    text=chunks[0],
                )
            except Exception:
                pass

        # Send remaining chunks as new messages
        for chunk in chunks[1:]:
            try:
                await self._bot.send_message(
                    chat_id=self._chat_id, text=chunk, parse_mode=ParseMode.HTML,
                )
            except Exception:
                await self._bot.send_message(chat_id=self._chat_id, text=chunk)

        return full_text


class TelegramBot:
    """Telegram bot — all state as instance attributes, no module globals."""

    def __init__(self, config: dict, job_queue: asyncio.Queue):
        self.config = config
        self.job_queue = job_queue
        self.pending_confirmations: dict[int, asyncio.Future] = {}
        self.app: Application | None = None
        self._typing_tasks: dict[int, asyncio.Task] = {}
        self._boot_time = time.monotonic()

        # State file for persisting chat_id across restarts
        self.state_file = self._resolve_state_file()

    def _resolve_state_file(self) -> Path:
        """Determine where to save chat state."""
        onedrive_cfg = self.config.get("onedrive", {})
        if onedrive_cfg.get("sync_enabled", False):
            path = Path(onedrive_cfg.get("path", ""))
            if path and str(path) != ".":
                return path / ".chat-state.json"
        return OUTPUT_DIR / ".chat-state.json"

    def _load_chat_id(self) -> int | None:
        """Load saved chat_id from state file."""
        if self.state_file and self.state_file.exists():
            try:
                data = json.loads(self.state_file.read_text(encoding="utf-8"))
                return data.get("chat_id")
            except Exception:
                pass
        return None

    def _save_chat_id(self, chat_id: int):
        """Persist chat_id so proactive messages survive restarts."""
        if self.state_file:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(json.dumps({"chat_id": chat_id}), encoding="utf-8")

    def get_proactive_chat_id(self) -> int | None:
        """Get the chat_id for proactive messages (heartbeat results, etc.)."""
        return self._load_chat_id()

    def _is_authorized(self, update: Update) -> bool:
        """Check if the user is in the allowed_users list (empty = allow all)."""
        allowed = self.config.get("telegram", {}).get("allowed_users", [])
        if not allowed:
            return True
        return update.effective_user.id in allowed

    async def start(self) -> Application | None:
        """Start the Telegram bot. Returns the Application or None if disabled."""
        tg_config = self.config.get("telegram", {})
        if not tg_config.get("enabled", False):
            return None

        token = tg_config.get("bot_token", "")
        if not token:
            log.warning("Telegram enabled but no bot_token configured")
            return None

        app = Application.builder().token(token).build()

        # Slash commands
        app.add_handler(CommandHandler("start", self._cmd_start))
        app.add_handler(CommandHandler("help", self._cmd_start))
        app.add_handler(CommandHandler("latest", self._cmd_latest))
        app.add_handler(CommandHandler("digest", self._cmd_job))
        app.add_handler(CommandHandler("triage", self._cmd_job))
        app.add_handler(CommandHandler("intel", self._cmd_job))
        app.add_handler(CommandHandler("transcripts", self._cmd_job))
        app.add_handler(CommandHandler("status", self._cmd_status))

        # Inline button callbacks (actions from triage)
        app.add_handler(CallbackQueryHandler(self._handle_callback))

        # All non-command text -> LLM chat (no keyword matching)
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message))

        await app.initialize()
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)

        self.app = app
        log.info("Telegram bot started")
        return app

    async def stop(self):
        """Gracefully stop the Telegram bot."""
        if self.app is None:
            return
        try:
            for task in self._typing_tasks.values():
                task.cancel()
            self._typing_tasks.clear()
            await self.app.updater.stop()
            await self.app.stop()
            await self.app.shutdown()
            log.info("Telegram bot stopped")
        except Exception as e:
            log.warning(f"Telegram shutdown error: {e}")

    async def notify(self, chat_id: int, text: str):
        """Send a notification message to a Telegram chat (HTML formatted)."""
        if self.app is None:
            return
        self.stop_typing(chat_id)
        try:
            formatted = md_to_telegram_html(text)
            await self._send_chunked(self.app.bot, chat_id, formatted, ParseMode.HTML)
        except Exception as e:
            log.warning(f"Telegram notify failed: {e}")

    async def send_latest_digest(self, chat_id: int):
        """Send the most recent digest to a chat, with inline action buttons."""
        if self.app is None:
            return
        digests_dir = OUTPUT_DIR / "digests"
        if not digests_dir.exists():
            await self.app.bot.send_message(chat_id=chat_id, text="No digests yet.")
            return

        md_files = sorted(digests_dir.glob("*.md"), reverse=True)
        if not md_files:
            await self.app.bot.send_message(chat_id=chat_id, text="No digests yet.")
            return

        content = md_files[0].read_text(encoding="utf-8")
        formatted = md_to_telegram_html(content)

        # Build inline keyboard from the corresponding digest JSON
        markup = self._build_digest_keyboard(digests_dir)

        await self._send_chunked(self.app.bot, chat_id, formatted, ParseMode.HTML,
                                 reply_markup=markup)

    def _build_digest_keyboard(self, digests_dir) -> InlineKeyboardMarkup | None:
        """Build an InlineKeyboardMarkup from the latest digest JSON."""
        import json as _json
        json_files = sorted(digests_dir.glob("*.json"), reverse=True)
        if not json_files:
            return None
        try:
            data = _json.loads(json_files[0].read_text(encoding="utf-8"))
        except Exception:
            return None

        items = data.get("items", [])
        actionable = [i for i in items if i.get("suggested_actions")]
        if not actionable:
            return None

        buttons = []
        for item in actionable:
            item_id = item.get("id", "unknown")
            for i, action in enumerate(item.get("suggested_actions", [])):
                label = action.get("label", "Action")[:30]
                cb = f"action:{item_id}:{i}"
                if len(cb) > 64:
                    cb = f"action:{item_id[:50]}:{i}"
                buttons.append([InlineKeyboardButton(label, callback_data=cb)])
            # Dismiss button per item
            dismiss_cb = f"dismiss:{item_id}"
            if len(dismiss_cb) > 64:
                dismiss_cb = f"dismiss:{item_id[:56]}"
            buttons.append([InlineKeyboardButton(
                f"Dismiss: {item.get('title', item_id)[:20]}", callback_data=dismiss_cb
            )])

        return InlineKeyboardMarkup(buttons) if buttons else None

    # --- Typing indicators ---

    def start_typing(self, chat_id: int):
        """Start showing 'typing...' indicator. Cancels any existing typing task."""
        self.stop_typing(chat_id)
        if self.app:
            self._typing_tasks[chat_id] = asyncio.create_task(
                self._typing_loop(chat_id)
            )

    def stop_typing(self, chat_id: int):
        """Stop the typing indicator for a chat."""
        task = self._typing_tasks.pop(chat_id, None)
        if task:
            task.cancel()

    async def _typing_loop(self, chat_id: int):
        """Send typing action every 5 seconds until cancelled."""
        try:
            while True:
                try:
                    await self.app.bot.send_chat_action(
                        chat_id=chat_id, action=ChatAction.TYPING
                    )
                except Exception:
                    pass
                await asyncio.sleep(5)
        except asyncio.CancelledError:
            pass

    # --- Handlers ---

    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            return
        self._save_chat_id(update.effective_chat.id)
        await update.message.reply_text(
            "<b>Pulse Agent</b> here. Just talk to me naturally.\n\n"
            "<b>Commands:</b>\n"
            "/digest — run a full digest\n"
            "/triage — inbox triage\n"
            "/intel — external intel brief\n"
            "/transcripts — collect meeting transcripts\n"
            "/status — show agent status\n"
            "/latest — show latest digest\n\n"
            "Or just ask me anything — I'll handle it.",
            parse_mode=ParseMode.HTML,
        )

    async def _cmd_latest(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Send the latest digest directly (no LLM needed)."""
        if not self._is_authorized(update):
            return
        self._save_chat_id(update.effective_chat.id)
        await self.send_latest_digest(update.effective_chat.id)

    async def _cmd_job(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /digest, /triage, /intel, /transcripts — queue the job."""
        if not self._is_authorized(update):
            return
        chat_id = update.effective_chat.id
        self._save_chat_id(chat_id)

        # Extract command name (handles /digest@BotName too)
        cmd = update.message.text.strip().split()[0].lstrip("/").split("@")[0].lower()

        job_types = {
            "digest": "digest",
            "triage": "monitor",
            "intel": "intel",
            "transcripts": "transcripts",
        }
        labels = {
            "digest": "Digest",
            "triage": "Triage",
            "intel": "Intel brief",
            "transcripts": "Transcript collection",
        }

        job_type = job_types.get(cmd)
        if not job_type:
            return

        self.job_queue.put_nowait({
            "type": job_type,
            "_source": "telegram",
            "_chat_id": chat_id,
        })
        self.start_typing(chat_id)
        await update.message.reply_text(
            f"{html.escape(labels.get(cmd, cmd))} started.",
            parse_mode=ParseMode.HTML,
        )

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show daemon status."""
        if not self._is_authorized(update):
            return
        self._save_chat_id(update.effective_chat.id)

        uptime_s = int(time.monotonic() - self._boot_time)
        hours, rem = divmod(uptime_s, 3600)
        minutes, secs = divmod(rem, 60)
        queue_size = self.job_queue.qsize()

        lines = [
            "<b>Pulse Agent Status</b>",
            f"Uptime: {hours}h {minutes}m {secs}s",
            f"Queue: {queue_size} pending job{'s' if queue_size != 1 else ''}",
        ]
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

    async def _handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle any text message — all natural language goes to the LLM."""
        if not self._is_authorized(update):
            return

        text = update.message.text.strip()
        if not text:
            return

        chat_id = update.effective_chat.id
        self._save_chat_id(chat_id)

        # If there's a pending confirmation (e.g., Teams send), resolve it
        if has_pending_confirmation(self.pending_confirmations, chat_id):
            resolve_confirmation(self.pending_confirmations, chat_id, text)
            return

        # Everything goes to the LLM — no keyword matching
        self.job_queue.put_nowait({
            "type": "chat",
            "prompt": text,
            "_source": "telegram",
            "_chat_id": chat_id,
        })
        self.start_typing(chat_id)

    # --- Action buttons (triage output) ---

    async def send_triage_actions(self, chat_id: int, triage_json: dict):
        """Render triage items as Telegram messages with inline action buttons.

        Each actionable item becomes a message with context and buttons for
        suggested actions. Tapping a button shows the draft for review.
        """
        if self.app is None:
            return

        items = triage_json.get("items", [])
        actionable = [i for i in items if i.get("suggested_actions")]
        if not actionable:
            return

        for item in actionable:
            priority = item.get("priority", "medium").upper()
            source = html.escape(item.get("source", "Unknown"))
            summary = html.escape(item.get("summary", ""))
            context = html.escape(item.get("context", ""))
            item_id = item.get("id", "unknown")

            text = (
                f"<b>[{priority}]</b> {source}\n"
                f"{summary}\n"
            )
            if context:
                text += f"\n<i>{context}</i>\n"

            # Build inline keyboard from suggested actions
            buttons = []
            for i, action in enumerate(item.get("suggested_actions", [])):
                label = action.get("label", "Action")[:30]
                # Callback data: action index + item id (max 64 bytes for Telegram)
                callback_data = f"action:{item_id}:{i}"
                if len(callback_data) > 64:
                    callback_data = f"action:{item_id[:50]}:{i}"
                buttons.append([InlineKeyboardButton(label, callback_data=callback_data)])

            # Add dismiss button
            dismiss_data = f"dismiss:{item_id}"
            if len(dismiss_data) > 64:
                dismiss_data = f"dismiss:{item_id[:56]}"
            buttons.append([InlineKeyboardButton("Dismiss", callback_data=dismiss_data)])

            markup = InlineKeyboardMarkup(buttons)

            try:
                await self.app.bot.send_message(
                    chat_id=chat_id, text=text,
                    parse_mode=ParseMode.HTML, reply_markup=markup,
                )
            except Exception as e:
                log.warning(f"Failed to send action message: {e}")

    async def _handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle inline button taps — show draft for review or dismiss items."""
        query = update.callback_query
        if not query:
            return
        await query.answer()

        if not self._is_authorized(update):
            return

        chat_id = query.message.chat_id
        data = query.data or ""

        if data.startswith("dismiss:"):
            item_id = data[len("dismiss:"):]
            # Dismiss via the SDK tool
            from sdk.tools import load_actions, _save_actions
            actions = load_actions()
            actions["dismissed"].append({
                "item": item_id,
                "dismissed_at": datetime.now().isoformat(),
                "reason": "dismissed via Telegram",
            })
            _save_actions(actions)

            await query.edit_message_reply_markup(reply_markup=None)
            await self.app.bot.send_message(
                chat_id=chat_id,
                text=f"Dismissed: <code>{html.escape(item_id)}</code>",
                parse_mode=ParseMode.HTML,
            )

        elif data.startswith("action:"):
            # Parse action:item_id:action_index
            parts = data.split(":")
            if len(parts) < 3:
                return
            item_id = parts[1]
            try:
                action_idx = int(parts[2])
            except ValueError:
                return

            # Load the triage JSON to find the draft
            draft_info = self._find_action_draft(item_id, action_idx)
            if not draft_info:
                await self.app.bot.send_message(
                    chat_id=chat_id, text="Could not find draft — triage data may have been overwritten.",
                )
                return

            # Show draft for review with send/edit/cancel buttons
            draft_text = html.escape(draft_info.get("draft", ""))
            target = html.escape(draft_info.get("target", ""))
            action_type = draft_info.get("action_type", "")

            review_text = (
                f"<b>Draft for {target}:</b>\n\n"
                f"{draft_text}\n\n"
                f"<i>Type: {action_type}</i>"
            )

            send_data = f"send:{item_id}:{action_idx}"
            if len(send_data) > 64:
                send_data = f"send:{item_id[:52]}:{action_idx}"
            cancel_data = f"cancel:{item_id}"
            if len(cancel_data) > 64:
                cancel_data = f"cancel:{item_id[:55]}"

            markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("Send", callback_data=send_data)],
                [InlineKeyboardButton("Cancel", callback_data=cancel_data)],
            ])

            await self.app.bot.send_message(
                chat_id=chat_id, text=review_text,
                parse_mode=ParseMode.HTML, reply_markup=markup,
            )

        elif data.startswith("send:"):
            # User approved — execute via deterministic senders (no LLM needed)
            parts = data.split(":")
            if len(parts) < 3:
                return
            item_id = parts[1]
            try:
                action_idx = int(parts[2])
            except ValueError:
                return

            draft_info = self._find_action_draft(item_id, action_idx)
            if not draft_info:
                await self.app.bot.send_message(chat_id=chat_id, text="Draft not found.")
                return

            target = draft_info.get("target", "")
            draft = draft_info.get("draft", "")
            action_type = draft_info.get("action_type", "draft_teams_reply")
            metadata = draft_info.get("metadata", "")

            await query.edit_message_reply_markup(reply_markup=None)

            # Route to deterministic senders for Teams/email, keep chat for meetings
            if action_type == "schedule_meeting":
                # Meeting scheduling still needs LLM (Copilot Chat interaction)
                prompt = f"Schedule a meeting: {metadata or draft}"
                self.job_queue.put_nowait({
                    "type": "chat",
                    "prompt": prompt,
                    "_source": "telegram_action",
                    "_chat_id": chat_id,
                })
                self.start_typing(chat_id)
                await self.app.bot.send_message(
                    chat_id=chat_id,
                    text="Scheduling meeting via Copilot...",
                    parse_mode=ParseMode.HTML,
                )
            else:
                # Teams and email: queue direct execution (no SDK/LLM)
                job_type = "email_reply" if action_type == "send_email_reply" else "teams_send"
                self.job_queue.put_nowait({
                    "type": job_type,
                    "recipient": target,
                    "message": draft,
                    "search_query": target,
                    "_source": "telegram_action",
                    "_chat_id": chat_id,
                })
                self.start_typing(chat_id)
                label = "Replying to email" if job_type == "email_reply" else "Sending Teams message"
                await self.app.bot.send_message(
                    chat_id=chat_id,
                    text=f"{label} to <b>{html.escape(target)}</b>...",
                    parse_mode=ParseMode.HTML,
                )

        elif data.startswith("cancel:"):
            await query.edit_message_reply_markup(reply_markup=None)
            await self.app.bot.send_message(chat_id=chat_id, text="Cancelled.")

    @staticmethod
    def _build_action_prompt(action_type: str, target: str, draft: str, metadata: str) -> tuple[str, str]:
        """Build the chat prompt and status text for a triage action.

        Returns (prompt, status_text) tuple. The prompt is sent to the SDK
        agent, which picks the right skill based on the instruction.
        """
        if action_type == "send_email_reply":
            return (
                f"Reply to the email from {target} with this message: {draft}",
                f"Replying to <b>{html.escape(target)}</b>'s email...",
            )
        elif action_type == "schedule_meeting":
            return (
                f"Schedule a meeting: {metadata or draft}",
                f"Scheduling meeting via Copilot...",
            )
        else:
            # Default: Teams reply (draft_teams_reply or any unrecognized type)
            return (
                f"Send this Teams message to {target}: {draft}",
                f"Sending to <b>{html.escape(target)}</b>...",
            )

    def _find_action_draft(self, item_id: str, action_idx: int) -> dict | None:
        """Find a specific action draft from the latest triage or digest JSON."""
        # Search monitoring JSONs first, then digest JSONs
        for pattern in ("monitoring-*.json", "digests/*.json"):
            reports = sorted(OUTPUT_DIR.glob(pattern), reverse=True)
            if not reports:
                continue
            try:
                data = json.loads(reports[0].read_text(encoding="utf-8"))
                for item in data.get("items", []):
                    if item.get("id") == item_id:
                        actions = item.get("suggested_actions", [])
                        if 0 <= action_idx < len(actions):
                            return actions[action_idx]
            except Exception:
                log.warning(f"Failed to load {pattern} for action draft", exc_info=True)
        return None

    # --- Send helpers ---

    async def _send(self, bot, chat_id: int, text: str, parse_mode=None,
                    reply_markup=None):
        """Send a single message, falling back to plain text on parse errors."""
        text = scrub_pii(text)
        try:
            await bot.send_message(chat_id=chat_id, text=text,
                                   parse_mode=parse_mode,
                                   reply_markup=reply_markup)
        except Exception:
            if parse_mode is not None:
                await bot.send_message(chat_id=chat_id, text=text,
                                       reply_markup=reply_markup)
            else:
                raise

    async def _send_chunked(self, bot, chat_id: int, text: str, parse_mode=None,
                            reply_markup=None):
        """Send text, splitting at natural boundaries if needed.

        If reply_markup is provided, it is attached to the last chunk only.
        """
        chunks = split_message(text)
        for i, chunk in enumerate(chunks):
            is_last = (i == len(chunks) - 1)
            await self._send(bot, chat_id, chunk, parse_mode,
                             reply_markup=reply_markup if is_last else None)


# --- Module-level convenience functions for backward compatibility ---
# These are used by daemon/worker.py and sdk/session.py during the transition.

_bot_instance: TelegramBot | None = None


async def start_telegram_bot(config: dict, job_queue: asyncio.Queue) -> Application | None:
    """Start the Telegram bot. Returns the Application or None if disabled."""
    global _bot_instance
    _bot_instance = TelegramBot(config, job_queue)
    return await _bot_instance.start()


async def stop_telegram_bot(app: Application | None):
    """Gracefully stop the Telegram bot."""
    if _bot_instance:
        await _bot_instance.stop()


async def notify(app: Application | None, chat_id: int, text: str):
    """Send a notification — delegates to the bot instance."""
    if _bot_instance:
        await _bot_instance.notify(chat_id, text)


async def send_latest_digest(chat_id: int, app: Application | None = None):
    """Send the latest digest — delegates to the bot instance."""
    if _bot_instance:
        await _bot_instance.send_latest_digest(chat_id)


def get_proactive_chat_id() -> int | None:
    """Get saved chat_id for proactive messages."""
    if _bot_instance:
        return _bot_instance.get_proactive_chat_id()
    return None


def start_typing(chat_id: int):
    """Start typing indicator — delegates to the bot instance."""
    if _bot_instance:
        _bot_instance.start_typing(chat_id)


def stop_typing(chat_id: int):
    """Stop typing indicator — delegates to the bot instance."""
    if _bot_instance:
        _bot_instance.stop_typing(chat_id)


async def create_streaming_reply(chat_id: int) -> StreamingReply | None:
    """Create a StreamingReply for progressive message editing."""
    if _bot_instance and _bot_instance.app:
        reply = StreamingReply(_bot_instance.app.bot, chat_id)
        await reply.start()
        return reply
    return None


async def send_triage_actions(chat_id: int, triage_json: dict):
    """Send triage action buttons — delegates to the bot instance."""
    if _bot_instance:
        await _bot_instance.send_triage_actions(chat_id, triage_json)


async def wait_for_confirmation(chat_id: int, timeout: float = 120) -> str:
    """Wait for a user confirmation — delegates to confirmations module."""
    if _bot_instance:
        from tg.confirmations import wait_for_confirmation as _wait
        return await _wait(_bot_instance.pending_confirmations, chat_id, timeout)
    raise RuntimeError("Telegram bot not started")
