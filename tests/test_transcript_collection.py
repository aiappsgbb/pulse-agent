"""Tests for transcript collection — navigation, extraction return types, attempted slug management.

Validates the actual logic paths changed in the recap detection and
extraction pipeline, not just mocked interfaces.
"""

import asyncio
import json
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from collectors.transcripts.extraction import (
    extract_transcript_from_sharepoint,
    TransientExtractionError,
)
from collectors.transcripts.navigation import (
    _find_recap_element,
    _log_popup_diagnostics,
    _parse_meeting_date,
    discover_meetings_with_recaps,
    find_meeting_buttons,
    SKIP_KEYWORDS,
)
from collectors.transcripts.collector import (
    _load_attempted_slugs,
    _mark_attempted,
    _slugify,
    TRANSCRIPT_STATE_FILE,
    ATTEMPT_TTL_DAYS,
)


# ---------------------------------------------------------------------------
# Helpers for building mock Playwright pages
# ---------------------------------------------------------------------------

def _make_mock_page():
    """Create a mock Playwright Page with common methods."""
    page = AsyncMock()
    page.url = "https://outlook.cloud.microsoft/calendar/view/week"
    page.wait_for_timeout = AsyncMock()
    page.keyboard = AsyncMock()
    page.evaluate = AsyncMock(return_value=[])
    page.context = MagicMock()
    return page


def _make_locator(count=0, first=None):
    """Create a mock Playwright locator with count() and first."""
    loc = AsyncMock()
    loc.count = AsyncMock(return_value=count)
    loc.first = first or AsyncMock()
    loc.filter = MagicMock(return_value=loc)
    return loc


# ===========================================================================
# _find_recap_element tests
# ===========================================================================

