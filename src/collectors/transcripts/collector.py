"""Main transcript collection orchestrator — launch browser, iterate meetings, save."""

import asyncio
import re
from datetime import datetime, timedelta
from pathlib import Path

from playwright.async_api import async_playwright

from core.constants import PULSE_HOME, TRANSCRIPTS_DIR
from core.logging import log
from core.state import load_json_state, save_json_state
from collectors.transcripts.navigation import (
    return_to_calendar, find_meeting_buttons, navigate_weeks_back,
)
from collectors.transcripts.extraction import extract_meeting_transcript
# Per-meeting timeout (extraction only — compression deferred to Phase 0b).
# Budget: up to ~16s for frame-detection retries + scroll extraction.
PER_MEETING_TIMEOUT = 90  # 90 seconds (no inline compression)

# Max retries when returning to calendar fails (stale iframe recovery)
MAX_CALENDAR_RETRIES = 2

# Persistent state — tracks slugs we've already attempted (success or failure).
# Avoids re-clicking meetings that have no transcript every single run.
TRANSCRIPT_STATE_FILE = PULSE_HOME / ".transcript-state.json"
ATTEMPT_TTL_DAYS = 14  # retry after 14 days in case transcript appears later


def _load_attempted_slugs() -> dict[str, str]:
    """Load attempted slugs from state file, pruning entries older than TTL."""
    state = load_json_state(TRANSCRIPT_STATE_FILE, {"attempted": {}})
    attempted = state.get("attempted", {})
    cutoff = (datetime.now() - timedelta(days=ATTEMPT_TTL_DAYS)).isoformat()
    # Prune expired entries
    pruned = {slug: ts for slug, ts in attempted.items() if ts > cutoff}
    if len(pruned) < len(attempted):
        save_json_state(TRANSCRIPT_STATE_FILE, {"attempted": pruned})
    return pruned


def _mark_attempted(attempted: dict[str, str], slug: str):
    """Record that we attempted a slug and persist to disk."""
    attempted[slug] = datetime.now().isoformat()
    save_json_state(TRANSCRIPT_STATE_FILE, {"attempted": attempted})


def _slugify(text: str) -> str:
    """Convert meeting title to filesystem-safe slug."""
    text = text.lower().strip()
    text = re.sub(r'[^a-z0-9\s-]', '', text)
    text = re.sub(r'[\s]+', '-', text)
    return text[:60]


async def _process_single_meeting(page, iframe, meeting_name, slug, output_dir):
    """Extract a single meeting transcript (raw only — compression deferred to Phase 0b).

    Returns result dict with collected, opened_recap, should_persist.
    """
    transcript, opened_recap, should_persist = await extract_meeting_transcript(page, iframe, meeting_name)
    if not transcript:
        return {"collected": False, "opened_recap": opened_recap, "should_persist": should_persist}

    date_str = datetime.now().strftime("%Y-%m-%d")
    filename = f"{date_str}_{slug}.txt"
    filepath = output_dir / filename
    filepath.write_text(transcript, encoding="utf-8")
    log.info(f"  SAVED (raw): {filename} ({len(transcript)} chars)")

    return {"collected": True, "opened_recap": opened_recap, "should_persist": True}


async def _navigate_to_calendar(page) -> object:
    """Navigate to Teams Calendar and return the calendar iframe locator.

    teams.microsoft.com/v2/calendar redirects to teams.cloud.microsoft/ (Chat
    view, NOT Calendar) since early 2026.  Primary strategy: load Teams root,
    then click the Calendar button in the left app-bar.  Fall back to the new
    /calendar URL on teams.cloud.microsoft for later retries.

    Verifies success by checking for buttons inside the calendar iframe —
    NOT page.title() which crashes during SPA redirects.

    Raises RuntimeError if calendar cannot be opened after all retries.
    """
    iframe = None

    for attempt in range(4):
        try:
            if attempt == 0:
                # Primary: load Teams root, then click Calendar button
                log.info("  Opening Teams via root + Calendar button...")
                await page.goto("https://teams.cloud.microsoft/",
                                wait_until="domcontentloaded")
                await page.wait_for_timeout(8000)
                try:
                    cal_btn = page.get_by_role("button", name=re.compile(r"Calendar"))
                    if await cal_btn.count() > 0:
                        await cal_btn.click()
                        await page.wait_for_timeout(5000)
                except Exception:
                    pass
            elif attempt == 1:
                # Try direct calendar URL on the new domain
                log.info(f"  Calendar attempt {attempt + 1}: direct URL on new domain...")
                await page.goto("https://teams.cloud.microsoft/calendar",
                                wait_until="domcontentloaded")
                await page.wait_for_timeout(8000)
            elif attempt == 2:
                # Reload root with longer settle, then button click
                log.info(f"  Calendar attempt {attempt + 1}: root + button with long wait...")
                await page.goto("https://teams.cloud.microsoft/",
                                wait_until="domcontentloaded")
                await page.wait_for_timeout(12000)
                try:
                    cal_btn = page.get_by_role("button", name=re.compile(r"Calendar"))
                    if await cal_btn.count() > 0:
                        await cal_btn.click()
                        await page.wait_for_timeout(5000)
                except Exception:
                    pass
            else:
                # Last resort: old URL (may still work via redirect chain)
                log.info(f"  Calendar attempt {attempt + 1}: legacy URL fallback...")
                await page.goto("https://teams.microsoft.com/v2/calendar",
                                wait_until="domcontentloaded")
                await page.wait_for_timeout(10000)
                try:
                    cal_btn = page.get_by_role("button", name=re.compile(r"Calendar"))
                    if await cal_btn.count() > 0:
                        await cal_btn.click()
                        await page.wait_for_timeout(5000)
                except Exception:
                    pass

            # Verify: look for the calendar iframe with buttons inside
            iframe = page.frame_locator('iframe[name="embedded-page-container"]')
            try:
                await iframe.get_by_role("button").first.wait_for(
                    state="visible", timeout=15000
                )
                # Success — iframe has rendered buttons
                log.info("  Calendar loaded (iframe has buttons).")
                return iframe
            except Exception:
                log.warning(f"  Calendar iframe not ready (attempt {attempt + 1}/4)")

        except Exception as e:
            log.warning(f"  Calendar navigation error (attempt {attempt + 1}/4): {e}")
            await page.wait_for_timeout(3000)

    raise RuntimeError("Cannot open Calendar view after 4 attempts")


