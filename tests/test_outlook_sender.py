"""Tests for collectors/outlook_sender.py — deterministic Outlook email reply."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from collectors.outlook_sender import reply_to_email


# --- Browser unavailable ---


async def test_reply_to_email_no_browser():
    with patch("core.browser.ensure_browser", new_callable=AsyncMock, return_value=None):
        result = await reply_to_email("Alice", "Thanks!")
    assert result["success"] is False
    assert "No shared browser" in result["detail"]


async def test_reply_to_email_no_browser():
    with patch("core.browser.ensure_browser", new_callable=AsyncMock, return_value=None):
        result = await reply_to_email("Alice", "Thanks!")
    assert result["success"] is False
    assert "No shared browser" in result["detail"]


# --- Login page detection ---


async def test_reply_to_email_login_detected():
    page = AsyncMock()
    page.url = "https://login.microsoftonline.com/auth"
    page.goto = AsyncMock()
    page.wait_for_load_state = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    page.close = AsyncMock()

    mgr = MagicMock()
    mgr.context = MagicMock()
    mgr.new_page = AsyncMock(return_value=page)

    with patch("core.browser.ensure_browser", new_callable=AsyncMock, return_value=mgr):
        result = await reply_to_email("Alice", "Thanks!")
    assert result["success"] is False
    assert "login" in result["detail"].lower() or "expired" in result["detail"].lower()


# --- Exception handling ---


async def test_reply_to_email_exception_handling():
    page = AsyncMock()
    page.goto = AsyncMock(side_effect=Exception("Network error"))
    page.close = AsyncMock()

    mgr = MagicMock()
    mgr.context = MagicMock()
    mgr.new_page = AsyncMock(return_value=page)

    with patch("core.browser.ensure_browser", new_callable=AsyncMock, return_value=mgr):
        result = await reply_to_email("Alice", "Thanks!")
    assert result["success"] is False
    assert "Network error" in result["detail"]


async def test_reply_to_email_page_always_closed():
    """Page should be closed even if an exception occurs."""
    page = AsyncMock()
    page.goto = AsyncMock(side_effect=Exception("Error"))
    page.close = AsyncMock()

    mgr = MagicMock()
    mgr.context = MagicMock()
    mgr.new_page = AsyncMock(return_value=page)

    with patch("core.browser.ensure_browser", new_callable=AsyncMock, return_value=mgr):
        await reply_to_email("Alice", "Thanks!")

    page.close.assert_called_once()


# --- Search box ---


async def test_reply_to_email_no_search_box():
    page = AsyncMock()
    page.url = "https://outlook.office.com/mail/inbox"
    page.goto = AsyncMock()
    page.wait_for_load_state = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    page.evaluate = AsyncMock(return_value=None)  # search box not found
    page.close = AsyncMock()

    mgr = MagicMock()
    mgr.context = MagicMock()
    mgr.new_page = AsyncMock(return_value=page)

    with patch("core.browser.ensure_browser", new_callable=AsyncMock, return_value=mgr):
        result = await reply_to_email("Alice", "Thanks!")
    assert result["success"] is False
    assert "search box" in result["detail"]


# --- No search results ---


async def test_reply_to_email_no_results():
    page = AsyncMock()
    page.url = "https://outlook.office.com/mail/inbox"
    page.goto = AsyncMock()
    page.wait_for_load_state = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    page.keyboard = AsyncMock()
    page.keyboard.type = AsyncMock()
    page.keyboard.press = AsyncMock()
    page.close = AsyncMock()

    # First evaluate: find search box -> found
    # Second evaluate: extract results -> empty
    page.evaluate = AsyncMock(side_effect=["found", []])

    mgr = MagicMock()
    mgr.context = MagicMock()
    mgr.new_page = AsyncMock(return_value=page)

    with patch("core.browser.ensure_browser", new_callable=AsyncMock, return_value=mgr):
        result = await reply_to_email("Alice", "Thanks!")
    assert result["success"] is False
    assert "No emails found" in result["detail"]
