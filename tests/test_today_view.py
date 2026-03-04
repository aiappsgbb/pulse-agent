"""Tests for the Today view — calendar parsing, commitment filtering, summary building."""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ---------------------------------------------------------------------------
# Calendar date parsing
# ---------------------------------------------------------------------------


class TestParseCalendarDate:
    def test_standard_format(self):
        from tui.screens import _parse_calendar_date
        assert _parse_calendar_date("Monday, March 3, 2026") == "2026-03-03"

    def test_with_leading_day_name(self):
        from tui.screens import _parse_calendar_date
        assert _parse_calendar_date("Thursday, February 20, 2026") == "2026-02-20"

    def test_december(self):
        from tui.screens import _parse_calendar_date
        assert _parse_calendar_date("Wednesday, December 25, 2026") == "2026-12-25"

    def test_january_single_digit_day(self):
        from tui.screens import _parse_calendar_date
        assert _parse_calendar_date("Friday, January 5, 2026") == "2026-01-05"

    def test_no_day_name(self):
        from tui.screens import _parse_calendar_date
        # Gracefully handle "March 3, 2026" without day-of-week prefix
        assert _parse_calendar_date("March 3, 2026") == "2026-03-03"

    def test_invalid_format(self):
        from tui.screens import _parse_calendar_date
        assert _parse_calendar_date("not a date") is None
        assert _parse_calendar_date("") is None

    def test_unknown_month(self):
        from tui.screens import _parse_calendar_date
        assert _parse_calendar_date("Monday, Smarch 3, 2026") is None


# ---------------------------------------------------------------------------
# Filter today events
# ---------------------------------------------------------------------------


class TestFilterTodayEvents:
    def _make_event(self, title="Meeting", date_str="Monday, March 3, 2026",
                    start="9:00 AM", end="10:00 AM", is_declined=False):
        return {
            "title": title,
            "date": date_str,
            "start_time": start,
            "end_time": end,
            "organizer": "Alice",
            "is_teams": True,
            "is_recurring": False,
            "is_declined": is_declined,
        }

    def test_filters_to_today_only(self):
        from tui.screens import _filter_today_events
        today = datetime.now()
        today_str = today.strftime("%A, %B %-d, %Y") if sys.platform != "win32" else today.strftime("%A, %B %#d, %Y")

        events = [
            self._make_event("Today Meeting", today_str, "9:00 AM"),
            self._make_event("Tomorrow Meeting", "Tuesday, December 31, 2030", "10:00 AM"),
        ]
        result = _filter_today_events(events)
        assert len(result) == 1
        assert result[0]["title"] == "Today Meeting"

    def test_excludes_declined(self):
        from tui.screens import _filter_today_events
        today = datetime.now()
        today_str = today.strftime("%A, %B %-d, %Y") if sys.platform != "win32" else today.strftime("%A, %B %#d, %Y")

        events = [
            self._make_event("Active", today_str, "9:00 AM"),
            self._make_event("Declined", today_str, "10:00 AM", is_declined=True),
        ]
        result = _filter_today_events(events)
        assert len(result) == 1
        assert result[0]["title"] == "Active"

    def test_sorts_by_start_time(self):
        from tui.screens import _filter_today_events
        today = datetime.now()
        today_str = today.strftime("%A, %B %-d, %Y") if sys.platform != "win32" else today.strftime("%A, %B %#d, %Y")

        events = [
            self._make_event("Afternoon", today_str, "2:00 PM"),
            self._make_event("Morning", today_str, "9:00 AM"),
            self._make_event("Noon", today_str, "12:00 PM"),
        ]
        result = _filter_today_events(events)
        assert [e["title"] for e in result] == ["Morning", "Noon", "Afternoon"]

    def test_empty_events(self):
        from tui.screens import _filter_today_events
        assert _filter_today_events([]) == []

    def test_no_today_events(self):
        from tui.screens import _filter_today_events
        events = [self._make_event("Future", "Monday, December 31, 2030")]
        assert _filter_today_events(events) == []