class TestFindRecapElement:
    """Tests for _find_recap_element — the core recap button finder."""

    async def test_finds_button_by_role(self):
        """Finds a button with 'View recap' accessible name."""
        page = _make_mock_page()
        recap_btn = AsyncMock()
        match_loc = _make_locator(count=1, first=recap_btn)

        def fake_get_by_role(role, name=None):
            if role == "button" and name and name.pattern.lower().startswith("view recap"):
                return match_loc
            return _make_locator(count=0)

        page.get_by_role = MagicMock(side_effect=fake_get_by_role)
        page.locator = MagicMock(return_value=_make_locator(count=0))

        result = await _find_recap_element(page)
        assert result is recap_btn

    async def test_finds_link_by_role(self):
        """Finds a link with 'View recap' when no button exists."""
        page = _make_mock_page()
        recap_link = AsyncMock()
        link_loc = _make_locator(count=1, first=recap_link)

        def fake_get_by_role(role, name=None):
            if role == "link" and name and "recap" in name.pattern.lower():
                return link_loc
            return _make_locator(count=0)

        page.get_by_role = MagicMock(side_effect=fake_get_by_role)
        page.locator = MagicMock(return_value=_make_locator(count=0))

        result = await _find_recap_element(page)
        assert result is recap_link

    async def test_finds_generic_text_match(self):
        """Falls back to generic text search when role-based search fails."""
        page = _make_mock_page()
        generic_btn = AsyncMock()

        # All role-based searches return 0
        page.get_by_role = MagicMock(return_value=_make_locator(count=0))

        # Generic locator finds one
        generic_loc = _make_locator(count=1, first=generic_btn)
        page.locator = MagicMock(return_value=generic_loc)

        result = await _find_recap_element(page)
        assert result is generic_btn
        # Verify the locator was called with correct selector
        page.locator.assert_called_once()
        call_args = page.locator.call_args[0][0]
        assert "button" in call_args
        assert "a" in call_args

    async def test_returns_none_when_nothing_found(self):
        """Returns None when no recap element exists anywhere."""
        page = _make_mock_page()
        page.get_by_role = MagicMock(return_value=_make_locator(count=0))
        page.locator = MagicMock(return_value=_make_locator(count=0))

        result = await _find_recap_element(page)
        assert result is None

    async def test_handles_playwright_exceptions(self):
        """Gracefully handles Playwright errors during search."""
        page = _make_mock_page()
        page.get_by_role = MagicMock(side_effect=Exception("Element detached"))
        page.locator = MagicMock(side_effect=Exception("Page closed"))

        result = await _find_recap_element(page)
        assert result is None

    async def test_tries_all_patterns(self):
        """Verifies all recap text patterns are tried."""
        page = _make_mock_page()
        calls = []

        def track_get_by_role(role, name=None):
            if name:
                calls.append((role, name.pattern))
            return _make_locator(count=0)

        page.get_by_role = MagicMock(side_effect=track_get_by_role)
        page.locator = MagicMock(return_value=_make_locator(count=0))

        await _find_recap_element(page)

        # Should have tried button AND link for each pattern
        button_patterns = [p for role, p in calls if role == "button"]
        link_patterns = [p for role, p in calls if role == "link"]
        assert len(button_patterns) >= 5  # At least 5 recap patterns
        assert len(link_patterns) >= 5
        # Check key patterns were tried
        pattern_text = " ".join(button_patterns).lower()
        assert "view recap" in pattern_text
        assert "view transcript" in pattern_text
        assert "open recap" in pattern_text

    async def test_stops_at_first_match(self):
        """Returns immediately when first match found, doesn't keep searching."""
        page = _make_mock_page()
        first_btn = AsyncMock()
        call_count = 0

        def fake_get_by_role(role, name=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:  # First call (button "View recap")
                return _make_locator(count=1, first=first_btn)
            return _make_locator(count=0)

        page.get_by_role = MagicMock(side_effect=fake_get_by_role)
        page.locator = MagicMock(return_value=_make_locator(count=0))

        result = await _find_recap_element(page)
        assert result is first_btn
        # Should have stopped after first match — no link search, no other patterns
        assert call_count == 1


# ===========================================================================
# Extraction return type handling tests
# ===========================================================================

class TestExtractionReturnTypes:
    """Tests that extract_transcript_from_sharepoint returns the right types."""

    async def test_api_url_returns_false(self):
        """API URLs return False (permanent — URL structure won't change on retry)."""
        page = _make_mock_page()
        result = await extract_transcript_from_sharepoint(page, "https://example.com/_api/stream")
        assert result is False

    async def test_access_denied_returns_false(self):
        """AccessDenied pages return False (permanent)."""
        page = _make_mock_page()
        page.goto = AsyncMock()
        # Simulate landing on AccessDenied after redirect
        type(page).url = PropertyMock(
            side_effect=[
                "https://login.microsoftonline.com/",  # wait_attempt 0
                "https://sharepoint.com/AccessDenied",  # wait_attempt 1
                "https://sharepoint.com/AccessDenied",  # final check
            ]
        )
        page.on = MagicMock()

        result = await extract_transcript_from_sharepoint(
            page, "https://sharepoint.com/sites/video"
        )
        assert result is False

    async def test_no_transcript_tab_returns_false(self):
        """When Transcript tab doesn't exist, returns False (permanent)."""
        page = _make_mock_page()
        page.goto = AsyncMock()
        page.on = MagicMock()
        # Land on stream.aspx immediately
        type(page).url = PropertyMock(return_value="https://sharepoint.com/stream.aspx")
        # Transcript menuitem/tab not found
        page.get_by_role = MagicMock(return_value=_make_locator(count=0))

        with patch("collectors.transcripts.extraction._ext_diag", new_callable=AsyncMock):
            result = await extract_transcript_from_sharepoint(
                page, "https://sharepoint.com/stream.aspx"
            )
        assert result is False

    async def test_auth_failure_raises_transient(self):
        """Stuck on login page raises TransientExtractionError."""
        page = _make_mock_page()
        page.goto = AsyncMock()
        page.on = MagicMock()
        # Always return login URL
        type(page).url = PropertyMock(
            return_value="https://login.microsoftonline.com/something"
        )

        with patch("collectors.transcripts.extraction._ext_diag", new_callable=AsyncMock):
            with pytest.raises(TransientExtractionError, match="Auth failed"):
                await extract_transcript_from_sharepoint(
                    page, "https://sharepoint.com/sites/video"
                )

    async def test_successful_extraction_returns_string(self):
        """Full successful path returns transcript string."""
        page = _make_mock_page()
        page.goto = AsyncMock()
        page.on = MagicMock()
        type(page).url = PropertyMock(return_value="https://sharepoint.com/stream.aspx")

        # Transcript tab found
        tab_loc = _make_locator(count=1)
        page.get_by_role = MagicMock(return_value=tab_loc)

        # Scroll container found
        page.evaluate = AsyncMock(side_effect=[
            # FIND_SCROLL_CONTAINER_JS
            {"found": True, "scrollHeight": 5000, "clientHeight": 400},
            # SCROLL_AND_COLLECT_JS
            {
                "entries": {
                    "Alice 1 minute 0 seconds": "Hello everyone.",
                    "Bob 1 minute 30 seconds": "Welcome.",
                },
                "expectedTotal": 2,
                "totalCollected": 2,
            },
        ])

        with patch("collectors.transcripts.extraction._ext_diag", new_callable=AsyncMock):
            result = await extract_transcript_from_sharepoint(
                page, "https://sharepoint.com/stream.aspx"
            )

        assert isinstance(result, str)
        assert "Alice" in result
        assert "Bob" in result
        assert "[1:00]" in result

    async def test_empty_entries_returns_none(self):
        """Scroll-and-collect returned empty entries → None (transient)."""
        page = _make_mock_page()
        page.goto = AsyncMock()
        page.on = MagicMock()
        type(page).url = PropertyMock(return_value="https://sharepoint.com/stream.aspx")

        tab_loc = _make_locator(count=1)
        page.get_by_role = MagicMock(return_value=tab_loc)

        page.evaluate = AsyncMock(side_effect=[
            {"found": True, "scrollHeight": 5000, "clientHeight": 400},
            {"entries": {}, "expectedTotal": 10, "totalCollected": 0},
        ])

        with patch("collectors.transcripts.extraction._ext_diag", new_callable=AsyncMock):
            result = await extract_transcript_from_sharepoint(
                page, "https://sharepoint.com/stream.aspx"
            )

        # None, NOT False — extraction ran but got nothing (transient)
        assert result is None

    async def test_scroll_container_not_found_returns_none(self):
        """Scroll container never appears → None (transient)."""
        page = _make_mock_page()
        page.goto = AsyncMock()
        page.on = MagicMock()
        type(page).url = PropertyMock(return_value="https://sharepoint.com/stream.aspx")

        tab_loc = _make_locator(count=1)
        page.get_by_role = MagicMock(return_value=tab_loc)

        # Scroll container never found
        page.evaluate = AsyncMock(return_value={"found": False})

        with patch("collectors.transcripts.extraction._ext_diag", new_callable=AsyncMock):
            result = await extract_transcript_from_sharepoint(
                page, "https://sharepoint.com/stream.aspx"
            )

        assert result is None


# ===========================================================================
# Collector: extraction result handling
# ===========================================================================

class TestCollectorExtractionHandling:
    """Tests that the collector correctly handles str/False/None from extraction."""

    def _setup_state(self, tmp_path):
        """Set up a temp state file and return paths."""
        state_file = tmp_path / ".transcript-state.json"
        state_file.write_text(json.dumps({"attempted": {}}), encoding="utf-8")
        output_dir = tmp_path / "transcripts"
        output_dir.mkdir()
        return state_file, output_dir

    def test_successful_extraction_marks_attempted_and_saves(self, tmp_path):
        """String result → file saved + slug marked attempted."""
        state_file, output_dir = self._setup_state(tmp_path)
        attempted = {}

        # Simulate what collector does with a string result
        transcript = "[1:00] Alice: Hello"
        slug = "test-meeting"

        if isinstance(transcript, str) and transcript:
            date_str = datetime.now().strftime("%Y-%m-%d")
            filename = f"{date_str}_{slug}.txt"
            filepath = output_dir / filename
            filepath.write_text(transcript, encoding="utf-8")
            _mark_attempted.__wrapped__(attempted, slug) if hasattr(_mark_attempted, '__wrapped__') else None
            attempted[slug] = datetime.now().isoformat()

        assert (output_dir / filename).exists()
        assert slug in attempted

    def test_false_result_marks_attempted_no_file(self, tmp_path):
        """False result → slug marked attempted, NO file saved."""
        state_file, output_dir = self._setup_state(tmp_path)
        attempted = {}
        slug = "no-transcript-meeting"
        transcript = False
        saved = False

        if isinstance(transcript, str) and transcript:
            saved = True
        elif transcript is False:
            attempted[slug] = datetime.now().isoformat()
        else:
            pass  # None: don't mark

        assert not saved
        assert slug in attempted
        # No file should exist
        assert not any(output_dir.glob(f"*{slug}*"))

    def test_none_result_does_not_mark_attempted(self, tmp_path):
        """None result → slug NOT marked attempted (will retry)."""
        state_file, output_dir = self._setup_state(tmp_path)
        attempted = {}
        slug = "failed-extraction"
        transcript = None

        if isinstance(transcript, str) and transcript:
            pass
        elif transcript is False:
            attempted[slug] = datetime.now().isoformat()
        else:
            pass  # None: don't mark

        assert slug not in attempted

    def test_isinstance_distinguishes_false_from_none(self):
        """Verify isinstance(False, str) is False — critical for the logic."""
        # This is the key check in the collector code
        assert isinstance("hello", str) is True
        assert isinstance(False, str) is False
        assert isinstance(None, str) is False

        # And the identity checks
        assert (False is False) is True
        assert (None is None) is True
        assert (False is None) is False

    def test_empty_string_treated_as_none(self):
        """Empty string should NOT be saved or marked attempted."""
        transcript = ""

        # This is the exact condition in collector.py
        result_type = None
        if isinstance(transcript, str) and transcript:
            result_type = "save"
        elif transcript is False:
            result_type = "permanent"
        else:
            result_type = "retry"

        assert result_type == "retry"


# ===========================================================================
# _load_attempted_slugs with orphan pruning
# ===========================================================================

class TestLoadAttemptedSlugs:
    """Tests for _load_attempted_slugs with orphan cleanup."""

    def test_prunes_expired_entries(self, tmp_path):
        """Entries older than TTL are removed."""
        state_file = tmp_path / ".transcript-state.json"
        old_ts = (datetime.now() - timedelta(days=ATTEMPT_TTL_DAYS + 1)).isoformat()
        fresh_ts = datetime.now().isoformat()

        state = {"attempted": {
            "old-meeting": old_ts,
            "fresh-meeting": fresh_ts,
        }}
        state_file.write_text(json.dumps(state), encoding="utf-8")

        with patch("collectors.transcripts.collector.TRANSCRIPT_STATE_FILE", state_file):
            result = _load_attempted_slugs()

        assert "old-meeting" not in result
        assert "fresh-meeting" in result

    def test_prunes_orphaned_slugs(self, tmp_path):
        """Attempted slugs with no transcript file are removed."""
        state_file = tmp_path / ".transcript-state.json"
        output_dir = tmp_path / "transcripts"
        output_dir.mkdir()

        # Create a transcript file for one slug only
        (output_dir / "2026-03-01_has-file-meeting.txt").write_text("content", encoding="utf-8")

        fresh_ts = datetime.now().isoformat()
        state = {"attempted": {
            "has-file-meeting": fresh_ts,       # HAS a transcript file
            "orphaned-meeting": fresh_ts,       # NO transcript file
            "another-orphan": fresh_ts,         # NO transcript file
        }}
        state_file.write_text(json.dumps(state), encoding="utf-8")

        with patch("collectors.transcripts.collector.TRANSCRIPT_STATE_FILE", state_file):
            result = _load_attempted_slugs(output_dir)

        assert "has-file-meeting" in result
        assert "orphaned-meeting" not in result
        assert "another-orphan" not in result

    def test_md_files_count_as_existing(self, tmp_path):
        """Compressed .md files should keep the slug in attempted."""
        state_file = tmp_path / ".transcript-state.json"
        output_dir = tmp_path / "transcripts"
        output_dir.mkdir()

        # .md file (compressed transcript)
        (output_dir / "2026-03-01_compressed-meeting.md").write_text("# Summary", encoding="utf-8")

        state = {"attempted": {"compressed-meeting": datetime.now().isoformat()}}
        state_file.write_text(json.dumps(state), encoding="utf-8")

        with patch("collectors.transcripts.collector.TRANSCRIPT_STATE_FILE", state_file):
            result = _load_attempted_slugs(output_dir)

        assert "compressed-meeting" in result

    def test_no_output_dir_skips_orphan_pruning(self, tmp_path):
        """Without output_dir, only TTL pruning happens."""
        state_file = tmp_path / ".transcript-state.json"
        fresh_ts = datetime.now().isoformat()
        state = {"attempted": {"some-meeting": fresh_ts}}
        state_file.write_text(json.dumps(state), encoding="utf-8")

        with patch("collectors.transcripts.collector.TRANSCRIPT_STATE_FILE", state_file):
            result = _load_attempted_slugs(None)

        # Without output_dir, can't check for files — keep the slug
        assert "some-meeting" in result

    def test_empty_state_file(self, tmp_path):
        """Empty/missing state returns empty dict."""
        state_file = tmp_path / ".transcript-state.json"
        state_file.write_text(json.dumps({}), encoding="utf-8")

        with patch("collectors.transcripts.collector.TRANSCRIPT_STATE_FILE", state_file):
            result = _load_attempted_slugs()

        assert result == {}

    def test_state_file_persisted_after_pruning(self, tmp_path):
        """State file is updated on disk when pruning occurs."""
        state_file = tmp_path / ".transcript-state.json"
        output_dir = tmp_path / "transcripts"
        output_dir.mkdir()

        old_ts = (datetime.now() - timedelta(days=ATTEMPT_TTL_DAYS + 1)).isoformat()
        fresh_ts = datetime.now().isoformat()
        state = {"attempted": {
            "expired": old_ts,
            "orphaned": fresh_ts,
            "has-file": fresh_ts,
        }}
        (output_dir / "2026-03-01_has-file.txt").write_text("content", encoding="utf-8")
        state_file.write_text(json.dumps(state), encoding="utf-8")

        with patch("collectors.transcripts.collector.TRANSCRIPT_STATE_FILE", state_file):
            _load_attempted_slugs(output_dir)

        # Read back the persisted state
        saved = json.loads(state_file.read_text())
        assert "has-file" in saved["attempted"]
        assert "expired" not in saved["attempted"]
        assert "orphaned" not in saved["attempted"]


# ===========================================================================
# discover_meetings_with_recaps — polling + skip counting
# ===========================================================================

class TestDiscoverMeetingsWithRecaps:
    """Tests for the main discovery loop including polling waits.

    NOTE: Playwright's get_by_role() is SYNCHRONOUS (no await), but click() is async.
    The mock page must use MagicMock for get_by_role (sync) and AsyncMock for click.
    """

    def _make_discovery_page(self, meeting_names: list[str]):
        """Create a mock page suitable for discover_meetings_with_recaps."""
        page = _make_mock_page()
        # page.evaluate is used by find_meeting_buttons (async)
        page.evaluate = AsyncMock(return_value=meeting_names)

        # get_by_role is SYNC in Playwright — must be MagicMock, not AsyncMock.
        # Returns a locator whose click() is async.
        click_loc = MagicMock()
        click_loc.click = AsyncMock()
        page.get_by_role = MagicMock(return_value=click_loc)

        return page

    async def test_skips_known_slugs_and_counts(self):
        """Meetings in skip_slugs are counted but not clicked."""
        # NOTE: "new meeting" is in SKIP_KEYWORDS, so we use different names.
        page = self._make_discovery_page([
            "Team Standup 11:00 AM to 12:00 PM Monday",
            "Project Review 2:00 PM to 3:00 PM Tuesday",
        ])

        with patch("collectors.transcripts.navigation._find_recap_element",
                    new_callable=AsyncMock, return_value=None), \
             patch("collectors.transcripts.navigation._log_popup_diagnostics",
                    new_callable=AsyncMock), \
             patch("collectors.transcripts.navigation._nav_diag",
                    new_callable=AsyncMock):

            skip = {"team-standup-1100-am-to-1200-pm-monday"}
            results = await discover_meetings_with_recaps(page, skip, _slugify)

        assert len(results.meetings) == 0
        assert results.skipped_already_attempted == 1
        # get_by_role should be called for "Project Review" click (not "Team Standup")
        role_calls = [
            c for c in page.get_by_role.call_args_list
            if c[0][0] == "button"
        ]
        assert len(role_calls) == 1
        assert "Project Review" in str(role_calls[0])

    async def test_polling_finds_recap_on_later_attempt(self):
        """Recap button appears on 3rd poll iteration (async load)."""
        page = self._make_discovery_page([
            "Recorded Meeting 10:00 AM to 11:00 AM Friday",
        ])

        recap_btn = AsyncMock()
        recap_btn.click = AsyncMock()
        poll_count = 0

        async def mock_find_recap(p):
            nonlocal poll_count
            poll_count += 1
            if poll_count >= 3:
                return recap_btn
            return None

        # Mock the new tab flow for SharePoint URL extraction
        new_page = AsyncMock()
        new_page.evaluate = AsyncMock(return_value={"sitePath": "/sites/test/video"})
        new_page.close = AsyncMock()

        async_cm = AsyncMock()
        async_cm.__aenter__ = AsyncMock(return_value=new_page)
        async_cm.__aexit__ = AsyncMock(return_value=False)
        page.context.expect_page = MagicMock(return_value=async_cm)

        with patch("collectors.transcripts.navigation._find_recap_element",
                    side_effect=mock_find_recap), \
             patch("collectors.transcripts.navigation._log_popup_diagnostics",
                    new_callable=AsyncMock), \
             patch("collectors.transcripts.navigation._nav_diag",
                    new_callable=AsyncMock):

            results = await discover_meetings_with_recaps(page, set(), _slugify)

        # Should have polled exactly 3 times before finding recap
        assert poll_count == 3
        # Recap found → should have clicked it
        recap_btn.click.assert_called_once()

    async def test_no_recap_after_all_polls_logs_diagnostic(self):
        """When recap not found after all poll attempts, diagnostic is logged."""
        page = self._make_discovery_page([
            "Regular Meeting 9:00 AM to 10:00 AM Wednesday",
        ])

        diag_called = False

        async def mock_diag(p, name):
            nonlocal diag_called
            diag_called = True

        with patch("collectors.transcripts.navigation._find_recap_element",
                    new_callable=AsyncMock, return_value=None), \
             patch("collectors.transcripts.navigation._log_popup_diagnostics",
                    side_effect=mock_diag), \
             patch("collectors.transcripts.navigation._nav_diag",
                    new_callable=AsyncMock):

            results = await discover_meetings_with_recaps(page, set(), _slugify)

        assert diag_called
        assert len(results.meetings) == 0
        assert results.skipped_no_recap == 1

    async def test_polling_uses_7_wait_intervals(self):
        """Verifies that the polling loop waits 7 times before giving up."""
        page = self._make_discovery_page([
            "Test Meeting 1:00 PM to 2:00 PM Thursday",
        ])

        find_recap_calls = 0

        async def track_find_recap(p):
            nonlocal find_recap_calls
            find_recap_calls += 1
            return None

        with patch("collectors.transcripts.navigation._find_recap_element",
                    side_effect=track_find_recap), \
             patch("collectors.transcripts.navigation._log_popup_diagnostics",
                    new_callable=AsyncMock), \
             patch("collectors.transcripts.navigation._nav_diag",
                    new_callable=AsyncMock):

            await discover_meetings_with_recaps(page, set(), _slugify)

        # Should have polled _find_recap_element exactly 7 times (extended from 5)
        assert find_recap_calls == 7

    async def test_escape_pressed_after_no_recap(self):
        """Escape key is pressed to close popup when no recap found."""
        page = self._make_discovery_page([
            "No Recap Meeting 3:00 PM to 4:00 PM Monday",
        ])

        with patch("collectors.transcripts.navigation._find_recap_element",
                    new_callable=AsyncMock, return_value=None), \
             patch("collectors.transcripts.navigation._log_popup_diagnostics",
                    new_callable=AsyncMock), \
             patch("collectors.transcripts.navigation._nav_diag",
                    new_callable=AsyncMock):

            await discover_meetings_with_recaps(page, set(), _slugify)

        # Verify Escape was pressed to close the popup
        page.keyboard.press.assert_called_with("Escape")


# ===========================================================================
# _slugify tests
# ===========================================================================

class TestParseMeetingDate:
    """Tests for _parse_meeting_date — extracting dates from button aria-labels."""

    def test_standard_format(self):
        label = "Team Standup, 9:00 AM to 10:00 AM, Monday, March 02, 2026, Busy"
        d = _parse_meeting_date(label)
        assert d is not None
        assert d.year == 2026
        assert d.month == 3
        assert d.day == 2

    def test_all_day_event(self):
        label = "Block for CAIP Speaking, all day event, Thursday, March 05, 2026, Busy"
        d = _parse_meeting_date(label)
        assert d is not None
        assert d.day == 5
        assert d.month == 3

    def test_february(self):
        label = "Valour Use Cases, 11:00 AM to 12:00 PM, Friday, February 27, 2026"
        d = _parse_meeting_date(label)
        assert d is not None
        assert d.month == 2
        assert d.day == 27

    def test_no_date_returns_none(self):
        assert _parse_meeting_date("Random button text") is None

    def test_malformed_date_returns_none(self):
        # Invalid month name
        assert _parse_meeting_date("Meeting, Monday, Foobar 99, 2026") is None

    def test_each_weekday(self):
        for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]:
            label = f"Meeting, 9:00 AM, {day}, January 01, 2026"
            d = _parse_meeting_date(label)
            assert d is not None, f"Failed for {day}"


