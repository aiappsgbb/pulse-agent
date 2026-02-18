"""Calendar navigation, meeting discovery, and return-to-calendar logic."""

import re

from playwright.async_api import Page

from core.logging import safe_encode


def _print(text: str):
    """Print with ASCII-safe encoding to avoid charmap errors on Windows."""
    print(safe_encode(text))


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


async def return_to_calendar(page: Page, iframe, force: bool = False):
    """Return to calendar view after processing a meeting.

    Two cases:
    1. Simple popup (no recap) — Escape closes it, still on calendar
    2. Recap view (force=True) — navigated away, must go back via Calendar button
    """
    if not force:
        # Simple popup — just Escape to close it
        try:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(1000)
        except Exception:
            pass
        return

    # We opened a recap page — always navigate back to Calendar
    _print("  Returning to calendar from recap view...")
    try:
        cal_btn = page.get_by_role("button", name="Calendar")
        await cal_btn.click()
        await page.wait_for_timeout(5000)
    except Exception:
        await page.goto("https://teams.microsoft.com", wait_until="domcontentloaded")
        await page.wait_for_timeout(8000)
        try:
            cal_btn = page.get_by_role("button", name="Calendar")
            await cal_btn.click()
            await page.wait_for_timeout(5000)
        except Exception:
            raise RuntimeError("Cannot navigate back to Calendar")

    # Go to previous week (calendar defaults to current week)
    try:
        iframe_loc = page.frame_locator('iframe[name="embedded-page-container"]')
        await iframe_loc.get_by_role("button").first.wait_for(state="visible", timeout=15000)
        prev_btn = iframe_loc.get_by_role("button", name=re.compile(r"Go to previous week"))
        await prev_btn.click()
        await page.wait_for_timeout(5000)
        _print("  Calendar restored (previous week).")
    except Exception as e:
        _print(f"  WARNING: Could not navigate to previous week: {e}")


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

    _print(f"    Matched {len(meetings)} meetings")
    for m in meetings[:5]:
        _print(f"      - {m[:80]}")

    return meetings