# ---------------------------------------------------------------------------
# Due commitments filtering
# ---------------------------------------------------------------------------


class TestGetDueCommitments:
    def _make_project(self, name, commitments):
        return {
            "_id": name.lower(),
            "project": name,
            "status": "active",
            "commitments": commitments,
        }

    def test_finds_due_today(self):
        from tui.screens import _get_due_commitments
        today = datetime.now().strftime("%Y-%m-%d")
        projects = [self._make_project("Alpha", [
            {"what": "Due today", "due": today, "status": "open"},
            {"what": "Future", "due": "2030-12-31", "status": "open"},
        ])]
        result = _get_due_commitments(projects, days_ahead=7)
        assert any(c["what"] == "Due today" for c in result)

    def test_finds_overdue(self):
        from tui.screens import _get_due_commitments
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        projects = [self._make_project("Beta", [
            {"what": "Overdue task", "due": yesterday, "status": "open"},
        ])]
        result = _get_due_commitments(projects, days_ahead=7)
        assert len(result) == 1
        assert result[0]["what"] == "Overdue task"

    def test_excludes_done(self):
        from tui.screens import _get_due_commitments
        today = datetime.now().strftime("%Y-%m-%d")
        projects = [self._make_project("Gamma", [
            {"what": "Done task", "due": today, "status": "done"},
            {"what": "Cancelled", "due": today, "status": "cancelled"},
        ])]
        result = _get_due_commitments(projects, days_ahead=7)
        assert len(result) == 0

    def test_excludes_far_future(self):
        from tui.screens import _get_due_commitments
        projects = [self._make_project("Delta", [
            {"what": "Far future", "due": "2030-12-31", "status": "open"},
        ])]
        result = _get_due_commitments(projects, days_ahead=7)
        assert len(result) == 0

    def test_includes_project_info(self):
        from tui.screens import _get_due_commitments
        today = datetime.now().strftime("%Y-%m-%d")
        projects = [self._make_project("Epsilon", [
            {"what": "Task", "due": today, "status": "open", "who": "You", "to": "Boss"},
        ])]
        result = _get_due_commitments(projects, days_ahead=7)
        assert result[0]["project_name"] == "Epsilon"
        assert result[0]["project_id"] == "epsilon"
        assert result[0]["is_today"] is True

    def test_sort_order_overdue_first(self):
        from tui.screens import _get_due_commitments
        today = datetime.now().strftime("%Y-%m-%d")
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        projects = [self._make_project("Sort", [
            {"what": "Tomorrow", "due": tomorrow, "status": "open"},
            {"what": "Today", "due": today, "status": "open"},
            {"what": "Yesterday", "due": yesterday, "status": "open"},
        ])]
        result = _get_due_commitments(projects, days_ahead=7)
        assert result[0]["what"] == "Yesterday"
        assert result[1]["what"] == "Today"
        assert result[2]["what"] == "Tomorrow"

    def test_empty_projects(self):
        from tui.screens import _get_due_commitments
        assert _get_due_commitments([], days_ahead=7) == []

    def test_no_due_dates(self):
        from tui.screens import _get_due_commitments
        projects = [self._make_project("NoDue", [
            {"what": "Task", "status": "open"},
        ])]
        result = _get_due_commitments(projects, days_ahead=7)
        assert len(result) == 0

    def test_handles_datetime_date_from_yaml(self):
        """YAML auto-parses '2026-03-03' as datetime.date, not str."""
        from datetime import date
        from tui.screens import _get_due_commitments
        today = date.today()
        projects = [self._make_project("DateObj", [
            {"what": "YAML date task", "due": today, "status": "open"},
        ])]
        result = _get_due_commitments(projects, days_ahead=7)
        assert len(result) == 1
        assert result[0]["what"] == "YAML date task"
        assert isinstance(result[0]["due"], str)  # Normalized to string
        assert result[0]["is_today"] is True


