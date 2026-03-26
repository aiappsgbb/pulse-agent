"""All JavaScript constants for DOM interaction with SharePoint Stream transcript viewer."""

# JS to extract URL parameters from the Teams launcher page.
# "View recap" in Outlook Calendar opens teams.microsoft.com/dl/launcher/launcher.html
# with a URL param containing sitePath (direct SharePoint Stream URL).
EXTRACT_LAUNCHER_PARAMS_JS = """
() => {
    const url = new URL(window.location.href);
    const innerUrl = url.searchParams.get('url') || '';
    let params;
    if (innerUrl.includes('?')) {
        params = new URLSearchParams(innerUrl.split('?')[1]);
    } else {
        params = new URLSearchParams(innerUrl);
    }
    return {
        sitePath: params.get('sitePath') || '',
        driveId: params.get('driveId') || '',
        driveItemId: params.get('driveItemId') || '',
        threadId: params.get('threadId') || '',
        organizerId: params.get('organizerId') || '',
        tenantId: params.get('tenantId') || '',
    };
}
"""

# JS to find the scroll container — the ms-FocusZone with the largest scrollHeight.
# SharePoint Stream transcript uses Fluent UI's ms-List inside a FocusZone scroll wrapper.
# We pick the FocusZone with the largest scrollHeight that exceeds clientHeight,
# avoiding the Copilot chat FocusZone which has a tiny scrollHeight.
FIND_SCROLL_CONTAINER_JS = """
() => {
    const zones = document.querySelectorAll('.ms-FocusZone');
    let best = null, maxHeight = 0;
    for (const z of zones) {
        if (z.scrollHeight > maxHeight && z.scrollHeight > z.clientHeight + 100) {
            maxHeight = z.scrollHeight;
            best = z;
        }
    }
    if (!best) return { found: false };
    return {
        found: true,
        tag: best.tagName,
        className: best.className.substring(0, 100),
        scrollHeight: best.scrollHeight,
        clientHeight: best.clientHeight,
        scrollTop: best.scrollTop,
    };
}
"""

# JS to scroll the FocusZone and collect ALL transcript entries in one call.
# Returns {entries: {ariaLabel: text}, totalCollected, expectedTotal}.
# This runs the entire scroll loop inside the browser to minimize round-trips.
SCROLL_AND_COLLECT_JS = """
async () => {
    // Find the scrollable FocusZone (largest scrollHeight)
    const zones = document.querySelectorAll('.ms-FocusZone');
    let scrollZone = null, maxHeight = 0;
    for (const z of zones) {
        if (z.scrollHeight > maxHeight && z.scrollHeight > z.clientHeight + 100) {
            maxHeight = z.scrollHeight;
            scrollZone = z;
        }
    }
    if (!scrollZone) return { error: 'No scrollable FocusZone found', entries: {} };

    // Get expected total from aria-setsize
    const firstItem = scrollZone.querySelector('[role="listitem"][aria-setsize]');
    const expectedTotal = firstItem ? parseInt(firstItem.getAttribute('aria-setsize'), 10) : null;

    const entries = {};
    const collectVisible = () => {
        const groups = scrollZone.querySelectorAll('[role="group"]');
        for (const g of groups) {
            const ariaLabel = (g.getAttribute('aria-label') || '').trim();
            if (!ariaLabel || ariaLabel === ' ' || ariaLabel.startsWith('Transcript.')) continue;
            const listitem = g.querySelector('[role="listitem"]');
            if (listitem) {
                const text = listitem.innerText.trim();
                if (text) entries[ariaLabel] = text;
            }
        }
    };

    // Collect initial visible entries
    collectVisible();

    const step = Math.max(scrollZone.clientHeight - 50, 200);
    let pos = 0, stale = 0;

    while (stale < 8) {
        const prev = Object.keys(entries).length;
        pos += step;
        scrollZone.scrollTop = pos;
        await new Promise(r => setTimeout(r, 300));
        collectVisible();
        stale = Object.keys(entries).length === prev ? stale + 1 : 0;
        if (pos > scrollZone.scrollHeight + step * 2) break;
    }

    return {
        entries: entries,
        totalCollected: Object.keys(entries).length,
        expectedTotal: expectedTotal,
        scrollHeight: scrollZone.scrollHeight,
        clientHeight: scrollZone.clientHeight,
    };
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
