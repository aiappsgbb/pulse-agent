## mem0 — Persistent Memory (MCP Server)

Use the mem0 MCP server **proactively** in every conversation. Do NOT wait for the user to ask.

**When to store memories (add_memory):**
- Architectural decisions made during the conversation
- Bug fixes and their root causes
- User preferences or workflow corrections ("always do X", "never do Y")
- Solutions to tricky problems that took multiple attempts
- Key findings from codebase exploration

**When to search memories (search_memory):**
- At the START of every conversation — search for context relevant to the user's request
- Before making architectural decisions — check if a decision was already made
- When encountering a bug or issue — search for prior fixes
- When unsure about project conventions — search for established patterns

**Rules:**
- Always use `user_id: "gbb-pulse"` (configured as default)
- Store memories with enough context to be useful standalone (not "fixed the bug" but "fixed Teams inbox scanner bug: unread badge selector changed from [class*=Badge] to [class*=unread]")
- Search BEFORE you start work, store AFTER you finish
- If mem0 tools are unavailable (server not running), proceed normally — don't block on it

---

# Pulse Agent — Your Information Processing Engine

## The Problem

Knowledge workers are drowning. You have 8 meetings a day and retain 20% of what's said. You're CC'd on 50 email threads you'll never read. Competitors announce pricing changes at 2 AM. Shared documents pile up unread. You're not failing because you're lazy — you're failing because there's physically too much information for one human to consume.

**Copilot helps when you ask. Pulse Agent works when you don't.**

## Vision

A local-first autonomous agent that **consumes everything you can't** — both internal and external — and tells you only what matters. It runs overnight, processes hours of content, and delivers a structured digest by morning.

Not a copilot. Not a chatbot. An information processing engine with standing instructions and full local machine access.

**"I had 8 meetings yesterday. I was distracted in half of them. At 7 AM, Pulse Agent told me the 3 things that actually need my attention — including an escalation I completely missed."**

## Modes

### Mode 1: Transcript Collection (`--mode transcripts`)
Automated extraction of meeting transcripts from Microsoft Teams using Playwright browser automation.

Teams transcripts exist only in the cloud (Stream/Teams). They do NOT sync as text files to local OneDrive. Recordings sync as `.mp4` (no text), notes as `.loop` (binary). The agent solves this by automating the browser to extract transcript text directly from the Teams web UI.

**How it works:**
1. Launches Edge with persistent auth profile (`--user-data-dir`)
2. Navigates to Teams Calendar -> multi-week lookback (configurable, default 2 weeks)
3. For each week: scans for meetings with "View recap" buttons (indicates recorded meetings)
4. For each meeting: clicks recap -> Transcript tab -> scrolls through virtualized list -> extracts all text
5. **Compresses via GHCP SDK** — sends raw transcript through a lightweight Copilot session to extract structured notes (TLDR, decisions, action items, key quotes). Reduces ~20k chars to ~2k.
6. Saves compressed `.md` file to `transcripts/` under PULSE_HOME (falls back to raw `.txt` if SDK unavailable)

**Automatic collection:** Transcript collection runs automatically as Phase 0a of the digest pipeline — no separate manual step needed. Can also be triggered standalone via `--mode transcripts`.

**Key technical detail -- Fluent UI virtualized list:**
Teams renders transcripts using Microsoft's Fluent UI `ms-List` component inside a `ms-FocusZone` scroll wrapper. Only ~50 items near the viewport exist in the DOM at any time. The actual scroll container is the `ms-FocusZone` ancestor (with `overflow-y: auto`), NOT the list or its direct parent. The agent scrolls this container in steps, collecting newly-rendered items at each position, until all entries are captured.

**Output:** `$PULSE_HOME/transcripts/2026-02-16_meeting-title-slug.md` (compressed) or `.txt` (raw fallback)

### Mode 2: Internal Digest (`--mode digest`)
Scans configured local folders for content you haven't had time to process.

The agent is **folder-driven** — it processes whatever lands in configured input paths. Transcript collection and compression run overnight in the knowledge pipeline (Mode 7), so the digest just reads whatever was already collected. Six-phase approach:
1. **Content collection** (Python, no LLM): Scans input folders, extracts text from supported file types, tracks what's already been processed via a state file for incremental runs.
2. **RSS feed collection** (Python, no LLM): Fetches RSS feeds, deduplicates, and pre-filters articles for relevance via SDK.
3. **Inbox scanning** (Playwright, no LLM): Scans Teams inbox, Outlook inbox, and calendar via Playwright for real-time unread state — serves as ground truth when WorkIQ is unavailable.
4. **Cross-reference phase**: Loads the previous digest JSON for carry-forward items. Items older than 5 days are auto-dropped as stale. Loads dismissed items and user notes from `.digest-actions.json` (dismissed items auto-expire after 30 days).
5. **Project loading phase**: Loads persistent project memory files (`projects/*.yaml` under PULSE_HOME), builds commitment summaries (overdue + approaching deadline), and injects project context into the prompt.
6. **Analysis phase** (GHCP SDK + LLM): Sends collected content, inbox scans, project context, and carry-forward items to a Copilot session. The `project-researcher` agent discovers new projects and updates existing ones. The `digest-writer` produces a project-oriented digest grouped by engagement. If WorkIQ fails, the browser inbox scans serve as fallback ground truth.

**What it processes:**
- Meeting transcripts (`.vtt`, `.txt`, `.md` -- any text format)
- Documents (`.pptx`, `.docx`, `.pdf`, `.xlsx`, `.csv`)
- Email exports (`.eml`)
- RSS feed articles (configured in standing instructions)

