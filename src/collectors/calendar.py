"""Scan Outlook Calendar for upcoming events via Playwright.

Deterministic script — no LLM involved. Uses the shared browser.
Returns structured data that gets injected into the digest/monitor trigger prompt.
Provides calendar coverage when WorkIQ is unavailable.

Scans the work week view to show upcoming meetings (not just today).

DOM structure (verified Feb 2026):
- Events: div[aria-label] containing "event" or "meeting" (case-insensitive)
- aria-label format (comma-separated):
    "Title, StartTime to EndTime, DayOfWeek, MonthDay Year,
     [Microsoft Teams Meeting,] [By Organizer,] Status[, Recurring event]"
- Declined events: title starts with "Declined:"
- Status values: "Busy", "Tentative", "Free"
- "+N more events" button exists if day has overflow
"""

import re
from datetime import datetime

from core.logging import log, safe_encode


EXTRACT_CALENDAR_JS = """
() => {
    // Collect both events and meetings — deduplicate by aria-label
    const selectors = [
        'div[aria-label*="event" i]',
        'div[aria-label*="meeting" i]',
    ];
    const seen = new Set();
    const results = [];

    for (const sel of selectors) {
        for (const el of document.querySelectorAll(sel)) {
            const aria = el.getAttribute('aria-label') || '';
            // Skip the "New event" button and "+N more events" button
            if (aria === 'New event' || aria.match(/^\\+\\d+ more events?$/)) continue;
            if (seen.has(aria)) continue;
            seen.add(aria);

            results.push({
                ariaLabel: aria,
                text: (el.innerText || '').substring(0, 200),
            });
        }
    }
    return results;
}
"""


def _parse_calendar_aria(aria: str) -> dict | None:
    """Parse a calendar event's aria-label into structured fields.

    Format: "Title, StartTime to EndTime, DayOfWeek, MonthDay Year,
             [Microsoft Teams Meeting,] [By Organizer,] Status[, Recurring event]"

    Returns dict with: title, start_time, end_time, date, organizer, status,
                       is_teams, is_recurring, is_declined
    """
    if not aria or len(aria) < 10:
        return None

    parts = [p.strip() for p in aria.split(",")]
    if len(parts) < 4:
        return None

    result = {
        "title": "",
        "start_time": "",
        "end_time": "",
        "date": "",
        "organizer": "",
        "status": "",
        "is_teams": False,
        "is_recurring": False,
        "is_declined": False,
    }

    # Title is first part
    title = parts[0]
    result["is_declined"] = title.startswith("Declined:")
    result["title"] = title.replace("Declined: ", "").replace("Declined:", "").strip()

    # Time range: "StartTime to EndTime"
    time_match = re.search(r'(\d{1,2}:\d{2}\s*[AP]M)\s+to\s+(\d{1,2}:\d{2}\s*[AP]M)', aria)
    if time_match:
        result["start_time"] = time_match.group(1)
        result["end_time"] = time_match.group(2)

    # Date: look for "DayOfWeek, Month Day, Year" pattern
    date_match = re.search(
        r'(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s+'
        r'(\w+ \d{1,2}),?\s*(\d{4})?',
        aria
    )
    if date_match:
        day_of_week = date_match.group(1)
        month_day = date_match.group(2)
        year = date_match.group(3) or str(datetime.now().year)
        result["date"] = f"{day_of_week}, {month_day} {year}"

    # Check remaining parts for structured fields
    for part in parts:
        part_stripped = part.strip()
        if part_stripped == "Microsoft Teams Meeting":
            result["is_teams"] = True
        elif part_stripped.startswith("By "):
            result["organizer"] = part_stripped[3:]
        elif part_stripped in ("Busy", "Tentative", "Free"):
            result["status"] = part_stripped
        elif "recurring" in part_stripped.lower():
            result["is_recurring"] = True

    return result


