"""Reply to an email via Outlook Web Playwright automation.

Deterministic script — no LLM involved. Uses the shared browser.
Same pattern as outlook_inbox.py but for sending replies.

Flow:
1. Navigate to Outlook inbox
2. Search for the email thread (by sender name or subject)
3. Open the matching email
4. Click Reply
5. Type the reply message
6. Click Send (or Ctrl+Enter)

DOM structure verified 2026-02-23 via Playwright MCP inspection:
- Search box: input[aria-label*="Search" i]
- Mail items: [role="option"][data-convid] (also [role="listitem"] as fallback)
- Reply button: button[aria-label="Reply" i] (also menuitem[name="Reply"] in email toolbar)
- Compose body: [role="textbox"][aria-label*="Message body" i] (contenteditable div)
- Send button: button[aria-label="Send" i] (title="Send (Ctrl+Enter)")
"""

from core.logging import log, safe_encode


# --- JS Snippets ---

# Search for an email by typing in the Outlook search box
FIND_SEARCH_BOX_JS = """
() => {
    // Outlook search box
    let box = document.querySelector('input[aria-label*="Search" i]');
    if (box) { box.focus(); box.click(); return 'found'; }

    box = document.querySelector('[role="search"] input');
    if (box) { box.focus(); box.click(); return 'found-role'; }

    box = document.querySelector('#topSearchInput');
    if (box) { box.focus(); box.click(); return 'found-id'; }

    return null;
}
"""

# Extract email search results
EXTRACT_SEARCH_RESULTS_JS = """
() => {
    const results = [];
    const items = document.querySelectorAll('[role="option"][data-convid], [role="listitem"]');
    for (const item of items) {
        const text = (item.innerText || '').trim();
        if (text && text.length > 5) {
            results.push({
                text: text.substring(0, 300),
                index: results.length,
            });
        }
    }
    return results;
}
"""

# Click the Nth search result (0-indexed)
CLICK_SEARCH_RESULT_JS = """
(index) => {
    const items = document.querySelectorAll('[role="option"][data-convid], [role="listitem"]');
    if (index < items.length) {
        items[index].click();
        return true;
    }
    return false;
}
"""

# Find and click the Reply button (not Reply All)
# Verified: button[aria-label="Reply" i] in ribbon, menuitem[name="Reply"] in email toolbar
CLICK_REPLY_JS = """
() => {
    // Ribbon reply button
    let btn = document.querySelector('button[aria-label="Reply" i]');
    if (btn) { btn.click(); return 'clicked reply button'; }

    // Email toolbar reply (menuitem, not button)
    btn = document.querySelector('[role="menuitem"][aria-label="Reply" i]');
    if (btn) { btn.click(); return 'clicked reply menuitem'; }

    return null;
}
"""

# Find the reply compose area
# Verified: [role="textbox"][aria-label*="Message body" i] with contenteditable="true"
FIND_REPLY_COMPOSE_JS = """
() => {
    let box = document.querySelector('[role="textbox"][aria-label*="Message body" i]');
    if (box) return 'textbox-message-body';

    box = document.querySelector('[aria-label*="Message body" i][contenteditable="true"]');
    if (box) return 'contenteditable-message-body';

    return null;
}
"""

# Focus the reply compose area
FOCUS_REPLY_COMPOSE_JS = """
() => {
    const selectors = [
        '[role="textbox"][aria-label*="Message body" i]',
        '[aria-label*="Message body" i][contenteditable="true"]',
    ];
    for (const sel of selectors) {
        const el = document.querySelector(sel);
        if (el) {
            el.focus();
            el.click();
            return true;
        }
    }
    return false;
}
"""

# Click the Send button
# Verified: button[aria-label="Send" i] (title="Send (Ctrl+Enter)")
CLICK_SEND_JS = """
() => {
    let btn = document.querySelector('button[aria-label="Send" i]');
    if (btn) { btn.click(); return 'clicked send'; }

    return null;
}
"""


async def reply_to_email(search_query: str, message: str) -> dict:
    """Reply to an email in Outlook Web.

    Searches for the email by sender name or subject, opens it,
    clicks Reply, types the message, and sends.

    Args:
        search_query: Sender name or subject line to search for
        message: Reply text to send

    Returns: {success: bool, detail: str}
    """
    from core.browser import browser_page

    async with browser_page() as page:
        if page is None:
            return {"success": False, "detail": "No shared browser available"}
        try:
            return await _do_reply(page, search_query, message)
        except Exception as e:
            log.error(f"Outlook reply failed: {e}")
            return {"success": False, "detail": str(e)}


