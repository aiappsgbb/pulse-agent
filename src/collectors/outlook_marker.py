"""Mark Outlook emails as read via Playwright browser automation.

Deterministic script — no LLM involved. Uses the shared browser.
Selects email items by data-convid and presses Ctrl+Q (mark as read shortcut).

Reuses the same DOM structure as outlook_inbox.py (Feb 2026):
- Mail items: [role="option"][data-convid] with data-focusable-row="true"
- Unread detection: aria-label starts with "Unread"
- Ctrl+Q toggles read/unread on the selected item.
"""

from core.logging import log, safe_encode


# JS: Click a mail item by conversation ID and return its current state.
SELECT_MAIL_BY_CONVID_JS = """
(convId) => {
    const item = document.querySelector('[role="option"][data-convid="' + convId + '"]');
    if (!item) return { found: false };
    const label = item.getAttribute('aria-label') || '';
    const isUnread = label.startsWith('Unread');
    item.click();
    return { found: true, unread: isUnread, label: label.substring(0, 200) };
}
"""

# JS: Check if a specific mail item is still unread (for verification).
CHECK_MAIL_UNREAD_JS = """
(convId) => {
    const item = document.querySelector('[role="option"][data-convid="' + convId + '"]');
    if (!item) return { found: false };
    const label = item.getAttribute('aria-label') || '';
    return { found: true, unread: label.startsWith('Unread') };
}
"""

# JS: Get all unread mail items with their conv IDs.
GET_ALL_UNREAD_JS = """
() => {
    const items = document.querySelectorAll('[role="option"][data-convid]');
    const unread = [];
    for (const item of items) {
        const label = item.getAttribute('aria-label') || '';
        if (label.startsWith('Unread')) {
            const text = item.innerText || '';
            const lines = text.split('\\n').map(l => l.trim()).filter(l => l && l.length > 1);
            unread.push({
                convId: item.getAttribute('data-convid') || '',
                sender: lines[0] || '',
                subject: lines[1] || '',
                label: label.substring(0, 200),
            });
        }
    }
    return unread;
}
"""


async def mark_outlook_emails_read(
    items: list[dict] | None = None,
) -> dict:
    """Mark Outlook emails as read by selecting them and pressing Ctrl+Q.

    Args:
        items: List of dicts with 'conv_id' (required for targeting).
               If None or empty, marks ALL visible unread emails as read.

    Returns:
        {success: bool, marked: int, failed: int, skipped: int, total_unread: int, details: list[str]}
    """
    from core.browser import get_browser_manager

    browser_mgr = get_browser_manager()
    if not browser_mgr or not browser_mgr.context or not browser_mgr.is_alive:
        return {
            "success": False, "marked": 0, "failed": 0, "skipped": 0,
            "total_unread": 0, "details": ["No shared browser available"],
        }

    page = None
    try:
        page = await browser_mgr.new_page()
        return await _do_mark_read(page, items)
    except Exception as e:
        log.error(f"Outlook mark-as-read failed: {e}")
        return {
            "success": False, "marked": 0, "failed": 0, "skipped": 0,
            "total_unread": 0, "details": [str(e)],
        }
    finally:
        if page:
            try:
                await page.close()
            except Exception:
                pass


async def _do_mark_read(page, items: list[dict] | None) -> dict:
    """Navigate to Outlook inbox and mark emails as read."""
    log.info("Marking Outlook emails as read...")

    await page.goto("https://outlook.office.com/mail/inbox", wait_until="domcontentloaded")
    try:
        await page.wait_for_load_state("networkidle", timeout=12000)
    except Exception:
        pass

    # Check for auth redirect
    url = page.url.lower()
    if "login" in url or "oauth" in url or "microsoftonline" in url:
        log.error("  Outlook session expired — redirected to login page")
        return {
            "success": False, "marked": 0, "failed": 0, "skipped": 0,
            "total_unread": 0, "details": ["Outlook session expired"],
        }

    # Wait for mail items
    try:
        await page.wait_for_selector('[role="option"][data-convid]', timeout=10000)
        await page.wait_for_timeout(1500)
    except Exception:
        log.warning("  Mail items not found — Outlook may still be loading")
        await page.wait_for_timeout(3000)

    details = []
    marked = 0
    failed = 0
    skipped = 0

    if not items:
        # Full sweep — get all visible unread and mark them
        unread_items = await page.evaluate(GET_ALL_UNREAD_JS)
        total_unread = len(unread_items)

        if not unread_items:
            log.info("  No unread Outlook emails found")
            return {
                "success": True, "marked": 0, "failed": 0, "skipped": 0,
                "total_unread": 0, "details": ["No unread emails"],
            }

        log.info(f"  Found {total_unread} unread emails — marking each...")

        for item_info in unread_items[:30]:  # cap at 30
            conv_id = item_info.get("convId", "")
            sender = item_info.get("sender", "?")
            if not conv_id:
                skipped += 1
                continue
            result = await _mark_single_email(page, conv_id, sender)
            if result == "marked":
                marked += 1
                details.append(f"Marked read: {sender}")
            elif result == "already_read":
                skipped += 1
            else:
                failed += 1
                details.append(f"Failed: {sender} ({result})")
    else:
        # Selective — mark specific emails by conv_id
        total_unread = len(items)
        for item in items[:30]:
            conv_id = item.get("conv_id", "")
            sender = item.get("sender", "?")
            if not conv_id:
                skipped += 1
                details.append(f"Skipped (no conv_id): {sender}")
                continue
            result = await _mark_single_email(page, conv_id, sender)
            if result == "marked":
                marked += 1
                details.append(f"Marked read: {sender}")
            elif result == "already_read":
                skipped += 1
                details.append(f"Already read: {sender}")
            else:
                failed += 1
                details.append(f"Failed: {sender} ({result})")

    log.info(f"  Outlook mark-as-read complete: {marked} marked, {failed} failed, {skipped} skipped")
    return {
        "success": failed == 0 or marked > 0,
        "marked": marked,
        "failed": failed,
        "skipped": skipped,
        "total_unread": total_unread,
        "details": details,
    }


async def _mark_single_email(page, conv_id: str, sender: str) -> str:
    """Mark a single email as read. Returns 'marked', 'already_read', or error string."""
    try:
        # Click to select the item
        select_result = await page.evaluate(SELECT_MAIL_BY_CONVID_JS, conv_id)
        if not select_result or not select_result.get("found"):
            return "not found"

        if not select_result.get("unread"):
            return "already_read"

        await page.wait_for_timeout(200)

        # Press Ctrl+Q to mark as read
        await page.keyboard.press("Control+q")
        await page.wait_for_timeout(400)

        # Verify it's now read
        check = await page.evaluate(CHECK_MAIL_UNREAD_JS, conv_id)
        if check and check.get("found") and not check.get("unread"):
            log.info(f"    Marked: {safe_encode(sender)}")
            return "marked"
        elif check and check.get("found") and check.get("unread"):
            # Ctrl+Q may not have worked — try once more
            await page.keyboard.press("Control+q")
            await page.wait_for_timeout(400)
            check2 = await page.evaluate(CHECK_MAIL_UNREAD_JS, conv_id)
            if check2 and check2.get("found") and not check2.get("unread"):
                log.info(f"    Marked (retry): {safe_encode(sender)}")
                return "marked"
            return "still unread after retry"
        else:
            # Item disappeared from DOM after click — likely marked
            return "marked"

    except Exception as e:
        return str(e)