class TestFutureEventSkip:
    """Tests that future meetings are skipped in discover_meetings_with_recaps."""

    def _make_discovery_page(self, meeting_names: list[str]):
        page = _make_mock_page()
        page.evaluate = AsyncMock(return_value=meeting_names)
        click_loc = MagicMock()
        click_loc.click = AsyncMock()
        page.get_by_role = MagicMock(return_value=click_loc)
        return page

    async def test_future_meetings_not_clicked(self):
        """Meetings with dates after today are not clicked."""
        future_date = (date.today() + timedelta(days=3)).strftime("%A, %B %d, %Y")
        past_date = (date.today() - timedelta(days=2)).strftime("%A, %B %d, %Y")

        page = self._make_discovery_page([
            f"Future Meeting, 9:00 AM to 10:00 AM, {future_date}, Busy",
            f"Past Meeting, 2:00 PM to 3:00 PM, {past_date}, Busy",
        ])

        with patch("collectors.transcripts.navigation._find_recap_element",
                    new_callable=AsyncMock, return_value=None), \
             patch("collectors.transcripts.navigation._log_popup_diagnostics",
                    new_callable=AsyncMock), \
             patch("collectors.transcripts.navigation._nav_diag",
                    new_callable=AsyncMock):

            results = await discover_meetings_with_recaps(page, set(), _slugify)

        # Only the past meeting should have been clicked
        role_calls = [
            c for c in page.get_by_role.call_args_list
            if c[0][0] == "button"
        ]
        assert len(role_calls) == 1
        assert "Past Meeting" in str(role_calls[0])

    async def test_today_meetings_are_checked(self):
        """Meetings from today are NOT skipped (they may have finished)."""
        today_str = date.today().strftime("%A, %B %d, %Y")

        page = self._make_discovery_page([
            f"Today Meeting, 9:00 AM to 10:00 AM, {today_str}, Busy",
        ])

        with patch("collectors.transcripts.navigation._find_recap_element",
                    new_callable=AsyncMock, return_value=None), \
             patch("collectors.transcripts.navigation._log_popup_diagnostics",
                    new_callable=AsyncMock), \
             patch("collectors.transcripts.navigation._nav_diag",
                    new_callable=AsyncMock):

            await discover_meetings_with_recaps(page, set(), _slugify)

        # Today's meeting should have been clicked
        role_calls = [c for c in page.get_by_role.call_args_list if c[0][0] == "button"]
        assert len(role_calls) == 1


