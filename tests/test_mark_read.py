"""Tests for inbox sweep / mark-as-read feature.

Covers:
- Sweep classification logic (pure Python)
- Teams marker (mock Playwright)
- Outlook marker (mock Playwright)
- IPC job queuing (filesystem)
- Worker job routing
- SDK tool
"""

import asyncio
import json
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

# ---------------------------------------------------------------------------
# Sweep classification tests (pure Python — no mocks needed)
# ---------------------------------------------------------------------------

from collectors.sweep import (
    parse_source_name,
    classify_for_sweep,
    get_sweep_config,
    DEFAULT_SWEEP_CONFIG,
    load_latest_triage_items,
)


class TestParseSourceName:
    def test_teams_source(self):
        assert parse_source_name("Teams: Fatos Ismali") == ("teams", "Fatos Ismali")

    def test_email_source(self):
        assert parse_source_name("Email: Bob Wilson") == ("outlook", "Bob Wilson")

    def test_email_from_source(self):
        assert parse_source_name("Email from Bob Wilson") == ("outlook", "Bob Wilson")

    def test_calendar_source(self):
        assert parse_source_name("Calendar: Standup") == ("calendar", "Standup")

    def test_rss_source(self):
        assert parse_source_name("RSS: TechCrunch") == ("rss", "TechCrunch")

    def test_unknown_source(self):
        assert parse_source_name("Something else") == ("unknown", "Something else")

    def test_empty_source(self):
        assert parse_source_name("") == ("unknown", "")

    def test_case_insensitive(self):
        assert parse_source_name("TEAMS: Test") == ("teams", "Test")
        assert parse_source_name("email: test@test.com") == ("outlook", "test@test.com")


class TestGetSweepConfig:
    def test_default_config(self):
        cfg = get_sweep_config({})
        assert cfg["enabled"] is False
        assert cfg["sweep_types"] == ["fyi"]
        assert cfg["max_priority"] == "low"
        assert "reply_needed" in cfg["never_sweep"]

    def test_custom_config(self):
        cfg = get_sweep_config({
            "monitoring": {
                "sweep": {
                    "enabled": True,
                    "max_priority": "medium",
                }
            }
        })
        assert cfg["enabled"] is True
        assert cfg["max_priority"] == "medium"
        # Defaults preserved
        assert cfg["sweep_types"] == ["fyi"]


class TestClassifyForSweep:
    def _make_item(self, item_type="fyi", priority="low", source="Teams: Test"):
        return {"type": item_type, "priority": priority, "source": source, "title": "Test"}

    def test_fyi_low_teams_is_swept(self):
        items = [self._make_item("fyi", "low", "Teams: John")]
        teams, outlook = classify_for_sweep(items, DEFAULT_SWEEP_CONFIG)
        assert teams == ["John"]
        assert outlook == []

    def test_fyi_low_email_is_swept(self):
        items = [self._make_item("fyi", "low", "Email: Alice")]
        teams, outlook = classify_for_sweep(items, DEFAULT_SWEEP_CONFIG)
        assert teams == []
        assert len(outlook) == 1
        assert outlook[0]["sender"] == "Alice"

    def test_reply_needed_never_swept(self):
        items = [self._make_item("reply_needed", "low", "Teams: Boss")]
        teams, outlook = classify_for_sweep(items, DEFAULT_SWEEP_CONFIG)
        assert teams == []
        assert outlook == []

    def test_escalation_never_swept(self):
        items = [self._make_item("escalation", "low", "Email: VP")]
        teams, outlook = classify_for_sweep(items, DEFAULT_SWEEP_CONFIG)
        assert teams == []
        assert outlook == []

    def test_urgent_priority_not_swept(self):
        items = [self._make_item("fyi", "urgent", "Teams: CEO")]
        teams, outlook = classify_for_sweep(items, DEFAULT_SWEEP_CONFIG)
        assert teams == []

    def test_high_priority_not_swept(self):
        items = [self._make_item("fyi", "high", "Teams: Manager")]
        teams, outlook = classify_for_sweep(items, DEFAULT_SWEEP_CONFIG)
        assert teams == []

    def test_medium_priority_not_swept_with_low_threshold(self):
        items = [self._make_item("fyi", "medium", "Teams: Peer")]
        teams, outlook = classify_for_sweep(items, DEFAULT_SWEEP_CONFIG)
        assert teams == []

    def test_medium_priority_swept_with_medium_threshold(self):
        config = dict(DEFAULT_SWEEP_CONFIG)
        config["max_priority"] = "medium"
        items = [self._make_item("fyi", "medium", "Teams: Peer")]
        teams, outlook = classify_for_sweep(items, config)
        assert teams == ["Peer"]

    def test_non_fyi_type_not_swept_by_default(self):
        items = [self._make_item("action_item", "low", "Teams: Peer")]
        teams, outlook = classify_for_sweep(items, DEFAULT_SWEEP_CONFIG)
        assert teams == []

    def test_calendar_source_not_swept(self):
        items = [self._make_item("fyi", "low", "Calendar: Standup")]
        teams, outlook = classify_for_sweep(items, DEFAULT_SWEEP_CONFIG)
        assert teams == []
        assert outlook == []

    def test_multiple_items_mixed(self):
        items = [
            self._make_item("fyi", "low", "Teams: Alice"),
            self._make_item("reply_needed", "urgent", "Email: Boss"),
            self._make_item("fyi", "low", "Email: Newsletter"),
            self._make_item("fyi", "high", "Teams: Important"),
        ]
        teams, outlook = classify_for_sweep(items, DEFAULT_SWEEP_CONFIG)
        assert teams == ["Alice"]
        assert len(outlook) == 1
        assert outlook[0]["sender"] == "Newsletter"

    def test_empty_items(self):
        teams, outlook = classify_for_sweep([], DEFAULT_SWEEP_CONFIG)
        assert teams == []
        assert outlook == []


