"""Shared browser manager — single Edge instance for all Playwright consumers.

Lazy lifecycle: the browser starts on first use and auto-stops after an idle
timeout (default 2 minutes). This saves 200-800 MB of RAM when no scans are
running — which is ~95% of the daemon's lifetime.

Resilient startup: tries CDP connect first (reuse surviving browser from a
previous crash), then fresh launch with a dedicated daemon profile.

- Direct Python code (transcripts, inbox scans) uses context/page objects.
- MCP Playwright servers (SDK sessions) connect via --cdp-endpoint.
"""

import asyncio
import logging
import subprocess
import time
from pathlib import Path

from playwright.async_api import async_playwright, Browser, BrowserContext, Page

log = logging.getLogger("pulse")

# Module-level singleton
_manager: "BrowserManager | None" = None

CDP_PORT = 9222
DAEMON_PROFILE = "pulse-daemon-profile"

# Idle timeout: browser shuts down after this many seconds without a new_page() call
BROWSER_IDLE_TIMEOUT = 120  # 2 minutes


def get_browser_manager() -> "BrowserManager | None":
    """Get the shared browser manager, or None if not started."""
    return _manager


async def ensure_browser() -> "BrowserManager | None":
    """Get the shared browser, starting it lazily if needed.

    Returns the BrowserManager if available, None if startup fails.
    All callers that need the browser should use this instead of
    get_browser_manager() to benefit from lazy start.
    """
    global _manager
    if _manager and _manager.is_alive:
        _manager.touch()
        return _manager

    # Start fresh
    mgr = BrowserManager()
    try:
        await mgr.start()
        return mgr
    except Exception as e:
        log.warning(f"Lazy browser start failed: {e}")
        return None


def _default_profile_dir() -> str:
    """Default user-data-dir for the daemon's dedicated Edge profile."""
    return str(Path.home() / "AppData/Local/ms-playwright" / DAEMON_PROFILE)


def _is_cdp_alive(port: int = CDP_PORT) -> bool:
    """Quick check if something is listening on the CDP port."""
    import socket
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except (ConnectionRefusedError, OSError, TimeoutError):
        return False


def _kill_orphan_edge(user_data_dir: str):
    """Kill Edge processes that hold a stale lock on our profile directory.

    Only kills processes whose command line includes our specific profile path,
    not the user's normal Edge browser.
    Uses PowerShell (WMIC is deprecated on Windows 11).
    """
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" | "
             f"Where-Object {{ $_.CommandLine -like '*{DAEMON_PROFILE}*' }} | "
             f"Select-Object -ExpandProperty ProcessId"],
            capture_output=True, text=True, timeout=10,
        )
        for line in result.stdout.strip().splitlines():
            line = line.strip()
            if line.isdigit():
                pid = int(line)
                log.info(f"Killing orphan Edge process {pid} (held {DAEMON_PROFILE} profile)")
                subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                               capture_output=True, timeout=5)
    except Exception as e:
        log.debug(f"Orphan cleanup failed (non-fatal): {e}")


