"""Tests for sdk/runner.py — trigger variable building and pre-processing."""

import json
from unittest.mock import patch, AsyncMock

import pytest

from sdk.runner import (
    _build_carry_forward,
    _build_trigger_variables,
    _load_previous_digest,
    _load_projects,
    _build_projects_block,
    _extract_commitments_summary,
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
    with patch("sdk.runner.DIGESTS_DIR", digests_dir):
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
    with patch("sdk.runner.DIGESTS_DIR", digests_dir):
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
    with patch("sdk.runner.DIGESTS_DIR", digests_dir):
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


async def test_pre_process_monitor_browser_unavailable():
    """When browser is unavailable, scanners return None and format shows UNAVAILABLE."""
    with patch("collectors.teams_inbox.scan_teams_inbox", new_callable=AsyncMock, return_value=None), \
         patch("collectors.teams_inbox.format_inbox_for_prompt", return_value="**SCAN UNAVAILABLE**"), \
         patch("collectors.outlook_inbox.scan_outlook_inbox", new_callable=AsyncMock, return_value=None), \
         patch("collectors.outlook_inbox.format_outlook_for_prompt", return_value="**SCAN UNAVAILABLE**"), \
         patch("collectors.calendar.scan_calendar", new_callable=AsyncMock, return_value=None), \
         patch("collectors.calendar.format_calendar_for_prompt", return_value="**SCAN UNAVAILABLE**"):
        result = await _pre_process_monitor({})
    assert "UNAVAILABLE" in result["teams_inbox"]
    # When browser unavailable, no timestamp prepended
    assert "*(Scanned at" not in result["teams_inbox"]
    assert "UNAVAILABLE" in result["outlook_inbox_block"]
    assert "UNAVAILABLE" in result["calendar_block"]


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


# --- _load_projects ---


def test_load_projects_no_dir(tmp_dir):
    with patch("sdk.runner.PROJECTS_DIR", tmp_dir / "nonexistent"):
        assert _load_projects() == []


def test_load_projects_empty_dir(tmp_dir):
    projects_dir = tmp_dir / "projects"
    projects_dir.mkdir()
    with patch("sdk.runner.PROJECTS_DIR", projects_dir):
        assert _load_projects() == []


def test_load_projects_reads_yaml(tmp_dir):
    import yaml
    projects_dir = tmp_dir / "projects"
    projects_dir.mkdir()
    data = {"project": "Acme Migration", "status": "active", "summary": "Cloud migration"}
    (projects_dir / "acme-migration.yaml").write_text(
        yaml.dump(data), encoding="utf-8"
    )
    with patch("sdk.runner.PROJECTS_DIR", projects_dir):
        projects = _load_projects()
    assert len(projects) == 1
    assert projects[0]["project"] == "Acme Migration"
    assert projects[0]["_file"] == "acme-migration.yaml"


def test_load_projects_skips_corrupt(tmp_dir):
    import yaml
    projects_dir = tmp_dir / "projects"
    projects_dir.mkdir()
    (projects_dir / "good.yaml").write_text(
        yaml.dump({"project": "Good"}), encoding="utf-8"
    )
    (projects_dir / "bad.yaml").write_text("{{{{not yaml", encoding="utf-8")
    with patch("sdk.runner.PROJECTS_DIR", projects_dir):
        projects = _load_projects()
    assert len(projects) == 1
    assert projects[0]["project"] == "Good"


# --- _build_projects_block ---


def test_build_projects_block_empty():
    assert _build_projects_block([]) == ""


def test_build_projects_block_with_active_project():
    projects = [{
        "project": "Contoso Deal",
        "status": "active",
        "risk_level": "medium",
        "_file": "contoso-deal.yaml",
        "stakeholders": [
            {"name": "Alice", "role": "PM"},
            {"name": "Bob", "role": "Engineer"},
        ],
        "summary": "Enterprise license renewal",
        "commitments": [
            {"what": "Send pricing", "to": "Alice", "due": "2026-02-25", "status": "open"},
        ],
        "next_meeting": "2026-02-24 10:00",
    }]
    result = _build_projects_block(projects)
    assert "Part D" in result
    assert "Contoso Deal" in result
    assert "Alice (PM)" in result
    assert "Bob (Engineer)" in result
    assert "Enterprise license renewal" in result
    assert "[OPEN] Send pricing" in result
    assert "Next meeting: 2026-02-24 10:00" in result


def test_build_projects_block_separates_active_and_other():
    projects = [
        {"project": "Active One", "status": "active", "_file": "active.yaml"},
        {"project": "Done One", "status": "completed", "_file": "done.yaml"},
        {"project": "Blocked One", "status": "blocked", "_file": "blocked.yaml"},
    ]
    result = _build_projects_block(projects)
    assert "Active One" in result
    assert "Blocked One" in result
    assert "Done One" not in result  # completed goes to "other"
    assert "1 other project(s)" in result


# --- _extract_commitments_summary ---


def test_commitments_summary_no_projects():
    assert _extract_commitments_summary([]) == ""


def test_commitments_summary_overdue():
    projects = [{
        "project": "Acme Deal",
        "_file": "acme.yaml",
        "commitments": [
            {"what": "Send proposal", "to": "Client", "due": "2026-01-01", "status": "open"},
        ],
    }]
    result = _extract_commitments_summary(projects)
    assert "OVERDUE" in result
    assert "Send proposal" in result
    assert "Acme Deal" in result


def test_commitments_summary_upcoming():
    from datetime import datetime, timedelta
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    projects = [{
        "project": "Beta Launch",
        "_file": "beta.yaml",
        "commitments": [
            {"what": "Final review", "to": "Team", "due": tomorrow, "status": "open"},
        ],
    }]
    result = _extract_commitments_summary(projects)
    assert "Due soon" in result
    assert "Final review" in result


def test_commitments_summary_skips_done():
    projects = [{
        "project": "Old Project",
        "_file": "old.yaml",
        "commitments": [
            {"what": "Already handled", "to": "Someone", "due": "2026-01-01", "status": "done"},
        ],
    }]
    result = _extract_commitments_summary(projects)
    assert result == ""  # no open commitments at all


def test_trigger_variables_digest_includes_projects(sample_config, tmp_dir):
    """Digest trigger variables include projects_block and commitments_summary."""
    with patch("sdk.runner.OUTPUT_DIR", tmp_dir):
        result = _build_trigger_variables("digest", sample_config, {
            "content_block": "content",
            "projects_block": "## Part D -- Projects\nSome project data",
            "commitments_summary": "## Commitment Status\n1 overdue",
        })
    assert result["projects_block"] == "## Part D -- Projects\nSome project data"
    assert result["commitments_summary"] == "## Commitment Status\n1 overdue"


def test_trigger_variables_digest_defaults_projects(sample_config, tmp_dir):
    """Digest trigger variables default to empty when no projects."""
    with patch("sdk.runner.OUTPUT_DIR", tmp_dir):
        result = _build_trigger_variables("digest", sample_config, {})
    assert result["projects_block"] == ""
    assert result["commitments_summary"] == ""


def test_trigger_variables_digest_dismissed_ttl(sample_config, tmp_dir):
    """Archived items older than 30 days are auto-expired; legacy entries treated as archived."""
    from datetime import datetime, timedelta
    old_ts = (datetime.now() - timedelta(days=45)).isoformat()
    fresh_ts = (datetime.now() - timedelta(days=5)).isoformat()
    with patch("sdk.runner.OUTPUT_DIR", tmp_dir), \
         patch("sdk.runner.load_actions", return_value={
             "dismissed": [
                 {"item": "old-thing", "dismissed_at": old_ts},              # legacy, no status → archived, expired
                 {"item": "fresh-thing", "dismissed_at": fresh_ts},          # legacy, no status → archived, fresh
             ],
             "notes": {},
         }):
        result = _build_trigger_variables("digest", sample_config, {})
    assert "fresh-thing" in result["dismissed_block"]
    assert "old-thing" not in result["dismissed_block"]


def test_trigger_variables_digest_snooze_ttl(sample_config, tmp_dir):
    """Snoozed items expire after 1 day; archived items last 30 days."""
    from datetime import datetime, timedelta
    today_ts = (datetime.now() - timedelta(hours=6)).isoformat()
    yesterday_ts = (datetime.now() - timedelta(days=2)).isoformat()
    recent_archived_ts = (datetime.now() - timedelta(days=10)).isoformat()
    with patch("sdk.runner.OUTPUT_DIR", tmp_dir), \
         patch("sdk.runner.load_actions", return_value={
             "dismissed": [
                 {"item": "snoozed-today", "dismissed_at": today_ts, "status": "dismissed"},
                 {"item": "snoozed-yesterday", "dismissed_at": yesterday_ts, "status": "dismissed"},
                 {"item": "archived-recent", "dismissed_at": recent_archived_ts, "status": "archived"},
             ],
             "notes": {},
         }):
        result = _build_trigger_variables("digest", sample_config, {})
    assert "snoozed-today" in result["dismissed_block"]
    assert "snoozed-yesterday" not in result["dismissed_block"]  # expired snooze
    assert "archived-recent" in result["dismissed_block"]


async def test_pre_process_monitor_none_returns_unavailable():
    """When all scanners return None, output contains UNAVAILABLE (not crash)."""
    with patch("collectors.teams_inbox.scan_teams_inbox", new_callable=AsyncMock, return_value=None), \
         patch("collectors.outlook_inbox.scan_outlook_inbox", new_callable=AsyncMock, return_value=None), \
         patch("collectors.calendar.scan_calendar", new_callable=AsyncMock, return_value=None):
        result = await _pre_process_monitor({})
    assert "UNAVAILABLE" in result["teams_inbox"]
    assert "UNAVAILABLE" in result["outlook_inbox_block"]
    assert "UNAVAILABLE" in result["calendar_block"]
