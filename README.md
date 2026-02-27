# Pulse Agent

**An autonomous information processing engine for knowledge workers.**

You have 8 meetings a day and retain 20% of what's said. You're CC'd on 50 email threads you'll never read. Competitors announce changes at 2 AM. Pulse Agent runs when you don't — it consumes everything you can't and tells you only what matters.

Not a copilot. Not a chatbot. A local daemon with standing instructions, full M365 visibility, and structured output.

**"I had 8 meetings yesterday. I was distracted in half of them. At 7 AM, Pulse Agent told me the 3 things that actually need my attention — including an escalation I completely missed."**

## The Problem

Knowledge workers drown in information. Copilot helps when you ask — but nobody asks at 2 AM when a competitor changes pricing. Nobody remembers to check 50 email threads. Nobody processes 8 hours of meeting transcripts overnight.

Pulse Agent solves this by running autonomously with standing instructions. No prompting required.

## What It Does

Pulse Agent processes three categories of information and delivers a single, filtered digest by morning:

| Source | How | What You Get |
|--------|-----|-------------|
| **Meeting transcripts** | Playwright scrapes Teams web UI + SDK compression | Decisions, action items, escalations |
| **Inbox + Teams** | Playwright inbox scans + WorkIQ M365 queries | Outstanding items you haven't dealt with yet |
| **Industry news** | RSS feeds via feedparser + SDK relevance filtering | Competitor moves, product launches, trends |

**The key insight:** It cross-references what needs attention against what you've already handled. If you replied to an email, it's filtered out. If you attended a meeting with no open actions, it's gone. You only see what's genuinely outstanding.

A typical digest is 30-50 lines. Not 400.

## Quick Start

### Prerequisites

