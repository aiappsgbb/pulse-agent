"""Tests for collectors/teams_inbox.py — Teams inbox scanning and formatting."""

from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from collectors.teams_inbox import format_inbox_for_prompt, scan_teams_inbox


# --- format_inbox_for_prompt (pure function) ---


def test_format_unavailable():
    result = format_inbox_for_prompt(None)
    assert "SCAN UNAVAILABLE" in result
    assert "DO NOT assume" in result


def test_format_empty_inbox():
    assert format_inbox_for_prompt([]) == "No unread Teams messages detected."


def test_format_single_raw_fallback():
    items = [{
        "name": "Teams Chat Pane (raw)",
        "preview": "Some raw text here",
        "time": "",
        "unread": True,
        "raw": "Some raw text here",
    }]
    result = format_inbox_for_prompt(items)
    assert "raw scan" in result.lower()
    assert "Some raw text here" in result
    assert "```" in result


def test_format_multiple_structured_items():
    items = [
        {"name": "Alice", "preview": "Can you review the doc?", "time": "2:30 PM", "unread": True},
        {"name": "Bob", "preview": "Meeting moved to 3 PM", "time": "2:45 PM", "unread": True},
    ]
    result = format_inbox_for_prompt(items)
    assert "2 Unread" in result
    assert "Alice" in result
    assert "Bob" in result
    assert "(2:30 PM)" in result


def test_format_item_missing_optional_fields():
    """Items with missing optional fields should not crash."""
    items = [{"name": "Charlie", "unread": True}]
    result = format_inbox_for_prompt(items)
    assert "Charlie" in result


# --- scan_teams_inbox (async, needs browser mock) ---


async def test_scan_no_browser_returns_none():
    with patch("core.browser.get_browser_manager", return_value=None):
        result = await scan_teams_inbox({})
    assert result is None


async def test_scan_browser_no_context_returns_none():
    mock_mgr = MagicMock()
    mock_mgr.context = None
    with patch("core.browser.get_browser_manager", return_value=mock_mgr):
        result = await scan_teams_inbox({})
    assert result is None


async def test_scan_exception_returns_empty():
    mock_mgr = MagicMock()
    mock_mgr.context = MagicMock()
    mock_mgr.new_page = AsyncMock(side_effect=Exception("browser crashed"))
    with patch("core.browser.get_browser_manager", return_value=mock_mgr):
        result = await scan_teams_inbox({})
    assert result == []
