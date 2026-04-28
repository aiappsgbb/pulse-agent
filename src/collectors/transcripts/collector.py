"""Main transcript collection orchestrator — Outlook Calendar + SharePoint Stream."""

import asyncio
import re
from contextlib import asynccontextmanager, nullcontext
from datetime import datetime, timedelta
from pathlib import Path

from playwright.async_api import async_playwright

from core.constants import PULSE_HOME, TRANSCRIPTS_DIR, TRANSCRIPT_STATUS_FILE
from core.logging import log
from core.state import load_json_state, save_json_state
from collectors.transcripts.navigation import (
    navigate_to_outlook_calendar,
    navigate_weeks_back,
    discover_meetings_with_recaps,
    DiscoveryResult,
    MeetingInfo,
)
from collectors.transcripts.extraction import (
    extract_transcript_from_sharepoint,
    TransientExtractionError,
)

# Screenshot diagnostics — saves to PULSE_HOME/logs/screenshots/
_SCREENSHOT_DIR = PULSE_HOME / "logs" / "screenshots"
_screenshot_seq = 0


async def _diag(page, label: str):
    """Save a diagnostic screenshot with sequential numbering."""
    global _screenshot_seq
    _SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    _screenshot_seq += 1
    fname = f"{_screenshot_seq:03d}_{label}.png"
    try:
        await page.screenshot(path=str(_SCREENSHOT_DIR / fname), full_page=False)
        log.info(f"  [DIAG] {fname}")
    except Exception as e:
        log.warning(f"  [DIAG] screenshot failed ({label}): {e}")


# Per-meeting timeout (extraction only — compression deferred to Phase 0b).
PER_MEETING_TIMEOUT = 120  # 120 seconds for SharePoint page load + scroll extraction

# Persistent state — tracks slugs we've already attempted (success or failure).
# Avoids re-clicking meetings that have no transcript every single run.
TRANSCRIPT_STATE_FILE = PULSE_HOME / ".transcript-state.json"
ATTEMPT_TTL_DAYS = 14  # retry after 14 days in case transcript appears later


def _load_state() -> dict:
    """Load the raw state dict, prune expired entries from both maps."""
    state = load_json_state(TRANSCRIPT_STATE_FILE, {"attempted": {}, "no_recap": {}})
    cutoff_dt = datetime.now() - timedelta(days=ATTEMPT_TTL_DAYS)

    def _prune_ttl(m: dict) -> dict:
        kept = {}
        for slug, ts in m.items():
            try:
                if datetime.fromisoformat(ts) > cutoff_dt:
                    kept[slug] = ts
            except (ValueError, TypeError):
                pass
        return kept

    state["attempted"] = _prune_ttl(state.get("attempted", {}))
    state["no_recap"] = _prune_ttl(state.get("no_recap", {}))
    return state


def _save_state(state: dict):
    save_json_state(TRANSCRIPT_STATE_FILE, state)


def _load_attempted_slugs(output_dir: Path | None = None) -> dict[str, str]:
    """Load attempted slugs from state, pruning expired entries and orphans.

    Returns the union of:
    - `attempted` (success / permanent-failure markers from the extraction stage)
    - `no_recap` (popup loaded but no recap button — memoized in discovery)

    Orphan pruning only applies to `attempted` (those should have produced a
    transcript file). `no_recap` entries are deterministic and never have a
    file by design.
    """
    state = _load_state()
    attempted = state["attempted"]
    no_recap = state["no_recap"]

    # Orphan-prune `attempted` only (no_recap entries never have files).
    if output_dir and output_dir.exists():
        existing_files = set()
        for f in output_dir.glob("*.txt"):
            parts = f.stem.split("_", 1)
            if len(parts) == 2:
                existing_files.add(parts[1])
        for f in output_dir.glob("*.md"):
            parts = f.stem.split("_", 1)
            if len(parts) == 2:
                existing_files.add(parts[1])

        orphaned = {s for s in attempted if s not in existing_files}
        if orphaned:
            log.info(f"  Pruning {len(orphaned)} attempted slugs with no transcript file (will retry)")
            attempted = {s: ts for s, ts in attempted.items() if s not in orphaned}
            state["attempted"] = attempted

    _save_state(state)

    merged = {**no_recap, **attempted}
    if no_recap:
        log.info(f"  Memoized no-recap slugs: {len(no_recap)}")
    return merged


