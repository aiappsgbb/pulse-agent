"""Send a Teams message via Playwright browser automation.

Deterministic script — no LLM involved. Uses the shared browser.
Same pattern as teams_inbox.py but for sending instead of reading.

Two flows:
1. send_teams_message(recipient, message) — opens 1:1 chat via "New chat" button
2. reply_to_chat(chat_name, message) — finds existing chat in sidebar and replies

DOM structure notes (Teams 2026 web UI):
- New chat button: button with compose/pen icon in the toolbar
- "To:" field in new chat: combobox or input for recipient search
- Autocomplete suggestions: listbox with person results
- Message compose: contenteditable div or textbox
- Send: Ctrl+Enter
"""

import re

from core.logging import log, safe_encode


# --- JS Snippets ---

# Find and click the "New message" button (Teams 2026 UI)
# Verified: button[aria-label*="New message"] with shortcut Alt+Shift+N
FIND_NEW_CHAT_BUTTON_JS = """
() => {
    // Teams 2026: "New message (Alt+Shift+N)"
    let btn = document.querySelector('button[aria-label*="New message" i]');
    if (btn) { btn.click(); return 'clicked new-message'; }

    // Fallback: "New chat" (older Teams versions)
    btn = document.querySelector('button[aria-label*="New chat" i]');
    if (btn) { btn.click(); return 'clicked new-chat'; }

    // Try data-tid
    btn = document.querySelector('[data-tid="new-chat-button"]');
    if (btn) { btn.click(); return 'clicked data-tid'; }

    btn = document.querySelector('button[aria-label*="Compose" i]');
    if (btn) { btn.click(); return 'clicked compose'; }

    return null;
}
"""

# Find the "To:" field in the new chat view
# Verified Teams 2026: input[placeholder="Enter name, chat, channel, email or tag"]
FIND_TO_FIELD_JS = """
() => {
    // Teams 2026: "Enter name, chat, channel, email or tag"
    let field = document.querySelector('input[placeholder*="Enter name" i]');
    if (!field) field = document.querySelector('input[placeholder*="name" i][placeholder*="email" i]');
    if (!field) field = document.querySelector('input[placeholder*="To" i]');
    if (!field) field = document.querySelector('[data-tid="new-chat-to-line"] input');
    if (!field) field = document.querySelector('[role="combobox"][aria-label*="To" i]');
    if (!field) field = document.querySelector('input[aria-label*="To" i]');
    if (!field) field = document.querySelector('input[aria-label*="Add people" i]');

    if (field) {
        field.focus();
        field.click();
        return 'found';
    }
    return null;
}
"""

# Extract autocomplete suggestions from the dropdown
EXTRACT_SUGGESTIONS_JS = """
() => {
    const results = [];

    // Look for listbox suggestions
    const options = document.querySelectorAll(
        '[role="listbox"] [role="option"], [role="list"] [role="listitem"]'
    );
    for (const opt of options) {
        const text = (opt.innerText || '').trim();
        if (text && text.length > 1) {
            results.push({
                text: text.substring(0, 200),
                index: results.length,
            });
        }
    }

    // Also check suggestion containers
    if (results.length === 0) {
        const items = document.querySelectorAll(
            '[class*="suggestion" i] [role="option"], [class*="Suggestion" i] [role="option"]'
        );
        for (const item of items) {
            const text = (item.innerText || '').trim();
            if (text) results.push({ text: text.substring(0, 200), index: results.length });
        }
    }

    return results;
}
"""

# Click the Nth suggestion (0-indexed)
CLICK_SUGGESTION_JS = """
(index) => {
    const options = document.querySelectorAll(
        '[role="listbox"] [role="option"], [role="list"] [role="listitem"]'
    );
    if (index < options.length) {
        options[index].click();
        return true;
    }

    const items = document.querySelectorAll(
        '[class*="suggestion" i] [role="option"], [class*="Suggestion" i] [role="option"]'
    );
    if (index < items.length) {
        items[index].click();
        return true;
    }
    return false;
}
"""

# Find the message compose box
FIND_COMPOSE_BOX_JS = """
() => {
    // Teams compose box — contenteditable div or textbox
    let box = document.querySelector('[data-tid="ckeditor"] [contenteditable="true"]');
    if (box) return 'ckeditor';

    box = document.querySelector('[role="textbox"][aria-label*="message" i]');
    if (box) return 'textbox-message';

    box = document.querySelector('[role="textbox"][contenteditable="true"]');
    if (box) return 'textbox-contenteditable';

    box = document.querySelector('[contenteditable="true"][data-placeholder*="message" i]');
    if (box) return 'contenteditable-placeholder';

    // Broader search — any contenteditable in the compose area
    box = document.querySelector(
        '[class*="compose" i] [contenteditable="true"], ' +
        '[class*="Compose" i] [contenteditable="true"]'
    );
    if (box) return 'compose-area';

    return null;
}
"""

