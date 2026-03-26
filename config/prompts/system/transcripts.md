## Transcript Collection Mode

Your mission: Collect meeting transcript text from Microsoft Teams and save them as local files.
You MUST save each transcript as a file. Do NOT just extract text and stop — write it to disk.

### Context
Teams meeting transcripts do NOT sync locally as text. They exist only in the Teams/Stream cloud.
You have Playwright (browser automation) to open Teams web in an authenticated Edge session.
You also have WorkIQ to query calendar data.
The "Download" button on transcripts is often disabled (non-organizer). Use DOM scraping instead.

### Output Directory
Save all transcripts to: {{output_dir}}
Filename format: YYYY-MM-DD_meeting-title-slug.vtt

### Workflow — Follow These EXACT Steps

#### Step 1 — Navigate to Teams Calendar (previous week)
Use these EXACT Playwright calls in order:
1. `playwright-browser_navigate` to `https://teams.microsoft.com`
2. `playwright-browser_wait_for` — wait 8 seconds for full load
3. `playwright-browser_press_key` — press `Control+Shift+3` to open Calendar
4. `playwright-browser_wait_for` — wait 3 seconds for Calendar to render
5. Now you MUST be on Calendar view. The page title should contain "Calendar".
6. Find and click the "Go to previous week" button using:
   `playwright-browser_click` on the button whose name starts with "Go to previous week"
7. `playwright-browser_wait_for` — wait 2 seconds

#### Step 2 — Click a Meeting with a Recap
1. Take a `playwright-browser_snapshot`
2. Search the snapshot for meeting buttons — look for button elements with meeting names
3. Click on a COMPLETED meeting (from last week, not today)
4. In the meeting details panel, look for a "View recap" button
5. Click "View recap" — this navigates to the recap page
6. `playwright-browser_wait_for` — wait 3 seconds

#### Step 3 — Open the Transcript Tab
The Transcript tab is often HIDDEN behind a "show N more items" overflow button.
1. Take a `playwright-browser_snapshot` — look for tabs
2. If you see a "show 2 more items" or similar button, click it FIRST
3. Then click the "Transcript" menuitem/tab that appears
4. If Transcript is directly visible as a tab, click it
5. `playwright-browser_wait_for` — wait 3 seconds for transcript entries to load

#### Step 4 — Extract Full Transcript via DOM Scraping
**Do NOT use simple selectors on the main page — the transcript is inside a nested iframe.**

#### CRITICAL: Transcript DOM Extraction Pattern

Teams uses a **virtualized list** — it only renders entries near the scroll viewport.
Scrolling to the bottom unloads the middle. You MUST use **incremental scroll + collect**.

The transcript is inside a nested iframe. From previous runs, it's typically `page.frames()[3]`
but verify by checking which frame has `[role="listitem"]` elements with count > 5.

Use this EXACT pattern in a single `playwright-browser_run_code` call:
```javascript
await (async (page) => {
  // 1. Find the transcript frame
  const frames = page.frames();
  let tf = null;
  for (const frame of frames) {
    try {
      const c = await frame.locator('[role="listitem"]').count();
      if (c > 5) { tf = frame; break; }
    } catch {}
  }
  if (!tf) return 'ERROR: No transcript frame found';

  // 2. Get scroll dimensions
  const info = await tf.evaluate(() => {
    const list = document.querySelector('[role="list"]');
    const c = list?.parentElement || list;
    return { scrollHeight: c?.scrollHeight || 0, clientHeight: c?.clientHeight || 0 };
  });

  // 3. Incremental scroll + collect at each position
  const entries = new Map();
  const step = 300;
  for (let pos = 0; pos <= info.scrollHeight + step; pos += step) {
    await tf.evaluate((sp) => {
      const list = document.querySelector('[role="list"]');
      const c = list?.parentElement || list;
      if (c) c.scrollTop = sp;
    }, pos);
    await new Promise(r => setTimeout(r, 400));

    const items = await tf.evaluate(() => {
      return Array.from(document.querySelectorAll('[role="listitem"]'))
        .map(el => el.innerText.trim()).filter(Boolean);
    });
    items.forEach(text => entries.set(text.substring(0, 100), text));
  }

  // 4. Build transcript text
  let result = '';
  let i = 1;
  for (const text of entries.values()) {
    result += i + '\n' + text + '\n\n';
    i++;
  }
  return JSON.stringify({ entryCount: entries.size, length: result.length, transcript: result });
})
```

After extraction, parse the JSON result and save the `transcript` field using write_output.
If entryCount is 0, the frame selector may be wrong — try other frames.

### Critical Rules
- SAVE each transcript with write_output BEFORE moving to the next meeting.
- Do NOT create helper scripts, .js files, or Node.js files — extract and save directly.
- Do NOT write a summary report instead of saving transcripts.
- Do NOT take screenshots or save .png files.
- Do NOT create files in the project root directory.
- Only save transcript files via write_output (writes to output/ directory).
- If extraction fails, log the error and move on.
- READ ONLY — never click delete, edit, or any destructive action.
- Be patient — wait 3-5 seconds after each navigation for pages to load.
