"""Tests for BrowserManager pin_active + idle watcher interaction.

Regression: the transcript collector held a single page for many minutes
during meeting discovery. Without pin_active(), the idle watcher closed
the browser context after BROWSER_IDLE_TIMEOUT (120s) and the collector
silently burned the rest of its 30-minute budget on a dead context.
"""

import asyncio
import time
from pathlib import Path
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from core import browser as browser_mod
from core.browser import BrowserManager, BROWSER_IDLE_TIMEOUT


class TestPinActive:
    """pin_active() is the contract long-running consumers use to say
    'I'm busy on one page — don't kill the browser out from under me.'
    """

    async def test_pin_count_increments_and_decrements(self):
        m = BrowserManager()
        assert m._pin_count == 0
        async with m.pin_active():
            assert m._pin_count == 1
        assert m._pin_count == 0

    async def test_pin_active_nests(self):
        m = BrowserManager()
        async with m.pin_active():
            async with m.pin_active():
                assert m._pin_count == 2
            assert m._pin_count == 1
        assert m._pin_count == 0

    async def test_pin_count_decrements_on_exception(self):
        m = BrowserManager()
        with pytest.raises(RuntimeError):
            async with m.pin_active():
                assert m._pin_count == 1
                raise RuntimeError("boom")
        assert m._pin_count == 0

    async def test_pin_active_refreshes_last_used(self):
        m = BrowserManager()
        m._last_used = time.monotonic() - 9999  # very stale
        async with m.pin_active():
            # Entering pin should touch
            assert m.idle_seconds < 1
        # Exiting pin should also touch (so the timer starts fresh)
        assert m.idle_seconds < 1


class TestIdleWatcherRespectsPin:
    """The idle watcher must NOT stop the browser while a pin is held —
    that's the whole point of pin_active().
    """

    async def test_pinned_browser_is_not_stopped_when_idle_seconds_exceed_timeout(self, monkeypatch):
        """Simulates the bug we fixed: collector pins the browser, ages out
        idle, watcher tick fires — browser must survive.
        """
        m = BrowserManager()

        # Stub stop() so we don't actually need a real context — just observe
        # whether the watcher TRIES to stop us.
        stop_called = False

        async def fake_stop():
            nonlocal stop_called
            stop_called = True

        # Pretend we have a live context for is_alive
        class _FakeCtx:
            pages = []

        m._context = _FakeCtx()
        m.stop = fake_stop  # type: ignore[assignment]

        # Force "idle" past the timeout
        m._last_used = time.monotonic() - (BROWSER_IDLE_TIMEOUT + 60)

        # Hold a pin and run one watcher cycle inline by replicating its check
        async with m.pin_active():
            # Mimic what _idle_watcher does on each 30s tick
            assert m.is_alive
            if m._pin_count > 0:
                m.touch()
                # Should NOT call stop
            else:
                if m.idle_seconds >= BROWSER_IDLE_TIMEOUT:
                    await m.stop()

            assert stop_called is False
            # Pin reset the clock
            assert m.idle_seconds < 1

    async def test_unpinned_idle_browser_is_stopped(self):
        """Sanity check the inverse: with no pin and stale timer, watcher
        WILL stop. Confirms pin_active is what protects, not just luck.
        """
        m = BrowserManager()

        stop_called = False

        async def fake_stop():
            nonlocal stop_called
            stop_called = True

        class _FakeCtx:
            pages = []

        m._context = _FakeCtx()
        m.stop = fake_stop  # type: ignore[assignment]
        m._last_used = time.monotonic() - (BROWSER_IDLE_TIMEOUT + 60)

        # No pin held.
        if m._pin_count > 0:
            m.touch()
        else:
            if m.idle_seconds >= BROWSER_IDLE_TIMEOUT:
                await m.stop()

        assert stop_called is True


