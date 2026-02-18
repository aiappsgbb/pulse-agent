"""Telegram user interface — bot, confirmations, notifications."""

from tg.bot import (
    TelegramBot,
    start_telegram_bot,
    stop_telegram_bot,
    notify,
    send_latest_digest,
    get_proactive_chat_id,
    wait_for_confirmation,
)
