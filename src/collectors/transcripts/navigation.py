"""Outlook Calendar navigation, meeting discovery, and SharePoint URL extraction."""

import re
from dataclasses import dataclass

from playwright.async_api import Page

from core.logging import log


async def _nav_diag(page, label: str):
    """Save a diagnostic screenshot (imported from collector at runtime to avoid circular)."""
    try:
        from collectors.transcripts.collector import _diag
        await _diag(page, label)
    except Exception:
        pass


@dataclass
class MeetingInfo:
    """Info about a meeting with a transcript available."""
    title: str
    sharepoint_url: str
    slug: str


# Keywords to skip when scanning calendar event buttons
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

OUTLOOK_CALENDAR_URL = "https://outlook.cloud.microsoft/calendar/view/week"


async def _dismiss_overlays(page: Page):
    """Dismiss any promotional overlays/popups that block calendar interaction.

    Outlook Calendar sometimes shows "Create bookable time" or other overlays
    in fluent-default-layer-host that intercept pointer events.
    """
    # Try Escape first
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(500)

    # Try to remove blocking overlay elements via JS
    try:
        await page.evaluate("""
            () => {
                // Remove fluent layer-host overlays that block clicks
                const host = document.getElementById('fluent-default-layer-host');
                if (host && host.children.length > 0) {
                    host.innerHTML = '';
                    return 'cleared';
                }
                return 'none';
            }
        """)
    except Exception:
        pass

    # Also try clicking any "dismiss" or "close" buttons in overlays
    try:
        dismiss = page.get_by_role("button", name=re.compile(r"[Cc]lose|[Dd]ismiss|[Nn]ot now|[Gg]ot it"))
        if await dismiss.count() > 0:
            await dismiss.first.click()
            await page.wait_for_timeout(500)
    except Exception:
        pass


async def navigate_to_outlook_calendar(page: Page):
    """Navigate to Outlook Calendar week view and wait for events to load."""
    log.info("  Navigating to Outlook Calendar...")
    await page.goto(OUTLOOK_CALENDAR_URL, wait_until="domcontentloaded")
    await page.wait_for_timeout(5000)

    # Wait for calendar events to render — look for buttons with time patterns
    for attempt in range(10):
        try:
            count = await page.evaluate("""
                () => {
                    const els = document.querySelectorAll('button, [role="button"]');
                    let count = 0;
                    const timeRe = /\\d{1,2}:\\d{2}/;
                    for (const btn of els) {
                        const label = btn.getAttribute('aria-label') || '';
                        if (timeRe.test(label)) count++;
                    }
                    return count;
                }
            """)
            if count > 0:
                log.info(f"    Outlook Calendar loaded ({count} event buttons)")
                # Dismiss any promotional overlays before interacting
                await _dismiss_overlays(page)
                await _nav_diag(page, "outlook-calendar-loaded")
                return
        except Exception:
            pass
        await page.wait_for_timeout(2000)

    log.warning("    Outlook Calendar: no event buttons found after waiting")
    await _nav_diag(page, "outlook-calendar-no-events")


async def navigate_weeks_back(page: Page, weeks: int = 1):
    """Click 'Go to previous week' N times in Outlook Calendar."""
    for i in range(weeks):
        try:
            prev_btn = page.get_by_role("button", name=re.compile(r"Go to previous week"))
            try:
                await prev_btn.click(timeout=5000)
            except Exception:
                # Overlay may be blocking — dismiss and force click
                await _dismiss_overlays(page)
                await prev_btn.click(force=True, timeout=5000)
            await page.wait_for_timeout(3000)
            await _nav_diag(page, f"nav-back-step{i+1}of{weeks}")
        except Exception as e:
            log.warning(f"  Could not navigate to previous week (step {i + 1}/{weeks}): {e}")
            break