def _mark_attempted(attempted: dict[str, str], slug: str):
    """Record that we attempted a slug (extraction stage) and persist to disk."""
    attempted[slug] = datetime.now().isoformat()
    state = _load_state()
    state["attempted"][slug] = attempted[slug]
    _save_state(state)


def _mark_no_recap(slug: str):
    """Record that a meeting popup loaded but had no recap button.

    Deterministic outcome — recap won't appear later for past meetings.
    Memoized to avoid re-polling for 15s on every subsequent run.
    """
    state = _load_state()
    state["no_recap"][slug] = datetime.now().isoformat()
    _save_state(state)


def _slugify(text: str) -> str:
    """Convert meeting title to filesystem-safe slug."""
    text = text.lower().strip()
    text = re.sub(r'[^a-z0-9\s-]', '', text)
    text = re.sub(r'[\s]+', '-', text)
    return text[:60]


async def run_transcript_collection(client, config: dict):
    """Collect meeting transcripts via Outlook Calendar + SharePoint Stream.

    Flow:
    1. Launch headful browser (SharePoint SSO requires visible browser)
    2. Navigate to Outlook Calendar week view
    3. For each week in lookback range:
       a. Scan all meeting events for "View recap" buttons
       b. Extract SharePoint Stream URLs from Teams launcher pages
    4. For each meeting with a transcript:
       a. Open new tab with SharePoint Stream URL
       b. Click Transcript tab, scroll-extract all entries
       c. Save raw .txt file
       d. Close tab

    No LLM involved — deterministic navigation script.
    Launches its own headful browser because SharePoint SSO doesn't work in
    headless mode (login.microsoftonline.com OAuth redirect requires visible browser).
    """
    log.info("Transcript collection start (Outlook+SharePoint)")

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

    # Load persistent attempt tracking — skip meetings we've already tried.
    # Pass output_dir to prune orphaned slugs (attempted but no file saved).
    attempted_history = _load_attempted_slugs(output_dir)
    log.info(f"  Transcript state: {len(attempted_history)} previously attempted slugs (TTL={ATTEMPT_TTL_DAYS}d)")

    # Try to connect to an existing authenticated browser via CDP.
    # The Playwright MCP server's browser (mcp-msedge-profile) has SharePoint cookies.
    # SharePoint SSO doesn't work with fresh profiles — needs cookies from prior auth.
    _pw_cm = async_playwright()
    _pw = await _pw_cm.__aenter__()
    context = None
    browser = None
    browser_mgr = None  # Only set when using the shared browser singleton
    own_context = False

    # Strategy 1: Connect to existing MCP browser via CDP
    cdp_port = await _find_cdp_port()
    if cdp_port:
        try:
            log.info(f"  Connecting to authenticated browser via CDP :{cdp_port}")
            browser = await _pw.chromium.connect_over_cdp(f"http://127.0.0.1:{cdp_port}")
            contexts = browser.contexts
            context = contexts[0] if contexts else None
            if context:
                log.info(f"  Connected to browser with {len(context.pages)} existing pages")
        except Exception as e:
            log.warning(f"  CDP connect failed: {e}")
            browser = None

    # Strategy 2: Try the shared browser manager
    if not context:
        from core.browser import get_browser_manager
        browser_mgr = get_browser_manager()
        if browser_mgr and browser_mgr.context:
            log.info("  Using shared browser instance")
            context = browser_mgr.context
        else:
            browser_mgr = None  # not actually using it

    # Strategy 3: Launch our own browser (last resort)
    if not context:
        log.info(f"  Launching own browser (profile: {user_data_dir})")
        try:
            own_ctx = await _pw.chromium.launch_persistent_context(
                user_data_dir,
                channel="msedge",
                headless=False,
                viewport={"width": 1280, "height": 900},
            )
            context = own_ctx
            own_context = True
        except Exception as e:
            log.warning(f"  Browser launch failed: {e}")
            await _pw_cm.__aexit__(None, None, None)
            return

    page = await context.new_page()

    # Pin the shared browser while we hold a single page for many minutes —
    # otherwise BrowserManager._idle_watcher will close the context out from
    # under us after BROWSER_IDLE_TIMEOUT (120s) of no new_page() calls.
    pin_ctx = browser_mgr.pin_active() if browser_mgr is not None else nullcontext()

    try:
        async with pin_ctx:
            # Navigate to Outlook Calendar
            await navigate_to_outlook_calendar(page)

            # Collect existing file slugs to skip already-collected transcripts
            existing_files = set()
            for f in output_dir.glob("*.txt"):
                parts = f.stem.split("_", 1)
                if len(parts) == 2:
                    existing_files.add(parts[1])
            for f in output_dir.glob("*.md"):
                parts = f.stem.split("_", 1)
                if len(parts) == 2:
                    existing_files.add(parts[1])

            skip_slugs = set(attempted_history.keys()) | existing_files

            # Aggregate skip reasons across all weeks
            total_scanned = 0
            total_skip_attempted = 0
            total_skip_future = 0
            total_skip_no_recap = 0
            total_skip_no_url = 0

            # Counters captured by the per-meeting extract callback. Use a list
            # to allow mutation from the inner closure without nonlocal gymnastics.
            counts = {"collected": 0, "skipped": 0}

            async def _on_meeting_found(meeting: MeetingInfo):
                """Extract + save this transcript NOW, before the next meeting.

                Interleaving discovery and extraction means partial progress
                survives crashes and timeouts — every saved transcript is
                durable on disk before we walk to the next event.
                """
                if counts["collected"] >= max_meetings:
                    return  # cap reached; ignore further finds

                log.info(f"    Extracting: {meeting.title[:60]}...")
                new_page = None
                try:
                    new_page = await context.new_page()
                    transcript = await asyncio.wait_for(
                        extract_transcript_from_sharepoint(new_page, meeting.sharepoint_url),
                        timeout=PER_MEETING_TIMEOUT,
                    )

                    if isinstance(transcript, str) and transcript:
                        date_str = datetime.now().strftime("%Y-%m-%d")
                        filename = f"{date_str}_{meeting.slug}.txt"
                        filepath = output_dir / filename
                        filepath.write_text(transcript, encoding="utf-8")
                        log.info(f"    SAVED: {filename} ({len(transcript)} chars)")
                        counts["collected"] += 1
                        _mark_attempted(attempted_history, meeting.slug)
                    elif transcript is False:
                        # Permanent: no Transcript tab or access denied — won't change.
                        log.info(f"    No transcript available (permanent — marking attempted)")
                        counts["skipped"] += 1
                        _mark_attempted(attempted_history, meeting.slug)
                    else:
                        # None: extraction failed for unknown reason — DON'T mark attempted.
                        # Will be retried on next run.
                        log.info(f"    Extraction returned empty — will retry next run")
                        counts["skipped"] += 1

                except TransientExtractionError as e:
                    log.warning(f"    TRANSIENT: {meeting.title[:40]}: {e}")
                    errors.append(f"{meeting.title[:40]}: {e} (will retry)")
                except asyncio.TimeoutError:
                    log.warning(f"    TIMEOUT: {meeting.title[:40]} exceeded {PER_MEETING_TIMEOUT}s")
                    errors.append(f"{meeting.title[:40]}: timeout")
                except Exception as e:
                    log.warning(f"    ERROR: {meeting.title[:40]}: {e}")
                    errors.append(f"{meeting.title[:40]}: {e}")
                finally:
                    if new_page:
                        try:
                            await new_page.close()
                        except Exception:
                            pass

            async def _on_no_recap(slug: str):
                """Persist no-recap memoization inline — survives mid-run crashes."""
                _mark_no_recap(slug)
                skip_slugs.add(slug)

            async def _scan_week(label: str):
                nonlocal total_scanned, total_skip_attempted, total_skip_future
                nonlocal total_skip_no_recap, total_skip_no_url
                discovery = await discover_meetings_with_recaps(
                    page,
                    skip_slugs,
                    _slugify,
                    on_meeting_found=_on_meeting_found,
                    on_no_recap=_on_no_recap,
                )
                total_scanned += discovery.total_scanned
                total_skip_attempted += discovery.skipped_already_attempted
                total_skip_future += discovery.skipped_future
                total_skip_no_recap += discovery.skipped_no_recap
                total_skip_no_url += discovery.skipped_no_url
                # Belt-and-braces: ensure newly-found meetings are also in
                # skip_slugs so the next week doesn't re-extract a duplicate.
                for m in discovery.meetings:
                    skip_slugs.add(m.slug)

            log.info(f"  --- Current week ---")
            await _diag(page, "week0-current")
            await _scan_week("current")

            for week_num in range(1, lookback_weeks + 1):
                if counts["collected"] >= max_meetings:
                    break

                log.info(f"  --- Week {week_num} of {lookback_weeks} ---")
                await navigate_weeks_back(page, 1)
                await _diag(page, f"week{week_num}-navigated")
                await _scan_week(f"week-{week_num}")

            collected = counts["collected"]
            skipped = counts["skipped"]

    finally:
        # Close the page we created
        try:
            await page.close()
        except Exception:
            pass

        # Only close browser/context if we own it
        if own_context and context:
            try:
                await context.close()
            except Exception:
                pass
        # NOTE: Don't call browser.close() for CDP connections — it kills the
        # browser process, which belongs to the Playwright MCP server.
        # Just disconnect by stopping the Playwright instance.
        try:
            await _pw_cm.__aexit__(None, None, None)
        except Exception:
            pass

    # Summary
    log.info(f"Transcript collection end — collected: {collected}, skipped: {skipped}, errors: {len(errors)}")
    for err in errors:
        log.warning(f"  Transcript error: {err}")

    # Write collection status for downstream consumers (digest, TUI)
    _write_collection_status(
        success=True, collected=collected, skipped=skipped,
        errors=len(errors), error_message=None,
        skip_reasons={
            "already_attempted": total_skip_attempted,
            "future_meeting": total_skip_future,
            "no_recap_button": total_skip_no_recap,
            "no_sharepoint_url": total_skip_no_url,
        },
        total_scanned=total_scanned,
    )