# ---------------------------------------------------------------------------
# Calendar scan file loading
# ---------------------------------------------------------------------------


class TestLoadCalendarEvents:
    def test_loads_from_file(self, tmp_dir):
        scan_file = tmp_dir / ".calendar-scan.json"
        events = [{"title": "Standup", "start_time": "9:00 AM"}]
        scan_file.write_text(json.dumps({
            "scanned_at": "2026-03-03T08:00:00",
            "events": events,
            "available": True,
        }), encoding="utf-8")

        with patch("tui.screens._CALENDAR_SCAN_FILE", scan_file):
            from tui.screens import _load_calendar_events
            result, scanned_at = _load_calendar_events()
            assert len(result) == 1
            assert result[0]["title"] == "Standup"
            assert "2026-03-03" in scanned_at

    def test_unavailable_scan(self, tmp_dir):
        scan_file = tmp_dir / ".calendar-scan.json"
        scan_file.write_text(json.dumps({
            "scanned_at": "2026-03-03T08:00:00",
            "events": [],
            "available": False,
        }), encoding="utf-8")

        with patch("tui.screens._CALENDAR_SCAN_FILE", scan_file):
            from tui.screens import _load_calendar_events
            result, scanned_at = _load_calendar_events()
            assert result == []
            assert scanned_at == ""

    def test_missing_file(self, tmp_dir):
        scan_file = tmp_dir / ".calendar-scan.json"
        with patch("tui.screens._CALENDAR_SCAN_FILE", scan_file):
            from tui.screens import _load_calendar_events
            result, scanned_at = _load_calendar_events()
            assert result == []
            assert scanned_at == ""

    def test_corrupt_file(self, tmp_dir):
        scan_file = tmp_dir / ".calendar-scan.json"
        scan_file.write_text("not json", encoding="utf-8")

        with patch("tui.screens._CALENDAR_SCAN_FILE", scan_file):
            from tui.screens import _load_calendar_events
            result, scanned_at = _load_calendar_events()
            assert result == []


# ---------------------------------------------------------------------------
# Match meeting to project
# ---------------------------------------------------------------------------


class TestMatchMeetingToProject:
    def test_matches_by_stakeholder_in_title(self):
        from tui.screens import _match_meeting_to_project
        event = {"title": "Review with Alice Smith", "organizer": "Someone"}
        projects = [{"_id": "p1", "project": "Contoso", "stakeholders": [{"name": "Alice Smith"}]}]
        assert _match_meeting_to_project(event, projects) == projects[0]

    def test_matches_by_stakeholder_in_organizer(self):
        from tui.screens import _match_meeting_to_project
        event = {"title": "Standup", "organizer": "Alice Smith"}
        projects = [{"_id": "p1", "project": "Contoso", "stakeholders": [{"name": "Alice Smith"}]}]
        assert _match_meeting_to_project(event, projects) == projects[0]

    def test_matches_by_project_name_in_title(self):
        from tui.screens import _match_meeting_to_project
        event = {"title": "Contoso quarterly review", "organizer": "X"}
        projects = [{"_id": "p1", "project": "Contoso", "stakeholders": []}]
        assert _match_meeting_to_project(event, projects) == projects[0]

    def test_no_match_returns_none(self):
        from tui.screens import _match_meeting_to_project
        event = {"title": "Unrelated meeting", "organizer": "Nobody"}
        projects = [{"_id": "p1", "project": "Contoso", "stakeholders": [{"name": "Alice"}]}]
        assert _match_meeting_to_project(event, projects) is None

    def test_case_insensitive(self):
        from tui.screens import _match_meeting_to_project
        event = {"title": "CONTOSO review", "organizer": ""}
        projects = [{"_id": "p1", "project": "contoso", "stakeholders": []}]
        assert _match_meeting_to_project(event, projects) == projects[0]

    def test_empty_stakeholders(self):
        from tui.screens import _match_meeting_to_project
        event = {"title": "Random meeting", "organizer": "Boss"}
        projects = [{"_id": "p1", "project": "Contoso"}]
        assert _match_meeting_to_project(event, projects) is None


