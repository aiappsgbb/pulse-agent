"""Outlook Calendar navigation, meeting discovery, and SharePoint URL extraction."""

import inspect
import re
from datetime import datetime, date
from dataclasses import dataclass
from typing import Awaitable, Callable

from playwright.async_api import Page

from core.logging import log


# Regex to extract date from Outlook Calendar meeting button aria-labels.
# Format: "..., Monday, March 02, 2026, ..."
_MEETING_DATE_RE = re.compile(
    r'(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s+'
    r'(\w+)\s+(\d{1,2}),\s+(\d{4})'
)


def _parse_meeting_date(label: str) -> date | None:
    """Extract the meeting date from an Outlook Calendar button aria-label.

    Returns None if the date can't be parsed.
    """
    m = _MEETING_DATE_RE.search(label)
    if not m:
        return None
    try:
        return datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%B %d %Y").date()
    except ValueError:
        return None


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


def _is_usable_transcript_url(url: str) -> bool:
    """Is this a URL the extractor knows how to handle?

    Accepts both navigable Stream pages and SharePoint's direct transcript-
    content API endpoints (extractor fetches those directly).
    """
    if not url or not url.startswith("http"):
        return False
    # SharePoint's direct transcript-content endpoint — extractor fetches it.
    if "/media/transcripts/" in url and "/content" in url:
        return True
    # Any OTHER /_api/ URL is unusable — we can't render or fetch it meaningfully.
    if "/_api/" in url:
        return False
    return True


# Backwards-compat alias used by tests.
_is_viewable_stream_url = _is_usable_transcript_url


def _pick_stream_url(params: dict, meeting_name: str) -> str:
    """Pick the best URL for transcript extraction from launcher params.

    Prefers `objectUrl` (newer launchers) then `fileUrl`, then falls back to
    `sitePath`. `sitePath` is often the direct API transcript-content endpoint
    — that's fine, the extractor handles it via an authenticated fetch.
    """
    candidates = [
        ("objectUrl", params.get("objectUrl", "")),
        ("fileUrl", params.get("fileUrl", "")),
        ("sitePath", params.get("sitePath", "")),
    ]
    for name, value in candidates:
        if _is_usable_transcript_url(value):
            return value

    # Nothing usable — emit diagnostics and return empty so the caller marks
    # skipped-no-url (which does NOT poison state).
    non_empty = {k: v for k, v in params.items() if v and k not in ("href", "innerUrl")}
    log.warning(
        f"    No usable transcript URL in launcher for '{meeting_name[:50]}' — "
        f"params: {non_empty}"
    )
    raw_href = params.get("href") or ""
    if raw_href:
        log.info(f"    [DIAG] launcher href: {raw_href[:200]}")
    return ""


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


async def _find_recap_element(page: Page):
    """Search for recap/transcript button or link on the current page.

    Tries multiple patterns and element roles. Returns the first match or None.
    """
    recap_patterns = [
        "View recap", "View recap and transcript", "View transcript",
        "Open recap", "Recap", "recap",
    ]

    for pattern in recap_patterns:
        try:
            btn = page.get_by_role("button", name=re.compile(pattern, re.IGNORECASE))
            if await btn.count() > 0:
                return btn.first
        except Exception:
            pass
        try:
            link = page.get_by_role("link", name=re.compile(pattern, re.IGNORECASE))
            if await link.count() > 0:
                return link.first
        except Exception:
            pass

    # Generic text-based search as last resort — catches any element type
    try:
        recap_el = page.locator("button, a, [role='button'], [role='link']").filter(
            has_text=re.compile(r"recap|transcript", re.IGNORECASE)
        )
        if await recap_el.count() > 0:
            return recap_el.first
    except Exception:
        pass

    return None