def _write_collection_status(
    success: bool,
    collected: int = 0,
    skipped: int = 0,
    errors: int = 0,
    error_message: str | None = None,
    skip_reasons: dict[str, int] | None = None,
    total_scanned: int = 0,
):
    """Write transcript collection status to a JSON file.

    Downstream consumers (digest pre-processing) read this to surface
    collection failures to the user instead of silently producing
    incomplete digests.

    skip_reasons breakdown:
    - already_attempted: slug in attempted history (collected or permanently failed)
    - future_meeting: meeting date is in the future
    - no_recap_button: no "View recap" button found (not recorded or transcription disabled)
    - no_sharepoint_url: recap clicked but no SharePoint URL extracted
    """
    import json
    status = {
        "timestamp": datetime.now().isoformat(),
        "success": success,
        "total_scanned": total_scanned,
        "collected": collected,
        "skipped": skipped,
        "errors": errors,
        "error_message": error_message,
        "skip_reasons": skip_reasons or {},
    }
    try:
        TRANSCRIPT_STATUS_FILE.write_text(
            json.dumps(status, indent=2), encoding="utf-8",
        )
    except Exception:
        pass  # Non-critical — don't break the pipeline


def write_collection_failure(error_message: str):
    """Write a failure status — called by the runner when collection crashes."""
    _write_collection_status(
        success=False, error_message=error_message,
    )


async def _find_cdp_port() -> int | None:
    """Find the CDP port of an existing authenticated Edge browser.

    Looks for Edge processes with --remote-debugging-port that use the
    mcp-msedge-profile (which has SharePoint auth cookies).
    """
    import subprocess
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" | "
             "Where-Object { $_.CommandLine -like '*remote-debugging-port*' -and $_.CommandLine -like '*mcp-msedge*' } | "
             "Select-Object -ExpandProperty CommandLine"],
            capture_output=True, text=True, timeout=10,
        )
        import re as _re
        match = _re.search(r'remote-debugging-port=(\d+)', result.stdout)
        if match:
            port = int(match.group(1))
            # Verify it's alive
            import socket
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=2):
                    return port
            except (ConnectionRefusedError, OSError, TimeoutError):
                pass
    except Exception:
        pass
    return None