# ---------------------------------------------------------------------------
# Build prep hints
# ---------------------------------------------------------------------------


class TestBuildPrepHints:
    def test_overdue_hint(self):
        from tui.screens import _build_prep_hints
        project = {"commitments": [
            {"status": "overdue", "what": "a"},
            {"status": "overdue", "what": "b"},
            {"status": "open", "what": "c"},
        ]}
        result = _build_prep_hints(project)
        assert "2 overdue" in result

    def test_open_hint_when_no_overdue(self):
        from tui.screens import _build_prep_hints
        project = {"commitments": [
            {"status": "open", "what": "a"},
            {"status": "done", "what": "b"},
        ]}
        result = _build_prep_hints(project)
        assert "1 open" in result

    def test_empty_when_all_done(self):
        from tui.screens import _build_prep_hints
        project = {"commitments": [{"status": "done", "what": "a"}]}
        assert _build_prep_hints(project) == ""

    def test_empty_no_commitments(self):
        from tui.screens import _build_prep_hints
        assert _build_prep_hints({}) == ""
        assert _build_prep_hints({"commitments": []}) == ""


# ---------------------------------------------------------------------------
# Load Today items (unified meetings + commitments)
# ---------------------------------------------------------------------------


class TestLoadTodayItems:
    def _today_str(self):
        today = datetime.now()
        if sys.platform != "win32":
            return today.strftime("%A, %B %-d, %Y")
        return today.strftime("%A, %B %#d, %Y")

    def test_empty_when_no_data(self):
        with patch("tui.screens._load_calendar_events", return_value=([], "")):
            from tui.screens import _load_today_items
            items, mc, cc = _load_today_items(projects=[])
            assert items == []
            assert mc == 0
            assert cc == 0

    def test_meetings_come_first(self):
        today_str = self._today_str()
        events = [{"title": "Standup", "date": today_str, "start_time": "9:00 AM",
                    "end_time": "9:30 AM", "organizer": "Boss", "is_teams": True,
                    "is_recurring": False, "is_declined": False}]
        today_iso = datetime.now().strftime("%Y-%m-%d")
        projects = [{
            "_id": "test", "project": "Test", "status": "active",
            "commitments": [{"what": "Task", "due": today_iso, "status": "open"}],
        }]
        with patch("tui.screens._load_calendar_events", return_value=(events, "2026-03-03T08:00:00")):
            from tui.screens import _load_today_items
            items, mc, cc = _load_today_items(projects=projects)
            assert mc == 1
            assert cc >= 1
            assert items[0]["_type"] == "meeting"
            assert items[0]["title"] == "Standup"
            # Commitments come after meetings
            commitment_items = [i for i in items if i["_type"] == "commitment"]
            assert len(commitment_items) >= 1
            assert commitment_items[0]["what"] == "Task"

    def test_meeting_has_type_tag(self):
        today_str = self._today_str()
        events = [{"title": "M", "date": today_str, "start_time": "9:00 AM",
                    "end_time": "10:00 AM", "organizer": "O", "is_teams": False,
                    "is_recurring": False, "is_declined": False}]
        with patch("tui.screens._load_calendar_events", return_value=(events, "")):
            from tui.screens import _load_today_items
            items, _, _ = _load_today_items(projects=[])
            assert items[0]["_type"] == "meeting"

    def test_commitment_has_urgency_tags(self):
        today_iso = datetime.now().strftime("%Y-%m-%d")
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        projects = [{
            "_id": "test", "project": "Test", "status": "active",
            "commitments": [
                {"what": "Overdue", "due": yesterday, "status": "open"},
                {"what": "Today", "due": today_iso, "status": "open"},
                {"what": "Tomorrow", "due": tomorrow, "status": "open"},
            ],
        }]
        with patch("tui.screens._load_calendar_events", return_value=([], "")):
            from tui.screens import _load_today_items
            items, _, _ = _load_today_items(projects=projects)
            tags = {i["what"]: i["_urgency_tag"] for i in items}
            assert tags["Overdue"] == "OVERDUE"
            assert tags["Today"] == "DUE TODAY"
            assert "DUE" in tags["Tomorrow"]

    def test_meeting_linked_project(self):
        today_str = self._today_str()
        events = [{"title": "Review with Alice", "date": today_str, "start_time": "10:00 AM",
                    "end_time": "11:00 AM", "organizer": "Alice Smith", "is_teams": True,
                    "is_recurring": False, "is_declined": False}]
        projects = [{
            "_id": "contoso", "project": "Contoso Deal", "status": "active",
            "stakeholders": [{"name": "Alice Smith", "role": "PM"}],
            "commitments": [{"what": "Send doc", "status": "overdue", "due": "2026-02-01"}],
        }]
        with patch("tui.screens._load_calendar_events", return_value=(events, "2026-03-03T08:00:00")):
            from tui.screens import _load_today_items
            items, _, _ = _load_today_items(projects=projects)
            meeting = items[0]
            assert meeting["_linked_project"]["_id"] == "contoso"
            assert "overdue" in meeting["_prep_hints"].lower()

    def test_commitment_linked_project(self):
        today_iso = datetime.now().strftime("%Y-%m-%d")
        projects = [{
            "_id": "test", "project": "Test Project", "status": "active",
            "commitments": [{"what": "Task", "due": today_iso, "status": "open"}],
        }]
        with patch("tui.screens._load_calendar_events", return_value=([], "")):
            from tui.screens import _load_today_items
            items, _, _ = _load_today_items(projects=projects)
            assert items[0]["_linked_project"]["_id"] == "test"

    def test_returns_counts(self):
        today_str = self._today_str()
        today_iso = datetime.now().strftime("%Y-%m-%d")
        events = [
            {"title": "M1", "date": today_str, "start_time": "9:00 AM", "end_time": "10:00 AM",
             "organizer": "", "is_teams": False, "is_recurring": False, "is_declined": False},
            {"title": "M2", "date": today_str, "start_time": "11:00 AM", "end_time": "12:00 PM",
             "organizer": "", "is_teams": False, "is_recurring": False, "is_declined": False},
        ]
        projects = [{
            "_id": "p", "project": "P", "status": "active",
            "commitments": [
                {"what": "C1", "due": today_iso, "status": "open"},
                {"what": "C2", "due": today_iso, "status": "open"},
                {"what": "C3", "due": today_iso, "status": "open"},
            ],
        }]
        with patch("tui.screens._load_calendar_events", return_value=(events, "")):
            from tui.screens import _load_today_items
            items, mc, cc = _load_today_items(projects=projects)
            assert mc == 2
            assert cc == 3
            assert len(items) == 5