# Click on the compose box to focus it
FOCUS_COMPOSE_BOX_JS = """
() => {
    const selectors = [
        '[data-tid="ckeditor"] [contenteditable="true"]',
        '[role="textbox"][aria-label*="message" i]',
        '[role="textbox"][contenteditable="true"]',
        '[contenteditable="true"][data-placeholder*="message" i]',
        '[class*="compose" i] [contenteditable="true"]',
        '[class*="Compose" i] [contenteditable="true"]',
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

# Find a chat by name in the sidebar
FIND_CHAT_IN_SIDEBAR_JS = """
(chatName) => {
    const lower = chatName.toLowerCase();
    const items = document.querySelectorAll('[role="treeitem"][data-item-type="chat"]');
    for (const item of items) {
        const text = (item.innerText || '').toLowerCase();
        if (text.includes(lower)) {
            item.click();
            return { found: true, text: item.innerText.substring(0, 200) };
        }
    }
    return { found: false };
}
"""


async def send_teams_message(recipient: str, message: str) -> dict:
    """Send a 1:1 message on Teams.

    Opens the new-chat flow, searches for the recipient, selects them,
    types the message, and sends with Ctrl+Enter.

    Returns: {success: bool, detail: str}
    """
    from core.browser import get_browser_manager

    browser_mgr = get_browser_manager()
    if not browser_mgr or not browser_mgr.context:
        return {"success": False, "detail": "No shared browser available"}

    page = None
    try:
        page = await browser_mgr.new_page()
        return await _do_send_new_chat(page, recipient, message)
    except Exception as e:
        log.error(f"Teams send failed: {e}")
        return {"success": False, "detail": str(e)}
    finally:
        if page:
            try:
                await page.close()
            except Exception:
                pass


async def reply_to_chat(chat_name: str, message: str) -> dict:
    """Reply in an existing Teams chat by name.

    Finds the chat in the sidebar, opens it, types the message, and sends.

    Returns: {success: bool, detail: str}
    """
    from core.browser import get_browser_manager

    browser_mgr = get_browser_manager()
    if not browser_mgr or not browser_mgr.context:
        return {"success": False, "detail": "No shared browser available"}

    page = None
    try:
        page = await browser_mgr.new_page()
        return await _do_reply_to_chat(page, chat_name, message)
    except Exception as e:
        log.error(f"Teams reply failed: {e}")
        return {"success": False, "detail": str(e)}
    finally:
        if page:
            try:
                await page.close()
            except Exception:
                pass


async def _navigate_to_teams(page) -> bool:
    """Navigate to Teams Chat view and wait until it fully loads.

    Polls for strong UI indicators (chat tree, new-chat button) rather than
    proceeding on a timer.  Teams SPA can take 30s+ to hydrate.
    """
    log.info("  Navigating to Teams Chat...")
    await page.goto("https://teams.microsoft.com/v2/", wait_until="domcontentloaded")

    try:
        await page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass  # networkidle can be flaky on SPAs

    # Poll until Teams is actually ready — don't proceed on a half-loaded page
    max_wait = 120  # seconds
    poll_interval = 3  # seconds
    waited = 0

    while waited < max_wait:
        # Check for login redirect first
        url = page.url.lower()
        if "login" in url or "oauth" in url or "microsoftonline" in url:
            log.error("  Teams session expired — login page detected")
            return False

        try:
            ready = await page.evaluate("""
            () => {
                const hasTree = !!document.querySelector('[role="treeitem"]');
                const hasNewChat = !!document.querySelector(
                    'button[aria-label*="New message" i], button[aria-label*="New chat" i]'
                );
                return { hasTree, hasNewChat, ready: hasTree || hasNewChat };
            }
            """)
        except Exception:
            # Page is mid-navigation (redirect) — context destroyed, just retry
            ready = None

        if ready and ready.get("ready"):
            log.info(
                f"  Teams loaded after {waited}s "
                f"(tree={ready['hasTree']}, newChat={ready['hasNewChat']})"
            )
            await page.wait_for_timeout(1000)  # brief settling
            return True

        await page.wait_for_timeout(poll_interval * 1000)
        waited += poll_interval
        if waited % 15 == 0:
            log.info(f"  Waiting for Teams to load... ({waited}s)")

    log.error(f"  Teams did not load after {max_wait}s")
    return False


async def _do_send_new_chat(page, recipient: str, message: str) -> dict:
    """Open new chat, search recipient, send message."""
    if not await _navigate_to_teams(page):
        return {"success": False, "detail": "Teams session expired — login page detected"}

    # Step 1: Click "New chat" button
    log.info(f"  Starting new chat with: {safe_encode(recipient)}")
    result = await page.evaluate(FIND_NEW_CHAT_BUTTON_JS)
    if not result:
        # Fallback: keyboard shortcut (Teams 2026: Alt+Shift+N)
        await page.keyboard.press("Alt+Shift+n")
        await page.wait_for_timeout(1000)
        result = "keyboard Alt+Shift+N"

    log.info(f"  New chat: {result}")

    # Step 2: Wait for "To:" field to appear — verifies new-chat dialog opened
    to_found = None
    for attempt in range(10):
        await page.wait_for_timeout(1500)
        to_found = await page.evaluate(FIND_TO_FIELD_JS)
        if to_found:
            break
        # Retry clicking the button every few attempts
        if attempt in (3, 6):
            log.info(f"  'To:' field not found — retrying new-chat (attempt {attempt + 1})")
            retry = await page.evaluate(FIND_NEW_CHAT_BUTTON_JS)
            if not retry:
                await page.keyboard.press("Alt+Shift+n")

    if not to_found:
        return {"success": False, "detail": "Could not find 'To:' field after 10 attempts"}

    log.info(f"  'To:' field found: {to_found}")
    await page.wait_for_timeout(500)

    # Step 3: Type the recipient name
    await page.keyboard.type(recipient, delay=50)
    await page.wait_for_timeout(2000)  # Wait for autocomplete

    # Step 4: Read autocomplete suggestions
    suggestions = await page.evaluate(EXTRACT_SUGGESTIONS_JS)
    if not suggestions:
        # Try pressing Enter to search
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(2000)
        suggestions = await page.evaluate(EXTRACT_SUGGESTIONS_JS)

    if not suggestions:
        return {
            "success": False,
            "detail": f"No autocomplete results for '{recipient}'",
        }

    log.info(f"  Found {len(suggestions)} suggestion(s):")
    for s in suggestions[:5]:
        log.info(f"    - {safe_encode(s.get('text', ''))}")

    # Step 5: Click the best suggestion
    # Prefer exact name match that is NOT "(You)" (the sender's own account)
    best_idx = 0
    recipient_lower = recipient.lower()
    candidates = []
    for s in suggestions:
        text = s.get("text", "").lower()
        first_line = text.split("\n")[0]
        if recipient_lower in first_line:
            is_self = "(you)" in first_line
            candidates.append((s["index"], is_self))

    if candidates:
        # Prefer non-self matches; fall back to self if that's the only one
        non_self = [idx for idx, is_self in candidates if not is_self]
        best_idx = non_self[0] if non_self else candidates[0][0]

    clicked = await page.evaluate(CLICK_SUGGESTION_JS, best_idx)
    if not clicked:
        return {"success": False, "detail": "Could not click on recipient suggestion"}

    log.info(f"  Selected suggestion #{best_idx}")
    await page.wait_for_timeout(1500)

    # Step 6: Type the message
    return await _type_and_send(page, message, recipient)


async def _do_reply_to_chat(page, chat_name: str, message: str) -> dict:
    """Find existing chat in sidebar and reply."""
    if not await _navigate_to_teams(page):
        return {"success": False, "detail": "Teams session expired — login page detected"}

    # Step 1: Find and click the chat in the sidebar
    log.info(f"  Looking for chat: {safe_encode(chat_name)}")
    result = await page.evaluate(FIND_CHAT_IN_SIDEBAR_JS, chat_name)

    if not result or not result.get("found"):
        return {
            "success": False,
            "detail": f"Chat '{chat_name}' not found in sidebar",
        }

    log.info(f"  Opened chat: {safe_encode(result.get('text', ''))}")
    await page.wait_for_timeout(1500)

    # Step 2: Type the message
    return await _type_and_send(page, message, chat_name)


async def _type_and_send(page, message: str, target: str) -> dict:
    """Find compose box, type message, send with Ctrl+Enter."""
    # Find the compose box
    compose_found = await page.evaluate(FIND_COMPOSE_BOX_JS)
    if not compose_found:
        return {"success": False, "detail": "Could not find message compose box"}

    log.info(f"  Compose box found: {compose_found}")

    # Focus the compose box
    focused = await page.evaluate(FOCUS_COMPOSE_BOX_JS)
    if not focused:
        return {"success": False, "detail": "Could not focus compose box"}

    await page.wait_for_timeout(300)

    # Type the message
    await page.keyboard.type(message, delay=20)
    await page.wait_for_timeout(500)

    # Send with Ctrl+Enter
    await page.keyboard.press("Control+Enter")
    await page.wait_for_timeout(2000)

    log.info(f"  Message sent to {safe_encode(target)}")
    return {
        "success": True,
        "detail": f"Message sent to {target}",
    }
