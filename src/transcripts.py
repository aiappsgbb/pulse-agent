"""Transcript collection — deterministic Playwright script, no LLM for navigation.

Uses Playwright Python library directly to navigate Teams web UI
and extract transcript text via DOM scraping. The LLM is not involved
in navigation — this is a scripted click-through of the exact path
that works reliably.
"""

import asyncio
import re
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright, Page, Frame

from session import INPUT_DIR

# Browser profile for persistent auth
USER_DATA_DIR = "C:/Users/arzielinski/AppData/Local/ms-playwright/mcp-msedge-profile"

# JS snippet to read currently visible listitem text from the transcript frame.
COLLECT_VISIBLE_JS = """
() => {
    const items = [];
    document.querySelectorAll('[role="listitem"]').forEach(el => {
        const text = el.innerText.trim();
        if (text) items.push(text);
    });
    return items;
}
"""

# JS to find the actual scroll container — the ms-FocusZone ancestor with overflow:auto.
# Teams transcript uses Fluent UI's ms-List inside a FocusZone scroll wrapper.
# The list itself has scrollHeight == clientHeight (no overflow).
# The FocusZone wrapper is the real scrollable element.
FIND_SCROLL_CONTAINER_JS = """
() => {
    const list = document.querySelector('[role="list"]');
    if (!list) return null;

    // Walk up the DOM tree to find the ancestor with overflow-y: auto/scroll
    let el = list.parentElement;
    while (el && el !== document.body) {
        const style = getComputedStyle(el);
        if ((style.overflowY === 'auto' || style.overflowY === 'scroll')
            && el.scrollHeight > el.clientHeight + 50) {
            return {
                found: true,
                tag: el.tagName,
                className: el.className.substring(0, 100),
                scrollHeight: el.scrollHeight,
                clientHeight: el.clientHeight,
                scrollTop: el.scrollTop,
            };
        }
        el = el.parentElement;
    }
    return { found: false };
}
"""

# JS to scroll the FocusZone container to a specific position.
SCROLL_TO_JS = """
(pos) => {
    const list = document.querySelector('[role="list"]');
    if (!list) return false;
    let el = list.parentElement;
    while (el && el !== document.body) {
        const style = getComputedStyle(el);
        if ((style.overflowY === 'auto' || style.overflowY === 'scroll')
            && el.scrollHeight > el.clientHeight + 50) {
            el.scrollTop = pos;
            return { scrollTop: el.scrollTop, scrollHeight: el.scrollHeight };
        }
        el = el.parentElement;
    }
    return false;
}
"""

# JS to get total expected items from aria-setsize attribute.
GET_TOTAL_ITEMS_JS = """
() => {
    const item = document.querySelector('[role="listitem"][aria-setsize]');
    if (item) return parseInt(item.getAttribute('aria-setsize'), 10);
    return null;
}
"""


def _print(text: str):
    """Print with ASCII-safe encoding to avoid charmap errors on Windows."""
    print(text.encode("ascii", "replace").decode("ascii"))


def _slugify(text: str) -> str:
    """Convert meeting title to filesystem-safe slug."""
    text = text.lower().strip()
    text = re.sub(r'[^a-z0-9\s-]', '', text)
    text = re.sub(r'[\s]+', '-', text)
    return text[:60]