class BrowserManager:
    """Single shared Edge browser instance for all Playwright consumers.

    Startup strategy:
    1. Check if CDP port is already responding (previous daemon's browser survived)
       → connect via CDP, reuse it
    2. If not, launch fresh Edge with dedicated profile + remote debugging port
    3. If launch fails (profile locked), kill orphan Edge processes and retry once
    """

    def __init__(self, user_data_dir: str | None = None):
        self._user_data_dir = user_data_dir or _default_profile_dir()
        self._playwright = None
        self._context: BrowserContext | None = None
        self._browser: Browser | None = None
        self._connected_via_cdp = False
        self._last_used: float = time.monotonic()
        self._idle_task: asyncio.Task | None = None

    def touch(self):
        """Update last-used timestamp — resets the idle shutdown timer."""
        self._last_used = time.monotonic()

    @property
    def idle_seconds(self) -> float:
        """Seconds since the browser was last used (new_page or touch)."""
        return time.monotonic() - self._last_used

    @property
    def cdp_endpoint(self) -> str:
        """CDP endpoint URL for MCP Playwright server to connect to."""
        return f"http://127.0.0.1:{CDP_PORT}"

    @property
    def context(self) -> BrowserContext | None:
        return self._context

    async def start(self):
        """Start the shared browser — connect to existing or launch new."""
        global _manager

        self._last_used = time.monotonic()
        self._playwright = await async_playwright().__aenter__()

        # Strategy 1: Reuse a surviving browser from a previous daemon run
        if _is_cdp_alive():
            try:
                self._browser = await self._playwright.chromium.connect_over_cdp(
                    f"http://127.0.0.1:{CDP_PORT}"
                )
                contexts = self._browser.contexts
                self._context = contexts[0] if contexts else await self._browser.new_context(
                    viewport={"width": 1280, "height": 900}
                )
                self._connected_via_cdp = True
                _manager = self
                self._start_idle_watcher()
                log.info(f"Connected to existing browser via CDP :{CDP_PORT}")
                return
            except Exception as e:
                log.debug(f"CDP connect failed: {e} — will launch fresh")
                self._browser = None

        # Strategy 2: Launch fresh Edge with dedicated daemon profile
        await self._launch_fresh()
        _manager = self
        self._start_idle_watcher()

    async def _launch_fresh(self, retry: bool = True):
        """Launch Edge with persistent profile. On lock failure, kill orphans and retry once."""
        try:
            self._context = await self._playwright.chromium.launch_persistent_context(
                self._user_data_dir,
                channel="msedge",
                headless=True,
                viewport={"width": 1280, "height": 900},
                args=[f"--remote-debugging-port={CDP_PORT}"],
            )
            self._connected_via_cdp = False

            # Close any restored pages from a previous session
            for page in self._context.pages[1:]:
                await page.close()

            log.info(f"Shared browser started (profile: {self._user_data_dir}, CDP: :{CDP_PORT})")

        except Exception as e:
            if not retry:
                raise

            log.warning(f"Browser launch failed: {e} — attempting orphan cleanup")
            _kill_orphan_edge(self._user_data_dir)
            await asyncio.sleep(2)  # Give OS time to release locks
            await self._launch_fresh(retry=False)

    @property
    def is_alive(self) -> bool:
        """Check if the browser context is still responsive.

        Returns False if the browser crashed or the CDP connection was lost.
        Callers should return None (scan unavailable) when this is False.
        """
        if not self._context:
            return False
        try:
            # Accessing pages on a dead context raises
            _ = self._context.pages
            return True
        except Exception:
            return False

    async def new_page(self) -> Page:
        """Create a new page in the shared browser context."""
        if not self._context:
            raise RuntimeError("Browser not started")
        self.touch()
        return await self._context.new_page()

    def _start_idle_watcher(self):
        """Start background task that auto-stops the browser after idle timeout."""
        if self._idle_task and not self._idle_task.done():
            return  # already watching
        try:
            loop = asyncio.get_running_loop()
            self._idle_task = loop.create_task(self._idle_watcher())
        except RuntimeError:
            pass  # no event loop — tests or CLI mode

    async def _idle_watcher(self):
        """Background task: check every 30s if browser has been idle too long."""
        try:
            while True:
                await asyncio.sleep(30)
                if not self.is_alive:
                    log.debug("Idle watcher: browser already dead, exiting")
                    return
                idle = self.idle_seconds
                if idle >= BROWSER_IDLE_TIMEOUT:
                    log.info(f"Browser idle for {idle:.0f}s — auto-stopping to free memory")
                    await self.stop()
                    return
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.debug(f"Idle watcher error (non-fatal): {e}")

    async def stop(self):
        """Close the browser cleanly."""
        global _manager

        # Cancel idle watcher first
        if self._idle_task and not self._idle_task.done():
            self._idle_task.cancel()
            self._idle_task = None

        if self._connected_via_cdp and self._browser:
            # Don't close the browser itself — just disconnect
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None
            self._context = None
        elif self._context:
            try:
                await self._context.close()
            except Exception as e:
                log.warning(f"Browser close error (non-fatal): {e}")
            self._context = None

        if self._playwright:
            try:
                await self._playwright.__aexit__(None, None, None)
            except Exception:
                pass
            self._playwright = None

        _manager = None
        log.info("Shared browser stopped")
