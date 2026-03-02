"""Scan Outlook Web inbox for unread emails via Playwright.

Deterministic script — no LLM involved. Uses the shared browser.
Returns structured data that gets injected into the digest/monitor trigger prompt.
Provides email coverage when WorkIQ is unavailable.

DOM structure (verified Feb 2026):
- Mail items: [role="option"][data-convid] with data-focusable-row="true"
- aria-label contains structured info starting with status flags:
  "Unread Collapsed Has attachments Flagged Pinned Replied
   Marked as high/low priority by Copilot External sender
   <sender> <subject> <date/time> <preview>"
- Unread detection: aria-label starts with "Unread"
- innerText lines: icon-chars, sender, icon-chars, subject, preview, date/time
"""

import re
from datetime import datetime

from core.logging import log, safe_encode


# Status flag prefixes that appear at the start of aria-label before the sender
_STATUS_FLAGS = [
    "Unread",
    "Collapsed",
    "Has attachments",
    "Flagged",
    "Pinned",
    "Replied",
    "Forwarded",
    "Marked as high priority by Copilot",
    "Marked as low priority by Copilot",
    "External sender",
]

EXTRACT_MAIL_LIST_JS = """
() => {
    const items = document.querySelectorAll('[role="option"][data-convid]');
    return Array.from(items).slice(0, 30).map(el => ({
        ariaLabel: el.getAttribute('aria-label') || '',
        innerText: (el.innerText || '').substring(0, 500),
        convId: el.getAttribute('data-convid') || '',
    }));
}
"""

# Fallback: raw text from the mail list area
EXTRACT_MAIL_PANE_TEXT_JS = """
() => {
    const list = document.querySelector('[role="listbox"]');
    if (list) return list.innerText.substring(0, 5000);
    const main = document.querySelector('[role="main"]');
    if (main) return main.innerText.substring(0, 5000);
    return '';
}
"""


def _parse_aria_label(aria: str) -> dict:
    """Parse an Outlook mail item's aria-label into structured fields.

    The aria-label format is:
      [Unread] [Collapsed] [Has attachments] [Flagged] [Pinned] [Replied]
      [Marked as high/low priority by Copilot] [External sender]
      <sender> <subject> <date/time> <preview>

    Returns dict with: unread, has_attachment, flagged, sender, subject, time, preview
    """
    result = {
        "unread": False,
        "has_attachment": False,
        "flagged": False,
        "replied": False,
        "sender": "",
        "subject": "",
        "time": "",
        "preview": "",
    }

    if not aria:
        return result

    # Strip known status flags from the front
    remaining = aria
    result["unread"] = remaining.startswith("Unread")

    for flag in _STATUS_FLAGS:
        if remaining.startswith(flag):
            remaining = remaining[len(flag):].lstrip()
            if flag == "Has attachments":
                result["has_attachment"] = True
            elif flag == "Flagged":
                result["flagged"] = True
            elif flag == "Replied":
                result["replied"] = True

    # After stripping flags, remaining is: "sender subject date preview"
    # Try to find a date pattern to split the fields
    # Dates appear as "2026-02-18" or "11:02 AM" or "6:17 AM" or "12:47 AM"
    date_match = re.search(r'\b(\d{4}-\d{2}-\d{2}|\d{1,2}:\d{2}\s*[AP]M)\b', remaining)
    if date_match:
        before_date = remaining[:date_match.start()].strip()
        result["time"] = date_match.group(1)
        result["preview"] = remaining[date_match.end():].strip()[:200]

        # Split before_date into sender + subject
        # The sender comes first, subject after. No clear delimiter, but
        # innerText parsing is more reliable for this split.
        result["sender"] = before_date
    else:
        result["sender"] = remaining[:200]

    return result


def _parse_inner_text(text: str) -> dict:
    """Parse innerText lines to extract sender and subject.

    innerText lines (with icon chars as single-char lines):
      ? (or empty)
      sender name
      ? (icons)
      subject
      preview text
      date/time
    """
    # Filter out single-char lines (icon characters) and empty lines
    lines = [l.strip() for l in text.split("\n") if l.strip() and len(l.strip()) > 1]

    result = {"sender": "", "subject": "", "preview": "", "time": ""}
    if len(lines) >= 1:
        result["sender"] = lines[0]
    if len(lines) >= 2:
        result["subject"] = lines[1]
    if len(lines) >= 3:
        # Last line is often the date, everything in between is preview
        result["time"] = lines[-1]
        if len(lines) >= 4:
            result["preview"] = " ".join(lines[2:-1])[:200]

    return result


