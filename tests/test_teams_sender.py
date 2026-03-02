"""Tests for collectors/teams_sender.py — deterministic Teams message sending."""

from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from collectors.teams_sender import (
    send_teams_message,
    reply_to_chat,
    _navigate_to_teams,
    _do_send_new_chat,
    _type_and_send,
)

# Patch target: lazy import inside function bodies reads from core.browser
_BROWSER_PATCH = "core.browser.get_browser_manager"


def _make_page(url="https://teams.cloud.microsoft/"):
    """Create a mock page with sensible defaults."""
    page = AsyncMock()
    page.url = url
    page.goto = AsyncMock()
    page.wait_for_load_state = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    page.keyboard = AsyncMock()
    page.keyboard.type = AsyncMock()
    page.keyboard.press = AsyncMock()
    page.close = AsyncMock()
    return page


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


# --- Navigate to Teams: polling behavior ---


async def test_navigate_teams_immediate_ready():
    """Teams loads on the first poll — should return True quickly."""
    page = _make_page()
    page.evaluate = AsyncMock(return_value={"hasTree": True, "hasNewChat": True, "ready": True})

    result = await _navigate_to_teams(page)
    assert result is True
    # Should have evaluated the readiness check exactly once (no retries needed)
    assert page.evaluate.call_count == 1


async def test_navigate_teams_slow_load():
    """Teams takes several polls to load — should keep waiting and succeed."""
    page = _make_page()
    not_ready = {"hasTree": False, "hasNewChat": False, "ready": False}
    ready = {"hasTree": True, "hasNewChat": True, "ready": True}
    # Simulate 4 polls of "not ready" then success on 5th
    page.evaluate = AsyncMock(side_effect=[not_ready, not_ready, not_ready, not_ready, ready])

    result = await _navigate_to_teams(page)
    assert result is True
    assert page.evaluate.call_count == 5


async def test_navigate_teams_never_loads():
    """Teams never loads within timeout — should return False."""
    page = _make_page()
    not_ready = {"hasTree": False, "hasNewChat": False, "ready": False}
    # Return not-ready forever (more than enough for 120s / 3s = 40 polls)
    page.evaluate = AsyncMock(return_value=not_ready)

    result = await _navigate_to_teams(page)
    assert result is False
    # Should have polled ~40 times (120s / 3s interval)
    assert page.evaluate.call_count == 40


async def test_navigate_teams_login_detected():
    """Login redirect that doesn't resolve — should bail after 15s grace period."""
    page = _make_page(url="https://login.microsoftonline.com/something")

    result = await _navigate_to_teams(page)
    assert result is False
    # Should not have tried to evaluate readiness (skips JS eval on login pages)
    page.evaluate.assert_not_called()


async def test_navigate_teams_networkidle_timeout_still_polls():
    """networkidle timeout should not prevent polling."""
    page = _make_page()
    page.wait_for_load_state = AsyncMock(side_effect=Exception("timeout"))
    ready = {"hasTree": True, "hasNewChat": False, "ready": True}
    page.evaluate = AsyncMock(side_effect=[
        {"hasTree": False, "hasNewChat": False, "ready": False},
        ready,
    ])

    result = await _navigate_to_teams(page)
    assert result is True
    assert page.evaluate.call_count == 2


# --- New chat: To field retry logic ---


async def test_new_chat_to_field_found_immediately():
    """To field appears on first check after clicking new chat."""
    page = _make_page()
    # evaluate calls: _navigate readiness, FIND_NEW_CHAT_BUTTON (found), FIND_TO_FIELD (found),
    #   then autocomplete, click suggestion, compose, focus, and so on
    page.evaluate = AsyncMock(side_effect=[
        # _navigate_to_teams
        {"hasTree": True, "hasNewChat": True, "ready": True},
        # FIND_NEW_CHAT_BUTTON_JS
        "clicked new-message",
        # FIND_TO_FIELD_JS (attempt 0)
        "found",
        # EXTRACT_SUGGESTIONS_JS
        [{"text": "Alice\nAvailable", "index": 0}],
        # CLICK_SUGGESTION_JS
        True,
        # FIND_COMPOSE_BOX_JS
        "ckeditor",
        # FOCUS_COMPOSE_BOX_JS
        True,
    ])

    result = await _do_send_new_chat(page, "Alice", "Hi")
    assert result["success"] is True