- Python 3.12+
- [GitHub Copilot CLI](https://docs.github.com/en/copilot/github-copilot-in-the-cli) installed and authenticated
- GitHub Copilot subscription (Individual, Business, or Enterprise)
- Microsoft Edge with an authenticated Teams session (for transcript collection + inbox scanning)
- [WorkIQ MCP server](https://github.com/microsoft/work-iq-mcp) for M365 data access

### Setup

```bash
# Clone
git clone <repo-url>
cd gbb-pulse

# Create virtual environment
python -m venv .venv
.venv\Scripts\activate  # Windows
# source .venv/bin/activate  # Linux/Mac

# Install dependencies
pip install -r requirements.txt

# For transcript collection + inbox scanning, install Playwright browsers
playwright install msedge
```

### Configuration

Set your data directory:

```bash
# Add to .env (already gitignored)
PULSE_HOME=$USERPROFILE/OneDrive - Microsoft/Documents/Pulse
```

On first run, `config/standing-instructions.yaml` is copied to `$PULSE_HOME/standing-instructions.yaml`. Edit the copy to customize priorities, RSS feeds, model preferences, and schedules.

The config supports environment variables (`$LOCALAPPDATA`, `$HOME`, `~`) in all string fields.

**Config resolution chain:** `--config` flag > `PULSE_CONFIG` env var > `$PULSE_HOME/standing-instructions.yaml` > `config/standing-instructions.yaml` (repo template fallback)

### Run

```bash
# Start Pulse — daemon + TUI launch together
python src/pulse.py

# Start with alternate config (inter-agent testing, secondary instance)
python src/pulse.py --config config/standing-instructions-alpha.yaml

# Single cycle then exit
python src/pulse.py --once

# Run a specific mode (dev/debugging)
python src/pulse.py --mode digest --once
python src/pulse.py --mode monitor --once
python src/pulse.py --mode transcripts --once
python src/pulse.py --mode intel --once
python src/pulse.py --mode knowledge --once
```

### User Interface

Pulse Agent uses two complementary interfaces — both local, no data leaves the tenant:

**Windows Toast Notifications (winotify)** — Proactive push alerts for triage items, digest completion, and urgent escalations. Delivered via native Windows notification system.

**Textual TUI Dashboard** (`python src/pulse.py`) — Interactive 4-tab terminal application:
- **Triage** — Latest triage items with dismiss/reply/note actions
- **Digest** — Morning digest items, grouped by project
- **Projects** — Per-engagement project memory and commitment tracking
- **Chat** — Streaming chat with the agent (natural language queries)

Key bindings: `Ctrl+D/T/I/X` to queue digest/triage/intel/transcript jobs, `D/R/N` to dismiss/reply/note items, `Ctrl+R` to refresh.

Communication between daemon and TUI uses file-based IPC (`.chat-request.json` → daemon → `.chat-stream.jsonl`), keeping both processes independent.

### Job Files

Drop YAML files into `$PULSE_HOME/jobs/pending/` (picked up within 60 seconds):

```yaml
type: digest
```

```yaml
type: research
task: "Compare AWS Bedrock pricing vs Azure OpenAI"
description: "Pull latest public pricing, summarize differences"
```

## Modes

| Mode | Trigger | What It Does |
|------|---------|-------------|
| **Transcript Collection** | `--mode transcripts` | Playwright scrapes Teams Calendar for meeting transcripts, SDK compresses to structured notes |
| **Internal Digest** | `--mode digest` | Scans local content + RSS + inbox scans + WorkIQ, generates a 30-50 line filtered digest with action buttons |
| **Triage** | `--mode monitor` | 30-min inbox triage with 1-tap action buttons (Teams reply, email reply, schedule meeting) |
| **Deep Research** | `--mode research` | Autonomous long-running research with full WorkIQ + local tool access (60 min timeout) |
| **External Intel** | `--mode intel` | RSS feeds filtered for relevance, generates a concise intelligence brief |
| **Chat** | TUI chat tab | Natural language with streaming replies, WorkIQ, local search, and browser actions |
| **Knowledge Mining** | `--mode knowledge` | Overnight pipeline: collect transcripts, compress, archive emails/Teams, enrich project memory |

All modes are config-driven via `config/modes.yaml` — no hardcoded if/elif chains.

## Architecture

```
                         ┌─────────────────────────────────────────────┐
                         │         Data Collection Layer               │
                         │         (Playwright + Python, no LLM)       │
                         ├─────────────────────────────────────────────┤
                         │  Teams Transcripts ── browser → virtualized │
                         │    list scraping → SDK compression          │
                         │  Teams Inbox ──── unread message scan       │
                         │  Outlook Inbox ── unread email scan         │
                         │  Calendar ─────── upcoming event scan       │
                         │  Local Content ── .docx .pdf .pptx .xlsx   │
                         │    .csv .eml .vtt .txt .md                  │
                         │  RSS Feeds ────── feedparser + relevance    │
                         └──────────────────────┬──────────────────────┘
                                                │
                                                ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                    Pulse Agent (Python daemon)                           │
│                    asyncio event loop, always-on                         │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌──────────────┐  ┌──────────────────┐  ┌────────────────────────────┐ │
│  │  Scheduler    │  │  Job Worker       │  │  TUI Backend + Toasts     │ │
│  │  (every 60s)  │  │  (one at a time)  │  │  (winotify + file IPC)   │ │
│  │               │  │                   │  │                           │ │
│  │  Cron-like    │  │  Routes to GHCP   │  │  Status writes            │ │
│  │  patterns +   │  │  SDK sessions     │  │  Chat request polling     │ │
│  │  OneDrive     │  │  per mode         │  │  Stream delta writes      │ │
│  │  job sync     │  │                   │  │  Windows toast alerts     │ │
│  └──────┬───────┘  └────────┬──────────┘  └────────────────────────────┘ │
│         │                   │                                            │
│         └───────────┬───────┘                                            │
│                     ▼                                                    │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │  GitHub Copilot SDK (CopilotClient → JSON-RPC → Copilot CLI)    │   │
│  ├──────────────────────────────────────────────────────────────────┤   │
│  │  WorkIQ MCP ──── calendar, email, Teams, people, documents      │   │
│  │  Custom tools ── 14 tools (write, search, schedule, send,       │   │
│  │                  dismiss, projects, inter-agent)                  │   │
│  │  Session hooks ─ audit trail, path guardrails, error recovery,  │   │
│  │                  session metrics                                  │   │
│  │  Sub-agents ──── digest-writer, project-researcher,              │   │
│  │                  knowledge-miner, m365-query, pulse-reader,      │   │
│  │                  signal-drafter                                   │   │
│  │  Multi-model ─── gpt-4.1 (triage/chat), claude-sonnet (digest), │   │
│  │                  claude-opus (research/intel)                     │   │
│  └──────────────────────────────────────────────────────────────────┘   │
└───────────────────────────────┬──────────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                Output → $PULSE_HOME (OneDrive-synced)                   │
├──────────────────────────────────────────────────────────────────────────┤
│  digests/YYYY-MM-DD.json + .md ── structured + human-readable digest    │
│  intel/YYYY-MM-DD.md ──────────── external intel brief                  │
│  projects/*.yaml ──────────────── persistent project memory             │
│  monitoring-*.json + .md ──────── triage reports with action buttons    │
│  transcripts/*.md ─────────────── compressed meeting transcripts        │
│  logs/YYYY-MM-DD.jsonl ────────── structured audit trail                │
│  jobs/pending/ + completed/ ───── task queue (inter-agent compatible)   │
└───────────────────────────────┬──────────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                      User Interfaces (local only)                        │
├──────────────────────────────────────────────────────────────────────────┤
│  Textual TUI ── 4-tab dashboard: Triage | Digest | Projects | Chat     │
│  winotify ───── Windows toast notifications for proactive alerts        │
│  Job files ──── Drop YAML into jobs/pending/ for ad-hoc tasks           │
└──────────────────────────────────────────────────────────────────────────┘
```

## How It Works

The daemon runs three things concurrently on one async event loop:

1. **Config-driven scheduler** (every 60s) — checks cron-like patterns (`daily 07:00`, `every 30m`, `weekdays HH:MM`), fires due schedules, syncs OneDrive job files
2. **Worker** — processes jobs from the queue one at a time (GHCP SDK sessions)
3. **TUI backend + toast alerts** — writes daemon status, polls for chat requests, streams response deltas via file IPC, sends Windows toast notifications for urgent items

Jobs execute immediately when queued — no waiting for the next cycle.

**Default schedule:**
```yaml
schedule:
  - id: morning-digest
    type: digest
    pattern: "daily 07:00"
  - id: triage
    type: monitor
    pattern: "every 30m"
    office_hours_only: true
  - id: daily-intel
    type: intel
    pattern: "daily 09:00"
```

## Project Structure

```
gbb-pulse/                               # Code only — no data here
|-- CLAUDE.md                            # Architecture & design decisions
|-- AGENTS.md                            # Agent behavior instructions
|-- README.md                            # This file
|-- .mcp.json                            # MCP server config (WorkIQ)
|-- requirements.txt                     # Python dependencies
|-- pytest.ini                           # Test configuration
|-- src/
|   |-- pulse.py                         # Unified entry point — daemon + TUI in one command
|   |-- core/                            # Shared infrastructure
|   |   |-- constants.py                 # Path constants (PULSE_HOME, named dirs)
|   |   |-- config.py                    # YAML config loading + env var expansion
|   |   |-- state.py                     # Generic JSON state persistence
|   |   |-- logging.py                   # Structured logging + safe_encode
|   |   |-- browser.py                   # Shared Edge browser manager (CDP singleton)
|   |   |-- scheduler.py                 # Persistent cron-like scheduler
|   |   |-- onboarding.py               # First-run detection + config writing
|   |   +-- diagnostics.py              # System health checks
|   |-- sdk/                             # GHCP SDK integration layer
|   |   |-- runner.py                    # Unified job runner (all modes)
|   |   |-- session.py                   # Config-driven SessionConfig builder
|   |   |-- event_handler.py             # Event-driven session completion
|   |   |-- hooks.py                     # Session hooks (audit, guardrails, recovery, metrics)
|   |   |-- tools.py                     # 14 custom tool definitions
|   |   |-- prompts.py                   # Prompt loading + {{variable}} interpolation
|   |   +-- agents.py                    # Agent definition loading
|   |-- collectors/                      # Data collection (deterministic, no LLM)
|   |   |-- content.py                   # Local file scanning + text extraction
|   |   |-- feeds.py                     # RSS feed collection + dedup
|   |   |-- article_filter.py           # RSS article relevance filtering via SDK
|   |   |-- teams_inbox.py              # Teams unread scanning (Playwright)
|   |   |-- teams_sender.py             # Teams message sending (Playwright)
|   |   |-- outlook_inbox.py            # Outlook unread scanning (Playwright)
|   |   |-- outlook_sender.py           # Outlook email reply (Playwright)
|   |   |-- calendar.py                 # Calendar scanning (Playwright)
|   |   |-- extractors.py               # File-type text extractors
|   |   +-- transcripts/                 # Meeting transcript collection
|   |       |-- collector.py             # Orchestration
|   |       |-- navigation.py            # Teams calendar navigation
|   |       |-- extraction.py            # Virtualized list scraping
|   |       |-- compressor.py            # SDK-based transcript compression
|   |       +-- js_snippets.py           # JavaScript for DOM interaction
|   |-- daemon/                          # Always-on daemon components
|   |   |-- heartbeat.py                 # Utilities (parse_interval)
|   |   |-- worker.py                    # Job queue worker
|   |   |-- tasks.py                     # Extracted daemon tasks (status writer, chat poller)
|   |   +-- sync.py                      # OneDrive job sync + instruction seeding
|   +-- tui/                             # Terminal UI (Textual)
|       |-- app.py                       # 4-tab dashboard application
|       |-- screens.py                   # Triage, Digest, Projects, Chat panes
|       |-- ipc.py                       # File-based IPC (daemon <-> TUI)
|       +-- styles.tcss                  # Textual CSS styles
|-- config/
|   |-- standing-instructions.yaml       # Template config
|   |-- standing-instructions-alpha.yaml # Alternate config (inter-agent testing)
|   |-- modes.yaml                       # Mode definitions (8 modes + 2 sub-modes)
|   |-- prompts/
|   |   |-- system/                      # System prompts per mode
|   |   |-- triggers/                    # Trigger prompt templates
|   |   +-- agents/                      # Sub-agent definitions
|   +-- skills/                          # 4 Playwright-based skill definitions
|-- tests/                               # 391 tests (pytest + pytest-asyncio)
|-- docs/
|   |-- SUMMARY.md                       # 150-word solution summary
|   |-- RAI.md                           # Responsible AI notes
|   |-- SDK-FEEDBACK.md                  # GitHub Copilot SDK product feedback
|   |-- knowledge.md                     # Knowledge mining architecture
|   +-- roadmap.md                       # Future phases
+-- presentations/
    +-- PulseAgent.pptx                  # Demo deck
```

**Data directory** (`$PULSE_HOME`, OneDrive-synced):
```
$PULSE_HOME/
|-- transcripts/              # Meeting transcripts (.md compressed, .txt raw)
|-- documents/                # User-dropped docs
|-- emails/                   # Email exports
|-- digests/                  # Structured + human-readable digests
|-- intel/                    # Intel briefs
|-- projects/                 # Project memory (.yaml per engagement)
|-- pulse-signals/            # Drafted GBB Pulse signals
|-- jobs/pending/ + completed/# Task queue (also used for inter-agent communication)
|-- logs/                     # Structured JSONL audit trail
+-- .scheduler.json, .digest-state.json, .digest-actions.json, etc.
```

## Custom Tools

The agent has 14 custom tools registered via the GHCP SDK `@define_tool` decorator:

| Tool | Description |
|------|-------------|
| `write_output` | Write files under `$PULSE_HOME` (path traversal blocked) |
| `queue_task` | Add a job to `jobs/pending/` (digest, research, transcripts, intel) |
| `dismiss_item` | Mark a digest item as handled (won't reappear) |
| `add_note` | Annotate a digest item for future context |
| `schedule_task` | Create a recurring schedule (`daily HH:MM`, `every Nm`, etc.) |
| `list_schedules` | List all configured recurring schedules with status |
| `update_schedule` | Update a schedule's pattern, description, or enabled status |
| `cancel_schedule` | Remove a recurring schedule by ID |
| `search_local_files` | Search transcripts, documents, emails, Teams messages, digests, intel, projects |
| `update_project` | Create/update a project memory file (YAML) |
| `send_teams_message` | Queue a Teams message for delivery via shared browser |
| `send_email_reply` | Queue an email reply for delivery via Outlook Web |
| `send_task_to_agent` | Send a task/question to another team member's Pulse Agent via OneDrive |
| `save_config` | Save standing instructions config from onboarding conversation |

All tool usage is automatically logged to the JSONL audit trail via the `on_post_tool_use` session hook.

## Security & Responsible AI

- **Draft-first for outbound actions** — triage suggests draft replies shown for user review before sending
- **Local-first processing** — content processed on your machine, not uploaded to external services
- **Full audit trail** — every tool call automatically logged via `on_post_tool_use` hook to `logs/YYYY-MM-DD.jsonl` (100% coverage)
- **Defense-in-depth guardrails** — `on_pre_tool_use` hook validates file paths before tools execute
- **PII filtering** — output is scrubbed of emails, phone numbers, credit cards, and IBANs before display
- **No destructive actions** — agent cannot delete, cancel, or overwrite
- **Path-traversal protection** — `write_output` and `update_project` validate paths at both hook and handler level
- **Configurable scope** — user controls what folders to scan, what topics to watch
- **Scoped permissions** — WorkIQ only accesses your own M365 data, Playwright uses your browser session

See [docs/RAI.md](docs/RAI.md) for detailed Responsible AI notes.

## Testing

```bash
# Run all tests (391 tests)
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

## Tech Stack

- **Language:** Python 3.12
- **Agent runtime:** GitHub Copilot SDK (`github-copilot-sdk`) -> Copilot CLI server mode (JSON-RPC)
- **User interface:** Textual TUI (4-tab dashboard) + winotify (Windows toast notifications)
- **M365 integration:** WorkIQ MCP server (emails, calendar, Teams, files, people)
- **Browser automation:** Playwright Python (Edge) — transcript collection, inbox scanning, message sending
- **External intel:** feedparser (RSS)
- **Document extraction:** python-docx, python-pptx, PyPDF2, openpyxl
- **Output sync:** OneDrive (local folder sync)
- **Logging:** Structured JSONL via session hooks (automatic, 100% coverage)
- **Config:** YAML standing instructions with env var expansion

## Troubleshooting

**"Config not found"** — Make sure `config/standing-instructions.yaml` exists, or set `PULSE_HOME` to a directory containing `standing-instructions.yaml`.

**"Failed to connect to GitHub Copilot SDK"** — The Copilot CLI must be installed and authenticated. Run `github-copilot-cli auth` first.

**Transcript collection fails** — The browser needs a logged-in Teams session. Open Edge manually, navigate to `teams.microsoft.com`, sign in, then try again. The `user_data_dir` in config must point to a valid Edge profile.

**WorkIQ queries return nothing** — Accept the WorkIQ EULA first. The MCP server must be installed and accessible on your PATH.

**charmap encoding errors on Windows** — All terminal output is ASCII-safe encoded. If you still see errors, check that your Python is 3.12+ and your terminal supports UTF-8.

## Further Reading

- [CLAUDE.md](CLAUDE.md) — Full architecture details, technical deep-dives, and design decisions
- [AGENTS.md](AGENTS.md) — Agent behavior instructions and guardrails
- [docs/RAI.md](docs/RAI.md) — Responsible AI notes
- [docs/SDK-FEEDBACK.md](docs/SDK-FEEDBACK.md) — GitHub Copilot SDK product feedback
- [docs/knowledge.md](docs/knowledge.md) — Knowledge mining architecture
