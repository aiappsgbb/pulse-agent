"""Main transcript collection orchestrator — launch browser, iterate meetings, save."""

import re
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright

from core.constants import INPUT_DIR
from core.logging import safe_encode
from collectors.transcripts.navigation import return_to_calendar, find_meeting_buttons
from collectors.transcripts.extraction import extract_meeting_transcript
from collectors.transcripts.compressor import compress_transcript


def _print(text: str):
    """Print with ASCII-safe encoding to avoid charmap errors on Windows."""
    print(safe_encode(text))


def _slugify(text: str) -> str:
    """Convert meeting title to filesystem-safe slug."""
    text = text.lower().strip()
    text = re.sub(r'[^a-z0-9\s-]', '', text)
    text = re.sub(r'[\s]+', '-', text)
    return text[:60]


async def run_transcript_collection(client, config: dict):
    """Collect meeting transcripts from Teams web using Playwright directly.

    No LLM involved — deterministic navigation script.
    The `client` param is accepted for interface compatibility but not used.

    Uses the shared BrowserManager when available (daemon mode).
    Falls back to launching its own browser (CLI --once mode).
    """
    _print("\n=== Transcript collection start ===")

    tc = config.get("transcripts", {})
    max_meetings = tc.get("max_per_run", 10)
    output_dir = Path(tc.get("output_dir", str(INPUT_DIR / "transcripts")))
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
        _print("  Using shared browser instance")
        page = await browser_mgr.new_page()
        own_context = None
    else:
        _print("  Launching standalone browser (no shared instance)")
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
        _print("  Opening Teams...")
        await page.goto("https://teams.microsoft.com", wait_until="domcontentloaded")
        await page.wait_for_timeout(10000)

        try:
            title = await page.title()
            _print(f"  Page loaded: {title}")
        except Exception:
            _print("  Page navigated during load (Teams SPA redirect) — continuing.")

        # Step 2: Click Calendar in the left nav bar (works from any view)
        _print("  Clicking Calendar nav button...")
        try:
            cal_btn = page.get_by_role("button", name="Calendar")
            await cal_btn.click()
            await page.wait_for_timeout(5000)
        except Exception:
            # Fallback: keyboard shortcut
            _print("  Calendar button not found, trying Ctrl+Shift+3...")
            await page.keyboard.press("Control+Shift+3")
            await page.wait_for_timeout(5000)

        title = await page.title()
        _print(f"  After nav: {title}")

        if "Calendar" not in title:
            _print(f"  ERROR: Could not open Calendar view. Got: {title}")
            return

        # Step 3: Wait for the calendar iframe to fully load
        _print("  Waiting for calendar iframe to load...")
        iframe = page.frame_locator('iframe[name="embedded-page-container"]')
        try:
            await iframe.get_by_role("button").first.wait_for(state="visible", timeout=30000)
            _print("  Calendar iframe loaded.")
        except Exception:
            _print("  WARNING: Calendar iframe slow to load, waiting 10 more seconds...")
            await page.wait_for_timeout(10000)

        # Step 3b: Go to previous week (completed meetings have transcripts)
        _print("  Navigating to previous week...")
        try:
            prev_btn = iframe.get_by_role("button", name=re.compile(r"Go to previous week"))
            await prev_btn.click()
            await page.wait_for_timeout(3000)
            _print("  Previous week loaded.")
        except Exception as e:
            _print(f"  WARNING: Could not find 'Go to previous week' button: {e}")

        # Step 4: Find meetings — wait for async meeting renders
        _print("  Scanning for meetings...")
        meeting_buttons = await find_meeting_buttons(page, iframe)

        # If few meetings found, calendar may still be loading — retry
        if len(meeting_buttons) < 3:
            _print("  Few meetings found, waiting 5s for calendar to finish rendering...")
            await page.wait_for_timeout(5000)
            meeting_buttons = await find_meeting_buttons(page, iframe)
        _print(f"  Found {len(meeting_buttons)} meeting buttons in calendar.")

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
                _print(f"  SKIP (already exists): {meeting_name[:50]}")
                skipped += 1
                continue

            _print(f"\n  Processing: {meeting_name[:60]}...")
            opened_recap = False
            try:
                transcript, opened_recap = await extract_meeting_transcript(page, iframe, meeting_name)
                if transcript:
                    date_str = datetime.now().strftime("%Y-%m-%d")

                    # Compress via GHCP SDK if client is available
                    compressed = None
                    if client:
                        tc_models = config.get("models", {})
                        compress_model = tc_models.get("transcripts", tc_models.get("default", "claude-sonnet"))
                        compressed = await compress_transcript(client, transcript, meeting_name, model=compress_model)

                    if compressed:
                        filename = f"{date_str}_{slug}.md"
                        filepath = output_dir / filename
                        # Prepend metadata header
                        header = (
                            f"# {meeting_name}\n"
                            f"**Date**: {date_str} | "
                            f"**Original length**: {len(transcript)} chars | "
                            f"**Compressed**: {len(compressed)} chars\n\n"
                        )
                        filepath.write_text(header + compressed, encoding="utf-8")
                        _print(f"  SAVED (compressed): {filename} ({len(compressed)} chars from {len(transcript)})")
                    else:
                        filename = f"{date_str}_{slug}.txt"
                        filepath = output_dir / filename
                        filepath.write_text(transcript, encoding="utf-8")
                        _print(f"  SAVED (raw): {filename} ({len(transcript)} chars)")

                    collected += 1
                else:
                    _print(f"  No transcript available for this meeting.")
                    skipped += 1
            except Exception as e:
                err_msg = f"{meeting_name[:40]}: {e}"
                _print(f"  ERROR: {err_msg}")
                errors.append(err_msg)

            # Navigate back to calendar for next meeting
            try:
                await return_to_calendar(page, iframe, force=opened_recap)
            except Exception:
                _print("  FATAL: Cannot return to calendar, stopping collection.")
                break

    finally:
        # Close the page we created, but only close the context if we own it
        if own_context:
            await own_context.close()
        else:
            await page.close()

    # Summary
    _print(f"\n=== Transcript collection end ===")
    _print(f"  Collected: {collected}")
    _print(f"  Skipped: {skipped}")
    _print(f"  Errors: {len(errors)}")
    for err in errors:
        _print(f"    - {err}")