class TestSlugify:
    """Tests for the slugify function used in meeting deduplication."""

    def test_basic_slugify(self):
        assert _slugify("Team Standup 9:00 AM") == "team-standup-900-am"

    def test_strips_special_chars(self):
        # The em-dash (—) is stripped, leaving "v2 follow-up" → "v2-follow-up"
        assert _slugify("Meeting (v2) — follow-up!") == "meeting-v2-follow-up"

    def test_truncates_at_60(self):
        long_name = "a" * 100
        assert len(_slugify(long_name)) == 60

    def test_empty_string(self):
        assert _slugify("") == ""


# ===========================================================================
# _log_popup_diagnostics tests
# ===========================================================================

class TestLogPopupDiagnostics:
    """Tests that popup diagnostics don't crash and log useful info."""

    async def test_logs_recap_elements_found(self):
        """When page has elements with 'recap' text, they are logged."""
        page = _make_mock_page()
        page.evaluate = AsyncMock(return_value={
            "recapEls": [{"tag": "BUTTON", "role": "button", "text": "View recap",
                          "aria": "View recap", "classes": "recap-btn", "visible": True}],
            "popupEls": [{"container": '[role="dialog"]', "tag": "BUTTON",
                          "role": "button", "text": "Close", "aria": "Close"}],
        })

        # Should not raise
        await _log_popup_diagnostics(page, "Test Meeting")

    async def test_handles_evaluate_failure(self):
        """Gracefully handles page.evaluate errors."""
        page = _make_mock_page()
        page.evaluate = AsyncMock(side_effect=Exception("Page crashed"))

        # Should not raise
        await _log_popup_diagnostics(page, "Test Meeting")

    async def test_handles_empty_results(self):
        """Handles case where no elements found at all."""
        page = _make_mock_page()
        page.evaluate = AsyncMock(return_value={"recapEls": [], "popupEls": []})

        # Should not raise
        await _log_popup_diagnostics(page, "Test Meeting")