class TestLoadLatestTriageItems:
    def test_no_reports(self, tmp_path, monkeypatch):
        monkeypatch.setattr("collectors.sweep.PULSE_HOME", tmp_path)
        assert load_latest_triage_items() == []

    def test_loads_most_recent(self, tmp_path, monkeypatch):
        monkeypatch.setattr("collectors.sweep.PULSE_HOME", tmp_path)
        data = {"items": [{"id": "test-1", "type": "fyi"}]}
        (tmp_path / "monitoring-2026-03-04T10-00.json").write_text(
            json.dumps(data), encoding="utf-8"
        )
        result = load_latest_triage_items()
        assert len(result) == 1
        assert result[0]["id"] == "test-1"


# ---------------------------------------------------------------------------
# Teams marker tests (mock Playwright)
# ---------------------------------------------------------------------------

from collectors.teams_marker import mark_teams_chats_read, CLICK_UNREAD_CHAT_JS


class TestTeamsMarker:
    @pytest.mark.asyncio
    async def test_no_browser_returns_error(self):
        with patch("core.browser.get_browser_manager", return_value=None):
            result = await mark_teams_chats_read(["Test"])
            assert result["success"] is False
            assert "No shared browser" in result["details"][0]

    @pytest.mark.asyncio
    async def test_auth_redirect_returns_error(self):
        page = AsyncMock()
        page.url = "https://login.microsoftonline.com/..."
        page.goto = AsyncMock()
        page.wait_for_load_state = AsyncMock()

        mgr = MagicMock()
        mgr.context = True
        mgr.is_alive = True
        mgr.new_page = AsyncMock(return_value=page)

        with patch("core.browser.get_browser_manager", return_value=mgr):
            result = await mark_teams_chats_read(["Chat"])
            assert result["success"] is False
            assert "expired" in result["details"][0].lower()

    @pytest.mark.asyncio
    async def test_no_unread_chats(self):
        page = AsyncMock()
        page.url = "https://teams.cloud.microsoft/"
        page.goto = AsyncMock()
        page.wait_for_load_state = AsyncMock()
        page.wait_for_selector = AsyncMock()
        page.wait_for_timeout = AsyncMock()
        page.evaluate = AsyncMock(side_effect=[
            0,  # expand categories
            [],  # CLICK_ALL_UNREAD_CHATS_JS returns empty
        ])

        mgr = MagicMock()
        mgr.context = True
        mgr.is_alive = True
        mgr.new_page = AsyncMock(return_value=page)

        with patch("core.browser.get_browser_manager", return_value=mgr):
            result = await mark_teams_chats_read()
            assert result["success"] is True
            assert result["marked"] == 0

    @pytest.mark.asyncio
    async def test_marks_specific_chat(self):
        page = AsyncMock()
        page.url = "https://teams.cloud.microsoft/"
        page.goto = AsyncMock()
        page.wait_for_load_state = AsyncMock()
        page.wait_for_selector = AsyncMock()
        page.wait_for_timeout = AsyncMock()
        page.evaluate = AsyncMock(side_effect=[
            0,  # expand categories
            {"found": True, "clicked": True, "name": "Alice"},  # CLICK_UNREAD_CHAT_JS
        ])

        mgr = MagicMock()
        mgr.context = True
        mgr.is_alive = True
        mgr.new_page = AsyncMock(return_value=page)

        with patch("core.browser.get_browser_manager", return_value=mgr):
            result = await mark_teams_chats_read(["Alice"])
            assert result["marked"] == 1
            assert result["failed"] == 0

    @pytest.mark.asyncio
    async def test_chat_not_found(self):
        page = AsyncMock()
        page.url = "https://teams.cloud.microsoft/"
        page.goto = AsyncMock()
        page.wait_for_load_state = AsyncMock()
        page.wait_for_selector = AsyncMock()
        page.wait_for_timeout = AsyncMock()
        page.evaluate = AsyncMock(side_effect=[
            0,  # expand
            {"found": False, "reason": "not found in unread items"},  # click
        ])

        mgr = MagicMock()
        mgr.context = True
        mgr.is_alive = True
        mgr.new_page = AsyncMock(return_value=page)

        with patch("core.browser.get_browser_manager", return_value=mgr):
            result = await mark_teams_chats_read(["NonExistent"])
            assert result["failed"] == 1
            assert result["marked"] == 0