**Digest accuracy controls:**
- Automatic transcript collection (multi-week lookback) ensures fresh meeting data
- Carry-forward staleness cutoff (5 days) prevents zombie items
- Dismissed items auto-expire after 30 days (TTL cleanup)
- Teams inbox scan provides ground truth for unread messages
- WorkIQ cross-references what's been replied to / acted on
- Article filter failure is flagged explicitly (UNFILTERED warning in prompt)
- Browser unavailability produces clear UNAVAILABLE messages (not crashes)
- Strict responsibility filter: only surfaces items where YOU need to act

**1-tap action buttons:** Digest items that need a reply (`reply_needed`) include `suggested_actions` with drafted replies — same format as triage. The TUI renders action buttons for each actionable item. The user can review drafts and send with one keystroke, exactly like triage.

**Output:** `$PULSE_HOME/digests/YYYY-MM-DD.json` (structured, with action buttons) + `$PULSE_HOME/digests/YYYY-MM-DD.md` (human-readable)

### Mode 3: Monitoring with Actionable Triage (`--mode triage`)
Real-time 30-minute triage cycle with 1-tap action buttons. Three-phase approach:
1. **Inbox scans** (Playwright, no LLM): Scans Teams chat list, Outlook inbox, and calendar for real-time state that WorkIQ cannot provide.
2. **Context assembly** (GHCP SDK + local search): The agent uses `search_local_files` to look up sender names and topics in local transcripts before triaging, providing meeting context for informed suggestions.
3. **Enrichment + drafting** (GHCP SDK + WorkIQ): For each unread message/email, queries WorkIQ for sender context and thread history. Produces a structured JSON output with suggested actions and drafted replies.

**1-tap action flow:**
- Triage produces a structured JSON (`monitoring-*.json`) with items and `suggested_actions`
- Each action includes a `draft` field and `action_type` (teams reply, email reply, schedule meeting)
- TUI renders items with action key bindings (D=dismiss, R=reply, N=note)
- Reply action shows the draft for review (not auto-sent)
- User approves to send, or cancels to discard
- Approved Teams/email actions execute via **deterministic Playwright senders** (no LLM in the loop — fast, reliable)
- Meeting scheduling still routes through chat mode (needs LLM for Copilot Chat interaction)
- Dismiss action marks items as handled via `.digest-actions.json`

Also checks emails via Outlook inbox scan + WorkIQ (TO field only, not CC) and upcoming meetings via calendar scan + WorkIQ.

**Output:** `$PULSE_HOME/monitoring-YYYY-MM-DDTHH-MM.md` (report) + `$PULSE_HOME/monitoring-YYYY-MM-DDTHH-MM.json` (structured actions)

### Mode 4: Deep Research (`--mode research`)
Executes autonomous research missions using WorkIQ + local tools. Thorough, long-running tasks (up to 60 min timeout).

**Output:** `$PULSE_HOME/research-*.md`

### Mode 5: External Intel (`--mode intel`)
Fetches RSS feeds from configured sources (competitors, industry news), analyzes articles for relevance, and generates a concise intelligence brief. Configured via `intel.rss_feeds` in standing instructions.

**Output:** `$PULSE_HOME/intel/YYYY-MM-DD.md`

### Mode 6: Chat (TUI)
Conversational interface — natural language queries via the TUI Chat tab. Responses stream progressively via file-based IPC (daemon writes `.chat-stream.jsonl`, TUI reads and renders). The agent has access to WorkIQ, local file search, and browser action tools (`send_teams_message`, `send_email_reply`). Browser actions use deterministic Playwright scripts (same shared browser as collectors), NOT the Playwright MCP server.

### Mode 7: Knowledge Mining (`--mode knowledge`)
Agent-driven pipeline that runs overnight to archive communications and enrich project memory. Minimal Python orchestration, maximum agent autonomy. Three-phase pipeline:
1. **Phase 0: Transcript collection + compression** (Playwright + GHCP SDK): Collects fresh transcripts from Teams (multi-week lookback), then compresses any raw `.txt` transcripts into structured `.md` notes via SDK.
2. **Phase 1: Archive session** (GHCP SDK + WorkIQ): The `knowledge-miner` agent fetches recent emails and Teams messages via WorkIQ, discovers new projects, and archives relevant communications.
3. **Phase 2: Per-project enrichment** (GHCP SDK + WorkIQ): One focused session per active/blocked project. The `knowledge-miner` agent uses WorkIQ and `search_local_files` to update project timelines, stakeholders, commitments, and watch queries.

Knowledge mining runs as a scheduled overnight job (typically 02:00) so that the morning digest has fresh project context and compressed transcripts.

**Output:** Updated `$PULSE_HOME/projects/*.yaml` files + compressed transcripts in `$PULSE_HOME/transcripts/`

## Architecture

