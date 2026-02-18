"""Shared session configuration builder — avoids circular imports."""

from pathlib import Path

from copilot import (
    CustomAgentConfig,
    SessionConfig,
    MCPLocalServerConfig,
    Tool,
    PermissionRequest,
    PermissionRequestResult,
)

PROJECT_ROOT = Path(__file__).parent.parent
OUTPUT_DIR = PROJECT_ROOT / "output"
INPUT_DIR = PROJECT_ROOT / "input"
LOCAL_INSTRUCTIONS = PROJECT_ROOT / "config" / "instructions"


def _load_instruction(name: str, config: dict) -> str:
    """Load an instruction file — checks OneDrive first, then local defaults.

    Users can edit instructions from OneDrive; changes are picked up next run.
    """
    onedrive_cfg = config.get("onedrive", {})
    if onedrive_cfg.get("sync_enabled", False):
        onedrive_path = Path(onedrive_cfg.get("path", ""))
        if onedrive_path and str(onedrive_path) != ".":
            onedrive_file = onedrive_path / "Agent Instructions" / f"{name}.md"
            if onedrive_file.exists():
                return onedrive_file.read_text(encoding="utf-8")

    local_file = LOCAL_INSTRUCTIONS / f"{name}.md"
    if local_file.exists():
        return local_file.read_text(encoding="utf-8")

    return ""


def auto_approve_handler(request: PermissionRequest, context: dict) -> PermissionRequestResult:
    """Auto-approve all tool calls — agent runs autonomously."""
    return PermissionRequestResult(kind="approved", rules=[])


def make_user_input_handler(telegram_app, chat_id: int):
    """Create an async UserInputHandler that relays ask_user calls to Telegram.

    When the agent calls ask_user (e.g., to confirm a Teams message), this
    handler sends the question to the user's Telegram chat and waits for
    their reply. Timeout after 120s → returns "no".
    """
    async def handler(request, context):
        from telegram_bot import notify, wait_for_confirmation
        import asyncio

        question = request.get("question", "")
        await notify(telegram_app, chat_id, question)

        try:
            answer = await wait_for_confirmation(chat_id, timeout=120)
        except asyncio.TimeoutError:
            await notify(telegram_app, chat_id, "(Timed out — action cancelled)")
            answer = "no"

        return {"answer": answer, "wasFreeform": True}

    return handler


# ---------------------------------------------------------------------------
# Custom Agent Definitions
# ---------------------------------------------------------------------------

def _workiq_mcp() -> MCPLocalServerConfig:
    """Standard WorkIQ MCP config — reused across agents."""
    return MCPLocalServerConfig(
        type="local",
        command="workiq",
        args=["mcp"],
        tools=["*"],
        timeout=60000,
    )


def _agent_pulse_reader() -> CustomAgentConfig:
    """Agent that finds and reads local Pulse reports."""
    return {
        "name": "pulse-reader",
        "display_name": "Pulse Reader",
        "description": "Finds and reads local Pulse Agent reports — monitoring triage reports, daily digests, intel briefs, and pulse signals. Delegate to this agent when you need to retrieve or summarize local report data.",
        "prompt": """You are the Pulse Reader — a specialist in finding and reading local Pulse Agent reports.

Your working directory is the project root. Reports are under `output/`:

## File Structure
- `output/monitoring-YYYY-MM-DDTHH-MM.md` — Triage reports (email/calendar/Teams summaries)
- `output/digests/YYYY-MM-DD.md` — Daily digests (human-readable)
- `output/digests/YYYY-MM-DD.json` — Daily digests (structured JSON)
- `output/intel/YYYY-MM-DD.md` — External intel briefs (RSS/competitor analysis)
- `output/pulse-signals/*.md` — Drafted GBB Pulse signals

## How to Find Reports
1. Use list_directory on the relevant folder to see available files
2. Pick the most recent file (filenames are date-sorted)
3. Use read_file to read it
4. Return the content to the caller

## Rules
- ALWAYS use list_directory first, then read_file. Never guess filenames.
- Return the FULL content — let the caller decide what to summarize.
- If no reports exist for the requested type, say so clearly.
- Do NOT call WorkIQ — you only read local files.""",
        "infer": True,
    }


def _agent_m365_query() -> CustomAgentConfig:
    """Agent that queries M365 data via WorkIQ."""
    return {
        "name": "m365-query",
        "display_name": "M365 Query",
        "description": "Queries Microsoft 365 data via WorkIQ — emails, calendar, Teams messages, people, and documents. Delegate to this agent when you need LIVE data from Outlook, Teams, or calendar that isn't in local reports.",
        "prompt": """You are the M365 Query agent — a specialist in retrieving Microsoft 365 data via WorkIQ.

## What You Can Query
- Emails (inbox, sent, threads)
- Calendar (meetings, attendees, agendas)
- Teams messages (channels, chats, mentions)
- People (contacts, org info)
- Documents (recent files, shared items)

## How to Query
Use the WorkIQ ask_work_iq tool. Be SPECIFIC in your queries:
- BAD: "What's new?" (too vague)
- GOOD: "Show me emails from the last 24 hours that need a reply, with sender, subject, and preview"
- GOOD: "What meetings do I have tomorrow? Include attendees and agenda"

## Rules
- Make ONE focused query per request. Don't try to get everything at once.
- Return the full WorkIQ response — let the caller decide what to summarize.
- If WorkIQ times out or returns an error, say so clearly.
- Do NOT read or write local files — you only query M365 data.""",
        "mcp_servers": {
            "workiq": _workiq_mcp(),
        },
        "infer": True,
    }