async def run_transcript_collection(client, config: dict):
    """Collect meeting transcripts from Teams web using Playwright directly.

    No LLM involved — deterministic navigation script.
    The `client` param is accepted for interface compatibility but not used.
    """
    _print("\n=== Transcript collection start ===")

    tc = config.get("transcripts", {})
    max_meetings = tc.get("max_per_run", 10)
    output_dir = Path(tc.get("output_dir", str(INPUT_DIR / "transcripts")))
    output_dir.mkdir(parents=True, exist_ok=True)
    user_data_dir = tc.get("playwright", {}).get("user_data_dir", USER_DATA_DIR)

    collected = 0
    skipped = 0
    errors = []

    async with async_playwright() as p:
        # Launch Edge with persistent auth profile (headless)
        context = await p.chromium.launch_persistent_context(
            user_data_dir,
            channel="msedge",
            headless=False,  # TEMP: headful for debugging
            viewport={"width": 1280, "height": 900},
        )
        # Use first restored page or create one (creating after closing all can fail)
        if context.pages:
            page = context.pages[0]
            for old_page in context.pages[1:]:
                await old_page.close()
        else:
            page = await context.new_page()

        try:
            # Step 1: Navigate to Teams — fresh page, no stale SPA state
            _print("  Opening Teams...")
            await page.goto("https://teams.microsoft.com", wait_until="domcontentloaded")
            await page.wait_for_timeout(8000)

            title = await page.title()
            _print(f"  Page loaded: {title}")

            # Step 2: Click Calendar in the left nav bar (works from any view)
            _print("  Clicking Calendar nav button...")
            try:
                cal_btn = page.get_by_role("button", name="Calendar")
                await cal_btn.click()
                await page.wait_for_timeout(5000)
            except Exception:
                # Fallback: keyboard shortcut
                _print("  Calendar button not found, trying Ctrl+Shift+3...")
                await page.keyboard.press("Control+Shift+3")
                await page.wait_for_timeout(5000)

            title = await page.title()
            _print(f"  After nav: {title}")

            if "Calendar" not in title:
                _print(f"  ERROR: Could not open Calendar view. Got: {title}")
                return

            # Step 3: Wait for the calendar iframe to fully load
            _print("  Waiting for calendar iframe to load...")
            iframe = page.frame_locator('iframe[name="embedded-page-container"]')
            try:
                # Wait for any button to appear inside the iframe — signals it's loaded
                await iframe.get_by_role("button").first.wait_for(state="visible", timeout=30000)
                _print("  Calendar iframe loaded.")
            except Exception:
                _print("  WARNING: Calendar iframe slow to load, waiting 10 more seconds...")
                await page.wait_for_timeout(10000)

            # Step 3: Go to previous week (completed meetings have transcripts)
            _print("  Navigating to previous week...")
            try:
                prev_btn = iframe.get_by_role("button", name=re.compile(r"Go to previous week"))
                await prev_btn.click()
                await page.wait_for_timeout(3000)
                _print("  Previous week loaded.")
            except Exception as e:
                _print(f"  WARNING: Could not find 'Go to previous week' button: {e}")

            # Step 4: Find meetings — wait for async meeting renders
            _print("  Scanning for meetings...")
            meeting_buttons = await _find_meeting_buttons(page, iframe)

            # If few meetings found, calendar may still be loading — retry
            if len(meeting_buttons) < 3:
                _print("  Few meetings found, waiting 5s for calendar to finish rendering...")
                await page.wait_for_timeout(5000)
                meeting_buttons = await _find_meeting_buttons(page, iframe)
            _print(f"  Found {len(meeting_buttons)} meeting buttons in calendar.")

            # Step 5: Process each meeting — try all, most won't have transcripts
            for meeting_name in meeting_buttons:
                if collected >= max_meetings:
                    break

                slug = _slugify(meeting_name)
                if not slug:
                    continue

                # Check if already collected
                existing = list(output_dir.glob(f"*_{slug}*"))
                if existing:
                    _print(f"  SKIP (already exists): {meeting_name[:50]}")
                    skipped += 1
                    continue

                _print(f"\n  Processing: {meeting_name[:60]}...")
                try:
                    transcript = await _extract_meeting_transcript(page, iframe, meeting_name)
                    if transcript:
                        date_str = datetime.now().strftime("%Y-%m-%d")
                        filename = f"{date_str}_{slug}.txt"
                        filepath = output_dir / filename
                        filepath.write_text(transcript, encoding="utf-8")
                        _print(f"  SAVED: {filename} ({len(transcript)} chars)")
                        collected += 1
                    else:
                        _print(f"  No transcript available for this meeting.")
                        skipped += 1
                except Exception as e:
                    err_msg = f"{meeting_name[:40]}: {e}"
                    _print(f"  ERROR: {err_msg}")
                    errors.append(err_msg)

                # Navigate back to calendar for next meeting
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(2000)

        finally:
            await context.close()

    # Summary
    _print(f"\n=== Transcript collection end ===")
    _print(f"  Collected: {collected}")
    _print(f"  Skipped: {skipped}")
    _print(f"  Errors: {len(errors)}")
    for err in errors:
        _print(f"    - {err}")