async def scan_outlook_inbox(config: dict) -> list[dict] | None:
    """Scan Outlook Web for unread emails using the shared browser.

    Returns a list of dicts with unread=True items:
    [{sender, subject, preview, time, unread, has_attachment, conv_id}, ...]
    Returns None if the browser is unavailable (distinct from [] = scanned, nothing found).
    """
    from core.browser import get_browser_manager

    browser_mgr = get_browser_manager()
    if not browser_mgr or not browser_mgr.context or not browser_mgr.is_alive:
        log.warning("Outlook inbox scan skipped — no shared browser available")
        return None

    page = None
    try:
        page = await browser_mgr.new_page()
        return await _do_scan(page)
    except Exception as e:
        log.error(f"Outlook inbox scan failed: {e}")
        return None
    finally:
        if page:
            try:
                await page.close()
            except Exception:
                pass


async def _do_scan(page) -> list[dict]:
    """Navigate to Outlook inbox and extract unread emails."""
    log.info("Scanning Outlook inbox for unread emails...")

    await page.goto("https://outlook.office.com/mail/inbox", wait_until="domcontentloaded")
    try:
        await page.wait_for_load_state("networkidle", timeout=12000)
    except Exception:
        pass

    # Check for auth redirect — session may have expired
    url = page.url.lower()
    if "login" in url or "oauth" in url or "microsoftonline" in url:
        log.error("  Outlook session expired — redirected to login page")
        return None

    # Wait for mail items to render
    try:
        await page.wait_for_selector('[role="option"][data-convid]', timeout=10000)
        await page.wait_for_timeout(1500)
    except Exception:
        log.warning("  Mail items not found — Outlook may still be loading")
        await page.wait_for_timeout(3000)

    # Extract mail items
    raw_items = await page.evaluate(EXTRACT_MAIL_LIST_JS)

    if raw_items:
        items = []
        for raw in raw_items:
            aria = raw.get("ariaLabel", "")
            text = raw.get("innerText", "")

            # Parse from aria-label for status flags
            aria_parsed = _parse_aria_label(aria)
            # Parse from innerText for cleaner sender/subject split
            text_parsed = _parse_inner_text(text)

            item = {
                "sender": text_parsed["sender"] or aria_parsed["sender"][:80],
                "subject": text_parsed["subject"] or aria_parsed.get("subject", ""),
                "preview": text_parsed["preview"] or aria_parsed["preview"],
                "time": text_parsed["time"] or aria_parsed["time"],
                "unread": aria_parsed["unread"],
                "has_attachment": aria_parsed["has_attachment"],
                "replied": aria_parsed["replied"],
                "conv_id": raw.get("convId", ""),
            }
            items.append(item)

        unread = [i for i in items if i["unread"]]
        log.info(f"  Found {len(items)} emails, {len(unread)} unread")
        for u in unread[:10]:
            log.info(f"    - {safe_encode(u['sender'][:40])}: {safe_encode(u['subject'][:60])}")
        return unread

    # Fallback: raw text
    log.info("  Structured extraction found nothing — trying text fallback")
    raw_text = await page.evaluate(EXTRACT_MAIL_PANE_TEXT_JS)

    if raw_text:
        log.info(f"  Got {len(raw_text)} chars of mail pane text")
        return [{
            "sender": "Outlook Inbox (raw)",
            "subject": "Raw mail list text",
            "preview": raw_text[:3000],
            "time": "",
            "unread": True,
            "has_attachment": False,
            "replied": False,
            "conv_id": "",
        }]

    log.warning("  No mail data extracted — Outlook may not have loaded")
    return []


def format_outlook_for_prompt(items: list[dict] | None) -> str:
    """Format scanned Outlook items as text for injection into trigger prompt."""
    if items is None:
        return (
            "**SCAN UNAVAILABLE** — Browser was not running. Cannot determine "
            "Outlook unread status. DO NOT assume zero unread."
        )
    if not items:
        return "No unread emails detected (Outlook scan)."

    # Raw fallback
    if len(items) == 1 and items[0]["sender"] == "Outlook Inbox (raw)":
        return (
            "## Outlook Inbox (raw scan — parse carefully)\n\n"
            "The structured scanner couldn't identify individual emails. "
            "Here is the raw text from the Outlook mail list:\n\n"
            f"```\n{items[0]['preview']}\n```"
        )

    lines = [f"## Outlook Inbox — {len(items)} Unread Emails\n"]
    for i, item in enumerate(items, 1):
        sender = item.get("sender", "Unknown")
        subject = item.get("subject", "(no subject)")
        preview = item.get("preview", "")
        time = item.get("time", "")
        attachment = " [attachment]" if item.get("has_attachment") else ""
        replied = " [replied]" if item.get("replied") else ""
        time_str = f" ({time})" if time else ""
        preview_str = f" — {preview[:100]}" if preview else ""
        lines.append(f"{i}. **{sender}**{time_str}{attachment}{replied}: {subject}{preview_str}")

    lines.append(
        "\nFor each unread email above, determine if the sender is directly asking ME "
        "to do something or reply (not just CC/FYI/newsletter)."
    )
    return "\n".join(lines)