def _agent_digest_writer(config: dict) -> CustomAgentConfig:
    """Agent that produces structured digest output."""
    output_rules = _load_instruction("digest-output-rules", config)
    return {
        "name": "digest-writer",
        "display_name": "Digest Writer",
        "description": "Analyzes collected content and produces a structured daily digest with TLDRs, decisions, action items, risk flags, and a human-readable summary. Delegate to this agent with the collected content to generate digest output.",
        "prompt": f"""You are the Digest Writer — a specialist in producing structured daily digests.

You receive collected content (transcripts, documents, emails, RSS articles, WorkIQ summaries) and produce a structured digest.

{output_rules}

## Rules
- Use write_output to save both JSON and markdown files.
- Use log_action to log your analysis.
- Be SPECIFIC — names, dates, amounts. No vague summaries.
- FILTER OUT everything already dealt with.
- TARGET: 30-50 lines for the markdown digest. Be brutal about what makes the cut.""",
        "infer": False,
    }


def _playwright_mcp(config: dict, cdp_endpoint: str | None = None) -> MCPLocalServerConfig:
    """Playwright MCP config — reused across agents that need browser automation.

    When cdp_endpoint is provided, connects to an existing shared browser
    instead of launching a new one (avoids user-data-dir profile locking).
    """
    if cdp_endpoint:
        return MCPLocalServerConfig(
            type="local",
            command="npx",
            args=[
                "@playwright/mcp@latest",
                "--cdp-endpoint", cdp_endpoint,
            ],
            tools=["*"],
            timeout=120000,
        )

    # Fallback: launch own browser (CLI --once mode, no shared browser)
    playwright_cfg = config.get("transcripts", {}).get("playwright", {})
    default_data_dir = str(Path.home() / "AppData/Local/ms-playwright/mcp-msedge-profile")
    user_data_dir = playwright_cfg.get("user_data_dir", default_data_dir)
    return MCPLocalServerConfig(
        type="local",
        command="npx",
        args=[
            "@playwright/mcp@latest",
            "--browser", "msedge",
            "--headless",
            "--user-data-dir", user_data_dir,
        ],
        tools=["*"],
        timeout=120000,
    )



def _agent_signal_drafter() -> CustomAgentConfig:
    """Agent that drafts GBB Pulse signals."""
    return {
        "name": "signal-drafter",
        "display_name": "Signal Drafter",
        "description": "Drafts GBB Pulse signals from customer intel, wins, losses, escalations, compete intel, or product feedback found in content. Delegate to this agent with source material to draft signals.",
        "prompt": """You are the Signal Drafter — a specialist in drafting GBB Pulse signals.

GBB Pulse signals are field insights fed back to product groups and go-to-market teams.

## When to Draft a Signal
- Customer Win — deal closed, deployment succeeded, competitive displacement
- Customer Loss — lost to competitor, blocked by technical issue
- Customer Escalation — SLT-level issue, $$$ at risk
- Compete Signal — competitor pricing change, feature gap, strategy shift
- Product Signal — feature request, bug, performance issue
- IP/Initiative — reusable asset, best practice

## Output Format
Save each signal as `pulse-signals/YYYY-MM-DD-{slug}.md` using write_output:

```markdown
# [Signal Type]: [Title]

- **Customer/Topic**: name
- **Type**: Win / Loss / Escalation / Compete / Product / IP
- **Impact**: quantify in $$ or strategic terms
- **Description**: 3-4 sentences — situation, approach, outcome
- **Compete**: competitor name if applicable
- **Action/Ask**: what should happen next
```

## Rules
- Only draft signals with SPECIFIC facts (customer names, deal sizes, product names)
- Do NOT fabricate — if the source material is vague, skip it
- One file per signal
- Use log_action to log each signal drafted
- If nothing qualifies, say so — don't force it""",
        "infer": False,
    }


# ---------------------------------------------------------------------------
# Session Config Builder
# ---------------------------------------------------------------------------

