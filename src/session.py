"""Shared session configuration builder — avoids circular imports."""

from pathlib import Path

from copilot import (
    SessionConfig,
    MCPLocalServerConfig,
    Tool,
    PermissionRequest,
    PermissionRequestResult,
)

PROJECT_ROOT = Path(__file__).parent.parent
OUTPUT_DIR = PROJECT_ROOT / "output"
INPUT_DIR = PROJECT_ROOT / "input"


def auto_approve_handler(request: PermissionRequest, context: dict) -> PermissionRequestResult:
    """Auto-approve all tool calls — agent runs autonomously."""
    return PermissionRequestResult(kind="approved", rules=[])


def build_session_config(config: dict, mode: str, tools: list[Tool] | None = None) -> SessionConfig:
    """Build a SessionConfig from standing instructions.

    Args:
        config: Parsed standing-instructions.yaml
        mode: 'triage' for monitoring, 'digest' for content summarization,
              'research' for deep research, 'transcripts' for transcript collection
        tools: Custom tools to register on the session
    """
    models = config.get("models", {})
    model = models.get(mode, models.get("default", "claude-sonnet"))

    # Monitoring works in output/, research gets full project access
    working_dir = str(OUTPUT_DIR) if mode == "triage" else str(PROJECT_ROOT)

    # Base MCP servers — WorkIQ always available
    mcp_servers = {
        "workiq": MCPLocalServerConfig(
            type="local",
            command="workiq",
            args=["mcp"],
            tools=["*"],
            timeout=60000,
        ),
    }

    # Transcript mode needs Playwright MCP for browser automation
    if mode == "transcripts":
        playwright_cfg = config.get("transcripts", {}).get("playwright", {})
        user_data_dir = playwright_cfg.get(
            "user_data_dir",
            "C:/Users/arzielinski/AppData/Local/ms-playwright/mcp-msedge-profile",
        )
        mcp_servers["playwright"] = MCPLocalServerConfig(
            type="local",
            command="npx",
            args=[
                "@playwright/mcp@latest",
                "--browser", "msedge",
                "--headless",
                "--user-data-dir", user_data_dir,
            ],
            tools=["*"],
            timeout=120000,  # Browser ops can be slow
        )

    session_config: SessionConfig = {
        "model": model,
        "system_message": {
            "base": _build_system_prompt(config, mode),
        },
        "mcp_servers": mcp_servers,
        "skill_directories": [
            str(PROJECT_ROOT / "config" / "skills" / "pulse-signal-drafter"),
        ],
        "working_directory": working_dir,
        "streaming": True,
        "on_permission_request": auto_approve_handler,
    }

    if tools:
        session_config["tools"] = tools

    return session_config


