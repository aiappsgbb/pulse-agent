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
from collectors.transcripts.compressor import compress_transcript

# Per-meeting timeout (extraction + compression). Prevents one stuck meeting
# from eating the entire transcript collection budget.
PER_MEETING_TIMEOUT = 180  # 3 minutes

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


async def _process_single_meeting(page, iframe, meeting_name, slug, output_dir, client, config):
    """Extract and compress a single meeting transcript. Returns result dict."""
    transcript, opened_recap = await extract_meeting_transcript(page, iframe, meeting_name)
    if not transcript:
        return {"collected": False, "opened_recap": opened_recap}

    date_str = datetime.now().strftime("%Y-%m-%d")
    compressed = None
    if client:
        tc_models = config.get("models", {})
        compress_model = tc_models.get("transcripts", tc_models.get("default", "claude-sonnet"))
        compressed = await compress_transcript(client, transcript, meeting_name, model=compress_model)

    if compressed:
        filename = f"{date_str}_{slug}.md"
        filepath = output_dir / filename
        header = (
            f"# {meeting_name}\n"
            f"**Date**: {date_str} | "
            f"**Original length**: {len(transcript)} chars | "
            f"**Compressed**: {len(compressed)} chars\n\n"
        )
        filepath.write_text(header + compressed, encoding="utf-8")
        log.info(f"  SAVED (compressed): {filename} ({len(compressed)} chars from {len(transcript)})")
    else:
        filename = f"{date_str}_{slug}.txt"
        filepath = output_dir / filename
        filepath.write_text(transcript, encoding="utf-8")
        log.info(f"  SAVED (raw): {filename} ({len(transcript)} chars)")

    return {"collected": True, "opened_recap": opened_recap}


async def run_transcript_collection(client, config: dict):
    """Collect meeting transcripts from Teams web using Playwright directly.

    No LLM involved — deterministic navigation script.
    Uses multi-week lookback (config: transcripts.lookback_weeks, default 2).

    Uses the shared BrowserManager when available (daemon mode).
    Falls back to launching its own browser (CLI --once mode).
    """
    log.info("Transcript collection start")

    tc = config.get("transcripts", {})
    max_meetings = tc.get("max_per_run", 20)
    lookback_weeks = tc.get("lookback_weeks", 2)
    output_dir = Path(tc.get("output_dir", str(TRANSCRIPTS_DIR)))
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
        # Step 1: Navigate to Teams and wait for the SPA to fully load
        log.info("  Opening Teams...")
        await page.goto("https://teams.microsoft.com", wait_until="domcontentloaded")

        # Wait for Teams UI to actually render — look for the app bar / nav buttons
        try:
            await page.get_by_role("button", name="Chat").wait_for(state="visible", timeout=20000)
            log.info(f"  Teams loaded: {await page.title()}")
        except Exception:
            # App bar not found within 20s — wait a bit more and continue
            await page.wait_for_timeout(8000)
            log.info(f"  Teams slow load, continuing: {await page.title()}")

        # Step 2: Click Calendar — retry up to 3 times with different approaches
        log.info("  Navigating to Calendar...")
        calendar_opened = False

        for attempt in range(3):
            # Try clicking the Calendar button in the left nav
            try:
                cal_btn = page.get_by_role("button", name="Calendar")
                if await cal_btn.count() > 0:
                    await cal_btn.click()
                    await page.wait_for_timeout(3000)
                    title = await page.title()
                    if "Calendar" in title:
                        calendar_opened = True
                        break
            except Exception:
                pass

            # Fallback: navigate directly to Teams calendar URL
            if attempt >= 1:
                log.info(f"  Calendar button failed (attempt {attempt + 1}), trying direct URL...")
                try:
                    await page.goto("https://teams.microsoft.com/v2/calendar", wait_until="domcontentloaded")
                    await page.wait_for_timeout(5000)
                    title = await page.title()
                    if "Calendar" in title:
                        calendar_opened = True
                        break
                except Exception:
                    pass

            if attempt < 2:
                log.info(f"  Calendar nav attempt {attempt + 1} failed, waiting before retry...")
                await page.wait_for_timeout(3000)

        if not calendar_opened:
            title = await page.title()
            log.warning(f"  Could not open Calendar view after 3 attempts. Got: {title}")
            return

        log.info(f"  Calendar opened: {await page.title()}")

        # Step 3: Wait for the calendar iframe to fully load
        iframe = page.frame_locator('iframe[name="embedded-page-container"]')
        try:
            await iframe.get_by_role("button").first.wait_for(state="visible", timeout=15000)
        except Exception:
            log.warning("  Calendar iframe slow to load, waiting 5 more seconds...")
            await page.wait_for_timeout(5000)

        # Step 3b: Multi-week lookback — process each week from most recent to oldest
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
                try:
                    result = await asyncio.wait_for(
                        _process_single_meeting(page, iframe, meeting_name, slug, output_dir, client, config),
                        timeout=PER_MEETING_TIMEOUT,
                    )
                    opened_recap = result.get("opened_recap", False)
                    if result.get("collected"):
                        collected += 1
                    else:
                        skipped += 1
                    consecutive_click_failures = 0
                except asyncio.TimeoutError:
                    log.warning(f"  TIMEOUT: {meeting_name[:40]} exceeded {PER_MEETING_TIMEOUT}s")
                    errors.append(f"{meeting_name[:40]}: timeout after {PER_MEETING_TIMEOUT}s")
                    opened_recap = True  # assume we navigated away
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

                # Persist this attempt so we skip it in future runs
                _mark_attempted(attempted_history, slug)

                # Navigate back to calendar for next meeting
                if opened_recap:
                    try:
                        await return_to_calendar(page, iframe, force=True,
                                                 week_offset=current_week_offset)
                        # Re-scan buttons — calendar may have different state after return
                        meeting_buttons = await find_meeting_buttons(page, iframe)
                        log.info(f"  Re-scanned: {len(meeting_buttons)} meetings after return.")
                        consecutive_click_failures = 0
                    except Exception:
                        log.warning("  FATAL: Cannot return to calendar, stopping collection.")
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
