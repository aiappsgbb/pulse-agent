"""Tests for collectors/outlook_inbox.py — Outlook inbox scanning and formatting."""

from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from collectors.outlook_inbox import (
    _parse_aria_label,
    _parse_inner_text,
    format_outlook_for_prompt,
    scan_outlook_inbox,
)


# --- _parse_aria_label ---


def test_parse_aria_unread_with_attachment():
    aria = "Unread Has attachments John Smith Important document 11:02 AM Check the attached file"
    result = _parse_aria_label(aria)
    assert result["unread"] is True
    assert result["has_attachment"] is True


def test_parse_aria_read_message():
    aria = "Jane Doe Weekly update 2026-02-18 Here is the update"
    result = _parse_aria_label(aria)
    assert result["unread"] is False
    assert result["has_attachment"] is False


def test_parse_aria_flagged_replied():
    aria = "Unread Flagged Replied Bob Team sync 3:15 PM Let me know"
    result = _parse_aria_label(aria)
    assert result["unread"] is True
    assert result["flagged"] is True
    assert result["replied"] is True


def test_parse_aria_time_extraction():
    aria = "Unread Alice Meeting notes 6:17 AM See below"
    result = _parse_aria_label(aria)
    assert result["time"] == "6:17 AM"
    assert "See below" in result["preview"]


def test_parse_aria_date_extraction():
    aria = "Unread Charlie Report 2026-02-18 Final version"
    result = _parse_aria_label(aria)
    assert result["time"] == "2026-02-18"


def test_parse_aria_empty():
    result = _parse_aria_label("")
    assert result["unread"] is False
    assert result["sender"] == ""


def test_parse_aria_external_sender():
    aria = "Unread External sender Partner Inc Proposal 9:30 AM Please review"
    result = _parse_aria_label(aria)
    assert result["unread"] is True
    # "External sender" is stripped, sender starts after


# --- _parse_inner_text ---


def test_parse_inner_text_full():
    text = "\ue73e\nAlice Smith\n\ue8b7\nProject Update\nPlease review the latest changes\n2:30 PM"
    result = _parse_inner_text(text)
    assert result["sender"] == "Alice Smith"
    assert result["subject"] == "Project Update"
    assert result["time"] == "2:30 PM"
    assert "review" in result["preview"]


def test_parse_inner_text_minimal():
    text = "Bob\nHello\n3 PM"
    result = _parse_inner_text(text)
    assert result["sender"] == "Bob"
    assert result["subject"] == "Hello"
    assert result["time"] == "3 PM"


def test_parse_inner_text_empty():
    result = _parse_inner_text("")
    assert result["sender"] == ""
    assert result["subject"] == ""


def test_parse_inner_text_single_char_lines_filtered():
    """Single-character lines (icon chars) should be filtered out."""
    text = "X\nAlice\nY\nSubject line\nPreview text\n10 AM"
    result = _parse_inner_text(text)
    assert result["sender"] == "Alice"
    assert result["subject"] == "Subject line"


# --- format_outlook_for_prompt ---


def test_format_unavailable():
    result = format_outlook_for_prompt(None)
    assert "SCAN UNAVAILABLE" in result
    assert "DO NOT assume" in result


def test_format_empty():
    assert format_outlook_for_prompt([]) == "No unread emails detected (Outlook scan)."


def test_format_raw_fallback():
    items = [{
        "sender": "Outlook Inbox (raw)",
        "subject": "Raw mail list text",
        "preview": "raw content here",
        "time": "",
        "unread": True,
        "has_attachment": False,
        "replied": False,
        "conv_id": "",
    }]
    result = format_outlook_for_prompt(items)
    assert "raw scan" in result.lower()
    assert "raw content here" in result
    assert "```" in result


def test_format_structured_items():
    items = [
        {
            "sender": "Alice",
            "subject": "Review needed",
            "preview": "Please look at this",
            "time": "2:30 PM",
            "unread": True,
            "has_attachment": True,
            "replied": False,
            "conv_id": "abc123",
        },
        {
            "sender": "Bob",
            "subject": "Quick question",
            "preview": "Can you help?",
            "time": "3:00 PM",
            "unread": True,
            "has_attachment": False,
            "replied": True,
            "conv_id": "def456",
        },
    ]
    result = format_outlook_for_prompt(items)
    assert "2 Unread" in result
    assert "Alice" in result
    assert "[attachment]" in result
    assert "Bob" in result
    assert "[replied]" in result
    assert "(2:30 PM)" in result


def test_format_missing_optional_fields():
    """Items with missing optional fields should not crash."""
    items = [{"sender": "Charlie", "unread": True}]
    result = format_outlook_for_prompt(items)
    assert "Charlie" in result


# --- scan_outlook_inbox (async, needs browser mock) ---


async def test_scan_no_browser_returns_none():
    with patch("core.browser.get_browser_manager", return_value=None):
        result = await scan_outlook_inbox({})
    assert result is None


async def test_scan_browser_no_context_returns_none():
    mock_mgr = MagicMock()
    mock_mgr.context = None
    with patch("core.browser.get_browser_manager", return_value=mock_mgr):
        result = await scan_outlook_inbox({})
    assert result is None


async def test_scan_exception_returns_empty():
    mock_mgr = MagicMock()
    mock_mgr.context = MagicMock()
    mock_mgr.new_page = AsyncMock(side_effect=Exception("browser crashed"))
    with patch("core.browser.get_browser_manager", return_value=mock_mgr):
        result = await scan_outlook_inbox({})
    assert result == []