async def _do_reply(page, search_query: str, message: str) -> dict:
    """Navigate to Outlook, find email, reply."""
    log.info(f"  Replying to email matching: {safe_encode(search_query)}")

    # Step 1: Navigate to Outlook
    await page.goto("https://outlook.office.com/mail/inbox", wait_until="domcontentloaded")
    try:
        await page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass

    # Wait for auth redirects to settle — Outlook may do a silent token refresh
    # that briefly passes through microsoftonline.com before landing back
    for _ in range(6):  # up to 18s total (3s initial + 6*2.5s)
        await page.wait_for_timeout(2500)
        url = page.url.lower()
        if "outlook.office" in url or "outlook.live" in url:
            break  # landed on Outlook
    else:
        url = page.url.lower()
        if "login" in url or "oauth" in url or "microsoftonline" in url:
            return {"success": False, "detail": "Outlook session expired — login page detected"}

    # Step 2: Search for the email
    search_found = await page.evaluate(FIND_SEARCH_BOX_JS)
    if not search_found:
        return {"success": False, "detail": "Could not find Outlook search box"}

    await page.keyboard.type(search_query, delay=30)
    await page.keyboard.press("Enter")
    await page.wait_for_timeout(3000)

    # Step 3: Get search results
    results = await page.evaluate(EXTRACT_SEARCH_RESULTS_JS)
    if not results:
        return {
            "success": False,
            "detail": f"No emails found matching '{search_query}'",
        }

    log.info(f"  Found {len(results)} email result(s)")

    # Click the first result
    clicked = await page.evaluate(f"({CLICK_SEARCH_RESULT_JS})(0)")
    if not clicked:
        return {"success": False, "detail": "Could not open email from search results"}

    await page.wait_for_timeout(2000)

    # Step 4: Click Reply
    reply_result = await page.evaluate(CLICK_REPLY_JS)
    if not reply_result:
        # Try keyboard shortcut
        await page.keyboard.press("Control+r")
        await page.wait_for_timeout(1500)
        reply_result = "keyboard Ctrl+R"

    log.info(f"  Reply: {reply_result}")
    await page.wait_for_timeout(1500)

    # Step 5: Find and focus the reply compose area
    compose_found = await page.evaluate(FIND_REPLY_COMPOSE_JS)
    if not compose_found:
        return {"success": False, "detail": "Could not find reply compose area"}

    log.info(f"  Reply compose found: {compose_found}")
    focused = await page.evaluate(FOCUS_REPLY_COMPOSE_JS)
    if not focused:
        return {"success": False, "detail": "Could not focus reply compose area"}

    await page.wait_for_timeout(300)

    # Step 6: Position cursor at beginning and insert reply text.
    # Do NOT Ctrl+A + Backspace — that clears the quoted email thread.
    # Instead, press Home/Ctrl+Home to move cursor to the start, then insert.
    await page.keyboard.press("Control+Home")
    await page.wait_for_timeout(200)
    await page.keyboard.insert_text(message)
    await page.wait_for_timeout(500)

    # Step 7: Send
    send_result = await page.evaluate(CLICK_SEND_JS)
    if not send_result:
        # Fallback: Ctrl+Enter
        await page.keyboard.press("Control+Enter")
        send_result = "keyboard Ctrl+Enter"

    log.info(f"  Send: {send_result}")
    await page.wait_for_timeout(2000)

    # Verify send: compose area should disappear or be empty after send
    compose_gone = await page.evaluate("""
    () => {
        const box = document.querySelector('[role="textbox"][aria-label*="Message body" i]');
        if (!box) return true;  // compose area gone = sent
        const text = (box.innerText || '').trim();
        return text.length === 0;
    }
    """)

    if not compose_gone:
        log.warning(f"  Compose area still visible after send — reply may not have been sent for {safe_encode(search_query)}")
        return {
            "success": False,
            "detail": f"Reply may not have been sent for '{search_query}' — compose area still has content",
        }

    log.info(f"  Email reply sent for: {safe_encode(search_query)}")
    return {
        "success": True,
        "detail": f"Reply sent to email matching '{search_query}'",
    }
