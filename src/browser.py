"""Shared browser manager — single Edge instance for all Playwright consumers.

Avoids user-data-dir profile locking by launching ONE browser with
--remote-debugging-port, then sharing it via CDP endpoint.

- Direct Python code (transcripts.py) uses the context/page objects directly.
- MCP Playwright servers (SDK sessions) connect via --cdp-endpoint.
"""

import asyncio
import logging
from pathlib import Path

from playwright.async_api import async_playwright, BrowserContext, Page

log = logging.getLogger("pulse")

# Module-level singleton
_manager: "BrowserManager | None" = None

CDP_PORT = 9222


def get_browser_manager() -> "BrowserManager | None":
    """Get the shared browser manager, or None if not started."""
    return _manager


class BrowserManager:
    """Single shared Edge browser instance for all Playwright consumers."""

    def __init__(self, user_data_dir: str, headless: bool = True):
        self._user_data_dir = user_data_dir
        self._headless = headless
        self._playwright = None
        self._context: BrowserContext | None = None

    @property
    def cdp_endpoint(self) -> str:
        """CDP endpoint URL for MCP Playwright server to connect to."""
        return f"http://localhost:{CDP_PORT}"

    @property
    def context(self) -> BrowserContext | None:
        return self._context

    async def start(self):
        """Launch Edge with persistent profile + remote debugging port."""
        global _manager

        self._playwright = await async_playwright().__aenter__()
        self._context = await self._playwright.chromium.launch_persistent_context(
            self._user_data_dir,
            channel="msedge",
            headless=self._headless,
            viewport={"width": 1280, "height": 900},
            args=[f"--remote-debugging-port={CDP_PORT}"],
        )

        # Close any restored pages from a previous session
        for page in self._context.pages[1:]:
            await page.close()

        _manager = self
        log.info(f"Shared browser started (profile: {self._user_data_dir}, CDP: :{CDP_PORT})")

    async def new_page(self) -> Page:
        """Create a new page in the shared browser context."""
        if not self._context:
            raise RuntimeError("Browser not started")
        return await self._context.new_page()

    async def stop(self):
        """Close the browser cleanly."""
        global _manager

        if self._context:
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