# ---------------------------------------------------------------------------
# Outlook marker tests (mock Playwright)
# ---------------------------------------------------------------------------

from collectors.outlook_marker import mark_outlook_emails_read


class TestOutlookMarker:
    @pytest.mark.asyncio
    async def test_no_browser_returns_error(self):
        with patch("core.browser.get_browser_manager", return_value=None):
            result = await mark_outlook_emails_read([{"conv_id": "123"}])
            assert result["success"] is False

    @pytest.mark.asyncio
    async def test_auth_redirect_returns_error(self):
        page = AsyncMock()
        page.url = "https://login.microsoftonline.com/..."
        page.goto = AsyncMock()
        page.wait_for_load_state = AsyncMock()

        mgr = MagicMock()
        mgr.context = True
        mgr.is_alive = True
        mgr.new_page = AsyncMock(return_value=page)

        with patch("core.browser.get_browser_manager", return_value=mgr):
            result = await mark_outlook_emails_read([{"conv_id": "123"}])
            assert result["success"] is False

    @pytest.mark.asyncio
    async def test_no_unread_emails(self):
        page = AsyncMock()
        page.url = "https://outlook.office.com/mail/inbox"
        page.goto = AsyncMock()
        page.wait_for_load_state = AsyncMock()
        page.wait_for_selector = AsyncMock()
        page.wait_for_timeout = AsyncMock()
        page.evaluate = AsyncMock(return_value=[])  # GET_ALL_UNREAD_JS

        mgr = MagicMock()
        mgr.context = True
        mgr.is_alive = True
        mgr.new_page = AsyncMock(return_value=page)

        with patch("core.browser.get_browser_manager", return_value=mgr):
            result = await mark_outlook_emails_read()
            assert result["success"] is True
            assert result["marked"] == 0

    @pytest.mark.asyncio
    async def test_marks_by_conv_id(self):
        page = AsyncMock()
        page.url = "https://outlook.office.com/mail/inbox"
        page.goto = AsyncMock()
        page.wait_for_load_state = AsyncMock()
        page.wait_for_selector = AsyncMock()
        page.wait_for_timeout = AsyncMock()
        page.keyboard = AsyncMock()
        page.keyboard.press = AsyncMock()

        # SELECT -> CHECK (verified read)
        page.evaluate = AsyncMock(side_effect=[
            {"found": True, "unread": True, "label": "Unread from Alice"},  # select
            {"found": True, "unread": False},  # check (now read)
        ])

        mgr = MagicMock()
        mgr.context = True
        mgr.is_alive = True
        mgr.new_page = AsyncMock(return_value=page)

        with patch("core.browser.get_browser_manager", return_value=mgr):
            result = await mark_outlook_emails_read([{
                "conv_id": "abc123", "sender": "Alice"
            }])
            assert result["marked"] == 1
            page.keyboard.press.assert_called()

    @pytest.mark.asyncio
    async def test_skips_no_conv_id(self):
        page = AsyncMock()
        page.url = "https://outlook.office.com/mail/inbox"
        page.goto = AsyncMock()
        page.wait_for_load_state = AsyncMock()
        page.wait_for_selector = AsyncMock()
        page.wait_for_timeout = AsyncMock()

        mgr = MagicMock()
        mgr.context = True
        mgr.is_alive = True
        mgr.new_page = AsyncMock(return_value=page)

        with patch("core.browser.get_browser_manager", return_value=mgr):
            result = await mark_outlook_emails_read([{
                "conv_id": "", "sender": "NoId"
            }])
            assert result["skipped"] == 1
            assert result["marked"] == 0

    @pytest.mark.asyncio
    async def test_already_read_skipped(self):
        page = AsyncMock()
        page.url = "https://outlook.office.com/mail/inbox"
        page.goto = AsyncMock()
        page.wait_for_load_state = AsyncMock()
        page.wait_for_selector = AsyncMock()
        page.wait_for_timeout = AsyncMock()

        page.evaluate = AsyncMock(return_value={"found": True, "unread": False, "label": "from Bob"})

        mgr = MagicMock()
        mgr.context = True
        mgr.is_alive = True
        mgr.new_page = AsyncMock(return_value=page)

        with patch("core.browser.get_browser_manager", return_value=mgr):
            result = await mark_outlook_emails_read([{
                "conv_id": "abc", "sender": "Bob"
            }])
            assert result["skipped"] == 1
            assert result["marked"] == 0


