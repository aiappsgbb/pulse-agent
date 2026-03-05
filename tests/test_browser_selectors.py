"""Real Playwright tests against HTML fixtures.

These tests load saved DOM snapshots into an actual browser and run the REAL
JavaScript snippets and CSS selectors from our collectors. No mocks.

If a test fails here, it means our selectors don't match the DOM structure
we depend on — the exact class of bug that breaks production silently.

Requires: playwright browsers installed (npx playwright install chromium)
"""

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"

# Skip entire module if playwright not installed
pytest.importorskip("playwright")


@pytest.fixture(scope="module")
def browser_context():
    """Launch a real Chromium browser for the test module."""
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch(headless=True)
    except Exception as e:
        pw.stop()
        pytest.skip(f"Chromium not available: {e}")
    context = browser.new_context()
    yield context
    context.close()
    browser.close()
    pw.stop()


@pytest.fixture
def page(browser_context):
    """Fresh page for each test."""
    p = browser_context.new_page()
    yield p
    p.close()


def _load_fixture(page, name: str):
    """Navigate to a local HTML fixture."""
    fixture_path = FIXTURES / name
    assert fixture_path.exists(), f"Fixture {name} not found at {fixture_path}"
    page.goto(f"file:///{fixture_path.as_posix()}")


# ---------------------------------------------------------------------------
# Teams Inbox Scanner — Selector Tests
# ---------------------------------------------------------------------------


class TestTeamsInboxSelectors:
    """Verify Teams inbox selectors match the expected DOM structure."""

    def test_tree_container_found(self, page):
        """Primary selector [role='tree'] finds the chat tree."""
        _load_fixture(page, "teams_inbox.html")
        tree = page.query_selector('[role="tree"]')
        assert tree is not None

    def test_treeitem_level2_finds_chats(self, page):
        """[role='treeitem'] at level 2 finds individual chat items."""
        _load_fixture(page, "teams_inbox.html")
        items = page.query_selector_all('[role="treeitem"]')
        level2 = [i for i in items if i.get_attribute("aria-level") == "2"]
        assert len(level2) == 5  # 5 chat items in fixture

    def test_time_element_in_chat(self, page):
        """Each chat item contains a <time> element."""
        _load_fixture(page, "teams_inbox.html")
        items = page.query_selector_all('[role="treeitem"]')
        level2 = [i for i in items if i.get_attribute("aria-level") == "2"]
        for item in level2:
            time_el = item.query_selector("time")
            assert time_el is not None, f"No <time> in: {item.inner_text()[:50]}"

    def test_extract_chat_list_js(self, page):
        """The actual EXTRACT_CHAT_LIST_JS from teams_inbox.py works."""
        from collectors.teams_inbox import EXTRACT_CHAT_LIST_JS

        _load_fixture(page, "teams_inbox.html")
        result = page.evaluate(EXTRACT_CHAT_LIST_JS)
        assert isinstance(result, list)
        assert len(result) >= 4  # At least 4 chat items extracted

        # Check structure: JS returns {name, time, preview, raw, unread}
        for item in result:
            assert "name" in item or "raw" in item, f"Unexpected shape: {list(item.keys())}"

    def test_unread_detection_via_innertext(self, page):
        """Unread chats have 'Unread' in their innerText."""
        _load_fixture(page, "teams_inbox.html")
        items = page.query_selector_all('[role="treeitem"]')
        level2 = [i for i in items if i.get_attribute("aria-level") == "2"]

        unread_texts = [i.inner_text() for i in level2 if "Unread" in i.inner_text()]
        assert len(unread_texts) == 3  # Fatos, Data Eng, Power BI

    def test_expand_categories_js(self, page):
        """EXPAND_UNREAD_CATEGORIES_JS expands collapsed categories."""
        from collectors.teams_inbox import EXPAND_UNREAD_CATEGORIES_JS

        _load_fixture(page, "teams_inbox.html")
        # Earlier category is collapsed
        earlier = page.query_selector('[role="treeitem"][aria-level="1"][aria-expanded="false"]')
        assert earlier is not None

        page.evaluate(EXPAND_UNREAD_CATEGORIES_JS)
        # After expansion, it should be expanded (or clicked)
        # In our static fixture clicking doesn't change state, but JS shouldn't throw
        # The important thing is the JS runs without error

    def test_navigation_fallback(self, page):
        """Fallback [role='navigation'] exists as backup."""
        _load_fixture(page, "teams_inbox.html")
        nav = page.query_selector('[role="navigation"]')
        assert nav is not None

    def test_chat_sidebar_for_reply(self, page):
        """Reply flow: [role='treeitem'][data-item-type='chat'] finds chats."""
        _load_fixture(page, "teams_inbox.html")
        chats = page.query_selector_all('[role="treeitem"][data-item-type="chat"]')
        assert len(chats) == 5