async def find_meeting_buttons(page: Page) -> list[str]:
    """Find meeting button labels in the Outlook Calendar via a single JS call."""
    all_names = await page.evaluate("""
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
        meetings.append(name)

    log.info(f"    {len(all_names)} buttons on page, {len(meetings)} meetings matched")
    if len(meetings) == 0 and len(all_names) > 0:
        sample = all_names[:10]
        for i, name in enumerate(sample):
            log.info(f"    [DIAG btn {i}] {name[:100]}")
    return meetings


async def discover_meetings_with_recaps(
    page: Page,
    skip_slugs: set[str],
    slugify_fn,
) -> list[MeetingInfo]:
    """Click each meeting event, check for 'View recap', extract SharePoint URLs.

    For each meeting with a recap:
    1. Click the meeting event button to open popup
    2. Look for "View recap" button
    3. Click "View recap" — opens Teams launcher in a new tab
    4. Extract sitePath from launcher URL params
    5. Close launcher tab, return to calendar tab
    6. Close popup (Escape)

    Returns list of MeetingInfo with SharePoint URLs.

    skip_slugs: set of slugs to skip (already collected/attempted).
    slugify_fn: function to convert meeting title to slug.
    """
    meeting_buttons = await find_meeting_buttons(page)
    log.info(f"  Scanning {len(meeting_buttons)} meetings for recaps...")
    await _nav_diag(page, "discover-start")

    results = []
    seen_slugs = set()

    for meeting_name in meeting_buttons:
        slug = slugify_fn(meeting_name)
        if not slug or slug in seen_slugs or slug in skip_slugs:
            continue
        seen_slugs.add(slug)

        # Click the meeting event to open popup
        try:
            btn = page.get_by_role("button", name=meeting_name)
            await btn.click()
            await page.wait_for_timeout(2500)  # Allow popup to fully render
        except Exception as e:
            log.warning(f"    Could not click meeting '{meeting_name[:50]}': {e}")
            continue

        # Look for recap-related elements in the popup — try buttons AND links
        # with multiple text patterns (Outlook UI varies across tenants/versions)
        recap_btn = None
        recap_patterns = [
            "View recap", "View recap and transcript", "View transcript",
            "Open recap", "Recap", "recap",
        ]

        for pattern in recap_patterns:
            try:
                btn = page.get_by_role("button", name=re.compile(pattern, re.IGNORECASE))
                if await btn.count() > 0:
                    recap_btn = btn.first
                    break
            except Exception:
                pass
            try:
                link = page.get_by_role("link", name=re.compile(pattern, re.IGNORECASE))
                if await link.count() > 0:
                    recap_btn = link.first
                    break
            except Exception:
                pass

        # Also try a generic text-based search as last resort
        if not recap_btn:
            try:
                recap_btn_by_text = page.locator("button, a, [role='button'], [role='link']").filter(
                    has_text=re.compile(r"recap|transcript", re.IGNORECASE)
                )
                if await recap_btn_by_text.count() > 0:
                    recap_btn = recap_btn_by_text.first
            except Exception:
                pass

        if not recap_btn:
            # Diagnostic: log what interactive elements ARE in the popup
            # (only for the first few meetings to avoid log spam)
            if len(seen_slugs) <= 3:
                try:
                    popup_els = await page.evaluate("""
                        () => {
                            const els = document.querySelectorAll(
                                '[role="dialog"] button, [role="dialog"] a, ' +
                                '[class*="popup"] button, [class*="popup"] a, ' +
                                '[class*="Popup"] button, [class*="Popup"] a, ' +
                                '[class*="callout"] button, [class*="callout"] a, ' +
                                '[class*="Callout"] button, [class*="Callout"] a'
                            );
                            return Array.from(els).slice(0, 20).map(el => ({
                                tag: el.tagName,
                                role: el.getAttribute('role') || '',
                                text: (el.textContent || '').trim().substring(0, 80),
                                aria: el.getAttribute('aria-label') || '',
                                href: el.getAttribute('href') || '',
                            }));
                        }
                    """)
                    if popup_els:
                        log.info(f"    [DIAG popup] {len(popup_els)} elements in popup:")
                        for el in popup_els[:10]:
                            log.info(f"      {el.get('tag')} role={el.get('role')!r} text={el.get('text')!r} aria={el.get('aria')!r} href={el.get('href','')[:60]!r}")
                    else:
                        log.info(f"    [DIAG popup] No interactive elements found in popup containers")
                except Exception as e:
                    log.info(f"    [DIAG popup] Could not inspect popup: {e}")

            # No recap — close popup and continue
            try:
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(500)
            except Exception:
                pass
            continue

        # Click recap element — should open Teams launcher in a new tab
        log.info(f"    Found recap: {meeting_name[:60]}")
        launcher_page = None
        sharepoint_url = ""

        try:
            # Listen for new page (popup/tab) before clicking
            async with page.context.expect_page(timeout=10000) as new_page_info:
                await recap_btn.click()
            launcher_page = await new_page_info.value
            # Don't wait for full load — launcher may redirect forever.
            # Just wait a few seconds for the URL to settle.
            await launcher_page.wait_for_timeout(3000)
        except Exception as e:
            log.warning(f"    View recap did not open new tab: {e}")
            # Maybe it navigated in the same tab — check URL
            current_url = page.url
            if "launcher" in current_url or "teams.microsoft.com" in current_url:
                log.info(f"    Recap opened in same tab: {current_url[:80]}")
                # Extract from same page, then navigate back
                try:
                    from collectors.transcripts.js_snippets import EXTRACT_LAUNCHER_PARAMS_JS
                    params = await page.evaluate(EXTRACT_LAUNCHER_PARAMS_JS)
                    sharepoint_url = params.get("sitePath", "")
                except Exception:
                    pass
                # Navigate back to calendar
                await page.goto(OUTLOOK_CALENDAR_URL, wait_until="domcontentloaded")
                await page.wait_for_timeout(5000)
                if sharepoint_url:
                    results.append(MeetingInfo(
                        title=meeting_name, sharepoint_url=sharepoint_url, slug=slug,
                    ))
                    log.info(f"    -> SharePoint URL: {sharepoint_url[:80]}...")
                continue

        # Extract SharePoint URL from launcher page
        if launcher_page:
            try:
                from collectors.transcripts.js_snippets import EXTRACT_LAUNCHER_PARAMS_JS
                params = await launcher_page.evaluate(EXTRACT_LAUNCHER_PARAMS_JS)
                sharepoint_url = params.get("sitePath", "")
            except Exception as e:
                log.warning(f"    Could not extract launcher params: {e}")

            # Close launcher tab
            try:
                await launcher_page.close()
            except Exception:
                pass

        # Close popup on calendar page
        try:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(500)
        except Exception:
            pass

        if sharepoint_url:
            results.append(MeetingInfo(
                title=meeting_name,
                sharepoint_url=sharepoint_url,
                slug=slug,
            ))
            log.info(f"    → SharePoint URL: {sharepoint_url[:80]}...")
        else:
            log.warning(f"    No SharePoint URL found for: {meeting_name[:50]}")

    log.info(f"  Discovery complete: {len(results)} meetings with recaps")
    await _nav_diag(page, "discover-end")
    return results