def build_session_config(
    config: dict,
    mode: str,
    tools: list[Tool] | None = None,
    telegram_app=None,
    chat_id: int | None = None,
    cdp_endpoint: str | None = None,
) -> SessionConfig:
    """Build a SessionConfig from standing instructions.

    Args:
        config: Parsed standing-instructions.yaml
        mode: 'triage' for monitoring, 'digest' for content summarization,
              'research' for deep research, 'transcripts' for transcript collection,
              'chat' for conversational queries via Telegram
        tools: Custom tools to register on the session
        telegram_app: Telegram Application (for ask_user → Telegram relay)
        chat_id: Telegram chat ID (for ask_user → Telegram relay)
        cdp_endpoint: CDP endpoint for shared browser (avoids profile locking)
    """
    models = config.get("models", {})
    model = models.get(mode, models.get("default", "claude-sonnet"))

    # Triage works in output/, other modes get full project access
    working_dir = str(OUTPUT_DIR) if mode == "triage" else str(PROJECT_ROOT)

    # MCP servers — chat mode delegates to m365-query agent instead
    mcp_servers = {}
    if mode != "chat":
        mcp_servers["workiq"] = _workiq_mcp()

    # Playwright MCP for browser automation
    # Chat mode needs it for teams-sender (at session level due to copilot-cli#693)
    if mode in ("transcripts", "chat"):
        mcp_servers["playwright"] = _playwright_mcp(config, cdp_endpoint=cdp_endpoint)

    # Custom agents per mode
    custom_agents = []
    if mode == "chat":
        custom_agents = [_agent_pulse_reader(), _agent_m365_query()]
    elif mode == "digest":
        custom_agents = [
            _agent_m365_query(),
            _agent_digest_writer(config),
            _agent_signal_drafter(),
        ]

    # Chat mode: replace the CLI's default system prompt entirely so the model
    # doesn't think it's "GitHub Copilot CLI".  Other modes: append to the
    # CLI's built-in prompt (keeps its tool-usage guidance).
    prompt_text = _build_system_prompt(config, mode)
    if mode == "chat":
        sys_msg = {"mode": "replace", "content": prompt_text}
    else:
        sys_msg = {"mode": "append", "content": prompt_text}

    session_config: SessionConfig = {
        "model": model,
        "system_message": sys_msg,
        "mcp_servers": mcp_servers,
        "custom_agents": custom_agents,
        "skill_directories": [
            str(PROJECT_ROOT / "config" / "skills" / "pulse-signal-drafter"),
            str(PROJECT_ROOT / "config" / "skills" / "teams-sender"),
        ],
        "working_directory": working_dir,
        "streaming": True,
        "on_permission_request": auto_approve_handler,
    }

    # Chat mode: block CLI self-docs + enable ask_user for confirmations
    if mode == "chat":
        session_config["excluded_tools"] = ["fetch_copilot_cli_documentation"]
        if telegram_app and chat_id:
            session_config["on_user_input_request"] = make_user_input_handler(
                telegram_app, chat_id
            )

    if tools:
        session_config["tools"] = tools

    return session_config


# ---------------------------------------------------------------------------
# System Prompts
# ---------------------------------------------------------------------------

def _build_system_prompt(config: dict, mode: str) -> str:
    """Build system prompt from standing instructions."""
    monitoring = config.get("monitoring", {})
    priorities = monitoring.get("priorities", [])
    autonomy = monitoring.get("autonomy", {})
    vips = monitoring.get("vip_contacts", [])

    priorities_str = "\n".join(f"- {p}" for p in priorities)
    vips_str = ", ".join(vips) if vips else "None configured"

    base = """You are Pulse Agent, an autonomous digital employee.

You have access to local file system, browser, and shell tools.
You MUST use the log_action tool to log every significant action you take with reasoning.
Do NOT ask the user any questions — work autonomously.
"""

    if mode == "triage":
        base += f"""
You have access to WorkIQ to read and interact with Microsoft 365 data (emails, calendar, Teams, files).
Use WorkIQ to determine who you are working for (name, email, timezone) — do not assume.

## Monitoring Mode — Standing Instructions

Your priorities for this cycle:
{priorities_str}

VIP contacts (prioritize these): {vips_str}

Autonomy settings:
- Auto-send emails: {autonomy.get('auto_send', False)}
- Auto-send low-risk (meeting accepts, simple acks): {autonomy.get('auto_send_low_risk', True)}
- Max nudges per follow-up: {autonomy.get('max_nudges', 2)}

"""
        base += _load_instruction("triage", config)
    elif mode == "digest":
        base += """
You have access to WorkIQ via the **m365-query** agent for live M365 data.
You have the **digest-writer** agent to produce structured output.
You have the **signal-drafter** agent to draft GBB Pulse signals.

Orchestrate these agents to produce a complete daily digest.
"""
        base += "\n" + _load_instruction("digest", config) + "\n"
    elif mode == "intel":
        base += "\nYou have access to WorkIQ to read and interact with Microsoft 365 data.\n"
        base += "\n" + _load_instruction("intel", config) + "\n"
    elif mode == "research":
        base += "\nYou have access to WorkIQ to read and interact with Microsoft 365 data.\n"
        base += "\n" + _load_instruction("research", config) + "\n"
    elif mode == "transcripts":
        base += _build_transcript_prompt(config)
    elif mode == "chat":
        base = _load_instruction("chat", config)

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