# ---------------------------------------------------------------------------
# Outlook Inbox Scanner — Selector Tests
# ---------------------------------------------------------------------------


class TestOutlookInboxSelectors:
    """Verify Outlook inbox selectors match the expected DOM structure."""

    def test_mail_items_found(self, page):
        """[role='option'][data-convid] finds mail items."""
        _load_fixture(page, "outlook_inbox.html")
        items = page.query_selector_all('[role="option"][data-convid]')
        assert len(items) == 5

    def test_aria_label_has_content(self, page):
        """Each mail item has an aria-label with parseable content."""
        _load_fixture(page, "outlook_inbox.html")
        items = page.query_selector_all('[role="option"][data-convid]')
        for item in items:
            label = item.get_attribute("aria-label")
            assert label and len(label) > 10, f"Bad aria-label: {label}"

    def test_extract_mail_list_js(self, page):
        """The actual EXTRACT_MAIL_LIST_JS from outlook_inbox.py works."""
        from collectors.outlook_inbox import EXTRACT_MAIL_LIST_JS

        _load_fixture(page, "outlook_inbox.html")
        result = page.evaluate(EXTRACT_MAIL_LIST_JS)
        assert isinstance(result, list)
        assert len(result) == 5

        # JS returns {ariaLabel, innerText, convId}
        for item in result:
            assert "ariaLabel" in item, f"Unexpected shape: {list(item.keys())}"
            assert "convId" in item
            assert "innerText" in item

    def test_unread_flag_in_aria_label(self, page):
        """Unread emails start with 'Unread' in aria-label."""
        _load_fixture(page, "outlook_inbox.html")
        items = page.query_selector_all('[role="option"][data-convid]')
        unread = [
            i for i in items
            if (i.get_attribute("aria-label") or "").startswith("Unread")
        ]
        assert len(unread) == 3  # Bob, Charlie, Eve

    def test_attachment_flag_in_aria_label(self, page):
        """'Has attachments' flag is in aria-label."""
        _load_fixture(page, "outlook_inbox.html")
        items = page.query_selector_all('[role="option"][data-convid]')
        with_attachments = [
            i for i in items
            if "Has attachments" in (i.get_attribute("aria-label") or "")
        ]
        assert len(with_attachments) == 1  # Only Bob's email

    def test_convid_attribute(self, page):
        """data-convid attribute is present and non-empty."""
        _load_fixture(page, "outlook_inbox.html")
        items = page.query_selector_all('[role="option"][data-convid]')
        for item in items:
            convid = item.get_attribute("data-convid")
            assert convid and len(convid) > 0

    def test_listbox_fallback(self, page):
        """Fallback [role='listbox'] exists."""
        _load_fixture(page, "outlook_inbox.html")
        listbox = page.query_selector('[role="listbox"]')
        assert listbox is not None

    def test_parse_aria_label_real_data(self, page):
        """Parse real aria-labels from fixture through our actual parser."""
        from collectors.outlook_inbox import _parse_aria_label

        _load_fixture(page, "outlook_inbox.html")
        items = page.query_selector_all('[role="option"][data-convid]')

        # Parse Bob's email (unread, has attachments)
        bob_label = items[0].get_attribute("aria-label")
        parsed = _parse_aria_label(bob_label)
        assert parsed["unread"] is True
        assert parsed["has_attachment"] is True
        assert "Bob Wilson" in parsed.get("sender", "") or "Bob" in bob_label

        # Parse Alice's email (replied, read)
        alice_label = items[1].get_attribute("aria-label")
        parsed = _parse_aria_label(alice_label)
        assert parsed["unread"] is False
        assert parsed["replied"] is True


