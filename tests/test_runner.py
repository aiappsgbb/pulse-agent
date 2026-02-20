"""Tests for sdk/runner.py — trigger variable building and pre-processing."""

import json
from unittest.mock import patch, AsyncMock

import pytest

from sdk.runner import (
    _build_carry_forward,
    _build_trigger_variables,
    _load_previous_digest,
    _pre_process_monitor,
    MAX_CARRY_FORWARD_DAYS,
)


# --- _build_carry_forward ---


def test_carry_forward_none():
    assert _build_carry_forward(None) == ""


def test_carry_forward_no_items():
    assert _build_carry_forward({"items": []}) == ""
    assert _build_carry_forward({}) == ""


def test_carry_forward_with_items():
    from datetime import datetime, timedelta
    # Use dates within the staleness window
    today = datetime.now().strftime("%Y-%m-%d")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    prev = {
        "items": [
            {"priority": "urgent", "title": "Reply to Alice", "id": "reply-alice", "source": "Email", "date": today},
            {"priority": "high", "title": "Review PR", "id": "action-review-pr", "source": "Teams", "date": yesterday},
        ]
    }
    result = _build_carry_forward(prev)
    assert "Known Outstanding Items" in result
    assert "[URGENT]" in result
    assert "Reply to Alice" in result
    assert "[HIGH]" in result
    assert "KEEP" in result
    assert "DROP" in result


def test_carry_forward_drops_stale_items():
    """Items older than MAX_CARRY_FORWARD_DAYS are auto-dropped."""
    from datetime import datetime, timedelta
    old_date = (datetime.now() - timedelta(days=MAX_CARRY_FORWARD_DAYS + 2)).strftime("%Y-%m-%d")
    fresh_date = datetime.now().strftime("%Y-%m-%d")
    prev = {
        "items": [
            {"priority": "urgent", "title": "Old item", "id": "old", "source": "Email", "date": old_date},
            {"priority": "high", "title": "Fresh item", "id": "fresh", "source": "Teams", "date": fresh_date},
        ]
    }
    result = _build_carry_forward(prev)
    assert "Fresh item" in result
    assert "Old item" not in result
    assert "Auto-dropped 1" in result


def test_carry_forward_all_stale():
    """When all items are stale, return a note only."""
    from datetime import datetime, timedelta
    old_date = (datetime.now() - timedelta(days=MAX_CARRY_FORWARD_DAYS + 1)).strftime("%Y-%m-%d")
    prev = {
        "items": [
            {"priority": "high", "title": "Ancient", "id": "ancient", "source": "Email", "date": old_date},
        ]
    }
    result = _build_carry_forward(prev)
    assert "Ancient" not in result
    assert "Auto-dropped" in result


# --- _load_previous_digest ---


def test_load_previous_digest_missing_dir(tmp_dir):
    with patch("sdk.runner.OUTPUT_DIR", tmp_dir):
        assert _load_previous_digest() is None


def test_load_previous_digest_no_json_files(tmp_dir):
    (tmp_dir / "digests").mkdir()
    with patch("sdk.runner.OUTPUT_DIR", tmp_dir):
        assert _load_previous_digest() is None


def test_load_previous_digest_valid_json(tmp_dir):
    digests_dir = tmp_dir / "digests"
    digests_dir.mkdir()
    data = {"date": "2026-02-17", "items": [{"title": "Test"}]}
    (digests_dir / "2026-02-17.json").write_text(json.dumps(data), encoding="utf-8")
    with patch("sdk.runner.OUTPUT_DIR", tmp_dir):
        result = _load_previous_digest()
    assert result["date"] == "2026-02-17"


def test_load_previous_digest_corrupt_json(tmp_dir):
    digests_dir = tmp_dir / "digests"
    digests_dir.mkdir()
    (digests_dir / "2026-02-17.json").write_text("not valid json {{{", encoding="utf-8")
    with patch("sdk.runner.OUTPUT_DIR", tmp_dir):
        assert _load_previous_digest() is None


