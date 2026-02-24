"""Calendar navigation, meeting discovery, and return-to-calendar logic."""

import re

from playwright.async_api import Page

from core.logging import log


# Keywords to skip when scanning calendar buttons
SKIP_KEYWORDS = [
    "my work plan", "go to today", "go to previous",
    "go to next", "jump to a specific", "new meeting",
    "view more apps", "change the view", "select to change",
    "show navigation", "join with an id",
    "meet now", "filter", "copilot chat",
    "summary:", "holiday", "paid leave",
    "canceled:", "more options", "+1 more",
    "date selector", "skip to main",
]


async def navigate_weeks_back(page: Page, iframe, weeks: int = 1):
    """Click 'Go to previous week' N times to navigate backward in the calendar."""
    for i in range(weeks):
        try:
            prev_btn = iframe.get_by_role("button", name=re.compile(r"Go to previous week"))
            await prev_btn.click()
            # Wait for calendar iframe to re-render after navigation
            await page.wait_for_timeout(2500)
        except Exception as e:
            log.warning(f"  Could not navigate to previous week (step {i + 1}/{weeks}): {e}")
            break


async def go_to_today(page: Page, iframe):
    """Click 'Go to today' to reset the calendar to the current week.

    After clicking Calendar in the left nav, Teams may return to the LAST
    VIEWED week instead of the current week. This resets to today first.
    """
    try:
        today_btn = iframe.get_by_role("button", name=re.compile(r"Go to today"))
        if await today_btn.count() > 0:
            await today_btn.click()
            await page.wait_for_timeout(2000)
            return True
    except Exception:
        pass
    return False


async def return_to_calendar(page: Page, iframe, force: bool = False, week_offset: int = 1):
    """Return to calendar view after processing a meeting.

    Two cases:
    1. Simple popup (no recap) — Escape closes it, still on calendar
    2. Recap view (force=True) — navigated away, must go back via Calendar button

    week_offset: how many weeks back to navigate (used for multi-week collection).
    """
    if not force:
        # Simple popup — just Escape to close it
        try:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(500)
        except Exception:
            pass
        return

    # We opened a recap page — always navigate back to Calendar
    log.info("  Returning to calendar from recap view...")
    try:
        cal_btn = page.get_by_role("button", name=re.compile(r"Calendar"))
        await cal_btn.click()
        await page.wait_for_timeout(3000)
    except Exception:
        await page.goto("https://teams.cloud.microsoft/", wait_until="domcontentloaded")
        await page.wait_for_timeout(5000)
        try:
            cal_btn = page.get_by_role("button", name=re.compile(r"Calendar"))
            await cal_btn.click()
            await page.wait_for_timeout(3000)
        except Exception:
            raise RuntimeError("Cannot navigate back to Calendar")

    # Reset to current week first, THEN navigate back.
    # Calendar button may return to the last-viewed week, not "today".
    # Without this reset, navigate_weeks_back overshoots.
    iframe_loc = page.frame_locator('iframe[name="embedded-page-container"]')
    try:
        await iframe_loc.get_by_role("button").first.wait_for(state="visible", timeout=10000)
    except Exception:
        await page.wait_for_timeout(3000)

    await go_to_today(page, iframe_loc)

    # Navigate back to the correct week offset
    if week_offset > 0:
        await navigate_weeks_back(page, iframe_loc, week_offset)
        # Wait for calendar to re-render after week navigation
        await page.wait_for_timeout(2000)
    log.info(f"  Calendar restored ({week_offset} week(s) back).")


async def get_iframe_text(page: Page) -> str:
    """Get text content from the embedded-page-container iframe."""
    try:
        frame = page.frame("embedded-page-container")
        if frame:
            return await frame.evaluate("() => document.body.innerText")
    except Exception:
        pass
    return ""


async def find_meeting_buttons(page: Page, iframe) -> list[str]:
    """Find meeting button labels in the calendar iframe via a single JS call.

    Gets all button aria-labels at once instead of N individual async round-trips.
    """
    frame_obj = page.frame("embedded-page-container")
    if not frame_obj:
        log.warning("    Could not get calendar frame for button scan")
        return []

    all_names = await frame_obj.evaluate("""
        () => {
            const els = document.querySelectorAll('button, [role="button"]');
            return Array.from(els)
                .map(btn => btn.getAttribute('aria-label') || '')
                .filter(name => name.length > 10);
        }
    """)

    meetings = []
    for name in all_names:
        name_lower = name.lower()
        if any(kw in name_lower for kw in SKIP_KEYWORDS):
            continue
        has_time = bool(re.search(r'\d{1,2}:\d{2}', name))
        if not has_time and len(name) < 40:
            continue
        if "recap" in name_lower:
            meetings.insert(0, name)
        else:
            meetings.append(name)

    log.info(f"    {len(all_names)} buttons in iframe, {len(meetings)} meetings matched")
    return meetings