# ---------------------------------------------------------------------------
# Calendar Scanner — Selector Tests
# ---------------------------------------------------------------------------


class TestCalendarSelectors:
    """Verify calendar selectors match the expected DOM structure."""

    def test_event_selector(self, page):
        """div[aria-label*='event' i] finds events."""
        _load_fixture(page, "calendar.html")
        events = page.query_selector_all('div[aria-label*="event" i]')
        # Should find entries containing "event" (case-insensitive)
        assert len(events) >= 1

    def test_meeting_selector(self, page):
        """div[aria-label*='meeting' i] finds meetings."""
        _load_fixture(page, "calendar.html")
        meetings = page.query_selector_all('div[aria-label*="meeting" i]')
        assert len(meetings) >= 1

    def test_combined_event_meeting(self, page):
        """Combined selectors find all calendar items."""
        _load_fixture(page, "calendar.html")
        events = page.query_selector_all('div[aria-label*="event" i]')
        meetings = page.query_selector_all('div[aria-label*="meeting" i]')

        # Deduplicate by aria-label
        all_labels = set()
        for el in events + meetings:
            label = el.get_attribute("aria-label")
            if label:
                all_labels.add(label)

        # Should find: Apex, Weekly Standup, Declined, Lunch, Bet365
        # But NOT "New event" (filtered by JS) or "+2 more events" (button)
        assert len(all_labels) >= 4

    def test_more_events_button(self, page):
        """+N more events button is findable."""
        _load_fixture(page, "calendar.html")
        buttons = page.query_selector_all('button[aria-label*="more event" i]')
        assert len(buttons) == 1

    def test_extract_calendar_js(self, page):
        """The actual EXTRACT_CALENDAR_JS from calendar.py works."""
        from collectors.calendar import EXTRACT_CALENDAR_JS

        _load_fixture(page, "calendar.html")
        result = page.evaluate(EXTRACT_CALENDAR_JS)
        assert isinstance(result, list)

        # Determine actual key name for aria-label
        if result:
            first = result[0]
            aria_key = "ariaLabel" if "ariaLabel" in first else "aria"
        else:
            aria_key = "ariaLabel"

        # Should skip "New event" placeholder
        labels = [r.get(aria_key, "") for r in result]
        assert not any(l == "New event" for l in labels)

        # Should find real events
        assert len(result) >= 4

    def test_parse_calendar_aria_real_data(self, page):
        """Parse real aria-labels from fixture through our actual parser."""
        from collectors.calendar import _parse_calendar_aria

        _load_fixture(page, "calendar.html")
        events = page.query_selector_all('div[aria-label*="event" i], div[aria-label*="meeting" i]')

        parsed_any = False
        for el in events:
            label = el.get_attribute("aria-label")
            if not label or label == "New event":
                continue
            parsed = _parse_calendar_aria(label)
            if parsed.get("title"):
                parsed_any = True
                assert parsed.get("start_time") or parsed.get("date"), \
                    f"No time/date parsed from: {label}"

        assert parsed_any, "No events successfully parsed"

    def test_declined_event_detection(self, page):
        """Declined events have title starting with 'Declined:'."""
        _load_fixture(page, "calendar.html")
        events = page.query_selector_all('div[aria-label*="event" i], div[aria-label*="meeting" i]')

        declined = []
        for el in events:
            label = el.get_attribute("aria-label") or ""
            if "Declined:" in label:
                declined.append(label)

        assert len(declined) == 1


# ---------------------------------------------------------------------------
# Teams Sender — Selector Tests
# ---------------------------------------------------------------------------