# ---------------------------------------------------------------------------
# TodayPane formatting helpers (no Textual needed)
# ---------------------------------------------------------------------------


class TestTodayPaneFormatting:
    def test_fmt_meeting_with_teams(self):
        from tui.screens import TodayPane
        pane = TodayPane.__new__(TodayPane)
        item = {"start_time": "9:00 AM", "end_time": "10:00 AM",
                "title": "Standup", "is_teams": True, "organizer": "Boss",
                "_prep_hints": ""}
        text = pane._fmt_meeting(item, 50)
        assert "9:00 AM" in text
        assert "Standup" in text
        assert "Teams" in text
        assert "Boss" in text

    def test_fmt_meeting_without_teams(self):
        from tui.screens import TodayPane
        pane = TodayPane.__new__(TodayPane)
        item = {"start_time": "2:00 PM", "end_time": "3:00 PM",
                "title": "Lunch", "is_teams": False, "organizer": "",
                "_prep_hints": ""}
        text = pane._fmt_meeting(item, 50)
        assert "Teams" not in text
        assert "Lunch" in text

    def test_fmt_commitment_overdue(self):
        from tui.screens import TodayPane
        pane = TodayPane.__new__(TodayPane)
        item = {"what": "Late task", "project_name": "Proj",
                "_urgency_tag": "OVERDUE", "_urgency_color": "red"}
        text = pane._fmt_commitment(item, 50)
        assert "OVERDUE" in text
        assert "Late task" in text
        assert "Proj" in text

    def test_fmt_commitment_due_today(self):
        from tui.screens import TodayPane
        pane = TodayPane.__new__(TodayPane)
        item = {"what": "Do thing", "project_name": "P",
                "_urgency_tag": "DUE TODAY", "_urgency_color": "bold yellow"}
        text = pane._fmt_commitment(item, 50)
        assert "DUE TODAY" in text

    def test_fmt_meeting_with_prep_hints(self):
        from tui.screens import TodayPane
        pane = TodayPane.__new__(TodayPane)
        item = {"start_time": "9:00 AM", "end_time": "10:00 AM",
                "title": "Review", "is_teams": True, "organizer": "Alice",
                "_prep_hints": "[red](2 overdue)[/red]"}
        text = pane._fmt_meeting(item, 50)
        assert "2 overdue" in text