async def _return_to_calendar_with_retry(page, iframe, week_offset: int) -> bool:
    """Return to calendar with retry on stale iframe.

    When the calendar iframe goes stale after recap navigation, a simple
    return_to_calendar fails. This function retries with a full page reload
    to recover.

    Returns True if calendar was restored, False if all retries failed.
    """
    for attempt in range(MAX_CALENDAR_RETRIES + 1):
        try:
            if attempt > 0:
                # Full reload — the iframe is stale, need to start fresh
                log.info(f"  Stale iframe recovery (attempt {attempt + 1})...")
                await page.goto("https://teams.cloud.microsoft/",
                                wait_until="domcontentloaded")
                await page.wait_for_timeout(5000)
                # Click Calendar button to get to Calendar view
                try:
                    cal_btn = page.get_by_role("button", name=re.compile(r"Calendar"))
                    if await cal_btn.count() > 0:
                        await cal_btn.click()
                        await page.wait_for_timeout(3000)
                except Exception:
                    pass

                # Re-acquire the iframe after reload
                iframe_loc = page.frame_locator('iframe[name="embedded-page-container"]')
                try:
                    await iframe_loc.get_by_role("button").first.wait_for(
                        state="visible", timeout=15000
                    )
                except Exception:
                    await page.wait_for_timeout(5000)

                # Navigate back to correct week
                from collectors.transcripts.navigation import navigate_weeks_back, go_to_today
                await go_to_today(page, iframe_loc)
                if week_offset > 0:
                    await navigate_weeks_back(page, iframe_loc, week_offset)
                    await page.wait_for_timeout(2000)
            else:
                await return_to_calendar(page, iframe, force=True,
                                         week_offset=week_offset)

            # Verify calendar is actually usable
            buttons = await find_meeting_buttons(page, iframe)
            if len(buttons) > 0:
                return True
            log.warning(f"  Calendar returned 0 buttons (attempt {attempt + 1})")

        except Exception as e:
            log.warning(f"  Return to calendar failed (attempt {attempt + 1}): {e}")

    return False