def test_load_previous_digest_picks_latest(tmp_dir):
    digests_dir = tmp_dir / "digests"
    digests_dir.mkdir()
    for d in ["2026-02-15", "2026-02-17", "2026-02-16"]:
        (digests_dir / f"{d}.json").write_text(json.dumps({"date": d}), encoding="utf-8")
    with patch("sdk.runner.OUTPUT_DIR", tmp_dir):
        result = _load_previous_digest()
    assert result["date"] == "2026-02-17"


# --- _build_trigger_variables ---


def test_trigger_variables_monitor(sample_config):
    context = {"teams_inbox": "## 3 Unread Messages"}
    result = _build_trigger_variables("monitor", sample_config, context)
    assert result["teams_inbox"] == "## 3 Unread Messages"


def test_trigger_variables_monitor_default(sample_config):
    result = _build_trigger_variables("monitor", sample_config, {})
    assert result["teams_inbox"] == "No Teams inbox data available."


def test_trigger_variables_digest_no_previous(sample_config, tmp_dir):
    with patch("sdk.runner.OUTPUT_DIR", tmp_dir):
        result = _build_trigger_variables("digest", sample_config, {
            "content_block": "some content",
            "teams_inbox_block": "## 2 Unread Messages",
        })
    assert "date" in result
    assert result["workiq_window"] == "in the last 7 days"
    assert "Revenue deals" in result["priorities"]
    assert result["content_sections"] == "some content"
    assert result["teams_inbox_block"] == "## 2 Unread Messages"


def test_trigger_variables_digest_with_previous(sample_config, tmp_dir):
    digests_dir = tmp_dir / "digests"
    digests_dir.mkdir()
    (digests_dir / "2026-02-17.json").write_text(json.dumps({"date": "2026-02-17", "items": []}))
    with patch("sdk.runner.OUTPUT_DIR", tmp_dir):
        result = _build_trigger_variables("digest", sample_config, {})
    assert result["workiq_window"] == "since 2026-02-17"


def test_trigger_variables_digest_dismissed_and_notes(sample_config, tmp_dir):
    with patch("sdk.runner.OUTPUT_DIR", tmp_dir), \
         patch("sdk.runner.load_actions", return_value={
             "dismissed": [{"item": "old-thing"}],
             "notes": {"escalation-x": {"note": "follow up Monday"}},
         }):
        result = _build_trigger_variables("digest", sample_config, {})
    assert "old-thing" in result["dismissed_block"]
    assert "follow up Monday" in result["notes_block"]


def test_trigger_variables_intel(sample_config):
    articles = [
        {"source": "TechCrunch", "title": "AI News", "link": "http://x", "published": "2026-02-18", "summary": "big news"},
    ]
    result = _build_trigger_variables("intel", sample_config, {"articles": articles})
    assert result["article_count"] == "1"
    assert "AI, Cloud" in result["topics"]
    assert "Acme" in result["competitors"]
    assert "AI News" in result["articles"]


def test_trigger_variables_research():
    context = {"task": {"task": "competitor-analysis", "description": "Analyze pricing", "output": {"local": "./output/research/"}}}
    result = _build_trigger_variables("research", {}, context)
    assert result["task"] == "competitor-analysis"
    assert result["description"] == "Analyze pricing"
    assert result["output_path"] == "./output/research/"


# --- _pre_process_monitor ---


async def test_pre_process_monitor_with_items():
    mock_items = [{"name": "Alice", "preview": "Hey", "time": "3pm", "unread": True}]
    with patch("collectors.teams_inbox.scan_teams_inbox", new_callable=AsyncMock, return_value=mock_items), \
         patch("collectors.teams_inbox.format_inbox_for_prompt", return_value="## 1 Unread"), \
         patch("collectors.outlook_inbox.scan_outlook_inbox", new_callable=AsyncMock, return_value=[]), \
         patch("collectors.outlook_inbox.format_outlook_for_prompt", return_value="No unread emails"), \
         patch("collectors.calendar.scan_calendar", new_callable=AsyncMock, return_value=[]), \
         patch("collectors.calendar.format_calendar_for_prompt", return_value="No events"):
        result = await _pre_process_monitor({})
    assert "## 1 Unread" in result["teams_inbox"]
    assert "*(Scanned at" in result["teams_inbox"]
    assert "outlook_inbox_block" in result
    assert "calendar_block" in result


