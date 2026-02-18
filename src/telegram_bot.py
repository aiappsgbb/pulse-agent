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
import json
from pathlib import Path

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

from utils import log

OUTPUT_DIR = Path(__file__).parent.parent / "output"

# Set by start_telegram_bot() — shared with main.py
_job_queue = None
_config: dict = {}
_state_file: Path | None = None

# Pending confirmations — keyed by chat_id, value is an asyncio.Future
# Used by ask_user handler to pause agent execution until user replies
_pending_confirmations: dict[int, asyncio.Future] = {}


def has_pending_confirmation(chat_id: int) -> bool:
    """Check if there's a confirmation waiting for this chat."""
    return chat_id in _pending_confirmations


def resolve_confirmation(chat_id: int, answer: str):
    """Resolve a pending confirmation with the user's answer."""
    fut = _pending_confirmations.pop(chat_id, None)
    if fut and not fut.done():
        fut.get_loop().call_soon_threadsafe(fut.set_result, answer)


async def wait_for_confirmation(chat_id: int, timeout: float = 120) -> str:
    """Block until the user replies to a confirmation prompt. Raises TimeoutError."""
    loop = asyncio.get_running_loop()
    fut = loop.create_future()
    _pending_confirmations[chat_id] = fut
    try:
        return await asyncio.wait_for(fut, timeout=timeout)
    except asyncio.TimeoutError:
        _pending_confirmations.pop(chat_id, None)
        raise


def _get_pulse_dir() -> Path | None:
    """Get the OneDrive Pulse folder path from config."""
    onedrive_cfg = _config.get("onedrive", {})
    if not onedrive_cfg.get("sync_enabled", False):
        return None
    path = Path(onedrive_cfg.get("path", ""))
    if not path or str(path) == ".":
        return None
    return path


def _load_chat_id() -> int | None:
    """Load saved chat_id from state file."""
    if _state_file and _state_file.exists():
        try:
            data = json.loads(_state_file.read_text(encoding="utf-8"))
            return data.get("chat_id")
        except Exception:
            pass
    return None


def _save_chat_id(chat_id: int):
    """Persist chat_id so proactive messages survive restarts."""
    if _state_file:
        _state_file.parent.mkdir(parents=True, exist_ok=True)
        _state_file.write_text(json.dumps({"chat_id": chat_id}), encoding="utf-8")


def get_proactive_chat_id() -> int | None:
    """Get the chat_id for proactive messages (heartbeat results, etc.)."""
    return _load_chat_id()


def _is_authorized(update: Update) -> bool:
    """Check if the user is in the allowed_users list (empty = allow all)."""
    allowed = _config.get("telegram", {}).get("allowed_users", [])
    if not allowed:
        return True
    return update.effective_user.id in allowed


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


def _match_quick_action(text: str) -> str | None:
    """Check if the message matches a known quick action."""
    lower = text.strip().lower()
    for trigger, job_type in _QUICK_ACTIONS.items():
        if lower == trigger or lower == f"/{trigger}":
            return job_type
    return None


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle any text message — route to quick action or conversational query."""
    if not _is_authorized(update):
        return

    text = update.message.text.strip()
    if not text:
        return

    chat_id = update.effective_chat.id

    # Persist chat_id for proactive messages
    _save_chat_id(chat_id)

    # If there's a pending confirmation (e.g., Teams send), resolve it
    if has_pending_confirmation(chat_id):
        resolve_confirmation(chat_id, text)
        return

    # Check for quick actions first
    action = _match_quick_action(text)
    if action:
        _job_queue.put_nowait({
            "type": action,
            "_source": "telegram",
            "_chat_id": chat_id,
        })
        labels = {"digest": "Digest", "monitor": "Triage", "intel": "Intel brief", "transcripts": "Transcript collection"}
        await update.message.reply_text(f"{labels.get(action, action)} started.")
        return

    # Everything else -> conversational query via GHCP SDK
    _job_queue.put_nowait({
        "type": "chat",
        "prompt": text,
        "_source": "telegram",
        "_chat_id": chat_id,
    })


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    _save_chat_id(update.effective_chat.id)
    await update.message.reply_text(
        "Pulse Agent here. Just talk to me:\n\n"
        "\"What's new?\" - quick triage of your inbox\n"
        "\"Run a digest\" - full digest\n"
        "\"Analyze X vs Y\" - deep research task\n"
        "\"Did I miss anything yesterday?\" - check recent activity\n\n"
        "I'll also send you proactive updates during office hours."
    )


async def cmd_latest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send the latest digest directly (no LLM needed)."""
    if not _is_authorized(update):
        return
    _save_chat_id(update.effective_chat.id)
    await send_latest_digest(update.effective_chat.id, context.application)


async def send_latest_digest(chat_id: int, app: Application):
    """Send the most recent digest to a chat."""
    digests_dir = OUTPUT_DIR / "digests"
    if not digests_dir.exists():
        await app.bot.send_message(chat_id=chat_id, text="No digests yet.")
        return

    md_files = sorted(digests_dir.glob("*.md"), reverse=True)
    if not md_files:
        await app.bot.send_message(chat_id=chat_id, text="No digests yet.")
        return

    content = md_files[0].read_text(encoding="utf-8")
    # Telegram has a 4096 char limit per message
    if len(content) > 4000:
        chunks = [content[i:i + 4000] for i in range(0, len(content), 4000)]
        for chunk in chunks:
            await app.bot.send_message(chat_id=chat_id, text=chunk)
    else:
        await app.bot.send_message(chat_id=chat_id, text=content)


async def notify(app: Application | None, chat_id: int, text: str):
    """Send a notification message to a Telegram chat."""
    if app is None:
        return
    try:
        if len(text) > 4000:
            chunks = [text[i:i + 4000] for i in range(0, len(text), 4000)]
            for chunk in chunks:
                await app.bot.send_message(chat_id=chat_id, text=chunk)
        else:
            await app.bot.send_message(chat_id=chat_id, text=text)
    except Exception as e:
        log.warning(f"Telegram notify failed: {e}")


async def start_telegram_bot(config: dict, job_queue) -> Application | None:
    """Start the Telegram bot. Returns the Application or None if disabled."""
    global _job_queue, _config, _state_file
    _job_queue = job_queue
    _config = config

    # Set up state file path
    pulse_dir = _get_pulse_dir()
    if pulse_dir:
        _state_file = pulse_dir / ".chat-state.json"
    else:
        _state_file = OUTPUT_DIR / ".chat-state.json"

    tg_config = config.get("telegram", {})
    if not tg_config.get("enabled", False):
        return None

    token = tg_config.get("bot_token", "")
    if not token:
        log.warning("Telegram enabled but no bot_token configured")
        return None

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("latest", cmd_latest))
    # Catch all text messages
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)

    log.info("Telegram bot started")
    return app


async def stop_telegram_bot(app: Application | None):
    """Gracefully stop the Telegram bot."""
    if app is None:
        return
    try:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
        log.info("Telegram bot stopped")
    except Exception as e:
        log.warning(f"Telegram shutdown error: {e}")