# ---------------------------------------------------------------------------
# IPC tests (filesystem)
# ---------------------------------------------------------------------------

from tui.ipc import queue_mark_read_job


class TestQueueMarkReadJob:
    def test_teams_item(self, tmp_path, monkeypatch):
        monkeypatch.setattr("tui.ipc.JOBS_DIR", tmp_path / "jobs")
        item = {"id": "t-1", "source": "Teams: Alice", "title": "Hello"}
        assert queue_mark_read_job(item) is True
        # Verify YAML was written
        pending = list((tmp_path / "jobs" / "pending").glob("*.yaml"))
        assert len(pending) == 1
        data = yaml.safe_load(pending[0].read_text())
        assert data["type"] == "mark_read_teams"
        assert data["chat_name"] == "Alice"

    def test_email_item(self, tmp_path, monkeypatch):
        monkeypatch.setattr("tui.ipc.JOBS_DIR", tmp_path / "jobs")
        item = {"id": "e-1", "source": "Email: Bob", "title": "Proposal", "conv_id": "xyz"}
        assert queue_mark_read_job(item) is True
        pending = list((tmp_path / "jobs" / "pending").glob("*.yaml"))
        data = yaml.safe_load(pending[0].read_text())
        assert data["type"] == "mark_read_outlook"
        assert data["sender"] == "Bob"
        assert data["conv_id"] == "xyz"

    def test_email_from_item(self, tmp_path, monkeypatch):
        monkeypatch.setattr("tui.ipc.JOBS_DIR", tmp_path / "jobs")
        item = {"id": "e-2", "source": "Email from Carol", "title": "Update"}
        assert queue_mark_read_job(item) is True
        pending = list((tmp_path / "jobs" / "pending").glob("*.yaml"))
        data = yaml.safe_load(pending[0].read_text())
        assert data["type"] == "mark_read_outlook"
        assert data["sender"] == "Carol"

    def test_unknown_source_returns_false(self):
        item = {"id": "x-1", "source": "RSS: TechCrunch"}
        assert queue_mark_read_job(item) is False

    def test_empty_source_returns_false(self):
        item = {"id": "x-2", "source": ""}
        assert queue_mark_read_job(item) is False


# ---------------------------------------------------------------------------
# SDK tool test
# ---------------------------------------------------------------------------

