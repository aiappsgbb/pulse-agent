"""Mark Teams chats as read via Playwright browser automation.

Deterministic script — no LLM involved. Uses the shared browser.
Simply clicking on a chat treeitem in the Teams sidebar marks it as read.

Reuses the same DOM structure as teams_inbox.py (Feb 2026):
- Chat tree: [role="tree"] > [role="treeitem"][aria-level="2"] with <time> elements
- Unread: "Unread" prefix in treeitem innerText first line
- Clicking an unread treeitem marks the conversation as read.
"""

from core.logging import log, safe_encode


# JS: Find and click an unread chat by name to mark it as read.
# Returns {found, clicked, name} or {found: false}.
CLICK_UNREAD_CHAT_JS = """
(chatName) => {
    const lower = chatName.toLowerCase().trim();
    const tree = document.querySelector('[role="tree"]');
    if (!tree) return { found: false, reason: 'no tree' };

    const items = tree.querySelectorAll('[role="treeitem"]');
    for (const item of items) {
        const level = item.getAttribute('aria-level');
        const timeEl = item.querySelector('time');
        if (level !== '2' || !timeEl) continue;

        const text = item.innerText || '';
        const lines = text.split('\\n').map(l => l.trim()).filter(Boolean);
        if (!lines.length) continue;

        // Only target unread items
        if (lines[0] !== 'Unread') continue;

        // Chat name is lines[1] (after "Unread")
        const name = (lines[1] || '').toLowerCase();

        // Word-boundary match (same logic as teams_sender FIND_CHAT_IN_SIDEBAR_JS)
        if (name === lower || name.startsWith(lower + ' ') ||
            name.endsWith(' ' + lower) || name.includes(' ' + lower + ' ')) {
            item.click();
            return { found: true, clicked: true, name: (lines[1] || '').substring(0, 200) };
        }
    }
    return { found: false, reason: 'not found in unread items' };
}
"""

# JS: Click ALL unread chats at once — returns a list of names.
# Used for full sweep (mark everything as read).
CLICK_ALL_UNREAD_CHATS_JS = """
() => {
    const tree = document.querySelector('[role="tree"]');
    if (!tree) return [];

    const items = tree.querySelectorAll('[role="treeitem"]');
    const clicked = [];

    for (const item of items) {
        const level = item.getAttribute('aria-level');
        const timeEl = item.querySelector('time');
        if (level !== '2' || !timeEl) continue;

        const text = item.innerText || '';
        const lines = text.split('\\n').map(l => l.trim()).filter(Boolean);
        if (!lines.length || lines[0] !== 'Unread') continue;

        const name = lines[1] || '(unknown)';
        clicked.push(name.substring(0, 200));
    }
    return clicked;
}
"""

# JS: reuse from teams_inbox.py — expand collapsed categories
EXPAND_CATEGORIES_JS = """
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


async def mark_teams_chats_read(
    chat_names: list[str] | None = None,
) -> dict:
    """Mark Teams chats as read by clicking on them in the sidebar.

    Args:
        chat_names: Specific chat names to mark as read.
                    If None or empty, marks ALL unread chats as read.

    Returns:
        {success: bool, marked: int, failed: int, total_unread: int, details: list[str]}
    """
    from core.browser import browser_page

    _fail = {"success": False, "marked": 0, "failed": 0,
             "total_unread": 0}
    async with browser_page() as page:
        if page is None:
            return {**_fail, "details": ["No shared browser available"]}
        try:
            return await _do_mark_read(page, chat_names)
        except Exception as e:
            log.error(f"Teams mark-as-read failed: {e}")
            return {**_fail, "details": [str(e)]}


async def _do_mark_read(page, chat_names: list[str] | None) -> dict:
    """Navigate to Teams and click on unread chats."""
    log.info("Marking Teams chats as read...")

    # Navigate to Teams Chat view
    await page.goto("https://teams.cloud.microsoft/", wait_until="domcontentloaded")
    try:
        await page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass

    # Check for auth redirect
    url = page.url.lower()
    if "login" in url or "oauth" in url or "microsoftonline" in url:
        log.error("  Teams session expired — redirected to login page")
        return {
            "success": False, "marked": 0, "failed": 0,
            "total_unread": 0, "details": ["Teams session expired"],
        }

    # Wait for chat tree
    try:
        await page.wait_for_selector('[role="tree"]', timeout=15000)
        await page.wait_for_selector(
            '[role="tree"] [role="treeitem"]', timeout=10000,
        )
        await page.wait_for_timeout(2000)
    except Exception:
        log.warning("  Chat tree not found — Teams may still be loading")
        await page.wait_for_timeout(5000)

    # Expand collapsed categories
    try:
        expanded = await page.evaluate(EXPAND_CATEGORIES_JS)
        if expanded:
            log.info(f"  Expanded {expanded} categories")
            await page.wait_for_timeout(2000)
    except Exception as e:
        log.warning(f"  Failed to expand categories: {e}")

    details = []
    marked = 0
    failed = 0

    if not chat_names:
        # Full sweep — click ALL unread chats
        unread_names = await page.evaluate(CLICK_ALL_UNREAD_CHATS_JS)
        total_unread = len(unread_names)

        if not unread_names:
            log.info("  No unread Teams chats found")
            return {
                "success": True, "marked": 0, "failed": 0,
                "total_unread": 0, "details": ["No unread chats"],
            }

        log.info(f"  Found {total_unread} unread chats — clicking each...")

        # Click each one individually (CLICK_ALL_UNREAD_CHATS_JS only collects names)
        for name in unread_names[:30]:  # cap at 30
            try:
                result = await page.evaluate(CLICK_UNREAD_CHAT_JS, name)
                if result and result.get("clicked"):
                    marked += 1
                    details.append(f"Marked read: {name}")
                    log.info(f"    Marked: {safe_encode(name)}")
                else:
                    failed += 1
                    details.append(f"Not found: {name}")
                await page.wait_for_timeout(600)
            except Exception as e:
                failed += 1
                details.append(f"Error for {name}: {e}")
    else:
        # Selective — click specific chats by name
        total_unread = len(chat_names)
        for name in chat_names[:30]:
            try:
                result = await page.evaluate(CLICK_UNREAD_CHAT_JS, name)
                if result and result.get("clicked"):
                    marked += 1
                    details.append(f"Marked read: {name}")
                    log.info(f"    Marked: {safe_encode(name)}")
                else:
                    failed += 1
                    reason = result.get("reason", "unknown") if result else "no result"
                    details.append(f"Not found: {name} ({reason})")
                await page.wait_for_timeout(600)
            except Exception as e:
                failed += 1
                details.append(f"Error for {name}: {e}")

    log.info(f"  Teams mark-as-read complete: {marked} marked, {failed} failed")
    return {
        "success": failed == 0 or marked > 0,
        "marked": marked,
        "failed": failed,
        "total_unread": total_unread,
        "details": details,
    }