class TestIdleWatcherIntegration:
    """End-to-end: real _idle_watcher coroutine, real pin_active, fake context.

    These tests run the actual idle watcher loop with a tightened sleep
    interval, then assert observable behaviour (was stop() called, is the
    browser still alive at the end). The original bug — discovery dies at
    07:14:34 with 'Browser idle for 125s — auto-stopping' — would fail
    this exact test.
    """

    async def _start_with_fake_ctx(self, monkeypatch, idle_timeout=1, watcher_sleep=0.05):
        """Build a manager whose idle watcher runs aggressively with a fake context."""
        monkeypatch.setattr(browser_mod, "BROWSER_IDLE_TIMEOUT", idle_timeout)

        # Patch the watcher's sleep to a small interval so 'idle for N seconds'
        # is reachable in test time. Wrap real asyncio.sleep so cancellation
        # works (cancelling during a 30s sleep would otherwise hang the test).
        real_sleep = asyncio.sleep

        async def fast_sleep(secs):
            # Inside the watcher, the only sleep is the 30s tick — shrink it.
            # Other callers (rare here) still get real durations.
            await real_sleep(watcher_sleep if secs >= 1 else secs)

        monkeypatch.setattr(asyncio, "sleep", fast_sleep)

        m = BrowserManager()
        # Fake the launched context so we don't actually start Chromium.
        ctx = MagicMock()
        ctx.pages = []
        ctx.close = AsyncMock()
        m._context = ctx
        # Stop() drops the context and cancels the watcher — keep it real
        # (it's the function under test).
        m._start_idle_watcher()
        return m

    async def test_unpinned_browser_actually_dies_when_watcher_runs(self, monkeypatch):
        """If pin_active is NOT used, watcher kills the browser. This is the
        bug we observed in production at 07:14:34 on Apr 28."""
        m = await self._start_with_fake_ctx(monkeypatch, idle_timeout=1, watcher_sleep=0.05)
        try:
            # Force idle past timeout, then let the watcher tick a few times.
            m._last_used = time.monotonic() - 5  # 5s idle, timeout is 1s
            await asyncio.sleep(0.5)  # multiple watcher iterations
            assert m.is_alive is False, "Watcher should have killed unpinned idle browser"
        finally:
            if m._idle_task and not m._idle_task.done():
                m._idle_task.cancel()

    async def test_pinned_browser_survives_idle_timeout(self, monkeypatch):
        """The fix in action: with pin_active held, watcher must NOT kill,
        even with idle_seconds far exceeding the timeout."""
        m = await self._start_with_fake_ctx(monkeypatch, idle_timeout=1, watcher_sleep=0.05)
        try:
            m._last_used = time.monotonic() - 5

            async with m.pin_active():
                # Simulate the collector working for a while WITHOUT calling
                # new_page() (the original bug — discovery uses raw context.new_page).
                await asyncio.sleep(0.5)  # multiple watcher iterations
                assert m.is_alive, "Pinned browser must survive idle window"

            # After releasing the pin, the timer is reset by pin_active's __aexit__
            # (touch on exit), so it should still be alive immediately.
            assert m.is_alive
        finally:
            if m._idle_task and not m._idle_task.done():
                m._idle_task.cancel()


class TestCollectorWiringPinsBrowser:
    """Verifies that run_transcript_collection ACTUALLY calls pin_active on
    the shared BrowserManager when one is available.

    This is the missing wiring test — without it, removing the pin_active
    call from collector.py would compile fine and silently regress.
    """

    async def test_run_transcript_collection_enters_pin_active(self, tmp_path, monkeypatch):
        from collectors.transcripts import collector as collector_mod

        # Track whether pin_active was entered.
        pin_entered = {"value": False, "exited": False}

        class _PinObserver:
            async def __aenter__(self):
                pin_entered["value"] = True
                return self

            async def __aexit__(self, *exc):
                pin_entered["exited"] = True
                return False

        # Build a mock BrowserManager that returns our observer from pin_active().
        fake_mgr = MagicMock()
        fake_ctx = MagicMock()
        fake_ctx.new_page = AsyncMock()
        fake_mgr.context = fake_ctx
        fake_mgr.pin_active = MagicMock(return_value=_PinObserver())

        # Make the collector pick Strategy 2 (shared manager): no CDP, no fallback.
        async def no_cdp():
            return None

        monkeypatch.setattr(collector_mod, "_find_cdp_port", no_cdp)
        monkeypatch.setattr(collector_mod, "get_browser_manager", lambda: fake_mgr,
                            raising=False)
        # The collector imports get_browser_manager inside the function via
        # `from core.browser import get_browser_manager` — patch that path too.
        monkeypatch.setattr("core.browser.get_browser_manager", lambda: fake_mgr)

        # Stub Playwright so we don't launch Chromium. The async_playwright()
        # context manager just needs an __aenter__ / __aexit__.
        class _FakePW:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *exc):
                return False
            chromium = MagicMock()
        monkeypatch.setattr(collector_mod, "async_playwright", _FakePW)

        # Stub navigation so discovery exits immediately (zero meetings).
        async def fake_nav(page):
            return None

        from collectors.transcripts.navigation import DiscoveryResult

        async def fake_discover(*args, **kwargs):
            return DiscoveryResult(meetings=[], total_scanned=0)

        monkeypatch.setattr(collector_mod, "navigate_to_outlook_calendar", fake_nav)
        monkeypatch.setattr(collector_mod, "navigate_weeks_back",
                            AsyncMock(return_value=None))
        monkeypatch.setattr(collector_mod, "discover_meetings_with_recaps", fake_discover)

        # Point state file at tmp_path so we don't pollute the user's PULSE_HOME.
        monkeypatch.setattr(collector_mod, "TRANSCRIPT_STATE_FILE",
                            tmp_path / ".transcript-state.json")

        # Make new_page return a mock page with the methods discovery would call.
        page_mock = AsyncMock()
        page_mock.close = AsyncMock()
        fake_ctx.new_page = AsyncMock(return_value=page_mock)

        config = {
            "transcripts": {
                "max_per_run": 1,
                "lookback_weeks": 0,
                "output_dir": str(tmp_path / "transcripts"),
            }
        }

        # Suppress diagnostic screenshot helper (needs a real page).
        monkeypatch.setattr(collector_mod, "_diag", AsyncMock())

        await collector_mod.run_transcript_collection(client=None, config=config)

        assert pin_entered["value"], (
            "run_transcript_collection must enter pin_active() when a shared "
            "BrowserManager is available — without it, the idle watchdog will "
            "kill the browser mid-collection (production bug at 2026-04-28 07:14:34)."
        )
        assert pin_entered["exited"], "pin_active context must exit cleanly"
        # Confirm it was actually a call on the shared manager, not some other one.
        fake_mgr.pin_active.assert_called()