async def test_new_chat_to_field_needs_retries():
    """To field doesn't appear until 3rd attempt — should retry and succeed."""
    page = _make_page()
    page.evaluate = AsyncMock(side_effect=[
        # _navigate_to_teams
        {"hasTree": True, "hasNewChat": True, "ready": True},
        # FIND_NEW_CHAT_BUTTON_JS
        "clicked new-message",
        # FIND_TO_FIELD_JS — fails 2 times then succeeds
        None,
        None,
        "found",
        # EXTRACT_SUGGESTIONS_JS
        [{"text": "Alice\nAvailable", "index": 0}],
        # CLICK_SUGGESTION_JS
        True,
        # FIND_COMPOSE_BOX_JS
        "ckeditor",
        # FOCUS_COMPOSE_BOX_JS
        True,
    ])

    result = await _do_send_new_chat(page, "Alice", "Hi")
    assert result["success"] is True


async def test_new_chat_to_field_retries_new_chat_button():
    """After 3 failed To-field checks, retries clicking the new-chat button."""
    page = _make_page()
    page.evaluate = AsyncMock(side_effect=[
        # _navigate_to_teams
        {"hasTree": True, "hasNewChat": True, "ready": True},
        # FIND_NEW_CHAT_BUTTON_JS (initial)
        "clicked new-message",
        # FIND_TO_FIELD_JS — fails attempts 0, 1, 2
        None,
        None,
        None,
        # attempt 3: FIND_TO_FIELD_JS still fails, THEN retry new-chat button
        None,
        "clicked new-message",
        # attempt 4: FIND_TO_FIELD_JS — now succeeds
        "found",
        # EXTRACT_SUGGESTIONS_JS
        [{"text": "Alice\nAvailable", "index": 0}],
        # CLICK_SUGGESTION_JS
        True,
        # FIND_COMPOSE_BOX_JS
        "ckeditor",
        # FOCUS_COMPOSE_BOX_JS
        True,
    ])

    result = await _do_send_new_chat(page, "Alice", "Hi")
    assert result["success"] is True


async def test_new_chat_to_field_never_found():
    """To field never appears after 10 attempts — should fail."""
    page = _make_page()
    page.evaluate = AsyncMock(side_effect=[
        # _navigate_to_teams
        {"hasTree": True, "hasNewChat": True, "ready": True},
        # FIND_NEW_CHAT_BUTTON_JS (initial)
        "clicked new-message",
        # 10 FIND_TO_FIELD_JS (all None) + 2 FIND_NEW_CHAT_BUTTON retries
        # attempts 0-2: FIND_TO_FIELD returns None
        None, None, None,
        # attempt 3: FIND_TO_FIELD (None), then retry FIND_NEW_CHAT_BUTTON
        None, "clicked new-message",
        # attempts 4-5: FIND_TO_FIELD returns None
        None, None,
        # attempt 6: FIND_TO_FIELD (None), then retry FIND_NEW_CHAT_BUTTON
        None, "clicked new-message",
        # attempts 7-9: FIND_TO_FIELD returns None
        None, None, None,
    ])

    result = await _do_send_new_chat(page, "Alice", "Hi")
    assert result["success"] is False
    assert "10 attempts" in result["detail"]


async def test_new_chat_button_not_found_uses_keyboard():
    """If new-chat button not in DOM, keyboard shortcut is used."""
    page = _make_page()
    page.evaluate = AsyncMock(side_effect=[
        # _navigate_to_teams
        {"hasTree": True, "hasNewChat": False, "ready": True},
        # FIND_NEW_CHAT_BUTTON_JS — not found
        None,
        # FIND_TO_FIELD_JS — found after keyboard shortcut
        "found",
        # EXTRACT_SUGGESTIONS_JS
        [{"text": "Alice\nAvailable", "index": 0}],
        # CLICK_SUGGESTION_JS
        True,
        # FIND_COMPOSE_BOX_JS
        "ckeditor",
        # FOCUS_COMPOSE_BOX_JS
        True,
    ])

    result = await _do_send_new_chat(page, "Alice", "Hi")
    assert result["success"] is True
    # Verify keyboard shortcut was used
    page.keyboard.press.assert_any_call("Alt+Shift+n")