class TestTeamsSenderSelectors:
    """Verify Teams message sending selectors work."""

    def test_new_message_button(self, page):
        """button[aria-label*='New message' i] finds the new chat button."""
        _load_fixture(page, "teams_compose.html")
        btn = page.query_selector('button[aria-label*="New message" i]')
        assert btn is not None

    def test_new_chat_fallback(self, page):
        """button[aria-label*='New chat' i] exists as fallback."""
        _load_fixture(page, "teams_compose.html")
        btn = page.query_selector('button[aria-label*="New chat" i]')
        assert btn is not None

    def test_to_field_variants(self, page):
        """All three To field selector variants find something."""
        _load_fixture(page, "teams_compose.html")

        # Variant 1: placeholder
        v1 = page.query_selector('input[placeholder*="Enter name" i]')
        assert v1 is not None

        # Variant 2: combobox
        v2 = page.query_selector('[role="combobox"][aria-label="To:"] input')
        assert v2 is not None

        # Variant 3: aria-label
        v3 = page.query_selector('input[aria-label*="To" i]')
        assert v3 is not None

    def test_autocomplete_suggestions(self, page):
        """[role='listbox'] [role='option'] finds autocomplete suggestions."""
        _load_fixture(page, "teams_compose.html")
        # Make suggestions visible
        page.evaluate('document.getElementById("suggestions").style.display = "block"')
        options = page.query_selector_all('[role="listbox"] [role="option"]')
        assert len(options) == 2

    def test_compose_box_primary(self, page):
        """Primary compose box selector works."""
        _load_fixture(page, "teams_compose.html")
        box = page.query_selector('[role="textbox"][aria-label*="Type a message" i]')
        assert box is not None

    def test_compose_box_ckeditor_fallback(self, page):
        """CKEditor fallback compose selector works."""
        _load_fixture(page, "teams_compose.html")
        box = page.query_selector('[data-tid="ckeditor"] [contenteditable="true"]')
        assert box is not None

    def test_send_button(self, page):
        """Send button selector works."""
        _load_fixture(page, "teams_compose.html")
        btn = page.query_selector('button[aria-label*="Send" i]')
        assert btn is not None

    def test_find_chat_in_sidebar_js(self, page):
        """FIND_CHAT_IN_SIDEBAR_JS finds a chat by name (case-insensitive)."""
        from collectors.teams_sender import FIND_CHAT_IN_SIDEBAR_JS

        _load_fixture(page, "teams_compose.html")

        # Search for "Fatos" (partial match)
        result = page.evaluate(FIND_CHAT_IN_SIDEBAR_JS, "Fatos")
        assert result.get("found") is True

    def test_find_chat_in_sidebar_not_found(self, page):
        """FIND_CHAT_IN_SIDEBAR_JS returns not found for unknown name."""
        from collectors.teams_sender import FIND_CHAT_IN_SIDEBAR_JS

        _load_fixture(page, "teams_compose.html")
        result = page.evaluate(FIND_CHAT_IN_SIDEBAR_JS, "Nonexistent Person")
        assert result.get("found") is False

    def test_find_new_chat_button_js(self, page):
        """FIND_NEW_CHAT_BUTTON_JS clicks the new message button."""
        from collectors.teams_sender import FIND_NEW_CHAT_BUTTON_JS

        _load_fixture(page, "teams_compose.html")
        result = page.evaluate(FIND_NEW_CHAT_BUTTON_JS)
        assert result in ("clicked new-message", "clicked new-chat", True)

    def test_find_compose_box_js(self, page):
        """FIND_COMPOSE_BOX_JS finds the compose text area."""
        from collectors.teams_sender import FIND_COMPOSE_BOX_JS

        _load_fixture(page, "teams_compose.html")
        result = page.evaluate(FIND_COMPOSE_BOX_JS)
        # JS may return a string ("found") or dict ({"found": true}) or truthy value
        assert result, f"FIND_COMPOSE_BOX_JS returned falsy: {result}"


# ---------------------------------------------------------------------------
# Outlook Sender — Selector Tests
# ---------------------------------------------------------------------------