async def _log_popup_diagnostics(page: Page, meeting_name: str):
    """Log diagnostic info about popup elements when recap button not found.

    Searches the FULL page DOM for any element containing 'recap' text,
    plus inspects elements inside common popup/dialog containers.
    """
    try:
        diag = await page.evaluate("""
            () => {
                // 1. Search FULL page for any element with "recap" text
                const allEls = document.querySelectorAll('*');
                const recapEls = [];
                for (const el of allEls) {
                    const text = (el.textContent || '').toLowerCase();
                    if (text.includes('recap') && el.children.length < 3) {
                        recapEls.push({
                            tag: el.tagName,
                            role: el.getAttribute('role') || '',
                            text: (el.textContent || '').trim().substring(0, 100),
                            aria: el.getAttribute('aria-label') || '',
                            classes: el.className ? el.className.substring(0, 80) : '',
                            visible: el.offsetParent !== null || el.style.display !== 'none',
                        });
                    }
                }

                // 2. Get popup/dialog container elements
                const containerSels = [
                    '[role="dialog"]', '[role="tooltip"]', '[role="complementary"]',
                    '[class*="popup"]', '[class*="Popup"]',
                    '[class*="callout"]', '[class*="Callout"]',
                    '[class*="flyout"]', '[class*="Flyout"]',
                    '[class*="panel"]', '[class*="Panel"]',
                    '#fluent-default-layer-host',
                ];
                const popupEls = [];
                for (const sel of containerSels) {
                    const containers = document.querySelectorAll(sel);
                    for (const c of containers) {
                        const children = c.querySelectorAll('button, a, [role="button"], [role="link"]');
                        for (const el of children) {
                            popupEls.push({
                                container: sel,
                                tag: el.tagName,
                                role: el.getAttribute('role') || '',
                                text: (el.textContent || '').trim().substring(0, 80),
                                aria: el.getAttribute('aria-label') || '',
                            });
                        }
                    }
                }

                return {recapEls: recapEls.slice(0, 10), popupEls: popupEls.slice(0, 15)};
            }
        """)

        recap_els = diag.get("recapEls", [])
        popup_els = diag.get("popupEls", [])

        if recap_els:
            log.info(f"    [DIAG] Found {len(recap_els)} elements with 'recap' text on page:")
            for el in recap_els[:5]:
                log.info(f"      <{el['tag']}> role={el['role']!r} visible={el['visible']} text={el['text'][:60]!r} classes={el['classes'][:40]!r}")
        else:
            log.info(f"    [DIAG] No elements with 'recap' text found anywhere on page")

        if popup_els:
            log.info(f"    [DIAG popup] {len(popup_els)} interactive elements in popup containers:")
            for el in popup_els[:8]:
                log.info(f"      [{el['container']}] <{el['tag']}> role={el['role']!r} text={el['text'][:50]!r} aria={el['aria'][:50]!r}")
        else:
            log.info(f"    [DIAG popup] No interactive elements in any popup containers")

    except Exception as e:
        log.info(f"    [DIAG] Could not inspect popup for '{meeting_name[:40]}': {e}")


@dataclass
class DiscoveryResult:
    """Result of meeting discovery including skip reason breakdown."""
    meetings: list[MeetingInfo]
    total_scanned: int = 0
    skipped_already_attempted: int = 0
    skipped_future: int = 0
    skipped_no_recap: int = 0
    skipped_no_url: int = 0
    # Slugs whose popups loaded but had no recap button. Caller should
    # memoize these so we don't re-poll the same dead meetings every run.
    no_recap_slugs: list[str] | None = None

    def __post_init__(self):
        if self.no_recap_slugs is None:
            self.no_recap_slugs = []


async def _maybe_await(value):
    """Helper: await if it's awaitable, else return as-is."""
    if inspect.isawaitable(value):
        return await value
    return value


