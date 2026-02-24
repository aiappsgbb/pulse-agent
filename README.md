# Pulse Agent

**An autonomous information processing engine for knowledge workers.**

You have 8 meetings a day and retain 20% of what's said. You're CC'd on 50 email threads you'll never read. Competitors announce changes at 2 AM. Pulse Agent runs when you don't — it consumes everything you can't and tells you only what matters.

Not a copilot. Not a chatbot. A local daemon with standing instructions, full M365 visibility, and structured output.

**"I had 8 meetings yesterday. I was distracted in half of them. At 7 AM, Pulse Agent told me the 3 things that actually need my attention — including an escalation I completely missed."**

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

### Telegram Setup

1. Message [@BotFather](https://t.me/BotFather) on Telegram, create a new bot, copy the token
2. Edit `standing-instructions.yaml`:
   ```yaml
   telegram:
     enabled: true
     bot_token: "your-token-here"
   ```
3. Start the daemon — your bot is live

### Run

```bash
# Start the daemon — Telegram + scheduler + job worker
python src/main.py

# Start with alternate config (inter-agent testing, secondary instance)
python src/main.py --config config/standing-instructions-alpha.yaml

# Single cycle then exit
python src/main.py --once

# Run a specific mode (dev/debugging)
python src/main.py --mode digest --once
python src/main.py --mode monitor --once
python src/main.py --mode transcripts --once
python src/main.py --mode intel --once
python src/main.py --mode knowledge --once
```

### Interacting via Telegram

Just talk to the bot naturally:

- "What's new?" — queries WorkIQ for recent activity
- "Did I miss anything in meetings yesterday?" — checks calendar + transcripts
- "Run a digest" — triggers a full digest immediately
- "Analyze Parloa vs 11Labs" — deep research task
- "Grab transcripts" — collects meeting transcripts from Teams
- "Send to Esther: here's the pricing update" — sends a Teams message (with draft review)

The bot also provides:
- `/digest`, `/triage`, `/intel`, `/transcripts` — queued jobs
- `/latest` — sends the most recent digest
- `/status` — daemon uptime + queue size
- 1-tap action buttons for triage items (review draft, send, dismiss)
- Proactive triage reports during office hours

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
| **Chat** | Telegram message | Natural language via Telegram with streaming replies, WorkIQ, local search, and browser actions |
| **Knowledge Mining** | `--mode knowledge` | Overnight pipeline: collect transcripts, compress, archive emails/Teams, enrich project memory |

## Architecture

```
Data Collection (Playwright + Python, no LLM)
  Teams Transcripts ── browser automation -> virtualized list scraping -> SDK compression
  Teams Inbox ──────── browser scan for unread messages (ground truth)
  Outlook Inbox ────── browser scan for unread emails (ground truth)
  Calendar ─────────── browser scan for upcoming events
  Local Content ────── file system scan (.docx, .pdf, .pptx, .xlsx, .csv, .eml, .vtt, .txt, .md)
  RSS Feeds ────────── feedparser + SDK relevance filtering
        |
        v
Pulse Agent (Python daemon, always-on)
  asyncio event loop with 3 concurrent tasks:
    Telegram bot ── conversational + action buttons + streaming replies
    Scheduler ───── config-driven schedules (every 60s) + OneDrive job sync
    Job worker ──── processes queue, one at a time (GHCP SDK sessions)
        |
        v
GitHub Copilot SDK (CopilotClient -> JSON-RPC -> Copilot CLI server mode)
  WorkIQ MCP ────── calendar, email, Teams, people, documents
  Custom tools ──── 13 tools (write, search, schedule, send, dismiss, projects, inter-agent)
  Session hooks ─── automatic audit trail, path guardrails, error recovery, metrics
  Sub-agents ────── digest-writer, project-researcher, knowledge-miner, m365-query, pulse-reader, signal-drafter
  Multi-model ───── gpt-4.1 (triage/chat), claude-sonnet (digest), claude-opus (research/intel)
        |
        v
Output -> $PULSE_HOME (OneDrive-synced)
  digests/YYYY-MM-DD.json + .md ─── structured + human-readable digest
  intel/YYYY-MM-DD.md ──────────── external intel brief
  projects/*.yaml ──────────────── persistent project memory
  monitoring-*.json + .md ──────── triage reports with action buttons
  transcripts/*.md ─────────────── compressed meeting transcripts
  pulse-signals/*.md ───────────── drafted GBB Pulse signals
  logs/YYYY-MM-DD.jsonl ────────── structured audit trail
        |
        v
Telegram Bot (user interface)
  Chat ─────── natural language -> streaming reply (progressive edits)
  Jobs ─────── /digest, /triage, /intel, /transcripts -> queued jobs
  Actions ──── 1-tap buttons: review draft -> send Teams/email -> dismiss
  Proactive ── triage reports + morning digest delivery
```

## How It Works

The daemon runs three things concurrently on one async event loop:

1. **Telegram bot** — listens for messages, puts jobs on an `asyncio.Queue`
2. **Config-driven scheduler** (every 60s) — checks cron-like patterns (`daily 07:00`, `every 30m`, `weekdays HH:MM`), fires due schedules, syncs OneDrive job files
3. **Worker** — processes jobs from the queue one at a time (GHCP SDK sessions)

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
|   |-- main.py                          # Daemon entry point — event loop, dotenv
|   |-- core/                            # Shared infrastructure
|   |   |-- constants.py                 # Path constants (PULSE_HOME, named dirs)
|   |   |-- config.py                    # YAML config loading + env var expansion
|   |   |-- state.py                     # Generic JSON state persistence
|   |   |-- logging.py                   # Structured logging + safe_encode
|   |   |-- browser.py                   # Shared Edge browser manager (CDP singleton)
|   |   |-- scheduler.py                 # Persistent cron-like scheduler
|   |   +-- diagnostics.py              # System health checks
|   |-- sdk/                             # GHCP SDK integration layer
|   |   |-- runner.py                    # Unified job runner (all modes)
|   |   |-- session.py                   # Config-driven SessionConfig builder
|   |   |-- event_handler.py             # Event-driven session completion
|   |   |-- hooks.py                     # Session hooks (audit, guardrails, recovery, metrics)
|   |   |-- tools.py                     # 13 custom tool definitions
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
|   |   |-- heartbeat.py                 # Legacy utilities (parse_interval)
|   |   |-- worker.py                    # Job queue worker
|   |   +-- sync.py                      # OneDrive job sync + instruction seeding
|   +-- tg/                              # Telegram bot interface
|       |-- bot.py                       # Commands, streaming, action buttons
|       |-- confirmations.py             # ask_user confirmation flow
|       +-- pii_filter.py               # PII masking for Telegram output
|-- config/
|   |-- standing-instructions.yaml       # Template config
|   |-- standing-instructions-alpha.yaml # Alternate config (inter-agent testing)
|   |-- modes.yaml                       # Mode definitions (8 modes + 2 sub-modes)
|   |-- prompts/
|   |   |-- system/                      # System prompts per mode
|   |   |-- triggers/                    # Trigger prompt templates
|   |   +-- agents/                      # Sub-agent definitions
|   +-- skills/                          # 4 Playwright-based skill definitions
|-- tests/                               # 396 tests (pytest + pytest-asyncio)
+-- presentations/
    +-- PulseAgent.pptx
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
|-- jobs/pending/ + completed/# Task queue
|-- logs/                     # Structured JSONL audit trail
+-- .scheduler.json, .digest-state.json, .digest-actions.json, etc.
```

## Custom Tools

The agent has 13 custom tools registered via the GHCP SDK `@define_tool` decorator:

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

All tool usage is automatically logged to the JSONL audit trail via the `on_post_tool_use` session hook.

## Security & Governance

- **Draft-first for outbound actions** — triage suggests draft replies shown for user review before sending
- **Local-first processing** — content processed on your machine, not uploaded
- **Full audit trail** — every tool call automatically logged via `on_post_tool_use` hook to `logs/YYYY-MM-DD.jsonl` (100% coverage)
- **Defense-in-depth guardrails** — `on_pre_tool_use` hook validates file paths before tools execute
- **PII filtering** — Telegram output is scrubbed of emails, phone numbers, credit cards, and IBANs
- **No destructive actions** — agent cannot delete, cancel, or overwrite
- **Path-traversal protection** — `write_output` and `update_project` validate paths at both hook and handler level
- **Configurable scope** — user controls what folders to scan, what topics to watch
- **Scoped permissions** — WorkIQ only accesses your own M365 data, Playwright uses your browser session

## Testing

```bash
# Run all tests (396 tests)
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
- **User interface:** Telegram bot (`python-telegram-bot`) — conversational + streaming replies + inline action buttons
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

See [CLAUDE.md](CLAUDE.md) for full architecture details, technical deep-dives, and design decisions.