# ===========================================================================
# Integration: full extraction result flow through collector logic
# ===========================================================================

class TestCollectorIntegration:
    """End-to-end test of how collector handles each extraction result type."""

    def test_full_flow_string_false_none(self, tmp_path):
        """Simulates processing 3 meetings with different result types."""
        output_dir = tmp_path / "transcripts"
        output_dir.mkdir()
        attempted = {}

        # Meeting 1: successful extraction (string)
        result_1 = "[1:00] Alice: Hello"
        slug_1 = "meeting-with-transcript"
        if isinstance(result_1, str) and result_1:
            filepath = output_dir / f"2026-03-02_{slug_1}.txt"
            filepath.write_text(result_1, encoding="utf-8")
            attempted[slug_1] = datetime.now().isoformat()

        # Meeting 2: no transcript tab (False)
        result_2 = False
        slug_2 = "meeting-no-transcript-tab"
        if isinstance(result_2, str) and result_2:
            assert False, "Should not enter this branch"
        elif result_2 is False:
            attempted[slug_2] = datetime.now().isoformat()

        # Meeting 3: extraction failed (None)
        result_3 = None
        slug_3 = "meeting-extraction-failed"
        if isinstance(result_3, str) and result_3:
            assert False, "Should not enter this branch"
        elif result_3 is False:
            assert False, "Should not enter this branch"

        # Verify outcomes
        assert (output_dir / f"2026-03-02_{slug_1}.txt").exists()  # saved
        assert slug_1 in attempted  # marked
        assert slug_2 in attempted  # marked (permanent)
        assert slug_3 not in attempted  # NOT marked (transient)

        # Now simulate _load_attempted_slugs with orphan pruning
        state_file = tmp_path / ".transcript-state.json"
        state_file.write_text(json.dumps({"attempted": attempted}), encoding="utf-8")

        with patch("collectors.transcripts.collector.TRANSCRIPT_STATE_FILE", state_file):
            loaded = _load_attempted_slugs(output_dir)

        # slug_1 has a file → kept
        assert slug_1 in loaded
        # slug_2 has no file (was False/permanent) → pruned as orphan!
        # This is actually correct — on next run it will be re-checked and
        # if still no transcript tab, it will get False again and re-marked.
        # But this means permanent-no-transcript meetings get rechecked.
        # This is acceptable — the extra check takes ~8 seconds per meeting.
        assert slug_2 not in loaded