# ---------------------------------------------------------------------------
# Calendar scan persistence (runner.py)
# ---------------------------------------------------------------------------


class TestPersistCalendarScan:
    def test_persist_writes_json(self, tmp_dir):
        scan_file = tmp_dir / ".calendar-scan.json"
        with patch("sdk.runner.PULSE_HOME", tmp_dir):
            from sdk.runner import _persist_calendar_scan
            events = [{"title": "Test", "start_time": "9:00 AM"}]
            _persist_calendar_scan(events)

            data = json.loads(scan_file.read_text(encoding="utf-8"))
            assert data["available"] is True
            assert len(data["events"]) == 1
            assert "scanned_at" in data

    def test_persist_none_events(self, tmp_dir):
        scan_file = tmp_dir / ".calendar-scan.json"
        with patch("sdk.runner.PULSE_HOME", tmp_dir):
            from sdk.runner import _persist_calendar_scan
            _persist_calendar_scan(None)

            data = json.loads(scan_file.read_text(encoding="utf-8"))
            assert data["available"] is False
            assert data["events"] == []

    def test_persist_empty_events(self, tmp_dir):
        scan_file = tmp_dir / ".calendar-scan.json"
        with patch("sdk.runner.PULSE_HOME", tmp_dir):
            from sdk.runner import _persist_calendar_scan
            _persist_calendar_scan([])

            data = json.loads(scan_file.read_text(encoding="utf-8"))
            assert data["available"] is True
            assert data["events"] == []

    def test_persist_handles_error_gracefully(self):
        with patch("sdk.runner.PULSE_HOME", Path("/nonexistent/xyz")):
            from sdk.runner import _persist_calendar_scan
            # Should not raise
            _persist_calendar_scan([{"title": "Test"}])


# ---------------------------------------------------------------------------
# Intel items for Inbox
# ---------------------------------------------------------------------------


