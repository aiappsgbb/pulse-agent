"""Transcript extraction from SharePoint Stream pages — click Transcript tab, scroll+collect."""

import re

from playwright.async_api import Page

from core.logging import log
from collectors.transcripts.js_snippets import (
    FIND_SCROLL_CONTAINER_JS,
    SCROLL_AND_COLLECT_JS,
)


class TransientExtractionError(Exception):
    """Raised when extraction fails due to transient issues (auth, page load).

    These should NOT be marked as attempted — they'll be retried next run.
    """
    pass


async def _ext_diag(page, label: str):
    """Save a diagnostic screenshot (imported from collector at runtime)."""
    try:
        from collectors.transcripts.collector import _diag
        await _diag(page, label)
    except Exception:
        pass


async def _handle_account_picker(page):
    """Handle 'Pick an account' dialog on login.microsoftonline.com.

    Some SharePoint domains trigger an account picker instead of silent SSO.
    Auto-clicks the @microsoft.com account to complete authentication.
    """
    try:
        # Look for account picker buttons (visible on "Pick an account" page)
        ms_account = page.locator('[data-test-id*="@microsoft.com"]')
        if await ms_account.count() > 0:
            log.info("    Auto-selecting @microsoft.com account in account picker")
            await ms_account.first.click()
            await page.wait_for_timeout(3000)
    except Exception:
        pass


def parse_aria_label(label: str) -> tuple[str, int]:
    """Parse speaker name and time (in seconds) from a group aria-label.

    aria-label format: "Speaker Name X minutes Y seconds"
    Time is always at the end, parsed right-to-left: seconds, then minutes, then hours.
    Everything before the time block is the speaker name.

    Returns (speaker_name, time_in_seconds).
    If parsing fails, returns ("Unknown", 0).
    """
    label = label.strip()

    # Match "N seconds" at end (required — all transcript entries have seconds)
    m = re.search(r'(\d+)\s+seconds?\s*$', label)
    if not m:
        return ("Unknown", 0)

    seconds = int(m.group(1))
    remaining = label[:m.start()].strip()

    # Check for "N minutes" before seconds
    minutes = 0
    m2 = re.search(r'(\d+)\s+minutes?\s*$', remaining)
    if m2:
        minutes = int(m2.group(1))
        remaining = remaining[:m2.start()].strip()

    # Check for "N hours" before minutes
    hours = 0
    m3 = re.search(r'(\d+)\s+hours?\s*$', remaining)
    if m3:
        hours = int(m3.group(1))
        remaining = remaining[:m3.start()].strip()

    total_seconds = hours * 3600 + minutes * 60 + seconds
    speaker = remaining if remaining else "Unknown"

    return (speaker, total_seconds)


def format_timestamp(seconds: int) -> str:
    """Convert seconds to M:SS or H:MM:SS format."""
    if seconds >= 3600:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        return f"{h}:{m:02d}:{s:02d}"
    else:
        m = seconds // 60
        s = seconds % 60
        return f"{m}:{s:02d}"


