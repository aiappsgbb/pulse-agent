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
        mode: 'triage' for monitoring, 'research' for deep research,
              'transcripts' for transcript collection
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

### Context
Teams meeting transcripts do NOT sync locally as text. They exist only in the Teams/Stream cloud.
You have Playwright (browser automation) to open Teams web in an authenticated Edge session.
You also have WorkIQ to query calendar data.

### Output Directory
Save all transcripts to: {output_dir}
Filename format: YYYY-MM-DD_meeting-title-slug.vtt (or .txt if VTT not available)

### Step-by-Step Workflow

#### Step 1 — Get Recent Meetings from WorkIQ
- Ask WorkIQ: "List my meetings from the last {lookback_days} days. Include meeting title, date/time, and organizer."
- Log the meeting list with log_action.

#### Step 2 — Check Which Transcripts Already Exist
- Check the output directory ({output_dir}) for files already downloaded.
- Skip any meeting whose transcript file already exists (avoid re-downloading).
- Log which meetings are new vs already collected.

#### Step 3 — Open Teams Web in Browser
- Use Playwright to navigate to https://teams.microsoft.com
- Wait for the page to fully load (you should see the Teams interface).
- If you see a sign-in page, the browser session is not authenticated — log this as an error and stop.
- Log successful login with log_action.

#### Step 4 — Navigate to Each Meeting's Transcript
For each meeting (up to {max_meetings} per run):

1. Navigate to the Teams Calendar view
2. Find the meeting by date and title
3. Click on the meeting to open it
4. Look for "Recap" or "Transcript" tab/section
5. If transcript is available:
   - Look for a "Download" button or option to copy/export the transcript text
   - If there's a download option, download the .vtt file
   - If no download button, select all transcript text and copy it
   - Save the content to {output_dir}/YYYY-MM-DD_meeting-slug.vtt
6. If no transcript available for this meeting, log it and move on
7. Log each transcript collected with log_action (meeting title, date, file size)

#### Step 5 — Summary
- Write a summary using write_output:
  - Filename: transcripts/collection-report-YYYY-MM-DD.md
  - Total meetings found
  - Transcripts successfully collected (with filenames)
  - Meetings skipped (already collected or no transcript)
  - Any errors encountered
- Log the final summary with log_action.

### Important Notes
- Be patient with page loads — Teams web can be slow. Wait for elements to appear.
- If a page fails to load, retry once before skipping.
- Do NOT click on anything that would modify data (delete, edit, etc.) — READ ONLY.
- If you encounter an error, log it and continue with the next meeting.
- The browser is YOUR authenticated session — you have the same access as if you opened Teams manually.
- Process meetings from newest to oldest (most recent first).
"""