def _build_system_prompt(config: dict, mode: str) -> str:
    """Build system prompt from standing instructions."""
    owner = config.get("owner", {})
    monitoring = config.get("monitoring", {})
    priorities = monitoring.get("priorities", [])
    autonomy = monitoring.get("autonomy", {})
    vips = monitoring.get("vip_contacts", [])

    priorities_str = "\n".join(f"- {p}" for p in priorities)
    vips_str = ", ".join(vips) if vips else "None configured"

    base = f"""You are Pulse Agent, an autonomous digital employee working on behalf of {owner.get('name', 'the user')}.
Email: {owner.get('email', 'unknown')}
Timezone: {owner.get('timezone', 'UTC')}

You have access to WorkIQ to read and interact with Microsoft 365 data (emails, calendar, Teams, files).
You have access to local file system, browser, and shell tools.
You MUST use the log_action tool to log every significant action you take with reasoning.
Do NOT ask the user any questions — work autonomously.
"""

    if mode == "triage":
        base += f"""
## Monitoring Mode — Standing Instructions

Your priorities for this cycle:
{priorities_str}

VIP contacts (prioritize these): {vips_str}

Autonomy settings:
- Auto-send emails: {autonomy.get('auto_send', False)}
- Auto-send low-risk (meeting accepts, simple acks): {autonomy.get('auto_send_low_risk', True)}
- Max nudges per follow-up: {autonomy.get('max_nudges', 2)}

## CRITICAL: You MUST make MULTIPLE WorkIQ queries. One broad query is NOT enough.

Follow this multi-step workflow. Each numbered step requires at least one separate WorkIQ query:

### Step 1 — Email Triage
- Ask WorkIQ: "Show me all unread/recent emails from the last 24 hours with sender, subject, and preview"
- For any email from a VIP or marked urgent, ask WorkIQ for the FULL content of that specific email
- For emails that need a reply, draft a response and save it using write_output
- Log each email you triaged with log_action

### Step 2 — Calendar & Meeting Prep
- Ask WorkIQ: "What meetings do I have in the next 12 hours? Include attendees and agenda"
- For each upcoming meeting, ask WorkIQ for CONTEXT: recent emails with those attendees, related documents, previous meeting notes
- Write a meeting brief for each meeting (who, what, prep notes, talking points) using write_output
- Log each brief with log_action

### Step 3 — Teams Activity
- Ask WorkIQ: "What are the most active or important Teams messages and threads from the last 24 hours?"
- For any thread that mentions the owner or has action items, ask WorkIQ for the full thread
- Identify any action items, blockers, or things that need attention
- Log findings with log_action

### Step 4 — Follow-ups & Action Items
- Ask WorkIQ: "What tasks, action items, or follow-ups are overdue or coming due?"
- For items overdue by more than 3 days, draft a nudge message
- Log each follow-up with log_action

### Step 5 — Final Summary
- Write a comprehensive monitoring report using write_output with filename format: monitoring-YYYY-MM-DDTHH-MM.md
- The report MUST include specific details: email subjects, sender names, meeting titles, action items
- Do NOT write vague summaries like "no urgent emails found" — list what you actually saw
- End with a "Needs Your Attention" section for anything the owner should act on personally

REMEMBER: Shallow one-query summaries are useless. Dig deep. Make 5-10+ WorkIQ queries per cycle.
"""
    elif mode == "digest":
        base += """
## Digest Mode — Content Summarization

You are analyzing content that was collected from local files (meeting transcripts,
documents, emails). The content is provided in the user prompt.

Your job:
1. Analyze each piece of content thoroughly
2. Extract TLDRs, decisions, action items, risk flags
3. Generate a structured daily digest
4. Use `write_output` to save the digest as a markdown file
5. Use `log_action` to log each file you analyze

Be SPECIFIC — use names, dates, numbers. Do NOT write vague summaries.
"""
    elif mode == "research":
        base += f"""
## Deep Research Mode

You are executing a research mission. Work autonomously and thoroughly.
Use WorkIQ to pull data from M365. Use local tools to write output files.
Be thorough — this task may take a long time and that's expected.
Write your findings as markdown files using the write_output tool.
Log each significant step with the log_action tool.
When complete, provide a summary of your research and key findings.
"""
    elif mode == "transcripts":
        base += _build_transcript_prompt(config)

    return base


def _build_transcript_prompt(config: dict) -> str:
    """Build the transcript collection system prompt."""
    tc = config.get("transcripts", {})
    lookback_days = tc.get("lookback_days", 7)
    output_dir = tc.get("output_dir", str(INPUT_DIR / "transcripts"))
    max_meetings = tc.get("max_per_run", 10)

    return f"""
## Transcript Collection Mode

Your mission: Collect meeting transcript text from Microsoft Teams and save them as local files.
You MUST save each transcript as a file. Do NOT just extract text and stop — write it to disk.

### Context
Teams meeting transcripts do NOT sync locally as text. They exist only in the Teams/Stream cloud.
You have Playwright (browser automation) to open Teams web in an authenticated Edge session.
You also have WorkIQ to query calendar data.
The "Download" button on transcripts is often disabled (non-organizer). Use DOM scraping instead.

### Output Directory
Save all transcripts to: {output_dir}
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
await (async (page) => {{
  // 1. Find the transcript frame
  const frames = page.frames();
  let tf = null;
  for (const frame of frames) {{
    try {{
      const c = await frame.locator('[role="listitem"]').count();
      if (c > 5) {{ tf = frame; break; }}
    }} catch {{}}
  }}
  if (!tf) return 'ERROR: No transcript frame found';

  // 2. Get scroll dimensions
  const info = await tf.evaluate(() => {{
    const list = document.querySelector('[role="list"]');
    const c = list?.parentElement || list;
    return {{ scrollHeight: c?.scrollHeight || 0, clientHeight: c?.clientHeight || 0 }};
  }});

  // 3. Incremental scroll + collect at each position
  const entries = new Map();
  const step = 300;
  for (let pos = 0; pos <= info.scrollHeight + step; pos += step) {{
    await tf.evaluate((sp) => {{
      const list = document.querySelector('[role="list"]');
      const c = list?.parentElement || list;
      if (c) c.scrollTop = sp;
    }}, pos);
    await new Promise(r => setTimeout(r, 400));

    const items = await tf.evaluate(() => {{
      return Array.from(document.querySelectorAll('[role="listitem"]'))
        .map(el => el.innerText.trim()).filter(Boolean);
    }});
    items.forEach(text => entries.set(text.substring(0, 100), text));
  }}

  // 4. Build transcript text
  let result = '';
  let i = 1;
  for (const text of entries.values()) {{
    result += i + '\\n' + text + '\\n\\n';
    i++;
  }}
  return JSON.stringify({{ entryCount: entries.size, length: result.length, transcript: result }});
}})
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
"""
