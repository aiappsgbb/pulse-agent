"""Tests for collectors/teams_sender.py — deterministic Teams message sending."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from collectors.teams_sender import (
    send_teams_message,
    reply_to_chat,
    _navigate_to_teams,
    _type_and_send,
)

# Patch target: lazy import inside function bodies reads from core.browser
_BROWSER_PATCH = "core.browser.get_browser_manager"


# --- Browser unavailable ---


async def test_send_teams_message_no_browser():
    with patch(_BROWSER_PATCH, return_value=None):
        result = await send_teams_message("Alice", "Hello")
    assert result["success"] is False
    assert "No shared browser" in result["detail"]


async def test_send_teams_message_no_context():
    mgr = MagicMock()
    mgr.context = None
    with patch(_BROWSER_PATCH, return_value=mgr):
        result = await send_teams_message("Alice", "Hello")
    assert result["success"] is False
    assert "No shared browser" in result["detail"]


async def test_reply_to_chat_no_browser():
    with patch(_BROWSER_PATCH, return_value=None):
        result = await reply_to_chat("Team Chat", "Hello")
    assert result["success"] is False
    assert "No shared browser" in result["detail"]


# --- Navigate to Teams ---


async def test_navigate_to_teams_login_detected():
    page = AsyncMock()
    page.url = "https://login.microsoftonline.com/something"
    page.goto = AsyncMock()
    page.wait_for_load_state = AsyncMock()
    page.wait_for_selector = AsyncMock()
    page.wait_for_timeout = AsyncMock()

    result = await _navigate_to_teams(page)
    assert result is False


async def test_navigate_to_teams_success():
    page = AsyncMock()
    page.url = "https://teams.cloud.microsoft/"
    page.goto = AsyncMock()
    page.wait_for_load_state = AsyncMock()
    page.wait_for_selector = AsyncMock()
    page.wait_for_timeout = AsyncMock()

    result = await _navigate_to_teams(page)
    assert result is True
    page.goto.assert_called_once()


async def test_navigate_to_teams_networkidle_timeout():
    """networkidle timeout should not fail navigation."""
    page = AsyncMock()
    page.url = "https://teams.cloud.microsoft/"
    page.goto = AsyncMock()
    page.wait_for_load_state = AsyncMock(side_effect=Exception("timeout"))
    page.wait_for_selector = AsyncMock()
    page.wait_for_timeout = AsyncMock()

    result = await _navigate_to_teams(page)
    assert result is True


# --- Type and send ---


async def test_type_and_send_no_compose_box():
    page = AsyncMock()
    page.evaluate = AsyncMock(return_value=None)

    result = await _type_and_send(page, "Hello", "Alice")
    assert result["success"] is False
    assert "compose box" in result["detail"]


async def test_type_and_send_success():
    page = AsyncMock()
    page.evaluate = AsyncMock(side_effect=["ckeditor", True])
    page.keyboard = AsyncMock()
    page.keyboard.type = AsyncMock()
    page.keyboard.press = AsyncMock()
    page.wait_for_timeout = AsyncMock()

    result = await _type_and_send(page, "Hello there", "Alice")
    assert result["success"] is True
    assert "Alice" in result["detail"]
    page.keyboard.type.assert_called_once_with("Hello there", delay=20)
    page.keyboard.press.assert_called_once_with("Control+Enter")


async def test_type_and_send_focus_fails():
    page = AsyncMock()
    page.evaluate = AsyncMock(side_effect=["ckeditor", False])

    result = await _type_and_send(page, "Hello", "Alice")
    assert result["success"] is False
    assert "focus" in result["detail"]


# --- Full flow (mocked) ---


async def test_send_teams_message_login_page():
    """If Teams shows login page, should fail gracefully."""
    page = AsyncMock()
    page.url = "https://login.microsoftonline.com/"
    page.goto = AsyncMock()
    page.wait_for_load_state = AsyncMock()
    page.wait_for_selector = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    page.close = AsyncMock()

    mgr = MagicMock()
    mgr.context = MagicMock()
    mgr.new_page = AsyncMock(return_value=page)

    with patch(_BROWSER_PATCH, return_value=mgr):
        result = await send_teams_message("Alice", "Hello")
    assert result["success"] is False
    assert "login" in result["detail"].lower() or "expired" in result["detail"].lower()


async def test_send_teams_message_exception_handling():
    """Exceptions should be caught and returned as failure."""
    page = AsyncMock()
    page.goto = AsyncMock(side_effect=Exception("Connection refused"))
    page.close = AsyncMock()

    mgr = MagicMock()
    mgr.context = MagicMock()
    mgr.new_page = AsyncMock(return_value=page)

    with patch(_BROWSER_PATCH, return_value=mgr):
        result = await send_teams_message("Alice", "Hello")
    assert result["success"] is False
    assert "Connection refused" in result["detail"]


async def test_send_teams_message_page_always_closed():
    """Page should be closed even if an exception occurs."""
    page = AsyncMock()
    page.goto = AsyncMock(side_effect=Exception("Error"))
    page.close = AsyncMock()

    mgr = MagicMock()
    mgr.context = MagicMock()
    mgr.new_page = AsyncMock(return_value=page)

    with patch(_BROWSER_PATCH, return_value=mgr):
        await send_teams_message("Alice", "Hello")

    page.close.assert_called_once()


async def test_reply_to_chat_login_page():
    """reply_to_chat should also detect login page."""
    page = AsyncMock()
    page.url = "https://login.microsoftonline.com/"
    page.goto = AsyncMock()
    page.wait_for_load_state = AsyncMock()
    page.wait_for_selector = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    page.close = AsyncMock()

    mgr = MagicMock()
    mgr.context = MagicMock()
    mgr.new_page = AsyncMock(return_value=page)

    with patch(_BROWSER_PATCH, return_value=mgr):
        result = await reply_to_chat("Team Chat", "Hello")
    assert result["success"] is False