async def discover_meetings_with_recaps(
    page: Page,
    skip_slugs: set[str],
    slugify_fn,
    on_meeting_found: Callable[["MeetingInfo"], Awaitable[None] | None] | None = None,
    on_no_recap: Callable[[str], Awaitable[None] | None] | None = None,
) -> DiscoveryResult:
    """Click each meeting event, check for 'View recap', extract SharePoint URLs.

    For each meeting with a recap:
    1. Click the meeting event button to open popup
    2. Look for "View recap" button
    3. Click "View recap" — opens Teams launcher in a new tab
    4. Extract sitePath from launcher URL params
    5. Close launcher tab, return to calendar tab
    6. Close popup (Escape)

    Returns DiscoveryResult with meetings and skip reason breakdown.

    skip_slugs: set of slugs to skip (already collected/attempted).
    slugify_fn: function to convert meeting title to slug.
    on_meeting_found: optional callback fired AS SOON AS a usable
        SharePoint URL is extracted, before moving to the next meeting.
        Used by the collector to extract+save the transcript inline so
        partial progress survives a timeout or crash.
    on_no_recap: optional callback fired AS SOON AS a meeting popup
        loads without a recap button. Used to persist the no-recap memo
        to disk inline (instead of only at function return), so a mid-
        run crash doesn't lose the work of clicking through dead meetings.
    """
    meeting_buttons = await find_meeting_buttons(page)
    log.info(f"  Scanning {len(meeting_buttons)} meetings for recaps...")
    await _nav_diag(page, "discover-start")

    results = []
    seen_slugs = set()
    no_recap_slugs: list[str] = []
    skipped_already_attempted = 0
    skipped_future = 0
    skipped_no_recap = 0
    skipped_no_url = 0
    today = date.today()

    async def _emit_found(meeting: MeetingInfo):
        results.append(meeting)
        log.info(f"    -> SharePoint URL: {meeting.sharepoint_url[:80]}...")
        if on_meeting_found is not None:
            try:
                await _maybe_await(on_meeting_found(meeting))
            except Exception as e:
                log.warning(f"    on_meeting_found callback raised: {e}")

    async def _emit_no_recap(slug: str):
        no_recap_slugs.append(slug)
        if on_no_recap is not None:
            try:
                await _maybe_await(on_no_recap(slug))
            except Exception as e:
                log.warning(f"    on_no_recap callback raised: {e}")

    for meeting_name in meeting_buttons:
        slug = slugify_fn(meeting_name)
        if not slug or slug in seen_slugs:
            continue
        if slug in skip_slugs:
            skipped_already_attempted += 1
            continue

        # Skip future meetings — they can't have recaps yet
        meeting_date = _parse_meeting_date(meeting_name)
        if meeting_date and meeting_date > today:
            skipped_future += 1
            continue

        seen_slugs.add(slug)

        # Click the meeting event to open popup
        try:
            btn = page.get_by_role("button", name=meeting_name)
            await btn.click()
        except Exception as e:
            log.warning(f"    Could not click meeting '{meeting_name[:50]}': {e}")
            continue

        # Poll for recap button with increasing wait — "View recap" loads
        # asynchronously as Outlook checks whether a recording exists.
        # Total budget: ~15 seconds (1+1+2+2+3+3+3) — extended from 8s
        # because Teams is often slow to render the recap button.
        recap_btn = None
        poll_waits = [1000, 1000, 2000, 2000, 3000, 3000, 3000]

        for poll_i, wait_ms in enumerate(poll_waits):
            await page.wait_for_timeout(wait_ms)
            recap_btn = await _find_recap_element(page)
            if recap_btn:
                break

        if not recap_btn:
            # Diagnostic: log what elements are in the popup
            # Log for first 5 unique meetings (enough to diagnose, not too spammy)
            if len(seen_slugs) <= 5:
                await _log_popup_diagnostics(page, meeting_name)

            # No recap — close popup and continue.
            # Memoize: this is a deterministic miss (past meeting, no recording).
            # Without this, every future run wastes ~15s re-polling the same dead meeting.
            skipped_no_recap += 1
            await _emit_no_recap(slug)
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
                    sharepoint_url = _pick_stream_url(params, meeting_name)
                except Exception:
                    pass
                # Navigate back to calendar
                await page.goto(OUTLOOK_CALENDAR_URL, wait_until="domcontentloaded")
                await page.wait_for_timeout(5000)
                if sharepoint_url:
                    await _emit_found(MeetingInfo(
                        title=meeting_name, sharepoint_url=sharepoint_url, slug=slug,
                    ))
                continue

        # Extract SharePoint URL from launcher page
        if launcher_page:
            try:
                from collectors.transcripts.js_snippets import EXTRACT_LAUNCHER_PARAMS_JS
                params = await launcher_page.evaluate(EXTRACT_LAUNCHER_PARAMS_JS)
                sharepoint_url = _pick_stream_url(params, meeting_name)
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
            await _emit_found(MeetingInfo(
                title=meeting_name,
                sharepoint_url=sharepoint_url,
                slug=slug,
            ))
        else:
            skipped_no_url += 1
            log.warning(f"    No SharePoint URL found for: {meeting_name[:50]}")

    log.info(
        f"  Discovery complete: {len(results)} recaps found, {len(seen_slugs)} checked, "
        f"{skipped_already_attempted} skipped (attempted), {skipped_future} skipped (future), "
        f"{skipped_no_recap} skipped (no recap), {skipped_no_url} skipped (no URL)"
    )
    await _nav_diag(page, "discover-end")
    return DiscoveryResult(
        meetings=results,
        total_scanned=len(meeting_buttons),
        skipped_already_attempted=skipped_already_attempted,
        skipped_future=skipped_future,
        skipped_no_recap=skipped_no_recap,
        skipped_no_url=skipped_no_url,
        no_recap_slugs=no_recap_slugs,
    )
