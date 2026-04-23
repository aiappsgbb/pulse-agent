"""Transcript extraction from SharePoint Stream pages — click Transcript tab, scroll+collect."""

import json as _json
import re

from playwright.async_api import Page

from core.logging import log
from collectors.transcripts.js_snippets import (
    FIND_SCROLL_CONTAINER_JS,
    SCROLL_AND_COLLECT_JS,
)


# URL pattern for SharePoint's direct transcript content endpoint:
#   .../_api/v2.1/drives/{driveId}/items/{itemId}/versions/current/media/transcripts/{uuid}/content
# The recap launcher routes to this when the Stream viewer isn't applicable.
# We can fetch it directly (auth cookies travel on the browser context's request
# client) and parse the WebVTT body — no DOM scraping needed.
_API_TRANSCRIPT_RE = re.compile(r"/_api/.+/media/transcripts/[^/]+/content(?:$|\?)")


def is_api_transcript_url(url: str) -> bool:
    """True if URL is a SharePoint API transcript-content endpoint."""
    return bool(url) and bool(_API_TRANSCRIPT_RE.search(url))


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
    # SharePoint's recap launcher often hands us a direct transcript-content
    # API URL rather than a viewable Stream page. Fetch + parse the VTT body
    # directly — no DOM scraping required.
    if is_api_transcript_url(sharepoint_url):
        return await _fetch_api_transcript(page, sharepoint_url)

    # Any OTHER /_api/ URL (non-transcript) is still useless to navigate to.
    if "/_api/" in sharepoint_url or "/media/transcripts/" in sharepoint_url:
        log.warning(f"    Skipping non-transcript API URL: {sharepoint_url[:80]}")
        return None

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


def parse_vtt(body: str) -> str | None:
    """Parse WebVTT body into the standard speaker-attributed transcript format.

    Accepts either Teams' `<v Speaker>text</v>` voice-tag style or the plain
    `Speaker: text` style, one cue at a time. Output matches clean_transcript:
        [M:SS] Speaker: text
    Returns None if no cues parsed.
    """
    if not body or "WEBVTT" not in body.split("\n", 1)[0].upper():
        # Allow VTT without header in case the server strips it — still try.
        if not body or "-->" not in body:
            return None

    # Split into cue blocks. A cue is a (optional id) + timestamp line + text line(s),
    # separated by blank lines.
    blocks = re.split(r"\n\s*\n", body.strip())
    voice_tag_re = re.compile(r"<v\s+([^>]+?)>(.*?)(?:</v>|$)", re.DOTALL)
    ts_re = re.compile(r"(\d{2}):(\d{2}):(\d{2})[.,]\d+\s+-->")

    out = []
    for block in blocks:
        if "-->" not in block:
            continue
        lines = [ln for ln in block.splitlines() if ln.strip()]
        ts_idx = next((i for i, ln in enumerate(lines) if "-->" in ln), None)
        if ts_idx is None:
            continue
        m = ts_re.search(lines[ts_idx])
        if not m:
            continue
        h, mnt, s = int(m.group(1)), int(m.group(2)), int(m.group(3))
        total_seconds = h * 3600 + mnt * 60 + s

        text_lines = lines[ts_idx + 1:]
        if not text_lines:
            continue
        text_blob = "\n".join(text_lines).strip()

        vm = voice_tag_re.search(text_blob)
        if vm:
            speaker = vm.group(1).strip()
            text = vm.group(2).strip()
            # Strip any trailing voice-tag pieces / other tags
            text = re.sub(r"<[^>]+>", "", text).strip()
        else:
            # Fall back to "Speaker: text" convention
            colon = text_blob.find(":")
            if 0 < colon < 60 and "\n" not in text_blob[:colon]:
                speaker = text_blob[:colon].strip()
                text = text_blob[colon + 1:].strip()
            else:
                speaker = "Unknown"
                text = text_blob
            text = re.sub(r"<[^>]+>", "", text).strip()

        if not text:
            continue
        out.append((total_seconds, f"[{format_timestamp(total_seconds)}] {speaker}: {text}"))

    if not out:
        return None
    out.sort(key=lambda x: x[0])
    return "\n".join(line for _, line in out) + "\n"


async def _fetch_api_transcript(page: Page, url: str) -> str | None | bool:
    """Fetch transcript directly from SharePoint's API content endpoint.

    Uses the browser context's request client so auth cookies travel with the
    request. Returns the parsed transcript text, or False if the server says
    the resource is gone/forbidden (permanent), or None for anything transient
    worth retrying.
    """
    log.info(f"    API transcript fetch: {url[:100]}")
    try:
        resp = await page.context.request.get(url)
    except Exception as e:
        log.warning(f"    API transcript request failed: {e}")
        return None

    status = resp.status
    if status == 404 or status == 410:
        log.info(f"    API transcript returned {status} — recording has no transcript")
        return False
    if status == 401 or status == 403:
        log.warning(f"    API transcript returned {status} — auth/permissions issue")
        raise TransientExtractionError(f"API transcript auth failed ({status})")
    if status >= 400:
        log.warning(f"    API transcript returned {status} — will retry")
        return None

    try:
        body = await resp.text()
    except Exception as e:
        log.warning(f"    Could not read API transcript body: {e}")
        return None

    if not body.strip():
        log.info(f"    API transcript body empty")
        return None

    # Some endpoints return JSON-wrapped VTT (`{"vtt": "WEBVTT..."}`) rather than raw.
    stripped = body.lstrip()
    if stripped.startswith("{"):
        try:
            data = _json.loads(body)
            for key in ("vtt", "content", "transcript"):
                if isinstance(data.get(key), str):
                    body = data[key]
                    break
        except Exception:
            pass  # fall through — treat as raw text

    parsed = parse_vtt(body)
    if parsed:
        n_cues = parsed.count("\n")
        log.info(f"    Parsed {n_cues} VTT cues ({len(parsed)} chars)")
        return parsed

    log.warning(
        f"    API transcript body did not parse as VTT "
        f"(first 120 chars: {body[:120]!r})"
    )
    return None


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
