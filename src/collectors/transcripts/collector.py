"""Main transcript collection orchestrator — launch browser, iterate meetings, save."""

import asyncio
import re
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright

from core.constants import TRANSCRIPTS_DIR
from core.logging import log
from collectors.transcripts.navigation import (
    return_to_calendar, find_meeting_buttons, navigate_weeks_back,
)
from collectors.transcripts.extraction import extract_meeting_transcript
from collectors.transcripts.compressor import compress_transcript

# Per-meeting timeout (extraction + compression). Prevents one stuck meeting
# from eating the entire transcript collection budget.
PER_MEETING_TIMEOUT = 180  # 3 minutes


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
        # Step 1: Navigate to Teams — fresh page, no stale SPA state
        log.info("  Opening Teams...")
        await page.goto("https://teams.microsoft.com", wait_until="domcontentloaded")
        await page.wait_for_timeout(6000)

        try:
            title = await page.title()
            log.info(f"  Page loaded: {title}")
        except Exception:
            log.info("  Page navigated during load (Teams SPA redirect) — continuing.")

        # Step 2: Click Calendar in the left nav bar (works from any view)
        log.info("  Clicking Calendar nav button...")
        try:
            cal_btn = page.get_by_role("button", name="Calendar")
            await cal_btn.click()
            await page.wait_for_timeout(3000)
        except Exception:
            # Fallback: keyboard shortcut
            log.info("  Calendar button not found, trying Ctrl+Shift+3...")
            await page.keyboard.press("Control+Shift+3")
            await page.wait_for_timeout(3000)

        title = await page.title()
        log.info(f"  After nav: {title}")

        if "Calendar" not in title:
            log.warning(f"  Could not open Calendar view. Got: {title}")
            return

        # Step 3: Wait for the calendar iframe to fully load
        iframe = page.frame_locator('iframe[name="embedded-page-container"]')
        try:
            await iframe.get_by_role("button").first.wait_for(state="visible", timeout=15000)
        except Exception:
            log.warning("  Calendar iframe slow to load, waiting 5 more seconds...")
            await page.wait_for_timeout(5000)

        # Step 3b: Multi-week lookback — process each week from most recent to oldest
        current_week_offset = 0

        for week_num in range(1, lookback_weeks + 1):
            if collected >= max_meetings:
                break

            log.info(f"  --- Week {week_num} of {lookback_weeks} ---")

            # Navigate one week further back
            await navigate_weeks_back(page, iframe, 1)
            current_week_offset += 1

            # Step 4: Find meetings
            meeting_buttons = await find_meeting_buttons(page, iframe)

            # If few meetings found, calendar may still be loading — retry
            if len(meeting_buttons) < 3:
                await page.wait_for_timeout(3000)
                meeting_buttons = await find_meeting_buttons(page, iframe)
            log.info(f"  Found {len(meeting_buttons)} meeting buttons in calendar.")

            # Step 5: Process each meeting — try all, most won't have transcripts
            for meeting_name in meeting_buttons:
                if collected >= max_meetings:
                    break

                slug = _slugify(meeting_name)
                if not slug:
                    continue

                # Check if already collected
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
                except asyncio.TimeoutError:
                    log.warning(f"  TIMEOUT: {meeting_name[:40]} exceeded {PER_MEETING_TIMEOUT}s")
                    errors.append(f"{meeting_name[:40]}: timeout after {PER_MEETING_TIMEOUT}s")
                    opened_recap = True  # assume we navigated away
                except Exception as e:
                    err_msg = f"{meeting_name[:40]}: {e}"
                    log.warning(f"  ERROR: {err_msg}")
                    errors.append(err_msg)

                # Navigate back to calendar for next meeting
                try:
                    await return_to_calendar(page, iframe, force=opened_recap,
                                             week_offset=current_week_offset)
                except Exception:
                    log.warning("  FATAL: Cannot return to calendar, stopping collection.")
                    break

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
