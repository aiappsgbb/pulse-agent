"""All JavaScript constants for DOM interaction with Teams transcript viewer."""

# JS snippet to read currently visible listitem text from the transcript frame.
COLLECT_VISIBLE_JS = """
() => {
    const items = [];
    document.querySelectorAll('[role="listitem"]').forEach(el => {
        const text = el.innerText.trim();
        if (text) items.push(text);
    });
    return items;
}
"""

# JS to find the actual scroll container — the ms-FocusZone ancestor with overflow:auto.
# Teams transcript uses Fluent UI's ms-List inside a FocusZone scroll wrapper.
# The list itself has scrollHeight == clientHeight (no overflow).
# The FocusZone wrapper is the real scrollable element.
FIND_SCROLL_CONTAINER_JS = """
() => {
    const list = document.querySelector('[role="list"]');
    if (!list) return null;

    // Walk up the DOM tree to find the ancestor with overflow-y: auto/scroll
    let el = list.parentElement;
    while (el && el !== document.body) {
        const style = getComputedStyle(el);
        if ((style.overflowY === 'auto' || style.overflowY === 'scroll')
            && el.scrollHeight > el.clientHeight + 50) {
            return {
                found: true,
                tag: el.tagName,
                className: el.className.substring(0, 100),
                scrollHeight: el.scrollHeight,
                clientHeight: el.clientHeight,
                scrollTop: el.scrollTop,
            };
        }
        el = el.parentElement;
    }
    return { found: false };
}
"""

# JS to scroll the FocusZone container to a specific position.
SCROLL_TO_JS = """
(pos) => {
    const list = document.querySelector('[role="list"]');
    if (!list) return false;
    let el = list.parentElement;
    while (el && el !== document.body) {
        const style = getComputedStyle(el);
        if ((style.overflowY === 'auto' || style.overflowY === 'scroll')
            && el.scrollHeight > el.clientHeight + 50) {
            el.scrollTop = pos;
            return { scrollTop: el.scrollTop, scrollHeight: el.scrollHeight };
        }
        el = el.parentElement;
    }
    return false;
}
"""

# JS to get total expected items from aria-setsize attribute.
GET_TOTAL_ITEMS_JS = """
() => {
    const item = document.querySelector('[role="listitem"][aria-setsize]');
    if (item) return parseInt(item.getAttribute('aria-setsize'), 10);
    return null;
}
"""
