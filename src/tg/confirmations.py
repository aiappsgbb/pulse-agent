"""Confirmation flow — pause agent execution until user replies via Telegram."""

import asyncio


def has_pending_confirmation(pending: dict, chat_id: int) -> bool:
    """Check if there's a confirmation waiting for this chat."""
    return chat_id in pending


def resolve_confirmation(pending: dict, chat_id: int, answer: str):
    """Resolve a pending confirmation with the user's answer."""
    fut = pending.pop(chat_id, None)
    if fut and not fut.done():
        fut.get_loop().call_soon_threadsafe(fut.set_result, answer)


async def wait_for_confirmation(pending: dict, chat_id: int, timeout: float = 120) -> str:
    """Block until the user replies to a confirmation prompt. Raises TimeoutError."""
    loop = asyncio.get_running_loop()
    fut = loop.create_future()
    pending[chat_id] = fut
    try:
        return await asyncio.wait_for(fut, timeout=timeout)
    except asyncio.TimeoutError:
        pending.pop(chat_id, None)
        raise