async def extract_transcript_from_sharepoint(page: Page, sharepoint_url: str) -> str | None | bool:
    """Navigate to SharePoint Stream URL, click Transcript tab, extract text.

    The page should be a fresh tab — caller is responsible for creating/closing it.

    Returns:
        str: Extracted transcript text (success).
        False: No transcript tab exists (permanent — safe to mark attempted).
        None: Extraction failed for other reasons (transient — should retry).
    Raises:
        TransientExtractionError: Auth/page-load failures (caller should NOT mark attempted).
    """
    # Skip API URLs that trigger downloads instead of rendering pages.
    # This is permanent — the URL structure won't change on retry.
    if "/_api/" in sharepoint_url or "/media/transcripts/" in sharepoint_url:
        log.info(f"    Skipping API/transcript URL (not a viewable page): {sharepoint_url[:80]}")
        return False

    log.info(f"    Navigating to SharePoint Stream: {sharepoint_url[:100]}...")
    try:
        # Set up download handler to prevent crashes on download URLs
        page.on("download", lambda dl: dl.cancel())
        await page.goto(sharepoint_url, wait_until="domcontentloaded", timeout=30000)
        # Wait for redirects to complete — sharing links redirect to stream.aspx,
        # and SSO redirects through login.microsoftonline.com
        for wait_attempt in range(15):
            await page.wait_for_timeout(2000)
            actual_url = page.url
            # Success: landed on stream.aspx or similar viewable page
            if "stream.aspx" in actual_url:
                break
            # Still on login page — check for "Pick an account" dialog
            if "login.microsoftonline.com" in actual_url:
                await _handle_account_picker(page)
                continue
            # Landed on AccessDenied or other error page
            if "AccessDenied" in actual_url:
                log.info(f"    Access denied for this recording")
                return False
            # Sharing link hasn't redirected yet — keep waiting
            if ":v:" in actual_url:
                continue
            # On some other SharePoint page — might be OK
            break

        actual_url = page.url
        log.info(f"    Landed on: {actual_url[:100]}")
        if "login.microsoftonline.com" in actual_url:
            log.warning(f"    Still on login page after 30s — auth failed")
            await _ext_diag(page, "sharepoint-auth-failed")
            raise TransientExtractionError("Auth failed — still on login page")
        await _ext_diag(page, "sharepoint-loaded")
    except TransientExtractionError:
        raise  # re-raise transient errors for caller to handle
    except Exception as e:
        err_msg = str(e)
        if "Download" in err_msg:
            log.info(f"    URL triggers download, not a viewable page — skipping")
        else:
            log.warning(f"    Failed to load SharePoint page: {e}")
            raise TransientExtractionError(f"Page load failed: {e}")

    # Click "Transcript" tab/menuitem — retry a few times as page loads
    transcript_clicked = False
    for attempt in range(5):
        try:
            # Try menuitem first (SharePoint Stream sidebar uses menuitems)
            menuitem = page.get_by_role("menuitem", name="Transcript")
            if await menuitem.count() > 0:
                await menuitem.click()
                transcript_clicked = True
                break
        except Exception:
            pass

        try:
            # Fallback: try tab role
            tab = page.get_by_role("tab", name="Transcript")
            if await tab.count() > 0:
                await tab.click()
                transcript_clicked = True
                break
        except Exception:
            pass

        if attempt < 4:
            await page.wait_for_timeout(2000)

    if not transcript_clicked:
        # Dump page URL and available roles for debugging
        try:
            current_url = page.url
            log.warning(f"    Transcript tab not found on: {current_url[:120]}")
            roles = await page.evaluate("""
                () => {
                    const items = [];
                    document.querySelectorAll('[role="menuitem"], [role="tab"]').forEach(el => {
                        items.push({role: el.getAttribute('role'), name: el.textContent.trim().substring(0, 50)});
                    });
                    return items.slice(0, 15);
                }
            """)
            for r in roles:
                log.info(f"    [DIAG role] {r['role']}: {r['name']}")
        except Exception:
            pass
        await _ext_diag(page, "no-transcript-tab")
        # Return False (not None) — this is a permanent condition (recording
        # exists but transcription wasn't enabled). Safe to mark as attempted.
        return False

    await page.wait_for_timeout(3000)
    await _ext_diag(page, "transcript-tab-clicked")

    # Wait for transcript list to render (may take 10-20s to load)
    for attempt in range(12):
        try:
            container_info = await page.evaluate(FIND_SCROLL_CONTAINER_JS)
            if container_info and container_info.get("found"):
                log.info(f"    Scroll container found: scrollHeight={container_info['scrollHeight']}, clientHeight={container_info['clientHeight']}")
                break
        except Exception:
            pass
        await page.wait_for_timeout(2000)
    else:
        log.warning("    Scroll container not found after waiting")
        await _ext_diag(page, "no-scroll-container")
        return None

    # Run the scroll-and-collect JS
    try:
        result = await page.evaluate(SCROLL_AND_COLLECT_JS)
    except Exception as e:
        log.warning(f"    Scroll-and-collect failed: {e}")
        return None

    if not result or result.get("error"):
        log.warning(f"    Scroll-and-collect error: {result}")
        return None

    entries = result.get("entries", {})
    expected = result.get("expectedTotal")
    collected = result.get("totalCollected", 0)

    log.info(f"    Extracted {collected} entries (expected {expected})")

    if not entries:
        return None

    return clean_transcript(entries)


def clean_transcript(entries: dict[str, str]) -> str | None:
    """Convert raw {ariaLabel: text} entries into clean speaker-attributed transcript.

    Input: dict where keys are aria-labels like "Speaker Name 5 minutes 30 seconds"
    and values are the spoken text.

    Output format:
      [5:30] Speaker Name: Good morning. Nice to meet you.
      [5:33] Other Person: Oh, I'm industry advisor...
    """
    lines = []

    for aria_label, text in entries.items():
        speaker, time_seconds = parse_aria_label(aria_label)
        timestamp = format_timestamp(time_seconds)
        lines.append((time_seconds, f"[{timestamp}] {speaker}: {text}"))

    if not lines:
        return None

    # Sort by timestamp
    lines.sort(key=lambda x: x[0])

    return "\n".join(line for _, line in lines) + "\n"
