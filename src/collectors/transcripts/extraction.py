"""Transcript extraction — scroll+collect virtualized list, clean text, find frame."""

import asyncio
import re

from playwright.async_api import Page, Frame

from core.logging import log
from collectors.transcripts.js_snippets import (
    COLLECT_VISIBLE_JS,
    FIND_SCROLL_CONTAINER_JS,
    SCROLL_TO_JS,
    GET_TOTAL_ITEMS_JS,
)


async def extract_meeting_transcript(page: Page, iframe, meeting_name: str) -> tuple[str | None, bool, bool]:
    """Click into a meeting, find transcript, extract text.

    Returns (transcript_text, opened_recap, should_persist) tuple.
    opened_recap=True means we navigated to a recap page and need to go back.
    should_persist=True means mark this meeting as "attempted" — the meeting
    definitively has no transcript (no recap, no tab). False means transient
    failure (frame didn't load) — should be retried next run.
    """
    # Click the meeting
    try:
        btn = iframe.get_by_role("button", name=meeting_name)
        await btn.click()
        await page.wait_for_timeout(1500)
    except Exception as e:
        log.warning(f"    Could not click meeting: {e}")
        return None, False, False  # transient — couldn't even click

    # Look for "View recap" button — ONLY recap, not "View event"
    try:
        recap_btn = iframe.get_by_role("button", name="View recap")
        if await recap_btn.count() == 0:
            return None, False, True  # no recording — persist
        await recap_btn.click()
        await page.wait_for_timeout(3000)
    except Exception as e:
        log.warning(f"    Could not click recap: {e}")
        return None, False, False  # transient

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
                await page.wait_for_timeout(500)
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
        log.info("    Transcript tab not found.")
        return None, True, True  # no transcript tab — persist (recording but no transcription)

    # Find the transcript frame — retry with backoff since the transcript
    # content loads asynchronously after clicking the tab.
    transcript_frame = None
    for attempt in range(5):
        wait_ms = [2000, 2000, 3000, 4000, 5000][attempt]
        await page.wait_for_timeout(wait_ms)
        transcript_frame = await find_transcript_frame(page)
        if transcript_frame:
            break
        if attempt < 4:
            log.info(f"    Transcript frame not found (attempt {attempt + 1}/5), waiting...")

    if not transcript_frame:
        log.warning("    Transcript frame not found after 5 attempts — will retry next run.")
        return None, True, False  # TRANSIENT — don't persist, retry next run

    try:
        transcript = await scroll_and_extract(transcript_frame)
        if not transcript:
            log.info("    No transcript entries found.")
            return None, True, False  # transient — frame loaded but empty, retry
        return transcript, True, True  # success — persist
    except Exception as e:
        log.warning(f"    Extraction failed: {e}")
        return None, True, False  # transient — retry next run


async def scroll_and_extract(frame: Frame) -> str | None:
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
        log.warning("    Could not find scroll container (FocusZone ancestor).")
        return None

    scroll_height = container_info["scrollHeight"]
    client_height = container_info["clientHeight"]

    # Step 2: Check expected total from aria-setsize
    expected_total = await frame.evaluate(GET_TOTAL_ITEMS_JS)
    log.info(f"    Scrolling transcript: scrollHeight={scroll_height}, expected={expected_total} items")

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

    while stale_count < max_stale:
        prev_count = len(entries)
        position += step

        # Scroll to new position
        result = await frame.evaluate(SCROLL_TO_JS, position)
        if not result:
            break

        # Wait for ms-List to re-render new items at this scroll position
        await asyncio.sleep(0.2)

        # Collect visible items
        new_items = await frame.evaluate(COLLECT_VISIBLE_JS)
        for text in new_items:
            entries[text[:120]] = text

        if len(entries) == prev_count:
            stale_count += 1
        else:
            stale_count = 0

        # Safety: if we've scrolled well past the scrollHeight, stop
        current_sh = result.get("scrollHeight", scroll_height) if isinstance(result, dict) else scroll_height
        if position > current_sh + step * 2:
            break

    log.info(f"    Extracted {len(entries)} entries (expected {expected_total})")

    if not entries:
        return None

    return clean_transcript(list(entries.values()))


def clean_transcript(raw_entries: list[str]) -> str | None:
    """Convert raw listitem texts into clean speaker-attributed transcript.

    Raw entries from the DOM are either:
      - Speaker header: "Name\\nN minutes N seconds\\nM:SS" (or "Name\\nN SS\\nM:SS")
      - Text entry: just the spoken text

    Output format:
      [0:13] Esther Dediashvili: Good morning. Nice to meet you.
      [0:15] Dorota Zimnoch: Oh, I'm industry advisor...
    """
    timestamp_re = re.compile(r'^\d+:\d+$')

    # Parse entries into (speaker, timestamp, text) tuples
    current_speaker = None
    current_timestamp = None
    lines = []

    for raw in raw_entries:
        parts = raw.strip().split('\n')

        # Check if this is a speaker header (has a visual timestamp like "0:13" as last line)
        if len(parts) >= 2 and timestamp_re.match(parts[-1].strip()):
            current_speaker = parts[0].strip()
            current_timestamp = parts[-1].strip()
        elif len(parts) == 1 and not timestamp_re.match(parts[0].strip()):
            # Single line — could be a standalone speaker name or actual text
            text = parts[0].strip()
            if not text:
                continue
            # If this looks like a name (no punctuation except apostrophes/hyphens,
            # title case) AND we don't have a current speaker yet, treat as speaker intro
            if (current_speaker is None
                    and text.replace("'", "").replace("-", "").replace(" ", "").isalpha()
                    and text[0].isupper()):
                current_speaker = text
            else:
                # It's actual transcript text
                speaker = current_speaker or "Unknown"
                ts = f"[{current_timestamp}] " if current_timestamp else ""
                lines.append(f"{ts}{speaker}: {text}")

    if not lines:
        return None

    return "\n".join(lines) + "\n"


async def find_transcript_frame(page: Page) -> Frame | None:
    """Find the iframe containing transcript list items.

    Checks all frames (main + iframes) for [role="listitem"] elements.
    Also tries ms-List as a fallback selector in case listitem roles changed.
    """
    best_frame = None
    best_count = 0

    for frame in page.frames:
        try:
            count = await frame.locator('[role="listitem"]').count()
            if count > best_count:
                best_count = count
                best_frame = frame
        except Exception:
            continue

    if best_count > 5:
        return best_frame

    # Fallback: look for ms-List class (Fluent UI virtualized list)
    for frame in page.frames:
        try:
            has_list = await frame.locator('.ms-List [role="listitem"], .ms-List-cell').count()
            if has_list > 0:
                log.info(f"    Found transcript via ms-List fallback ({has_list} items)")
                return frame
        except Exception:
            continue

    if best_count > 0:
        log.info(f"    Best frame had only {best_count} listitems (need >5)")

    return None