```
PULSE_HOME ($USERPROFILE/OneDrive - Microsoft/Documents/Pulse)
  All persistent data lives here — synced via OneDrive.
  |-- transcripts/              <-- collected by transcript mode or manual drop
  |-- documents/                <-- docs, presentations, spreadsheets
  |-- emails/                   <-- email exports
  |-- digests/                  <-- structured + human-readable digests
  |-- intel/                    <-- external intel briefs
  |-- projects/                 <-- persistent project memory (per-engagement)
  |-- pulse-signals/            <-- drafted GBB Pulse signals
  |-- jobs/                     <-- task queue (pending/ + completed/)
  |-- logs/                     <-- structured JSONL audit trail
  |-- Agent Instructions/       <-- editable instruction overrides
  |-- chat-history.md           <-- conversation memory
  +-- .scheduler.json, .digest-state.json, etc.  <-- state files

Transcript Collection (Playwright + GHCP SDK compression)
  -> Teams Web UI -> Calendar -> Recap -> Transcript tab
  -> DOM scraping of virtualized ms-List via FocusZone scrolling
  -> SDK compression: raw text -> structured notes (TLDR, decisions, actions, quotes)
  -> Saves compressed .md files to $PULSE_HOME/transcripts/ (raw .txt fallback)

Pulse Agent (Python daemon, always-on)
  -> asyncio event loop with 3 concurrent tasks:
    -> TUI backend + winotify toasts (status writes, chat polling, stream deltas, toast alerts)
    -> Scheduler (every 60s: config-driven schedules + job sync)
    -> Job worker (processes queue, one at a time)
  -> GHCP SDK (CopilotClient -> Copilot CLI server mode, JSON-RPC)
    -> WorkIQ MCP (calendar context, email summaries, people info)
    -> Custom tools (14 tools -- see table below)
    -> Session hooks (audit trail, path guardrails, error recovery, metrics)
    -> Standing Instructions (what to watch, what to flag, what matters to you)
  -> Event-driven completion (EventHandler + asyncio.wait_for, not send_and_wait)
  -> Chat history managed by SDK agent (reads/writes $PULSE_HOME/chat-history.md)

Consumption -- Textual TUI + winotify toasts
  -> TUI: 3-tab dashboard (Inbox | Projects | Chat)
  -> Chat tab: natural language -> streaming reply via file IPC
  -> Key bindings: Ctrl+D/T/I/X -> queue jobs, D/R/N -> dismiss/reply/note
  -> Triage: action keys -> draft review -> send
    -> Teams reply: teams-sender skill (Playwright on Teams)
    -> Email reply: email-reply skill (Playwright on Outlook Web)
    -> Schedule meeting: meeting-scheduler skill (Playwright on M365 Copilot Chat)
  -> winotify: proactive Windows toast notifications for urgent items
  -> StatusBar: daemon uptime + queue size
```

### Transcript Collection -- Technical Details

The transcript viewer in Teams uses a **virtualized list** (Fluent UI `ms-List`). The DOM structure:

```
DIV.ms-FocusZone  (overflow-y: auto, scrollHeight=15549, clientHeight=392)  <- REAL scroll container
  +-- DIV          (no overflow)
      +-- DIV.ms-List [role="list"]  (scrollHeight == clientHeight, NO overflow)
          +-- DIV (page container)
              |-- DIV [role="listitem"] aria-setsize="258"  <- speaker header
              |-- DIV [role="listitem"] aria-setsize="258"  <- transcript text
              +-- ... (~50 items rendered at any time)
```

The `aria-setsize` attribute tells us the total expected items (e.g. 258). Only ~50 are in the DOM at once. Scrolling the FocusZone triggers re-rendering of items near the new viewport position. The agent scrolls in steps of `clientHeight - 50px` (with overlap), collecting all visible `[role="listitem"]` elements at each position, until no new items appear for 8 consecutive steps.

### Config-Driven Modes (`config/modes.yaml`)

Each mode is defined declaratively in `config/modes.yaml` — Python code reads this and assembles `SessionConfig` without hardcoded if/elif chains. Each mode entry specifies:
- `model_key` — which model to use (maps to `models` in standing-instructions.yaml)
- `mcp_servers` — list of MCP server names to attach
- `agents` — sub-agent names to load from `config/prompts/agents/`
- `system_prompt` + `system_prompt_mode` — prompt file path and "append" vs "replace"
- `trigger_prompt` — template for the initial user message, with `{{variable}}` interpolation
- `pre_process` — data collection step before agent call (e.g., `scan_teams_inbox`, `collect_content_and_feeds`)
- `excluded_tools` — tools to block from the agent (e.g., Copilot CLI built-ins in chat mode)
- `standalone: true` — mode has no SDK session (e.g., transcripts uses Playwright directly)

### Browser-Based Inbox Scanning (Playwright, no LLM)

Before each monitor and digest cycle, the agent scans three M365 surfaces via Playwright browser automation. This provides real-time data that WorkIQ cannot — unread indicators and actual inbox/calendar state. These scans serve as **ground truth** when WorkIQ is unavailable.

**Teams Inbox Scanner** (`collectors/teams_inbox.py`):
1. Navigates to `teams.microsoft.com/v2/` Chat view
2. Waits for chat tree to render: `[role="treeitem"][data-item-type="chat"]` (level 2 items in the tree)
3. Extracts chat name, time, preview from `innerText` (newline-separated lines)
4. Checks for unread indicators: badge elements (`[class*="Badge"]`, `[class*="unread"]`), `aria-label` attributes
5. Returns structured list: `{name, preview, time, unread, raw}`
6. Falls back to raw `[role="tree"]` text if structured extraction finds nothing

**Outlook Inbox Scanner** (`collectors/outlook_inbox.py`):
1. Navigates to `outlook.office.com/mail/inbox`
2. Extracts `[role="option"][data-convid]` mail items
3. Parses `aria-label` for status flags (Unread, Has attachments, Flagged, Replied, etc.)
4. Parses `innerText` for cleaner sender/subject split (filters icon characters)
5. Returns structured list: `{sender, subject, preview, time, unread, has_attachment, replied}`
6. Falls back to raw `[role="listbox"]` text if structured extraction finds nothing

**Calendar Scanner** (`collectors/calendar.py`):
1. Navigates to `outlook.office.com/calendar/view/workweek`
2. Clicks "+N more events" buttons to reveal overflow events
3. Extracts `div[aria-label*="event" i]` and `div[aria-label*="meeting" i]` with deduplication
4. Parses comma-separated aria-label: title, time range, date, organizer, status, Teams flag, recurring
5. Detects declined events (title starts with "Declined:")
6. Returns structured list grouped by date: `{title, start_time, end_time, date, organizer, status, is_teams, is_recurring, is_declined}`
7. Returns `None` if browser unavailable (distinct from `[]` = scanned, nothing found)