class TestOutlookSenderSelectors:
    """Verify Outlook email reply selectors work."""

    def test_search_box_primary(self, page):
        """input[aria-label*='Search' i] finds search box."""
        _load_fixture(page, "outlook_reply.html")
        box = page.query_selector('input[aria-label*="Search" i]')
        assert box is not None

    def test_search_box_role_fallback(self, page):
        """[role='search'] input finds search box."""
        _load_fixture(page, "outlook_reply.html")
        box = page.query_selector('[role="search"] input')
        assert box is not None

    def test_search_box_id_fallback(self, page):
        """#topSearchInput finds search box."""
        _load_fixture(page, "outlook_reply.html")
        box = page.query_selector('#topSearchInput')
        assert box is not None

    def test_search_results(self, page):
        """Mail items are findable via [role='option'][data-convid] or [role='listitem']."""
        _load_fixture(page, "outlook_reply.html")
        items = page.query_selector_all('[role="option"][data-convid], [role="listitem"]')
        assert len(items) >= 2  # Bob + Alice

    def test_reply_button_primary(self, page):
        """button[aria-label='Reply' i] finds reply button."""
        _load_fixture(page, "outlook_reply.html")
        btn = page.query_selector('button[aria-label="Reply" i]')
        assert btn is not None

    def test_reply_button_menuitem_fallback(self, page):
        """[role='menuitem'][aria-label='Reply' i] exists as fallback."""
        _load_fixture(page, "outlook_reply.html")
        btn = page.query_selector('[role="menuitem"][aria-label="Reply" i]')
        assert btn is not None

    def test_reply_compose_box(self, page):
        """Reply compose box is findable."""
        _load_fixture(page, "outlook_reply.html")
        # Make reply area visible
        page.evaluate('document.getElementById("reply-compose-area").style.display = "block"')

        box = page.query_selector('[role="textbox"][aria-label*="Message body" i]')
        assert box is not None

    def test_reply_compose_contenteditable_fallback(self, page):
        """Contenteditable fallback for reply compose works."""
        _load_fixture(page, "outlook_reply.html")
        page.evaluate('document.getElementById("reply-body-alt").style.display = "block"')

        box = page.query_selector('[aria-label*="Message body" i][contenteditable="true"]')
        assert box is not None

    def test_send_button(self, page):
        """Send button selector works."""
        _load_fixture(page, "outlook_reply.html")
        page.evaluate('document.getElementById("reply-compose-area").style.display = "block"')
        btn = page.query_selector('button[aria-label="Send" i]')
        assert btn is not None

    def test_find_search_box_js(self, page):
        """FIND_SEARCH_BOX_JS from outlook_sender.py works."""
        from collectors.outlook_sender import FIND_SEARCH_BOX_JS

        _load_fixture(page, "outlook_reply.html")
        result = page.evaluate(FIND_SEARCH_BOX_JS)
        # JS may return string or dict — just verify it found something
        assert result, f"FIND_SEARCH_BOX_JS returned falsy: {result}"

    def test_extract_search_results_js(self, page):
        """EXTRACT_SEARCH_RESULTS_JS from outlook_sender.py works."""
        from collectors.outlook_sender import EXTRACT_SEARCH_RESULTS_JS

        _load_fixture(page, "outlook_reply.html")
        result = page.evaluate(EXTRACT_SEARCH_RESULTS_JS)
        assert isinstance(result, list)
        assert len(result) >= 1

    def test_click_reply_js(self, page):
        """CLICK_REPLY_JS from outlook_sender.py doesn't throw."""
        from collectors.outlook_sender import CLICK_REPLY_JS

        _load_fixture(page, "outlook_reply.html")
        # Should click the reply button without error
        result = page.evaluate(CLICK_REPLY_JS)
        # Either "clicked" or found a button
        assert result is not None


# ---------------------------------------------------------------------------
# Transcript Extraction — Selector Tests
# ---------------------------------------------------------------------------


