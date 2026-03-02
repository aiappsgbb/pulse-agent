"""Tests for collectors/calendar.py — Calendar scanning and formatting."""

from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from collectors.calendar import (
    _parse_calendar_aria,
    format_calendar_for_prompt,
    scan_calendar,
)


# --- _parse_calendar_aria ---


def test_parse_full_aria():
    aria = (
        "Team Standup, 9:00 AM to 9:30 AM, Thursday, February 20, 2026, "
        "Microsoft Teams Meeting, By Alice Smith, Busy, Recurring event"
    )
    result = _parse_calendar_aria(aria)
    assert result is not None
    assert result["title"] == "Team Standup"
    assert result["start_time"] == "9:00 AM"
    assert result["end_time"] == "9:30 AM"
    assert "February 20" in result["date"]
    assert result["organizer"] == "Alice Smith"
    assert result["status"] == "Busy"
    assert result["is_teams"] is True
    assert result["is_recurring"] is True
    assert result["is_declined"] is False


def test_parse_declined_event():
    aria = (
        "Declined: Optional Meeting, 2:00 PM to 3:00 PM, Thursday, February 20, 2026, "
        "By Bob Jones, Tentative"
    )
    result = _parse_calendar_aria(aria)
    assert result is not None
    assert result["is_declined"] is True
    assert result["title"] == "Optional Meeting"
    assert result["status"] == "Tentative"


def test_parse_non_teams_meeting():
    aria = (
        "Client Call, 10:00 AM to 11:00 AM, Friday, February 21, 2026, "
        "By External Partner, Busy"
    )
    result = _parse_calendar_aria(aria)
    assert result is not None
    assert result["is_teams"] is False
    assert result["title"] == "Client Call"
    assert result["organizer"] == "External Partner"


def test_parse_free_status():
    aria = (
        "Lunch Break, 12:00 PM to 1:00 PM, Thursday, February 20, 2026, Free"
    )
    result = _parse_calendar_aria(aria)
    assert result is not None
    assert result["status"] == "Free"


def test_parse_too_short():
    assert _parse_calendar_aria("") is None
    assert _parse_calendar_aria("short") is None


def test_parse_too_few_parts():
    assert _parse_calendar_aria("One, Two, Three") is None


def test_parse_no_time_range():
    """Events without a clear time range should still parse other fields."""
    aria = "All Day Event, Thursday, February 20, 2026, Free"
    result = _parse_calendar_aria(aria)
    assert result is not None
    assert result["title"] == "All Day Event"
    assert result["start_time"] == ""
    assert result["end_time"] == ""


# --- format_calendar_for_prompt ---


def test_format_unavailable():
    result = format_calendar_for_prompt(None)
    assert "SCAN UNAVAILABLE" in result
    assert "DO NOT assume" in result


def test_format_no_events():
    assert format_calendar_for_prompt([]) == "No calendar events found for the work week."


def test_format_active_events():
    events = [
        {
            "title": "Standup",
            "start_time": "9:00 AM",
            "end_time": "9:30 AM",
            "date": "Thursday, February 20 2026",
            "organizer": "Alice",
            "status": "Busy",
            "is_teams": True,
            "is_recurring": True,
            "is_declined": False,
        },
        {
            "title": "1:1 with Bob",
            "start_time": "10:00 AM",
            "end_time": "10:30 AM",
            "date": "Thursday, February 20 2026",
            "organizer": "",
            "status": "Tentative",
            "is_teams": False,
            "is_recurring": False,
            "is_declined": False,
        },
    ]
    result = format_calendar_for_prompt(events)
    assert "2 events" in result
    assert "Standup" in result
    assert "[Teams]" in result
    assert "[recurring]" in result
    assert "1:1 with Bob" in result
    assert "[Tentative]" in result
    assert "(by Alice)" in result
    assert "Thursday, February 20 2026" in result


def test_format_with_declined():
    events = [
        {
            "title": "Active Meeting",
            "start_time": "9:00 AM",
            "end_time": "10:00 AM",
            "date": "",
            "organizer": "",
            "status": "Busy",
            "is_teams": False,
            "is_recurring": False,
            "is_declined": False,
        },
        {
            "title": "Declined One",
            "start_time": "2:00 PM",
            "end_time": "3:00 PM",
            "date": "",
            "organizer": "",
            "status": "",
            "is_teams": False,
            "is_recurring": False,
            "is_declined": True,
        },
    ]
    result = format_calendar_for_prompt(events)
    assert "1 events" in result
    assert "1 declined" in result
    assert "Active Meeting" in result
    assert "Declined (1)" in result
    assert "Declined One" in result
    assert "Unknown day" in result


def test_format_all_day_event():
    events = [
        {
            "title": "Conference",
            "start_time": "",
            "end_time": "",
            "date": "",
            "organizer": "",
            "status": "Free",
            "is_teams": False,
            "is_recurring": False,
            "is_declined": False,
        },
    ]
    result = format_calendar_for_prompt(events)
    assert "All day" in result
    assert "Conference" in result


# --- scan_calendar (async, needs browser mock) ---


async def test_scan_no_browser_returns_none():
    with patch("core.browser.get_browser_manager", return_value=None):
        result = await scan_calendar({})
    assert result is None


async def test_scan_browser_no_context_returns_none():
    mock_mgr = MagicMock()
    mock_mgr.context = None
    with patch("core.browser.get_browser_manager", return_value=mock_mgr):
        result = await scan_calendar({})
    assert result is None


async def test_scan_exception_returns_none():
    """Exception during scan returns None (unavailable), not [] (empty)."""
    mock_mgr = MagicMock()
    mock_mgr.context = MagicMock()
    mock_mgr.new_page = AsyncMock(side_effect=Exception("browser crashed"))
    with patch("core.browser.get_browser_manager", return_value=mock_mgr):
        result = await scan_calendar({})
    assert result is None
