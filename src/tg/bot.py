"""Telegram bot interface for Pulse Agent.

Conversational interface — just talk to it naturally:
  "what's new?"
  "did I miss anything in meetings yesterday?"
  "analyze parloa versus 11labs"
  "run a digest"
  "grab this week's transcripts"

Also sends proactive notifications when jobs complete.
Chat history is managed by the GHCP SDK agent itself (reads/writes Pulse/chat-history.md).
"""

import asyncio
import html
import json
import re
from pathlib import Path

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

from core.constants import OUTPUT_DIR
from core.logging import log
from tg.confirmations import has_pending_confirmation, resolve_confirmation


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


# --- Quick-action keywords -> job types ---
_QUICK_ACTIONS = {
    "digest": "digest",
    "run digest": "digest",
    "run a digest": "digest",
    "morning digest": "digest",
    "triage": "monitor",
    "run triage": "monitor",
    "intel": "intel",
    "intel brief": "intel",
    "run intel": "intel",
    "transcripts": "transcripts",
    "grab transcripts": "transcripts",
    "collect transcripts": "transcripts",
}


class TelegramBot:
    """Telegram bot — all state as instance attributes, no module globals."""

    def __init__(self, config: dict, job_queue: asyncio.Queue):
        self.config = config
        self.job_queue = job_queue
        self.pending_confirmations: dict[int, asyncio.Future] = {}
        self.app: Application | None = None

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

        # Register handlers (pass self to closures)
        app.add_handler(CommandHandler("start", self._cmd_start))
        app.add_handler(CommandHandler("help", self._cmd_start))
        app.add_handler(CommandHandler("latest", self._cmd_latest))
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
        try:
            formatted = md_to_telegram_html(text)
            await self._send_chunked(self.app.bot, chat_id, formatted, ParseMode.HTML)
        except Exception as e:
            log.warning(f"Telegram notify failed: {e}")

    async def send_latest_digest(self, chat_id: int):
        """Send the most recent digest to a chat."""
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
        await self._send_chunked(self.app.bot, chat_id, formatted, ParseMode.HTML)

    # --- Handlers ---

    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            return
        self._save_chat_id(update.effective_chat.id)
        await update.message.reply_text(
            "<b>Pulse Agent</b> here. Just talk to me:\n\n"
            "<code>What's new?</code> — quick triage of your inbox\n"
            "<code>Run a digest</code> — full digest\n"
            "<code>Analyze X vs Y</code> — deep research task\n"
            "<code>Did I miss anything yesterday?</code> — check recent activity\n\n"
            "I'll also send you proactive updates during office hours.",
            parse_mode=ParseMode.HTML,
        )

    async def _cmd_latest(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Send the latest digest directly (no LLM needed)."""
        if not self._is_authorized(update):
            return
        self._save_chat_id(update.effective_chat.id)
        await self.send_latest_digest(update.effective_chat.id)

    async def _handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle any text message — route to quick action or conversational query."""
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

        # Check for quick actions first
        action = _match_quick_action(text)
        if action:
            self.job_queue.put_nowait({
                "type": action,
                "_source": "telegram",
                "_chat_id": chat_id,
            })
            labels = {
                "digest": "Digest", "monitor": "Triage",
                "intel": "Intel brief", "transcripts": "Transcript collection",
            }
            await update.message.reply_text(
                f"{html.escape(labels.get(action, action))} started.",
                parse_mode=ParseMode.HTML,
            )
            return

        # Everything else -> conversational query via GHCP SDK
        self.job_queue.put_nowait({
            "type": "chat",
            "prompt": text,
            "_source": "telegram",
            "_chat_id": chat_id,
        })

    # --- Send helpers ---

    async def _send(self, bot, chat_id: int, text: str, parse_mode=None):
        """Send a single message, falling back to plain text on parse errors."""
        try:
            await bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode)
        except Exception:
            if parse_mode is not None:
                await bot.send_message(chat_id=chat_id, text=text)
            else:
                raise

    async def _send_chunked(self, bot, chat_id: int, text: str, parse_mode=None):
        """Send text, splitting into 4000-char chunks if needed."""
        if len(text) > 4000:
            chunks = [text[i:i + 4000] for i in range(0, len(text), 4000)]
            for chunk in chunks:
                await self._send(bot, chat_id, chunk, parse_mode)
        else:
            await self._send(bot, chat_id, text, parse_mode)


def _match_quick_action(text: str) -> str | None:
    """Check if the message matches a known quick action."""
    lower = text.strip().lower()
    for trigger, job_type in _QUICK_ACTIONS.items():
        if lower == trigger or lower == f"/{trigger}":
            return job_type
    return None


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


async def wait_for_confirmation(chat_id: int, timeout: float = 120) -> str:
    """Wait for a user confirmation — delegates to confirmations module."""
    if _bot_instance:
        from tg.confirmations import wait_for_confirmation as _wait
        return await _wait(_bot_instance.pending_confirmations, chat_id, timeout)
    raise RuntimeError("Telegram bot not started")