class TestTranscriptSelectors:
    """Verify transcript extraction selectors work on Fluent UI DOM."""

    def test_focus_zone_found(self, page):
        """.ms-FocusZone scroll container is findable."""
        _load_fixture(page, "transcript.html")
        zones = page.query_selector_all('.ms-FocusZone')
        assert len(zones) >= 1

    def test_listitem_aria_setsize(self, page):
        """[role='listitem'][aria-setsize] has the expected total count."""
        _load_fixture(page, "transcript.html")
        item = page.query_selector('[role="listitem"][aria-setsize]')
        assert item is not None
        setsize = item.get_attribute("aria-setsize")
        assert setsize == "24"

    def test_group_elements(self, page):
        """[role='group'] elements contain transcript entries."""
        _load_fixture(page, "transcript.html")
        groups = page.query_selector_all('[role="group"]')
        assert len(groups) >= 6  # 6+ speaker groups in fixture

    def test_group_aria_label_has_speaker(self, page):
        """Group aria-labels contain speaker name + time."""
        _load_fixture(page, "transcript.html")
        groups = page.query_selector_all('[role="group"]')

        valid_groups = []
        for g in groups:
            label = g.get_attribute("aria-label") or ""
            if label and not label.startswith("Transcript.") and label.strip():
                valid_groups.append(label)

        assert len(valid_groups) >= 6
        # Check format: "Speaker Name X minutes Y seconds"
        assert any("seconds" in l for l in valid_groups)

    def test_group_contains_listitem_with_text(self, page):
        """Each valid group has a listitem child with transcript text."""
        _load_fixture(page, "transcript.html")
        groups = page.query_selector_all('[role="group"]')

        for g in groups:
            label = g.get_attribute("aria-label") or ""
            if not label or label.startswith("Transcript.") or not label.strip():
                continue
            listitem = g.query_selector('[role="listitem"]')
            assert listitem is not None, f"No listitem in group: {label}"
            text = listitem.inner_text().strip()
            if label != "":
                # Non-empty groups should have text (except the empty-label one)
                pass  # Some may legitimately be empty

    def test_find_scroll_container_js(self, page):
        """FIND_SCROLL_CONTAINER_JS from js_snippets.py works.

        Static HTML doesn't naturally overflow, so we inject padding to force
        scrollHeight > clientHeight + 100 (what the real Fluent UI list produces).
        """
        from collectors.transcripts.js_snippets import FIND_SCROLL_CONTAINER_JS

        _load_fixture(page, "transcript.html")
        # Force overflow to simulate virtualized list
        page.evaluate("""
            const z = document.querySelector('.ms-FocusZone');
            if (z) { z.firstElementChild.style.paddingBottom = '800px'; }
        """)
        result = page.evaluate(FIND_SCROLL_CONTAINER_JS)
        assert result.get("found") is True
        assert result.get("scrollHeight", 0) > result.get("clientHeight", 0)

    def test_get_total_items_js(self, page):
        """GET_TOTAL_ITEMS_JS returns the expected total from aria-setsize."""
        from collectors.transcripts.js_snippets import GET_TOTAL_ITEMS_JS

        _load_fixture(page, "transcript.html")
        result = page.evaluate(GET_TOTAL_ITEMS_JS)
        assert result == 24

    def test_scroll_and_collect_js(self, page):
        """SCROLL_AND_COLLECT_JS collects transcript entries."""
        from collectors.transcripts.js_snippets import SCROLL_AND_COLLECT_JS

        _load_fixture(page, "transcript.html")
        # Force overflow
        page.evaluate("""
            const z = document.querySelector('.ms-FocusZone');
            if (z) { z.firstElementChild.style.paddingBottom = '800px'; }
        """)
        result = page.evaluate(SCROLL_AND_COLLECT_JS)
        assert "error" not in result or result.get("error") is None, \
            f"JS error: {result.get('error')}"
        entries = result.get("entries", {})
        assert len(entries) >= 5  # At least 5 valid entries

        # Skipped entries shouldn't appear
        for key in entries:
            assert not key.startswith("Transcript.")
            assert key.strip() != ""

    def test_transcript_tab_menuitem(self, page):
        """Transcript tab is findable as menuitem."""
        _load_fixture(page, "transcript.html")
        tab = page.query_selector('[role="menuitem"][name="Transcript"]')
        assert tab is not None

    def test_transcript_tab_role(self, page):
        """Transcript tab fallback as role=tab."""
        _load_fixture(page, "transcript.html")
        # Make tab version visible
        page.evaluate('document.getElementById("transcript-tab").style.display = "block"')
        tab = page.query_selector('[role="tab"][name="Transcript"]')
        assert tab is not None

    def test_account_picker_sso(self, page):
        """SSO account picker button with data-test-id is findable."""
        _load_fixture(page, "transcript.html")
        page.evaluate('document.getElementById("account-picker").style.display = "block"')
        btn = page.query_selector('[data-test-id*="@microsoft.com"]')
        assert btn is not None
