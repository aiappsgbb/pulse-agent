# Pulse Agent

**An autonomous information processing engine for knowledge workers.**

You have 8 meetings a day and retain 20% of what's said. You're CC'd on 50 email threads you'll never read. Competitors announce changes at 2 AM. Pulse Agent runs when you don't — it consumes everything you can't and tells you only what matters.

Not a copilot. Not a chatbot. A local daemon with standing instructions, full M365 visibility, and structured output. The output lands in a OneDrive-synced folder so M365 Copilot can read and summarize it natively — no custom bot required.

## What It Does

Pulse Agent processes three categories of information overnight and delivers a single, filtered digest by morning:

| Source | How | What You Get |
|--------|-----|-------------|
| **Meeting transcripts** | Playwright scrapes Teams web UI | Decisions, action items, escalations |
| **Inbox + Teams** | WorkIQ queries M365 data | Outstanding items you haven't dealt with yet |
| **Industry news** | RSS feeds via feedparser | Competitor moves, product launches, trends |

**The key insight:** It cross-references what needs attention against what you've already handled. If you replied to an email, it's filtered out. If you attended a meeting with no open actions, it's gone. You only see what's genuinely outstanding.

A typical digest is 30-50 lines. Not 400.

## Quick Start

### Prerequisites

- Python 3.12+
- [GitHub Copilot CLI](https://docs.github.com/en/copilot/github-copilot-in-the-cli) installed and authenticated
- GitHub Copilot subscription (Individual, Business, or Enterprise)
- Microsoft Edge with an authenticated Teams session (for transcript collection)
- [WorkIQ MCP server](https://github.com/microsoft/work-iq-mcp) (`npm install -g @anthropic/workiq-mcp` or equivalent)

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

# For transcript collection, also install Playwright browsers
playwright install msedge
```

### Configuration

Copy and edit the config file:

```bash
cp config/standing-instructions.yaml config/standing-instructions.yaml.bak
```

Edit `config/standing-instructions.yaml` to customize priorities, RSS feeds, and model preferences. The agent identifies you automatically via WorkIQ — no manual identity config needed.

The config supports environment variables (`$LOCALAPPDATA`, `$HOME`, `~`) in all string fields.

### Telegram Setup (optional)

1. Message [@BotFather](https://t.me/BotFather) on Telegram, create a new bot, copy the token
2. Edit `config/standing-instructions.yaml`:
   ```yaml
   telegram:
     enabled: true
     bot_token: "your-token-here"
   ```
3. Start the daemon — your bot is live

### Run

```bash
# Start the daemon — Telegram + triage heartbeat + job queue
python src/main.py

# Single cycle then exit (triage + jobs + sync)
python src/main.py --once

# Run a specific stage only (dev/debugging)
python src/main.py --mode digest --once
python src/main.py --mode monitor --once
python src/main.py --mode transcripts --once
python src/main.py --mode intel --once
```

### Interacting via Telegram

Just talk to the bot naturally:

- "What's new?" — queries WorkIQ for recent activity
- "Did I miss anything in meetings yesterday?" — checks calendar + transcripts
- "Run a digest" — triggers a full digest immediately
- "Analyze Parloa vs 11Labs" — deep research task
- "Grab transcripts" — collects meeting transcripts from Teams

The bot also sends you proactive notifications when jobs complete.

### Job Files

You can also drop YAML files into `tasks/pending/` or OneDrive `Pulse/Jobs/`:

```yaml
type: digest
```

```yaml
type: research
task: "Compare AWS Bedrock pricing vs Azure OpenAI"
description: "Pull latest public pricing, summarize differences"
```

Jobs are picked up immediately (or on next heartbeat if from OneDrive).

## Architecture

```
Input Sources
  ├── Teams Transcripts ── Playwright (browser automation, no LLM)
  ├── M365 Inbox/Teams ── WorkIQ MCP (email, calendar, messages)
  └── RSS Feeds ───────── feedparser (Google News, TechCrunch, HN, etc.)
        │
        ▼
  Pulse Agent (Python)
  ├── Phase 1: Collect content locally (Python, deterministic)
  ├── Phase 1b: Fetch RSS feeds, deduplicate, filter by recency
  └── Phase 2: Send to GHCP SDK agent for analysis
        │
        ▼
  GitHub Copilot SDK
  ├── CopilotClient → JSON-RPC → Copilot CLI (server mode)
  ├── WorkIQ MCP ── queries M365 for what's handled vs. outstanding
  ├── Custom tools ── log_action, write_output, queue_task
  └── Multi-model routing ── gpt-4.1, claude-sonnet, claude-opus
        │
        ▼
  Output → OneDrive-synced folder
  ├── output/digests/2026-02-16.json   Structured digest
  ├── output/digests/2026-02-16.md     Human-readable digest
  ├── output/intel/2026-02-16.md       External intel brief
  ├── output/pulse-signals/*.md        Drafted GBB Pulse signals
  ├── output/monitoring-*.md           Triage reports
  └── logs/2026-02-16.jsonl            Structured audit trail
        │
        ▼
  Telegram Bot (conversational interface)
  └── "What's new?" → queries WorkIQ, responds instantly
  └── "Run a digest" → triggers job, notifies when done
  └── Proactive morning digest delivery
```

## How It Works

The daemon runs three things concurrently on one async event loop:

1. **Telegram bot** — listens for messages, puts jobs on an `asyncio.Queue`
2. **Heartbeat** (every 30min, office hours only) — puts a triage job on the same queue + pulls OneDrive job files
3. **Worker** — processes jobs from the queue one at a time (GHCP SDK sessions)

Jobs execute immediately when queued — no waiting for the next cycle.

### Job Types

| Type | What It Does |
|------|-------------|
| `digest` | Scans input folders + RSS feeds + WorkIQ inbox, generates a 30-50 line filtered digest |
| `transcripts` | Playwright scrapes Teams Calendar for meeting transcripts from the past week |
| `intel` | RSS-only intel brief (competitor moves, product launches, trends) |
| `research` | Autonomous deep research mission with full WorkIQ + local tool access |

## Project Structure

```
├── README.md                          This file
├── AGENTS.md                          Agent behavior spec (contest requirement)
├── .mcp.json                          MCP server config (WorkIQ)
├── requirements.txt                   Python dependencies (pinned)
├── src/
│   ├── main.py                        Daemon — event loop, job worker, heartbeat
│   ├── telegram_bot.py                Telegram interface — chat, notifications, state
│   ├── utils.py                       Shared logging, event streaming, session context manager
│   ├── config.py                      YAML config loader with env var expansion + validation
│   ├── session.py                     GHCP SDK session builder (prompts, MCP, tools, permissions)
│   ├── digest.py                      Digest mode — file extraction, RSS, WorkIQ, LLM analysis
│   ├── intel.py                       Intel mode — RSS collection + standalone analysis
│   ├── monitor.py                     Monitoring mode — WorkIQ triage
│   ├── researcher.py                  Research mode — task queue runner
│   ├── transcripts.py                 Transcript collection — Playwright DOM scraping
│   └── tools.py                       Custom GHCP SDK tools (log, write, queue, dismiss, note)
├── config/
│   ├── standing-instructions.yaml     All behavior config (priorities, models, feeds)
│   └── skills/                        GHCP SDK skill definitions
├── input/                             User content (gitignored)
│   ├── transcripts/                   Meeting transcripts (.txt)
│   ├── documents/                     Docs, presentations, spreadsheets
│   └── emails/                        Email exports (.eml)
├── output/                            Agent output (gitignored)
│   ├── digests/                       Daily digests
│   ├── intel/                         Intel briefs
│   ├── pulse-signals/                 Drafted GBB Pulse signals
│   ├── .digest-state.json             Incremental processing state
│   ├── .digest-actions.json           Dismiss/note state
│   └── .intel-state.json              RSS deduplication state
├── tasks/
│   ├── pending/                       Queued jobs (.yaml) — synced from OneDrive
│   └── completed/                     Done tasks (gitignored)
└── logs/                              Structured JSONL audit logs (gitignored)
```

## Custom Tools

The agent has 5 custom tools registered via the GHCP SDK:

| Tool | Description |
|------|-------------|
| `log_action` | Write action + reasoning to `logs/YYYY-MM-DD.jsonl` (audit trail) |
| `write_output` | Write files to `output/` (path-traversal protected) |
| `queue_task` | Add a job to `tasks/pending/` (digest, research, transcripts, intel) |
| `dismiss_item` | Mark a digest item as handled (won't reappear) |
| `add_note` | Annotate a digest item for future context |

## Security & Governance

- **Read-only by default** — the agent reads and summarizes, it does not send emails or post messages
- **Local-first** — all processing happens on your machine, content is not uploaded
- **Path-traversal protection** — `write_output` validates that file paths stay inside `output/`
- **Full audit trail** — every agent action logged with reasoning to structured JSONL
- **Config validation** — warns on placeholder values, missing fields, or misconfiguration at startup
- **Graceful shutdown** — handles SIGINT/SIGTERM cleanly, finishes current cycle before stopping
- **Scoped permissions** — WorkIQ only accesses your own M365 data, Playwright uses your browser session

## Configuration Reference

`config/standing-instructions.yaml` controls all behavior:

```yaml
monitoring:
  interval: "30m"               # Daemon loop interval (supports h/m/s)
  priorities: [...]             # What to watch for (list of strings)
  autonomy:
    auto_send: false            # Never auto-send emails
    auto_send_low_risk: true    # Auto-ack meeting invites, simple replies
    max_nudges: 2               # Max follow-up nudges per item

digest:
  input_paths:                  # Folders to scan for content
    - path: "input/transcripts"
      type: "transcripts"
  incremental: true             # Only process new/modified files
  priorities: [...]             # What to flag in digest output

intelligence:
  lookback_hours: 48            # How far back to check RSS feeds
  max_articles: 100             # Cap per run
  feeds:                        # RSS feed URLs + display names
    - url: "https://..."
      name: "Source Name"
  competitors:                  # Companies to track
    - company: "AWS"
      watch: ["Bedrock pricing", "new AI services"]

models:
  digest: "claude-sonnet"       # Model per mode
  triage: "gpt-4.1"
  research: "claude-opus"
  default: "claude-sonnet"
```

## Tech Stack

- **Language:** Python 3.12
- **Agent runtime:** GitHub Copilot SDK (`github-copilot-sdk`) → Copilot CLI server mode (JSON-RPC)
- **M365 integration:** WorkIQ MCP server (emails, calendar, Teams, files)
- **Transcript collection:** Playwright Python (Edge browser automation)
- **External intel:** feedparser (RSS)
- **Document extraction:** python-docx, python-pptx, PyPDF2, openpyxl
- **User interface:** Telegram bot (python-telegram-bot, conversational + proactive notifications)
- **Output sync:** OneDrive (local folder sync)
- **Logging:** Python `logging` module → structured JSON lines
- **Config:** YAML with env var expansion

## Troubleshooting

**"Config not found"** — Make sure `config/standing-instructions.yaml` exists. Check the path from the project root.

**"Failed to connect to GitHub Copilot SDK"** — The Copilot CLI must be installed and authenticated. Run `github-copilot-cli auth` first.


**Transcript collection fails** — The browser needs a logged-in Teams session. Open Edge manually, navigate to `teams.microsoft.com`, sign in, then try again. The `user_data_dir` in config must point to a valid Edge profile.

**WorkIQ queries return nothing** — Accept the WorkIQ EULA first. The MCP server must be installed and accessible on your PATH.

**charmap encoding errors on Windows** — All terminal output is ASCII-safe encoded. If you still see errors, check that your Python is 3.12+ and your terminal supports UTF-8.
