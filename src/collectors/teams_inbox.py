"""Scan Teams chat list for unread messages via Playwright.

Deterministic script — no LLM involved. Uses the shared browser.
Returns structured data that gets injected into the monitor trigger prompt.

DOM structure (verified Feb 24 2026):
- Chat tree: [role="tree"] contains categories and conversations
- Categories (level 1): Copilot, Quick views, Important, Regular, GBB Team, Chats, etc.
  User-defined categories — emoji-prefixed, may have "Unread" prefix
- Chat items (level 2): [role="treeitem"] inside [role="group"] under expanded categories
  Accessible name format: "[Unread message] [Chat type] TITLE Last message SENDER: PREVIEW TIME"
  Chat types: "Meeting chat", "Group chat", "Chat"
- Unread indicator: "Unread" prefix in treeitem accessible name (both category and chat level)
- Filter bar: "Unread (Ctrl+Alt+U)" button filters to unread-only view
"""

import re
from datetime import datetime

from core.logging import log, safe_encode


# JS snippet to extract chat items from Teams tree view (Feb 2026 DOM structure)
# NOTE: treeitems do NOT have aria-label attributes — use innerText instead.
# Level 2 treeitems with a <time> element are individual chats.
# innerText format: "Unread\\nTITLE\\nTIME\\nSENDER: PREVIEW" (newline-separated)
EXTRACT_CHAT_LIST_JS = """
() => {
    const results = [];
    const tree = document.querySelector('[role="tree"]');
    if (!tree) return results;

    const allItems = tree.querySelectorAll('[role="treeitem"]');

    for (const item of allItems) {
        // Individual chats are level 2 with a <time> element
        const level = item.getAttribute('aria-level');
        const timeEl = item.querySelector('time');
        if (level !== '2' || !timeEl) continue;

        const text = item.innerText || '';
        const lines = text.split('\\n').map(l => l.trim()).filter(Boolean);
        if (!lines.length) continue;

        // Skip utility items like "New message" or "See more"
        if (lines[0] === 'New message' || lines[0] === 'See more') continue;

        const time = timeEl ? timeEl.textContent.trim() : '';

        // Determine unread: first line is literally "Unread"
        let isUnread = false;
        let startIdx = 0;
        if (lines[0] === 'Unread') {
            isUnread = true;
            startIdx = 1;
        }

        // Chat name is the first real content line (after optional "Unread")
        const name = lines[startIdx] || '';

        // Preview is the last line (sender: message), skip if it equals time
        let preview = '';
        for (let i = lines.length - 1; i > startIdx; i--) {
            if (lines[i] !== time) {
                preview = lines[i];
                break;
            }
        }

        results.push({
            name: name.substring(0, 200),
            preview: preview.substring(0, 300),
            time: time,
            unread: isUnread,
            raw: text.substring(0, 400),
        });
    }
    return results;
}
"""

# JS to expand all collapsed chat categories so level 2 items become visible.
# Skips "Teams and channels" and "Communities" (not chat categories).
EXPAND_UNREAD_CATEGORIES_JS = """
() => {
    const tree = document.querySelector('[role="tree"]');
    if (!tree) return 0;
    let expanded = 0;
    const SKIP = ['teams and channels', 'communities', 'copilot', 'quick views'];
    const categories = tree.querySelectorAll(':scope > [role="treeitem"]');
    for (const cat of categories) {
        const name = (cat.innerText || '').split('\\n')[0].trim().toLowerCase();
        const isExpanded = cat.getAttribute('aria-expanded');
        if (isExpanded === 'false' && !SKIP.some(s => name.includes(s))) {
            cat.click();
            expanded++;
        }
    }
    return expanded;
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
    from core.browser import ensure_browser

    browser_mgr = await ensure_browser()
    if not browser_mgr:
        log.warning("Teams inbox scan skipped — no shared browser available")
        return None

    page = None
    try:
        page = await browser_mgr.new_page()
        return await _do_scan(page)
    except Exception as e:
        log.error(f"Teams inbox scan failed: {e}")
        return None
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
    await page.goto("https://teams.cloud.microsoft/", wait_until="domcontentloaded")
    try:
        await page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass

    # Check for auth redirect — session may have expired
    url = page.url.lower()
    if "login" in url or "oauth" in url or "microsoftonline" in url:
        log.error("  Teams session expired — redirected to login page")
        return None

    # Wait for the chat tree to render
    try:
        await page.wait_for_selector('[role="tree"]', timeout=15000)
        # Extra wait for treeitems to populate inside the tree
        await page.wait_for_selector(
            '[role="tree"] [role="treeitem"]',
            timeout=10000,
        )
        await page.wait_for_timeout(2000)
    except Exception:
        log.warning("  Chat tree not found — Teams may still be loading")
        await page.wait_for_timeout(5000)

    # Expand all collapsed categories that have unread items
    # (individual chats are level 2 treeitems inside category groups)
    try:
        expanded_count = await page.evaluate(EXPAND_UNREAD_CATEGORIES_JS)
        if expanded_count:
            log.info(f"  Expanded {expanded_count} chat categories with unread items")
            await page.wait_for_timeout(2000)
    except Exception as e:
        log.warning(f"  Failed to expand categories: {e}")

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