All three scanners use the shared browser (same Edge instance as transcript collection).

**Output:** Injected into trigger prompts as `{{teams_inbox}}`/`{{teams_inbox_block}}`, `{{outlook_inbox_block}}`, and `{{calendar_block}}`.

### WorkIQ Integration

WorkIQ accesses M365 data through the Copilot data layer. Configured as an MCP server with `tools=["*"]` and `timeout=60000`.

**What WorkIQ gives us:**
- Calendar (upcoming meetings, attendees)
- Emails (threads you're on)
- Teams messages (channels you're in)
- Documents you have access to
- People information

**What WorkIQ does NOT give us:**
- Transcripts (hence the Playwright-based transcript collection)
- Real-time unread message indicators (hence the Playwright-based inbox scanners)
- Content from meetings you declined/didn't attend
- Access beyond your own M365 permission scope

**WorkIQ failure handling:** When WorkIQ returns errors (e.g., "Failed to create conversation"), the agent falls back to browser inbox scans (Teams + Outlook + Calendar) as primary source of truth. Carry-forward items are only kept if corroborated by the inbox scans. Email items are checked against the Outlook scan; if not found and >3 days old, they're dropped.

### Event-Driven Session Management

SDK sessions use event-driven completion instead of `send_and_wait()`:
- `EventHandler` tracks `SESSION_IDLE` (done) and `SESSION_ERROR` events
- Callers use `session.send()` + `asyncio.wait_for(handler.done.wait(), timeout=...)`
- Timeouts: 1800s for chat/triage/digest/intel, 3600s for research
- Partial results returned on timeout if available

### Streaming Replies (TUI)

Chat responses stream progressively into the TUI Chat tab via file-based IPC:
- Daemon writes delta chunks to `.chat-stream.jsonl` as LLM deltas arrive
- TUI reads the JSONL file and renders progressively
- File-based IPC keeps daemon and TUI processes independent (no sockets)
- Communication protocol: `.chat-request.json` (TUI → daemon) → `.chat-stream.jsonl` (daemon → TUI)

### Config-Driven Scheduler + OneDrive Job Sync

All periodic scheduling is config-driven — defined in `standing-instructions.yaml` under `schedule:`. No hardcoded heartbeat or catch-up logic. Users can change digest time, triage frequency, or intel schedule by editing one YAML section.

**Default schedule config:**
```yaml
schedule:
  - id: morning-digest
    type: digest
    pattern: "daily 07:00"
    description: "Morning digest with transcript collection"
  - id: triage
    type: monitor
    pattern: "every 30m"
    description: "Inbox triage every 30 minutes"
    office_hours_only: true
  - id: daily-intel
    type: intel
    pattern: "daily 09:00"
    description: "Morning intel brief"
```

**How it works:**

**1. Config sync** (`ensure_default_schedules`):
- On daemon startup, syncs config schedules into `.scheduler.json`
- Config is authoritative for patterns/descriptions; state preserves `last_run`/`enabled`
- New schedules seed with `last_run=None` so catch-up fires naturally
- Agent-created schedules (via SDK tools) are left untouched
- Changing a pattern in config takes effect on next daemon restart

**2. Cron-like execution** (`.scheduler.json`):
- Patterns: `daily HH:MM`, `weekdays HH:MM`, `every Nh`, `every Nm`
- Minimum interval: 5 minutes (guard against runaway schedules)
- `office_hours_only: true` — schedule only fires during configured office hours
- Agent can also create/list/cancel schedules via SDK tools at runtime

**3. Automatic catch-up:**
- No separate "check missed" logic needed — `is_due("daily 07:00")` with `last_run=None` returns True if it's past 07:00
- If the daemon was off overnight and starts at 10am, the digest fires immediately

**4. Job sync** (`sync_jobs_from_onedrive`):
- Scans `$PULSE_HOME/jobs/pending/` for new YAML job files every 60 seconds
- Since data lives directly on OneDrive, no file copying needed — just enqueues to in-memory job queue
- Picks up inter-agent requests, externally-dropped research tasks, etc.
- Deduplicates: tracks enqueued files in-memory to avoid re-queueing
- This is how agents discover tasks from other agents — latency is ~60 seconds

## Key Insight: Why GHCP SDK, Not Just Copilot?

| Capability | M365 Copilot | Pulse Agent (GHCP SDK) |
|------------|-------------|----------------------|
| Answers questions when asked | Yes | -- |
| Runs autonomously without prompting | No | **Yes** |
| Reads local files (OneDrive sync) | No | **Yes** |
| Searches transcripts for context before responding | No | **Yes** |
| Batch-processes 50 transcripts overnight | No | **Yes** |
| Scrapes public web sources on schedule | No | **Yes** |
| Consistent structured output every time | No | **Yes** |
| Configurable standing instructions | No | **Yes** |
| Full audit trail of actions | No | **Yes** |
| Orchestrates multiple MCP servers | No | **Yes** |
| 1-tap action buttons with drafted replies | No | **Yes** |
| Schedules meetings via M365 Copilot Chat | Manual | **1-tap from triage** |
| Agent-to-agent communication across team | No | **Yes (OneDrive task queue)** |

## Tech Stack

- **Language**: Python 3.12 (`github-copilot-sdk`)
- **Agent Runtime**: GHCP SDK -> Copilot CLI (server mode, JSON-RPC)
- **User Interface**: Textual TUI (3-tab dashboard) + winotify (Windows toast notifications)
- **Transcript Collection**: Playwright Python library (direct, no MCP) — DOM scraping of Teams web UI + GHCP SDK compression
- **Inbox Scanning**: Playwright — real-time Teams, Outlook, and Calendar scanning
- **Internal Data**: Local file system + WorkIQ MCP (calendar/email context)
- **External Data**: RSS feeds via feedparser
- **Models**: Multi-model routing via config — `gpt-4.1` for triage/chat, `claude-sonnet` for digest, `claude-opus` for research
- **Logging**: Structured action logs (local JSONL)
- **Config**: YAML standing instructions (`$PULSE_HOME/standing-instructions.yaml`, falls back to `config/standing-instructions.yaml`)

## Data Storage: PULSE_HOME

All persistent data lives in `$PULSE_HOME` — an OneDrive-synced folder. The repo contains only code + prompt templates.

```bash
# Set in .env (already gitignored)
PULSE_HOME=$USERPROFILE/OneDrive - Microsoft/Documents/Pulse
```

**Config resolution chain**: `--config` flag > `PULSE_CONFIG` env var > `$PULSE_HOME/standing-instructions.yaml` > `config/standing-instructions.yaml` (repo template fallback)

**Fallback**: When `PULSE_HOME` is not set, defaults to `PROJECT_ROOT` for development. All named directory constants (`TRANSCRIPTS_DIR`, `DIGESTS_DIR`, etc.) are defined in `src/core/constants.py` and derived from `PULSE_HOME`.

```
$PULSE_HOME/
├── transcripts/              # Meeting transcripts (.md compressed, .txt raw)
├── documents/                # User-dropped docs (.pptx, .docx, .pdf, etc.)
├── emails/                   # Email exports (.eml)
├── digests/                  # Digest output (.json structured + .md readable)
├── intel/                    # Intel briefs (.md)
├── projects/                 # Project memory (.yaml per engagement)
├── pulse-signals/            # Drafted GBB Pulse signals (.md)
├── jobs/                     # Task queue: pending/ + completed/
├── logs/                     # Structured JSONL audit trail
├── Agent Instructions/       # Editable instruction overrides (.md)
├── chat-history.md           # Conversation memory
├── .scheduler.json           # Schedule state
├── .digest-state.json        # Content tracking (incremental processing)
├── .intel-state.json         # Feed dedup state
├── .digest-actions.json      # Dismiss/notes for digest items
├── .pending-actions/         # Browser action queue
└── .chat-state.json          # Chat persistence
```

## Project Structure

```
gbb-pulse/                               # Code only — no data here
|-- CLAUDE.md                            # Architecture & design decisions (this file)
|-- AGENTS.md                            # Agent behavior instructions (contest req)
|-- .mcp.json                            # MCP server config (WorkIQ)
|-- requirements.txt                     # Python dependencies
|-- pytest.ini                           # Test configuration
|-- src/
|   |-- pulse.py                         # Unified entry point — daemon + TUI in one command
|   |-- core/                            # Shared infrastructure
|   |   |-- constants.py                 # All path constants (PULSE_HOME, named dirs)
|   |   |-- config.py                    # YAML config loading + env var expansion
|   |   |-- state.py                     # Generic JSON state persistence
|   |   |-- logging.py                   # Structured logging + safe_encode
|   |   |-- browser.py                   # Shared Edge browser manager (CDP singleton)
|   |   |-- scheduler.py                 # Persistent cron-like scheduler (JSON state)
|   |   |-- onboarding.py               # First-run detection + config writing
|   |   +-- diagnostics.py              # System diagnostics (health checks)
|   |-- sdk/                             # GHCP SDK integration layer
|   |   |-- runner.py                    # Unified job runner (all modes go through here)
|   |   |-- session.py                   # Config-driven SessionConfig builder
|   |   |-- event_handler.py             # Event-driven session completion tracking
|   |   |-- hooks.py                     # Session hooks (audit trail, guardrails, error recovery, metrics)
|   |   |-- tools.py                     # Custom tool definitions (14 tools via @define_tool)
|   |   |-- prompts.py                   # Prompt loading + {{variable}} interpolation
|   |   +-- agents.py                    # Agent definition loading (front-matter parsing)
|   |-- collectors/                      # Data collection + browser actions (deterministic, no LLM)
|   |   |-- content.py                   # Local file scanning + text extraction
|   |   |-- feeds.py                     # RSS feed collection + dedup
|   |   |-- article_filter.py           # RSS article relevance filtering via SDK
|   |   |-- teams_inbox.py              # Teams unread message scanning (Playwright)
|   |   |-- teams_sender.py             # Teams message sending (Playwright, deterministic)
|   |   |-- outlook_inbox.py            # Outlook unread email scanning (Playwright)
|   |   |-- outlook_sender.py           # Outlook email reply (Playwright, deterministic)
|   |   |-- calendar.py                 # Outlook calendar day-view scanning (Playwright)
|   |   |-- extractors.py               # File-type text extractors (.docx, .pdf, etc.)
|   |   +-- transcripts/                 # Meeting transcript collection
|   |       |-- collector.py             # Orchestration
|   |       |-- navigation.py            # Teams calendar navigation
|   |       |-- extraction.py            # Virtualized list scraping
|   |       |-- compressor.py            # SDK-based transcript compression
|   |       +-- js_snippets.py           # JavaScript for DOM interaction
|   |-- daemon/                          # Always-on daemon components
|   |   |-- heartbeat.py                 # Legacy utilities (parse_interval); scheduling moved to core/scheduler.py
|   |   |-- worker.py                    # Job queue worker (routes to runner.py, handles agent_request/response)
|   |   |-- tasks.py                     # Extracted daemon tasks (status writer, chat poller)
|   |   +-- sync.py                      # OneDrive job sync + instruction seeding
|   +-- tui/                             # Terminal UI (Textual)
|       |-- app.py                       # 3-tab dashboard application
|       |-- screens.py                   # Inbox, Projects, Chat panes
|       |-- ipc.py                       # File-based IPC (daemon <-> TUI)
|       +-- styles.tcss                  # Textual CSS styles
|-- config/
|   |-- standing-instructions.yaml       # Template config (copied to PULSE_HOME on first run)
|   |-- standing-instructions-alpha.yaml # Alternate config (inter-agent testing)
|   |-- modes.yaml                       # Mode definitions (8 modes + 2 sub-modes)
|   |-- prompts/
|   |   |-- system/                      # System prompts per mode (base, chat, digest, intel, knowledge, monitor, research, transcripts)
|   |   |-- triggers/                    # Trigger prompt templates (digest, intel, knowledge, knowledge-archive, knowledge-project, monitor, research)
|   |   +-- agents/                      # Sub-agent definitions (digest-writer, knowledge-miner, m365-query, project-researcher, pulse-reader, signal-drafter)
|   |-- instructions/                    # Instruction files (triage, etc.)
|   +-- skills/
|       |-- teams-sender/                # Playwright-based Teams message sending
|       |   +-- SKILL.md
|       |-- email-reply/                 # Playwright-based Outlook email reply
|       |   +-- SKILL.md
|       |-- meeting-scheduler/           # M365 Copilot Chat meeting scheduling
|       |   +-- SKILL.md
|       +-- pulse-signal-drafter/        # GBB Pulse signal drafting
|           +-- SKILL.md
|-- tests/                               # 590 tests (pytest + pytest-asyncio)
|   |-- conftest.py                      # Shared fixtures (tmp_dir, sample_config)
|   |-- test_core.py                     # Constants, config, state, logging
|   |-- test_collectors.py               # Content extraction, transcript cleaning
|   |-- test_teams_inbox.py              # Teams inbox scanning + formatting
|   |-- test_teams_sender.py             # Teams message sending + error handling
|   |-- test_outlook_inbox.py            # Outlook inbox scanning + formatting
|   |-- test_outlook_sender.py           # Outlook email reply + error handling
|   |-- test_calendar.py                 # Calendar scanning + formatting
|   |-- test_pii_filter.py              # PII masking (emails, phones, credit cards, IBANs)
|   |-- test_sdk.py                      # Prompts, agents, session config building
|   |-- test_runner.py                   # Trigger variables, carry-forward, pre-process, project loading
|   |-- test_tools.py                    # All 13 tool handlers + path traversal security + inter-agent + projects
|   |-- test_hooks.py                    # Session hooks (audit trail, guardrails, error recovery, metrics)
|   |-- test_event_handler.py            # Event-driven completion tracking
|   |-- test_scheduler.py               # Config-driven scheduler, office hours, patterns, CRUD
|   |-- test_diagnostics.py             # System diagnostics
|   |-- test_knowledge.py               # Knowledge mining pipeline stages
|   |-- test_daemon.py                   # Parse interval, office hours, agent response
|   +-- test_tui.py                      # TUI IPC, screens, file-based streaming
+-- presentations/
    +-- PulseAgent.pptx
```

## Running

```bash
# Set PULSE_HOME (or add to .env)
export PULSE_HOME="$USERPROFILE/OneDrive - Microsoft/Documents/Pulse"

# Start Pulse — daemon + TUI together (triage every 30 min, job sync every 60s)
python src/pulse.py

# Start with alternate config (inter-agent testing, secondary instance)
python src/pulse.py --config config/standing-instructions-alpha.yaml

# Single cycle then exit
python src/pulse.py --once

# Run a specific stage (dev/debugging)
python src/pulse.py --mode digest --once
python src/pulse.py --mode monitor --once
python src/pulse.py --mode transcripts --once
python src/pulse.py --mode intel --once
```

To trigger a digest or research task, drop a YAML file into `$PULSE_HOME/jobs/pending/`. New files are detected within 60 seconds by the scheduler loop.

## Custom Tools (GHCP SDK)

| Tool | Description |
|------|-------------|
| `write_output` | Write files under `$PULSE_HOME` (path traversal blocked) |
| `queue_task` | Add a job to `$PULSE_HOME/jobs/pending/` (digest, research, transcripts, intel) |
| `dismiss_item` | Mark a digest item as handled (won't appear in future digests) |
| `add_note` | Attach a note to a digest item for future reference |
| `schedule_task` | Create a recurring schedule (`daily HH:MM`, `weekdays HH:MM`, `every Nh/Nm`) |
| `list_schedules` | List all configured recurring schedules with status |
| `update_schedule` | Update an existing schedule's pattern, description, or enabled status |
| `cancel_schedule` | Remove a recurring schedule by ID |
| `search_local_files` | Search data files (transcripts, docs, emails, digests, intel, projects) for keywords |
| `send_teams_message` | Queue a Teams message for deterministic delivery via shared browser |
| `send_email_reply` | Queue an email reply for deterministic delivery via shared browser |
| `send_task_to_agent` | Send a task/question to another team member's Pulse Agent via OneDrive |
| `update_project` | Create/update a project memory file (`$PULSE_HOME/projects/{id}.yaml`) with YAML content |
| `save_config` | Save standing instructions config from onboarding conversation |

Tools defined via GHCP SDK `@define_tool` decorator with Pydantic parameter schemas. The agent decides when to call them based on standing instructions and mode-specific prompts.

### Session Hooks (GHCP SDK)

All sessions are wired with 4 lifecycle hooks (`sdk/hooks.py`), providing automatic observability and guardrails without agent involvement:

| Hook | What it does |
|------|-------------|
| `on_post_tool_use` | **Automatic audit trail** — logs every tool call (name, args, result) to `$PULSE_HOME/logs/YYYY-MM-DD.jsonl`. Replaces the old `log_action` tool — 100% coverage, not agent-optional. |
| `on_pre_tool_use` | **Write path guardrails** — blocks `..` path traversal in `write_output` and `/`/`\` in `update_project` IDs. Defense-in-depth on top of in-handler validation. |
| `on_error_occurred` | **Structured error logging + recovery** — logs all session errors to JSONL with error context. Auto-retries recoverable tool execution errors once. |
| `on_session_end` | **Session metrics** — logs mode, duration, and end reason (complete/error/abort/timeout) per session. |

Hooks are crash-proof: every hook wraps its logic in try/except so a hook failure never disrupts the session. `_write_audit_entry` also silently swallows filesystem errors.

### Browser Action Architecture

Browser actions (Teams messages, email replies) use **deterministic Playwright scripts**, NOT the Playwright MCP server. This avoids the LLM-driven navigation problem where the agent would fumble through Teams UI with 15+ Playwright MCP tool calls.

**Two execution paths:**

1. **Triage action buttons** (1-tap flow): User presses "R" (reply) in TUI → job queued → worker calls `collectors/teams_sender.py` or `collectors/outlook_sender.py` directly → result shown in TUI + toast notification. No LLM involved.

2. **Chat mode** ("send to Esther: message"): Agent calls `send_teams_message` tool → tool writes action to `.pending-actions/` → after chat session completes, worker processes pending actions → deterministic sender executes → result shown in TUI.

Both paths use the **shared browser** (`core/browser.py` singleton), which is already authenticated. The Playwright MCP server (separate browser instance, separate profile) is no longer used for chat mode.

**3-layer dedup defense** (prevents duplicate message spam):
1. **Tool return message** — tells the LLM "Do NOT call this tool again for the same message"
2. **Tool-level dedup** (`sdk/tools.py`) — before creating a new `.pending-actions/` file, scans existing files for identical (target, message) pairs. Rejects duplicates with "already queued" response.
3. **Batch-level dedup** (`daemon/worker.py`) — `process_pending_actions()` tracks `(type, target, message)` tuples in a set. Skips duplicates within a single batch, even if multiple files were created (race condition or dedup bypass).

**CKEditor draft contamination fix** (`collectors/teams_sender.py`):
- Teams auto-saves drafts server-side. When opening an existing chat, the compose box may contain leftover text from a previous failed send.
- `Ctrl+A/Backspace` is unreliable with CKEditor (custom key handlers, contenteditable quirks).
- Fix: direct JS `innerHTML=''; textContent=''` to clear the compose box (bypasses CKEditor entirely).
- Content verification: after `insertText`, reads back compose box content and compares to intended message. If mismatched (draft contamination), clears and retries once before sending.

### Inter-Agent Communication

Multiple Pulse Agent daemons (20+ team members) can send tasks and questions to each other via OneDrive-synced folders. No new infrastructure — uses the existing task queue and OneDrive sync.

**Flow:**
```
Agent A (you)                                Agent B (colleague)
  |                                            |
  | 1. send_task_to_agent tool                 |
  |    writes YAML to B's                      |
  |    $PULSE_HOME/../B/jobs/pending/          |
  v                                            |
  B's OneDrive/Pulse-Team/B/jobs/pending/ -- OneDrive sync --> B picks up job
                                               |
                                       2. worker picks up agent_request
                                          runs chat query to answer
                                               |
                                       3. worker writes response YAML
                                          to A's reply_to path
                                               |
  A's $PULSE_HOME/jobs/pending/ <---- sync --- |
  |
  | 4. worker picks up agent_response
  |    surfaces result via TUI + toast
```

**Team directory** (`config/standing-instructions.yaml`):
```yaml
team:
  - name: "Esther Barthel"
    alias: "esther"
    agent_path: "$USERPROFILE/OneDrive - Microsoft/Documents/Pulse-Team/Esther"
```

**Task YAML schema** (written to target agent's jobs/pending/ folder):
```yaml
type: agent_request
kind: question          # question, research, intel, review
task: "What context do you have on the Vodafone deal?"
from: "Artur Zielinski"
from_alias: "artur"
reply_to: "C:/Users/arzielinski/OneDrive/Pulse/Jobs"
request_id: "uuid"
priority: normal
created_at: "ISO timestamp"
```

**Response YAML schema** (written back to requester's reply_to path):
```yaml
type: agent_response
kind: response
request_id: "uuid"
from: "Esther Barthel"
from_alias: "esther"
original_task: "What context do you have on the Vodafone deal?"
result: "Based on my meeting transcripts..."
created_at: "ISO timestamp"
```

**Worker routing:**
- `agent_request` → `_handle_agent_request()` (chat query or research) → `_write_agent_response()` to `reply_to` path
- `agent_response` → surface result to user via TUI + toast notification

**Polling:** The scheduler loop checks OneDrive every 60 seconds for new job files. Inter-agent requests are picked up within ~1 minute of arrival.

**Multi-instance:** Run multiple daemon instances with different configs for testing:
```bash
# Primary agent (default config)
python src/pulse.py

# Second agent with separate config
python src/pulse.py --config config/standing-instructions-alpha.yaml
# Or: PULSE_CONFIG=config/standing-instructions-alpha.yaml python src/pulse.py
```

Each instance needs its own `standing-instructions-*.yaml` with separate `user` and `team` entries, plus a distinct `PULSE_HOME` env var. The `--config` flag (or `PULSE_CONFIG` env var) tells `load_config()` which file to use.

**Graceful degradation:** If a teammate's laptop is off, the task YAML sits in their OneDrive jobs folder until they come back online. No timeouts, no connection errors — just async file-based messaging.

### Project Memory & Commitment Tracking

Persistent per-project context that survives across digest cycles. Projects are auto-discovered from content patterns (transcripts, emails, meetings) — no hardcoded project names.

**Storage:** `$PULSE_HOME/projects/*.yaml` — one file per project, lives on OneDrive.

**Schema:**
```yaml
project: "Human-readable project name"
status: active          # active | blocked | on-hold | completed
risk_level: medium      # low | medium | high | critical
summary: "1-2 sentence context"
stakeholders:
  - name: "Full Name"
    role: "PM"
commitments:
  - what: "Send pricing proposal"
    who: "You"
    to: "Customer Name"
    due: "2026-02-28"
    status: open        # open | done | overdue | cancelled
    source: "Feb 20 standup transcript"
next_meeting: "2026-02-25 14:00"
key_dates:
  - date: "2026-03-01"
    event: "Contract renewal deadline"
updated_at: "auto-set by tool"
```

**How it works:**
1. **Auto-discovery** — the `project-researcher` agent (part of digest pipeline) finds projects from recurring names, deals, and meeting series in content
2. **Persistence** — agent uses `update_project` tool to write/update YAML files. Tool validates project ID format, prevents path traversal, auto-sets `updated_at`
3. **Digest integration** — `_pre_process_digest()` loads all project files (Phase 1f), builds a projects block and commitment summary for prompt injection
4. **Commitment tracking** — overdue and approaching-deadline commitments surface at the top of the digest
5. **Project-oriented output** — digest items are grouped by project, not just by item type
6. **OneDrive native** — project files live directly on OneDrive alongside digests, intel, and pulse-signals

**Project IDs:** lowercase-hyphenated slugs matching `^[a-z0-9][a-z0-9-]{0,80}$` (e.g., `contoso-migration`, `partner-enablement-q1`).

## Testing

```bash
# Run all tests (590 tests)
python -m pytest tests/ -q

# Verbose output
python -m pytest tests/ -v

# Stop on first failure
python -m pytest tests/ -x --tb=short

# Run specific test file
python -m pytest tests/test_tools.py -v

# Filter by name pattern
python -m pytest tests/ -k "digest" -v
```

Tests auto-run via Claude Code hook after every Write/Edit (see `.claude/settings.json`). Shared fixtures live in `tests/conftest.py`. SDK tool handlers are tested via `await tool.handler({"arguments": {...}})`.

## Known Limitations

- **Transcript collection requires headful browser.** Playwright must launch visible Edge to navigate Teams. Headless mode does not reliably render the Teams SPA.
- **Persistent browser profile required.** The user must have an authenticated Edge session at the configured `--user-data-dir` path. If the session expires, transcript collection will fail.
- **Calendar view only shows one week at a time.** Multi-week lookback navigates backward incrementally (configurable via `transcripts.lookback_weeks`, default 2).
- **WorkIQ only sees what you've seen.** Scopes to your own M365 interactions — meetings you attended, emails you received.
- **WorkIQ availability is intermittent.** The MCP server sometimes returns "Failed to create conversation" errors. The system handles this gracefully by falling back to Teams inbox scan for unread verification and applying conservative rules for carry-forward items.
- **Teams message sending via Playwright is fragile.** Browser automation for sending messages depends on Teams UI structure. One Microsoft UI update can break it. Drafts are always shown for review before sending.
- **Local file search is keyword-based.** The `search_local_files` tool uses simple string matching (case-insensitive). It does not do semantic search. The agent must guess the right keywords to search for.

## RAI

- **Draft-first for outbound actions** — triage suggests draft replies shown for user review before sending
- **Microsoft-tenant processing** — content is processed through GitHub Copilot SDK (Microsoft cloud) and WorkIQ (M365 Copilot). No third-party services, but data does leave the local machine.
- **Full audit trail** — every tool call automatically logged via `on_post_tool_use` hook to `logs/YYYY-MM-DD.jsonl` (100% coverage, not agent-optional)
- **Defense-in-depth guardrails** — `on_pre_tool_use` hook validates file paths before tools execute, layered on top of in-handler validation
- **Minimal destructive actions** — agent cannot delete user files or cancel meetings. Only exception: transcript compressor deletes raw `.txt` after producing compressed `.md`.
- **Configurable scope** — user controls what folders to scan, what topics to watch
- **Browser automation is user-scoped** — Playwright uses your credentials, accesses only your data
- **Dismiss + notes** — user can mark items as handled or annotate them for future context

## Contest Scoring

| Category | Pts | Coverage |
|----------|-----|----------|
| Enterprise applicability & business value | 30 | Every knowledge worker drowning in information + multi-agent team collaboration |
| Azure/Microsoft integration | 25 | WorkIQ, Teams transcripts, Teams inbox scan, M365 ecosystem |
| Operational readiness | 15 | Daemon process, structured logging, config-driven, persistent scheduler, inter-agent OneDrive sync, session hooks (metrics + error recovery) |
| Security, governance & RAI | 15 | Draft-first actions, local-first, automatic audit trail (hooks), defense-in-depth path guardrails (hooks), permission handlers |
| Storytelling & clarity | 15 | "Processed 8 meetings overnight, found the escalation I missed" |
| WorkIQ/FoundryIQ bonus | 15 | Calendar context + email cross-reference + WorkIQ failure fallback |
| SDK product feedback | 10 | Document as we build |
| Customer validation | 10 | Stretch goal |
| **Total possible** | **135** | |