class TestIntelItems:
    def test_loads_intel_items_as_inbox_items(self, tmp_dir):
        intel_dir = tmp_dir / "intel"
        intel_dir.mkdir()
        md = (
            "# Intel Brief\n3 articles scanned\n\n"
            "## Moves & Announcements\n- **CompA** released pricing\n- **CompB** hired CEO\n\n"
            "## Trends\n- AI growing fast\n"
        )
        (intel_dir / "2026-03-04.md").write_text(md, encoding="utf-8")
        with patch("tui.screens.INTEL_DIR", intel_dir):
            from tui.screens import _load_intel_items
            items = _load_intel_items()
            assert len(items) == 2  # 2 sections with items
            assert items[0]["type"] == "intel"
            assert items[0]["_origin"] == "intel"
            assert items[0]["priority"] == "low"
            assert "Intel: Moves & Announcements" == items[0]["title"]
            assert "CompA released pricing" in items[0]["summary"]
            assert items[1]["title"] == "Intel: Trends"

    def test_returns_empty_when_no_intel(self, tmp_dir):
        intel_dir = tmp_dir / "intel"
        intel_dir.mkdir()
        with patch("tui.screens.INTEL_DIR", intel_dir):
            from tui.screens import _load_intel_items
            assert _load_intel_items() == []

    def test_skips_empty_sections(self, tmp_dir):
        intel_dir = tmp_dir / "intel"
        intel_dir.mkdir()
        md = "# Intel Brief\n\n## Empty Section\n\n## Has Items\n- one item\n"
        (intel_dir / "2026-03-04.md").write_text(md, encoding="utf-8")
        with patch("tui.screens.INTEL_DIR", intel_dir):
            from tui.screens import _load_intel_items
            items = _load_intel_items()
            assert len(items) == 1
            assert items[0]["title"] == "Intel: Has Items"

    def test_intel_item_ids_are_stable(self, tmp_dir):
        intel_dir = tmp_dir / "intel"
        intel_dir.mkdir()
        md = "# Intel Brief\n\n## Moves\n- item one\n"
        (intel_dir / "2026-03-04.md").write_text(md, encoding="utf-8")
        with patch("tui.screens.INTEL_DIR", intel_dir):
            from tui.screens import _load_intel_items
            items = _load_intel_items()
            assert items[0]["id"] == "intel-2026-03-04-moves"


# ---------------------------------------------------------------------------
# Digest summary + transcript status for Today briefing
# ---------------------------------------------------------------------------


class TestDigestSummary:
    def test_loads_digest_summary(self, tmp_dir):
        digests_dir = tmp_dir / "digests"
        digests_dir.mkdir()
        data = {"items": [
            {"status": "outstanding", "priority": "high"},
            {"status": "outstanding", "priority": "low"},
            {"status": "resolved"},
        ]}
        (digests_dir / "2026-03-04.json").write_text(json.dumps(data), encoding="utf-8")
        with patch("tui.screens.DIGESTS_DIR", digests_dir):
            from tui.screens import _load_digest_summary
            result = _load_digest_summary()
            assert result is not None
            assert result["date"] == "2026-03-04"
            assert result["outstanding"] == 2
            assert result["total"] == 3

    def test_returns_none_when_no_digests(self, tmp_dir):
        digests_dir = tmp_dir / "digests"
        digests_dir.mkdir()
        with patch("tui.screens.DIGESTS_DIR", digests_dir):
            from tui.screens import _load_digest_summary
            assert _load_digest_summary() is None


class TestTranscriptStatus:
    def test_loads_transcript_status(self, tmp_dir):
        status_file = tmp_dir / ".transcript-collection-status.json"
        data = {"success": True, "collected": 5, "errors": 0, "timestamp": "2026-03-04T08:00:00"}
        status_file.write_text(json.dumps(data), encoding="utf-8")
        with patch("tui.screens.TRANSCRIPT_STATUS_FILE", status_file):
            from tui.screens import _load_transcript_status
            result = _load_transcript_status()
            assert result is not None
            assert result["collected"] == 5

    def test_returns_none_when_no_status(self, tmp_dir):
        status_file = tmp_dir / ".nonexistent-status.json"
        with patch("tui.screens.TRANSCRIPT_STATUS_FILE", status_file):
            from tui.screens import _load_transcript_status
            assert _load_transcript_status() is None