async def scan_calendar(config: dict) -> list[dict] | None:
    """Scan Outlook Calendar for upcoming events using the shared browser.

    Returns a list of dicts:
    [{title, start_time, end_time, date, organizer, status, is_teams, is_recurring, is_declined}, ...]
    Returns None if the browser is unavailable (distinct from [] = scanned, nothing found).
    """
    from core.browser import ensure_browser

    browser_mgr = await ensure_browser()
    if not browser_mgr:
        log.warning("Calendar scan skipped — no shared browser available")
        return None

    page = None
    try:
        page = await browser_mgr.new_page()
        return await _do_scan(page)
    except Exception as e:
        log.error(f"Calendar scan failed: {e}")
        return None
    finally:
        if page:
            try:
                await page.close()
            except Exception:
                pass


async def _do_scan(page) -> list[dict]:
    """Navigate to Outlook Calendar work week view and extract events."""
    log.info("Scanning calendar for work week events...")

    await page.goto("https://outlook.office.com/calendar/view/workweek", wait_until="domcontentloaded", timeout=30000)
    try:
        await page.wait_for_load_state("networkidle", timeout=12000)
    except Exception:
        pass

    # Check for auth redirect — session may have expired
    url = page.url.lower()
    if "login" in url or "oauth" in url or "microsoftonline" in url:
        log.error("  Calendar session expired — redirected to login page")
        return None

    await page.wait_for_timeout(3000)

    # Click "+N more events" buttons to reveal hidden events
    try:
        more_btns = await page.query_selector_all('button[aria-label*="more event" i]')
        for btn in more_btns:
            await btn.click()
            await page.wait_for_timeout(500)
    except Exception:
        pass

    # Extract events
    raw_items = await page.evaluate(EXTRACT_CALENDAR_JS)

    if not raw_items:
        log.info("  No calendar events found for this week")
        return []

    events = []
    for raw in raw_items:
        parsed = _parse_calendar_aria(raw.get("ariaLabel", ""))
        if parsed:
            events.append(parsed)

    log.info(f"  Found {len(events)} calendar events this week")
    for e in events[:15]:
        status = " [DECLINED]" if e["is_declined"] else ""
        teams = " [Teams]" if e["is_teams"] else ""
        date_str = f" ({e['date']})" if e["date"] else ""
        log.info(f"    - {safe_encode(e['start_time'])} {safe_encode(e['title'][:50])}{teams}{status}{date_str}")

    return events


def format_calendar_for_prompt(events: list[dict] | None) -> str:
    """Format calendar events as text for injection into trigger prompt."""
    if events is None:
        return (
            "**SCAN UNAVAILABLE** — Browser was not running. Cannot determine "
            "calendar events. DO NOT assume no meetings today."
        )
    if not events:
        return "No calendar events found for the work week."

    # Filter out declined events for the summary
    active = [e for e in events if not e["is_declined"]]
    declined = [e for e in events if e["is_declined"]]

    lines = [f"## This Week's Calendar — {len(active)} events ({len(declined)} declined)\n"]

    # Group events by date for readability
    by_date: dict[str, list[dict]] = {}
    for e in active:
        day = e.get("date") or "Unknown day"
        by_date.setdefault(day, []).append(e)

    for day, day_events in by_date.items():
        lines.append(f"### {day}")
        for e in day_events:
            title = e["title"]
            time_range = f"{e['start_time']} - {e['end_time']}" if e["start_time"] else "All day"
            organizer = f" (by {e['organizer']})" if e["organizer"] else ""
            teams = " [Teams]" if e["is_teams"] else ""
            status = f" [{e['status']}]" if e["status"] and e["status"] != "Busy" else ""
            recurring = " [recurring]" if e["is_recurring"] else ""
            lines.append(f"- **{time_range}**: {title}{teams}{organizer}{status}{recurring}")

    if declined:
        lines.append(f"\nDeclined ({len(declined)}): " + ", ".join(
            f"{e['start_time']} {e['title'][:30]}" for e in declined
        ))

    return "\n".join(lines)