async def _get_iframe_text(page: Page) -> str:
    """Get text content from the embedded-page-container iframe."""
    try:
        frame = page.frame("embedded-page-container")
        if frame:
            return await frame.evaluate("() => document.body.innerText")
    except Exception:
        pass
    return ""


async def _find_meeting_buttons(page: Page, iframe) -> list[str]:
    """Find meeting button labels in the calendar iframe via a single JS call.

    Gets all button aria-labels at once instead of N individual async round-trips.
    """
    frame_obj = page.frame("embedded-page-container")
    if not frame_obj:
        _print("    ERROR: Could not get calendar frame for button scan")
        return []

    all_names = await frame_obj.evaluate("""
        () => {
            const els = document.querySelectorAll('button, [role="button"]');
            return Array.from(els)
                .map(btn => btn.getAttribute('aria-label') || '')
                .filter(name => name.length > 10);
        }
    """)

    _print(f"    Found {len(all_names)} buttons in iframe (via single JS call)")

    skip_keywords = ["my work plan", "go to today", "go to previous",
                     "go to next", "jump to a specific", "new meeting",
                     "view more apps", "change the view", "select to change",
                     "show navigation", "join with an id",
                     "meet now", "filter", "copilot chat",
                     "summary:", "holiday", "paid leave",
                     "canceled:", "declined:", "more options", "+1 more",
                     "date selector", "skip to main"]

    meetings = []
    for name in all_names:
        name_lower = name.lower()
        if any(kw in name_lower for kw in skip_keywords):
            continue
        has_time = bool(re.search(r'\d{1,2}:\d{2}', name))
        if not has_time and len(name) < 40:
            continue
        if "recap" in name_lower:
            meetings.insert(0, name)
        else:
            meetings.append(name)

    _print(f"    Matched {len(meetings)} meetings")
    for m in meetings[:5]:
        _print(f"      - {m[:80]}")

    return meetings


async def _extract_meeting_transcript(page: Page, iframe, meeting_name: str) -> str | None:
    """Click into a meeting, find transcript, extract text.

    Returns transcript text or None if not available.
    """
    # Click the meeting
    try:
        btn = iframe.get_by_role("button", name=meeting_name)
        await btn.click()
        await page.wait_for_timeout(2000)
    except Exception as e:
        _print(f"    Could not click meeting: {e}")
        return None

    # Look for "View recap" button — ONLY recap, not "View event"
    # "View event" is just event details, no transcript there
    try:
        recap_btn = iframe.get_by_role("button", name="View recap")
        if await recap_btn.count() == 0:
            _print("    No 'View recap' button — skipping (no recording).")
            return None
        await recap_btn.click()
        await page.wait_for_timeout(4000)
    except Exception as e:
        _print(f"    Could not click recap: {e}")
        return None

    # Find and click Transcript tab — may be hidden behind overflow menu
    transcript_clicked = False

    # Try direct tab first
    try:
        tab = page.get_by_role("tab", name="Transcript")
        if await tab.count() > 0 and await tab.is_visible():
            await tab.click()
            transcript_clicked = True
    except Exception:
        pass

    # Try overflow "show N more items" button
    if not transcript_clicked:
        try:
            overflow = page.get_by_role("button", name=re.compile(r"show \d+ more"))
            if await overflow.count() > 0:
                await overflow.click()
                await page.wait_for_timeout(1000)
                menuitem = page.get_by_role("menuitem", name="Transcript")
                if await menuitem.count() > 0:
                    await menuitem.click()
                    transcript_clicked = True
        except Exception:
            pass

    # Last resort: JS click on hidden tab
    if not transcript_clicked:
        try:
            clicked = await page.evaluate("""
                () => {
                    const tabs = document.querySelectorAll('[role="tab"]');
                    for (const tab of tabs) {
                        if (tab.textContent.includes('Transcript')) {
                            tab.click();
                            return true;
                        }
                    }
                    return false;
                }
            """)
            transcript_clicked = clicked
        except Exception:
            pass

    if not transcript_clicked:
        _print("    Transcript tab not found.")
        return None

    await page.wait_for_timeout(3000)

    # Find the transcript frame and extract text by scrolling the FocusZone
    transcript_frame = await _find_transcript_frame(page)
    if not transcript_frame:
        _print("    Transcript frame not found.")
        return None

    try:
        transcript = await _scroll_and_extract(transcript_frame)
        if not transcript:
            _print("    No transcript entries found.")
            return None
        return transcript
    except Exception as e:
        _print(f"    Extraction failed: {e}")
        return None


