"""Scan Teams chat list for unread messages via Playwright.

Deterministic script — no LLM involved. Uses the shared browser.
Returns structured data that gets injected into the monitor trigger prompt.
"""

import asyncio
from datetime import datetime

from core.logging import log, safe_encode


# JS snippet to extract chat list items from Teams
# Teams renders chat items as list items with unread indicators (bold text, badge counts)
EXTRACT_CHAT_LIST_JS = """
() => {
    const results = [];

    // Teams new UI: chat list items are in [role="listitem"] or similar containers
    // Look for the chat list panel first
    const chatItems = document.querySelectorAll(
        '[data-tid="chat-list-item"], [role="listitem"][data-is-focusable="true"]'
    );

    for (const item of chatItems) {
        const text = item.innerText || '';
        if (!text.trim()) continue;

        // Check for unread indicator: bold class, unread badge, or data attribute
        const isUnread = !!(
            item.querySelector('[class*="unread"], [class*="Unread"], [data-testid*="unread"]') ||
            item.querySelector('.fui-Badge') ||
            item.getAttribute('aria-label')?.includes('unread') ||
            item.classList.toString().includes('unread')
        );

        // Extract the text lines — typically: name, last message preview, time
        const lines = text.split('\\n').map(l => l.trim()).filter(Boolean);

        if (lines.length >= 1) {
            results.push({
                name: lines[0] || '',
                preview: lines.length > 1 ? lines.slice(1, -1).join(' ') : '',
                time: lines.length > 1 ? lines[lines.length - 1] : '',
                unread: isUnread,
                raw: text.substring(0, 200),
            });
        }
    }
    return results;
}
"""

# Fallback: just get all visible text from the chat pane
EXTRACT_CHAT_PANE_TEXT_JS = """
() => {
    // Try to find the chat list container
    const pane = document.querySelector('[role="tree"], [data-tid="chat-pane"], [aria-label*="Chat"]');
    if (pane) return pane.innerText.substring(0, 5000);

    // Broader fallback: get the left panel text
    const nav = document.querySelector('nav, [role="navigation"]');
    if (nav) return nav.innerText.substring(0, 5000);

    return '';
}
"""


async def scan_teams_inbox(config: dict) -> list[dict]:
    """Scan Teams for unread chat messages using the shared browser.

    Returns a list of dicts: [{name, preview, time, unread, raw}, ...]
    Only returns items with unread=True.
    """
    from core.browser import get_browser_manager

    browser_mgr = get_browser_manager()
    if not browser_mgr or not browser_mgr.context:
        log.warning("Teams inbox scan skipped — no shared browser available")
        return []

    page = None
    try:
        page = await browser_mgr.new_page()
        return await _do_scan(page)
    except Exception as e:
        log.error(f"Teams inbox scan failed: {e}")
        return []
    finally:
        if page:
            try:
                await page.close()
            except Exception:
                pass


async def _do_scan(page) -> list[dict]:
    """Navigate to Teams Chat and extract unread messages."""
    log.info("Scanning Teams inbox for unread messages...")

    # Navigate to Teams Chat view
    await page.goto("https://teams.microsoft.com/v2/", wait_until="domcontentloaded")
    await page.wait_for_timeout(3000)

    # Click Chat in the left sidebar
    try:
        chat_btn = page.get_by_role("button", name="Chat")
        await chat_btn.click()
        await page.wait_for_timeout(2000)
    except Exception:
        log.warning("Could not click Chat button — may already be on chat view")

    # Wait for chat list to render
    await page.wait_for_timeout(2000)

    # Try structured extraction first
    items = await page.evaluate(EXTRACT_CHAT_LIST_JS)

    if items:
        unread = [i for i in items if i.get("unread")]
        log.info(f"  Found {len(items)} chats, {len(unread)} unread")
        for u in unread[:10]:
            log.info(f"    - {safe_encode(u.get('name', '?'))}: {safe_encode(u.get('preview', '')[:60])}")
        return unread

    # Fallback: extract raw text from the chat pane
    log.info("  Structured extraction found nothing — trying text fallback")
    raw_text = await page.evaluate(EXTRACT_CHAT_PANE_TEXT_JS)

    if raw_text:
        log.info(f"  Got {len(raw_text)} chars of chat pane text")
        # Return as a single "raw" item for the LLM to parse
        return [{
            "name": "Teams Chat Pane (raw)",
            "preview": raw_text[:3000],
            "time": "",
            "unread": True,
            "raw": raw_text[:3000],
        }]

    log.warning("  No chat data extracted — Teams may not have loaded")
    return []


def format_inbox_for_prompt(items: list[dict]) -> str:
    """Format scanned inbox items as text for injection into monitor prompt."""
    if not items:
        return "No unread Teams messages detected."

    # Check if we got raw fallback
    if len(items) == 1 and items[0]["name"] == "Teams Chat Pane (raw)":
        return (
            "## Teams Inbox (raw scan — parse carefully)\n\n"
            "The structured scanner couldn't identify individual chats. "
            "Here is the raw text from the Teams chat pane. "
            "Look for names, message previews, and any unread indicators:\n\n"
            f"```\n{items[0]['preview']}\n```"
        )

    lines = [f"## Teams Inbox — {len(items)} Unread Messages\n"]
    for i, item in enumerate(items, 1):
        name = item.get("name", "Unknown")
        preview = item.get("preview", "")
        time = item.get("time", "")
        time_str = f" ({time})" if time else ""
        lines.append(f"{i}. **{name}**{time_str}: {preview}")

    lines.append("\nFor each unread message above, determine if the sender is waiting for MY reply.")
    return "\n".join(lines)
