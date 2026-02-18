"""Transcript extraction — scroll+collect virtualized list, clean text, find frame."""

import asyncio
import re

from playwright.async_api import Page, Frame

from core.logging import safe_encode
from collectors.transcripts.js_snippets import (
    COLLECT_VISIBLE_JS,
    FIND_SCROLL_CONTAINER_JS,
    SCROLL_TO_JS,
    GET_TOTAL_ITEMS_JS,
)


def _print(text: str):
    """Print with ASCII-safe encoding to avoid charmap errors on Windows."""
    print(safe_encode(text))


async def extract_meeting_transcript(page: Page, iframe, meeting_name: str) -> tuple[str | None, bool]:
    """Click into a meeting, find transcript, extract text.

    Returns (transcript_text, opened_recap) tuple.
    opened_recap=True means we navigated to a recap page and need to go back.
    """
    # Click the meeting
    try:
        btn = iframe.get_by_role("button", name=meeting_name)
        await btn.click()
        await page.wait_for_timeout(2000)
    except Exception as e:
        _print(f"    Could not click meeting: {e}")
        return None, False

    # Look for "View recap" button — ONLY recap, not "View event"
    try:
        recap_btn = iframe.get_by_role("button", name="View recap")
        if await recap_btn.count() == 0:
            _print("    No 'View recap' button — skipping (no recording).")
            return None, False
        await recap_btn.click()
        await page.wait_for_timeout(4000)
    except Exception as e:
        _print(f"    Could not click recap: {e}")
        return None, False

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
        return None, True  # opened recap but no transcript tab

    await page.wait_for_timeout(3000)

    # Find the transcript frame and extract text by scrolling the FocusZone
    transcript_frame = await find_transcript_frame(page)
    if not transcript_frame:
        _print("    Transcript frame not found.")
        return None, True

    try:
        transcript = await scroll_and_extract(transcript_frame)
        if not transcript:
            _print("    No transcript entries found.")
            return None, True
        return transcript, True
    except Exception as e:
        _print(f"    Extraction failed: {e}")
        return None, True


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
    """Find the iframe containing transcript list items."""
    for frame in page.frames:
        try:
            count = await frame.locator('[role="listitem"]').count()
            if count > 5:
                return frame
        except Exception:
            continue
    return None