async def test_new_chat_no_suggestions():
    """No autocomplete results for recipient — should fail."""
    page = _make_page()
    page.evaluate = AsyncMock(side_effect=[
        # _navigate_to_teams
        {"hasTree": True, "hasNewChat": True, "ready": True},
        # FIND_NEW_CHAT_BUTTON_JS
        "clicked new-message",
        # FIND_TO_FIELD_JS
        "found",
        # _search_recipient: 3 retries, all empty
        [], [], [],
    ])

    result = await _do_send_new_chat(page, "Nonexistent Person", "Hi")
    assert result["success"] is False
    assert "No autocomplete" in result["detail"]


# --- Type and send ---


async def test_type_and_send_no_compose_box():
    page = _make_page()
    page.evaluate = AsyncMock(return_value=None)

    result = await _type_and_send(page, "Hello", "Alice")
    assert result["success"] is False
    assert "compose box" in result["detail"]


async def test_type_and_send_success():
    page = _make_page()
    page.evaluate = AsyncMock(side_effect=["ckeditor", True])

    result = await _type_and_send(page, "Hello there", "Alice")
    assert result["success"] is True
    assert "Alice" in result["detail"]
    page.keyboard.insert_text.assert_called_once_with("Hello there")
    # Ctrl+A, Backspace (clear), then Control+Enter (send)
    press_calls = [c.args[0] for c in page.keyboard.press.call_args_list]
    assert "Control+a" in press_calls
    assert "Control+Enter" in press_calls


async def test_type_and_send_focus_fails():
    page = _make_page()
    page.evaluate = AsyncMock(side_effect=["ckeditor", False])

    result = await _type_and_send(page, "Hello", "Alice")
    assert result["success"] is False
    assert "focus" in result["detail"]


# --- Full flow (entry points) ---


async def test_send_teams_message_login_page():
    """If Teams shows login page, should fail gracefully."""
    page = _make_page(url="https://login.microsoftonline.com/")

    mgr = MagicMock()
    mgr.context = MagicMock()
    mgr.new_page = AsyncMock(return_value=page)

    with patch(_BROWSER_PATCH, return_value=mgr):
        result = await send_teams_message("Alice", "Hello")
    assert result["success"] is False
    assert "login" in result["detail"].lower() or "expired" in result["detail"].lower()


async def test_send_teams_message_exception_handling():
    """Exceptions should be caught and returned as failure."""
    page = _make_page()
    page.goto = AsyncMock(side_effect=Exception("Connection refused"))

    mgr = MagicMock()
    mgr.context = MagicMock()
    mgr.new_page = AsyncMock(return_value=page)

    with patch(_BROWSER_PATCH, return_value=mgr):
        result = await send_teams_message("Alice", "Hello")
    assert result["success"] is False
    assert "Connection refused" in result["detail"]


async def test_send_teams_message_page_always_closed():
    """Page should be closed even if an exception occurs."""
    page = _make_page()
    page.goto = AsyncMock(side_effect=Exception("Error"))

    mgr = MagicMock()
    mgr.context = MagicMock()
    mgr.new_page = AsyncMock(return_value=page)

    with patch(_BROWSER_PATCH, return_value=mgr):
        await send_teams_message("Alice", "Hello")

    page.close.assert_called_once()


async def test_reply_to_chat_login_page():
    """reply_to_chat should also detect login page."""
    page = _make_page(url="https://login.microsoftonline.com/")

    mgr = MagicMock()
    mgr.context = MagicMock()
    mgr.new_page = AsyncMock(return_value=page)

    with patch(_BROWSER_PATCH, return_value=mgr):
        result = await reply_to_chat("Team Chat", "Hello")
    assert result["success"] is False