async def _scroll_and_extract(frame: Frame) -> str | None:
    """Extract all transcript entries by scrolling the FocusZone scroll container.

    Teams uses Fluent UI's ms-List inside a ms-FocusZone wrapper.
    The FocusZone ancestor (with overflow-y: auto) is the real scroll container.
    The list itself has scrollHeight == clientHeight (no overflow on the list).

    We scroll the FocusZone in steps, collecting rendered listitems at each position.
    The ms-List virtualizes rendering — only items near the viewport exist in the DOM.
    """
    # Step 1: Find the scroll container
    container_info = await frame.evaluate(FIND_SCROLL_CONTAINER_JS)
    if not container_info or not container_info.get("found"):
        _print("    ERROR: Could not find scroll container (FocusZone ancestor).")
        return None

    scroll_height = container_info["scrollHeight"]
    client_height = container_info["clientHeight"]
    _print(f"    Scroll container: {container_info['tag']}.{container_info['className'][:40]}")
    _print(f"    scrollHeight={scroll_height}, clientHeight={client_height}")

    # Step 2: Check expected total from aria-setsize
    expected_total = await frame.evaluate(GET_TOTAL_ITEMS_JS)
    _print(f"    Expected items (aria-setsize): {expected_total}")

    # Step 3: Scroll through the container in steps, collecting at each position
    entries: dict[str, str] = {}
    step = max(client_height - 50, 200)  # Overlap by 50px to avoid missing items
    stale_count = 0
    max_stale = 8
    position = 0

    # Collect initial visible entries
    new_items = await frame.evaluate(COLLECT_VISIBLE_JS)
    for text in new_items:
        entries[text[:120]] = text
    _print(f"    Initial entries: {len(entries)}")

    while stale_count < max_stale:
        prev_count = len(entries)
        position += step

        # Scroll to new position
        result = await frame.evaluate(SCROLL_TO_JS, position)
        if not result:
            break

        # Wait for ms-List to re-render new items at this scroll position
        await asyncio.sleep(0.3)

        # Collect visible items
        new_items = await frame.evaluate(COLLECT_VISIBLE_JS)
        for text in new_items:
            entries[text[:120]] = text

        if len(entries) == prev_count:
            stale_count += 1
        else:
            stale_count = 0

        # Progress logging
        actual_scroll = result.get("scrollTop", position) if isinstance(result, dict) else position
        current_sh = result.get("scrollHeight", scroll_height) if isinstance(result, dict) else scroll_height
        if len(entries) % 20 == 0 and len(entries) != prev_count:
            _print(f"    ... {len(entries)} entries, scrollTop={actual_scroll}/{current_sh}")

        # Safety: if we've scrolled well past the scrollHeight, stop
        if position > current_sh + step * 2:
            break

    _print(f"    Extraction complete: {len(entries)} entries collected "
           f"(expected {expected_total}), scroll pos={position}")

    if not entries:
        return None

    # Build transcript text
    lines = []
    for i, text in enumerate(entries.values(), 1):
        lines.append(f"{i}\n{text}\n")
    return "\n".join(lines)


async def _find_transcript_frame(page: Page) -> Frame | None:
    """Find the iframe containing transcript list items."""
    for frame in page.frames:
        try:
            count = await frame.locator('[role="listitem"]').count()
            if count > 5:
                return frame
        except Exception:
            continue
    return None