async def run_transcript_collection(client, config: dict):
    """Collect meeting transcripts from Teams web using Playwright directly.

    No LLM involved — deterministic navigation script.
    Uses multi-week lookback (config: transcripts.lookback_weeks, default 2).
    Saves raw .txt files only — compression is deferred to Phase 0b.

    Uses the shared BrowserManager when available (daemon mode).
    Falls back to launching its own browser (CLI --once mode).
    """
    log.info("Transcript collection start")

    tc = config.get("transcripts", {})
    max_meetings = tc.get("max_per_run", 50)
    lookback_weeks = tc.get("lookback_weeks", 2)
    output_dir = Path(tc.get("output_dir", str(TRANSCRIPTS_DIR)))
    if not output_dir.is_absolute():
        output_dir = PULSE_HOME / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    playwright_cfg = tc.get("playwright", {})
    default_data_dir = str(Path.home() / "AppData/Local/ms-playwright/mcp-msedge-profile")
    user_data_dir = playwright_cfg.get("user_data_dir", default_data_dir)

    collected = 0
    skipped = 0
    errors = []

    # Load persistent attempt tracking — skip meetings we've already tried
    attempted_history = _load_attempted_slugs()
    log.info(f"  Transcript state: {len(attempted_history)} previously attempted slugs (TTL={ATTEMPT_TTL_DAYS}d)")

    # Use shared browser if available, otherwise launch our own
    from core.browser import get_browser_manager
    browser_mgr = get_browser_manager()

    if browser_mgr and browser_mgr.context:
        log.info("  Using shared browser instance")
        page = await browser_mgr.new_page()
        own_context = None
    else:
        log.info("  Launching standalone browser (no shared instance)")
        _pw = await async_playwright().__aenter__()
        own_context = await _pw.chromium.launch_persistent_context(
            user_data_dir,
            channel="msedge",
            headless=True,
            viewport={"width": 1280, "height": 900},
        )
        if own_context.pages:
            page = own_context.pages[0]
            for old_page in own_context.pages[1:]:
                await old_page.close()
        else:
            page = await own_context.new_page()

    try:
        # Navigate to Calendar and wait for the iframe to be usable.
        # Verification is done by checking for the calendar iframe with buttons
        # inside it — NOT page.title() which crashes during SPA redirects.
        try:
            iframe = await _navigate_to_calendar(page)
        except RuntimeError as e:
            log.warning(f"  {e}")
            return

        # Multi-week lookback — process each week from most recent to oldest
        current_week_offset = 0
        attempted_slugs: set[str] = set()  # Track slugs we've already tried

        for week_num in range(1, lookback_weeks + 1):
            if collected >= max_meetings:
                break

            log.info(f"  --- Week {week_num} of {lookback_weeks} ---")

            # Navigate one week further back
            await navigate_weeks_back(page, iframe, 1)
            current_week_offset += 1

            # Step 4: Find meetings — wait for calendar to fully render
            await page.wait_for_timeout(2000)
            meeting_buttons = await find_meeting_buttons(page, iframe)

            # If few meetings found, calendar may still be loading — retry
            if len(meeting_buttons) < 3:
                await page.wait_for_timeout(4000)
                meeting_buttons = await find_meeting_buttons(page, iframe)
            log.info(f"  Found {len(meeting_buttons)} meeting buttons in calendar.")

            # Step 5: Process meetings — re-scan after each recap return
            consecutive_click_failures = 0
            while meeting_buttons and collected < max_meetings:
                meeting_name = meeting_buttons.pop(0)

                slug = _slugify(meeting_name)
                if not slug or slug in attempted_slugs:
                    continue
                attempted_slugs.add(slug)

                # Check persistent history — already tried in a previous run?
                if slug in attempted_history:
                    skipped += 1
                    continue

                # Check if already collected on disk
                existing = list(output_dir.glob(f"*_{slug}*"))
                if existing:
                    skipped += 1
                    continue

                log.info(f"  Processing: {meeting_name[:60]}...")
                opened_recap = False
                should_persist = True  # default: persist unless told otherwise
                try:
                    result = await asyncio.wait_for(
                        _process_single_meeting(page, iframe, meeting_name, slug, output_dir),
                        timeout=PER_MEETING_TIMEOUT,
                    )
                    opened_recap = result.get("opened_recap", False)
                    should_persist = result.get("should_persist", True)
                    if result.get("collected"):
                        collected += 1
                    else:
                        skipped += 1
                    consecutive_click_failures = 0
                except asyncio.TimeoutError:
                    log.warning(f"  TIMEOUT: {meeting_name[:40]} exceeded {PER_MEETING_TIMEOUT}s")
                    errors.append(f"{meeting_name[:40]}: timeout after {PER_MEETING_TIMEOUT}s")
                    opened_recap = True  # assume we navigated away
                    should_persist = False  # timeout is transient — retry next run
                except Exception as e:
                    err_msg = str(e)
                    if "Timeout" in err_msg:
                        consecutive_click_failures += 1
                        if consecutive_click_failures >= 3:
                            log.warning("  3 consecutive click failures — calendar view likely stale, re-navigating...")
                            # Force re-navigation and re-scan
                            opened_recap = True
                    else:
                        consecutive_click_failures = 0
                    log.warning(f"  ERROR: {meeting_name[:40]}: {e}")
                    errors.append(f"{meeting_name[:40]}: {e}")
                    should_persist = False  # errors are transient — retry next run

                # Only persist to state if the meeting definitively has no transcript.
                # Transient failures (frame didn't load, timeout, error) are NOT persisted
                # so the meeting will be retried on the next run.
                if should_persist:
                    _mark_attempted(attempted_history, slug)

                # Navigate back to calendar for next meeting
                if opened_recap:
                    calendar_ok = await _return_to_calendar_with_retry(
                        page, iframe, current_week_offset
                    )
                    if calendar_ok:
                        meeting_buttons = await find_meeting_buttons(page, iframe)
                        log.info(f"  Re-scanned: {len(meeting_buttons)} meetings after return.")
                        consecutive_click_failures = 0
                    else:
                        log.warning("  FATAL: Cannot return to calendar, stopping week.")
                        meeting_buttons = []
                else:
                    # Simple popup — just escape
                    try:
                        await return_to_calendar(page, iframe, force=False)
                    except Exception:
                        pass

    finally:
        # Close the page we created, but only close the context if we own it
        if own_context:
            await own_context.close()
        else:
            await page.close()

    # Summary
    log.info(f"Transcript collection end — collected: {collected}, skipped: {skipped}, errors: {len(errors)}")
    for err in errors:
        log.warning(f"  Transcript error: {err}")