async def test_pre_process_monitor_empty():
    with patch("collectors.teams_inbox.scan_teams_inbox", new_callable=AsyncMock, return_value=[]), \
         patch("collectors.teams_inbox.format_inbox_for_prompt", return_value="No unread"), \
         patch("collectors.outlook_inbox.scan_outlook_inbox", new_callable=AsyncMock, return_value=[]), \
         patch("collectors.outlook_inbox.format_outlook_for_prompt", return_value="No unread emails"), \
         patch("collectors.calendar.scan_calendar", new_callable=AsyncMock, return_value=[]), \
         patch("collectors.calendar.format_calendar_for_prompt", return_value="No events"):
        result = await _pre_process_monitor({})
    assert "No unread" in result["teams_inbox"]
    assert "*(Scanned at" in result["teams_inbox"]


async def test_pre_process_monitor_returns_outlook_and_calendar():
    """Monitor pre-process returns Outlook inbox and Calendar blocks."""
    mock_outlook = [{"sender": "Bob", "subject": "Review", "unread": True}]
    mock_cal = [{"title": "Standup", "start_time": "9:00 AM", "is_declined": False}]
    with patch("collectors.teams_inbox.scan_teams_inbox", new_callable=AsyncMock, return_value=[]), \
         patch("collectors.teams_inbox.format_inbox_for_prompt", return_value="No unread"), \
         patch("collectors.outlook_inbox.scan_outlook_inbox", new_callable=AsyncMock, return_value=mock_outlook), \
         patch("collectors.outlook_inbox.format_outlook_for_prompt", return_value="## 1 Unread Email"), \
         patch("collectors.calendar.scan_calendar", new_callable=AsyncMock, return_value=mock_cal), \
         patch("collectors.calendar.format_calendar_for_prompt", return_value="## 1 Event"):
        result = await _pre_process_monitor({})
    assert "## 1 Unread Email" in result["outlook_inbox_block"]
    assert "*(Scanned at" in result["outlook_inbox_block"]
    assert result["calendar_block"] == "## 1 Event"


def test_trigger_variables_monitor_outlook_and_calendar(sample_config):
    """Monitor trigger variables include Outlook and Calendar blocks."""
    context = {
        "teams_inbox": "## Teams data",
        "outlook_inbox_block": "## Outlook data",
        "calendar_block": "## Calendar data",
    }
    result = _build_trigger_variables("monitor", sample_config, context)
    assert result["outlook_inbox_block"] == "## Outlook data"
    assert result["calendar_block"] == "## Calendar data"


def test_trigger_variables_digest_outlook_and_calendar(sample_config, tmp_dir):
    """Digest trigger variables include Outlook and Calendar blocks."""
    with patch("sdk.runner.OUTPUT_DIR", tmp_dir):
        result = _build_trigger_variables("digest", sample_config, {
            "content_block": "content",
            "teams_inbox_block": "teams",
            "outlook_inbox_block": "## Outlook data",
            "calendar_block": "## Calendar data",
        })
    assert result["outlook_inbox_block"] == "## Outlook data"
    assert result["calendar_block"] == "## Calendar data"


def test_trigger_variables_digest_defaults_outlook_calendar(sample_config, tmp_dir):
    """Digest trigger variables have defaults when scans unavailable."""
    with patch("sdk.runner.OUTPUT_DIR", tmp_dir):
        result = _build_trigger_variables("digest", sample_config, {})
    assert "unavailable" in result["outlook_inbox_block"].lower()
    assert "unavailable" in result["calendar_block"].lower()
