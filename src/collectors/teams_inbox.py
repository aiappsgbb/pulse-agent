"""Scan Teams chat list for unread messages via Playwright.

Deterministic script — no LLM involved. Uses the shared browser.
Returns structured data that gets injected into the monitor trigger prompt.

DOM structure (verified Feb 2026):
- Chat tree: [role="tree"] contains all chat categories + conversations
- Categories (level 1): Copilot, Discover, Mentions, Saved, Important, Regular, Chats
- Chat items (level 2): [role="treeitem"][data-item-type="chat"] with data-testid="list-item"
- Unread indicator: badge elements ([class*="Badge"]) inside the treeitem
- Text format: "chat name\\ntime\\nsender: preview" (newline-separated)
"""

import re
from datetime import datetime

from core.logging import log, safe_encode


# JS snippet to extract chat items from Teams tree view
EXTRACT_CHAT_LIST_JS = """
() => {
    const results = [];

    // Teams 2026 UI: chats are treeitem elements with data-item-type="chat"
    const chatItems = document.querySelectorAll(
        '[role="treeitem"][data-item-type="chat"]'
    );

    for (const item of chatItems) {
        const text = item.innerText || '';
        if (!text.trim()) continue;

        // Unread: badge elements inside the treeitem
        const hasBadge = !!(
            item.querySelector('[class*="Badge"], [class*="badge"]') ||
            item.querySelector('[class*="unread"], [class*="Unread"]') ||
            item.getAttribute('aria-label')?.toLowerCase().includes('unread')
        );

        // Parse text: lines are name, time, sender: preview (varies)
        const lines = text.split('\\n').map(l => l.trim()).filter(Boolean);

        // Extract name (first line), time, and preview
        let name = lines[0] || '';
        let time = '';
        let preview = '';

        // Look for time pattern (e.g. "1:53 PM", "11:35 AM", "Yesterday")
        for (let i = 1; i < lines.length; i++) {
            if (/^\\d{1,2}:\\d{2}\\s*(AM|PM)$/i.test(lines[i]) ||
                /^(Yesterday|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)/i.test(lines[i]) ||
                /^\\d{1,2}\\/\\d{1,2}\\/\\d{2,4}/.test(lines[i])) {
                time = lines[i];
            } else if (lines[i].includes(':') && !time) {
                // Could be "sender: message" - check if previous line was time
                preview = lines[i];
            } else if (i === lines.length - 1 && !time) {
                time = lines[i];
            } else if (!preview) {
                preview = lines[i];
            }
        }

        // If preview still empty, try last non-time line
        if (!preview && lines.length > 2) {
            preview = lines[lines.length - 1];
            if (preview === time) preview = lines.length > 3 ? lines[lines.length - 2] : '';
        }

        results.push({
            name: name.substring(0, 200),
            preview: preview.substring(0, 300),
            time: time,
            unread: hasBadge,
            raw: text.substring(0, 300),
        });
    }
    return results;
}
"""

# Fallback: raw text from the tree
EXTRACT_CHAT_PANE_TEXT_JS = """
() => {
    const tree = document.querySelector('[role="tree"]');
    if (tree) return tree.innerText.substring(0, 5000);

    const nav = document.querySelector('[role="navigation"]');
    if (nav) return nav.innerText.substring(0, 5000);

    return '';
}
"""


async def scan_teams_inbox(config: dict) -> list[dict] | None:
    """Scan Teams for unread chat messages using the shared browser.

    Returns a list of dicts: [{name, preview, time, unread, raw}, ...]
    Only returns items with unread=True.
    Returns None if the browser is unavailable (distinct from [] = scanned, nothing found).
    """
    from core.browser import get_browser_manager

    browser_mgr = get_browser_manager()
    if not browser_mgr or not browser_mgr.context:
        log.warning("Teams inbox scan skipped — no shared browser available")
        return None

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
    try:
        await page.wait_for_load_state("networkidle", timeout=12000)
    except Exception:
        pass

    # Wait for the chat tree to render (treeitems with chat data)
    try:
        await page.wait_for_selector(
            '[role="treeitem"][data-item-type="chat"]',
            timeout=10000,
        )
        await page.wait_for_timeout(2000)
    except Exception:
        log.warning("  Chat tree items not found — Teams may still be loading")
        await page.wait_for_timeout(3000)

    # Try structured extraction
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
        return [{
            "name": "Teams Chat Pane (raw)",
            "preview": raw_text[:3000],
            "time": "",
            "unread": True,
            "raw": raw_text[:3000],
        }]

    log.warning("  No chat data extracted — Teams may not have loaded")
    return []


def format_inbox_for_prompt(items: list[dict] | None) -> str:
    """Format scanned inbox items as text for injection into monitor prompt."""
    if items is None:
        return (
            "**SCAN UNAVAILABLE** — Browser was not running. Cannot determine "
            "Teams unread status. DO NOT assume zero unread."
        )
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