from sdk.tools import sweep_inbox, SweepInboxParams


class TestSweepInboxTool:
    @pytest.mark.asyncio
    async def test_queues_smart_sweep(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.constants.JOBS_DIR", tmp_path / "jobs")
        monkeypatch.setattr("sdk.tools.JOBS_DIR", tmp_path / "jobs")
        result = await sweep_inbox.handler({"arguments": {"full_sweep": False}})
        assert result["resultType"] == "success"
        pending = list((tmp_path / "jobs" / "pending").glob("*.yaml"))
        assert len(pending) == 1
        data = yaml.safe_load(pending[0].read_text())
        assert data["type"] == "inbox_sweep"
        assert data["full_sweep"] is False

    @pytest.mark.asyncio
    async def test_queues_full_sweep(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.constants.JOBS_DIR", tmp_path / "jobs")
        monkeypatch.setattr("sdk.tools.JOBS_DIR", tmp_path / "jobs")
        result = await sweep_inbox.handler({"arguments": {"full_sweep": True}})
        assert result["resultType"] == "success"
        pending = list((tmp_path / "jobs" / "pending").glob("*.yaml"))
        data = yaml.safe_load(pending[0].read_text())
        assert data["full_sweep"] is True


# ---------------------------------------------------------------------------
# Sweep execute_sweep integration test (mocked markers)
# ---------------------------------------------------------------------------

from collectors.sweep import execute_sweep


class TestExecuteSweep:
    @pytest.mark.asyncio
    async def test_full_sweep_calls_both_markers(self):
        mock_teams = AsyncMock(return_value={"marked": 5, "failed": 0})
        mock_outlook = AsyncMock(return_value={"marked": 3, "failed": 0})

        with patch("collectors.teams_marker.mark_teams_chats_read", mock_teams), \
             patch("collectors.outlook_marker.mark_outlook_emails_read", mock_outlook):
            result = await execute_sweep({}, full_sweep=True)
            assert result["success"] is True
            mock_teams.assert_called_once_with(chat_names=None)
            mock_outlook.assert_called_once_with(items=None)
            assert "8" in result["summary"]

    @pytest.mark.asyncio
    async def test_smart_sweep_with_triage_data(self, tmp_path, monkeypatch):
        monkeypatch.setattr("collectors.sweep.PULSE_HOME", tmp_path)

        # Write triage data
        triage_data = {
            "items": [
                {"type": "fyi", "priority": "low", "source": "Teams: Alice", "title": "FYI"},
                {"type": "reply_needed", "priority": "urgent", "source": "Email: Boss", "title": "Reply"},
            ]
        }
        (tmp_path / "monitoring-2026-03-04T10-00.json").write_text(
            json.dumps(triage_data), encoding="utf-8"
        )

        mock_teams = AsyncMock(return_value={"marked": 1, "failed": 0})
        mock_outlook = AsyncMock(return_value={"marked": 0, "failed": 0})

        with patch("collectors.teams_marker.mark_teams_chats_read", mock_teams), \
             patch("collectors.outlook_marker.mark_outlook_emails_read", mock_outlook):
            result = await execute_sweep(
                {"monitoring": {"sweep": {"enabled": True}}},
                full_sweep=False,
            )
            assert result["success"] is True
            # Only Alice (FYI/low) should be swept, not Boss (reply_needed/urgent)
            mock_teams.assert_called_once_with(chat_names=["Alice"])

    @pytest.mark.asyncio
    async def test_smart_sweep_no_triage_falls_back_to_full(self, tmp_path, monkeypatch):
        monkeypatch.setattr("collectors.sweep.PULSE_HOME", tmp_path)

        mock_teams = AsyncMock(return_value={"marked": 0, "failed": 0})
        mock_outlook = AsyncMock(return_value={"marked": 0, "failed": 0})

        with patch("collectors.teams_marker.mark_teams_chats_read", mock_teams), \
             patch("collectors.outlook_marker.mark_outlook_emails_read", mock_outlook):
            result = await execute_sweep({}, full_sweep=False)
            # Falls back to full sweep when no triage data
            mock_teams.assert_called_once_with(chat_names=None)
            mock_outlook.assert_called_once_with(items=None)
