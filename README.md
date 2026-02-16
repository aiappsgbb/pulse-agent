# Pulse Agent

**An autonomous information processing engine for knowledge workers.**

You have 8 meetings a day and retain 20% of what's said. You're CC'd on 50 email threads you'll never read. Competitors announce changes at 2 AM. Pulse Agent runs when you don't — it consumes everything you can't and tells you only what matters.

Not a copilot. Not a chatbot. A local daemon with standing instructions, full M365 visibility, and structured output.

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

Edit `config/standing-instructions.yaml`:

```yaml
owner:
  name: "Your Name"              # <- change this
  email: "you@company.com"       # <- change this
  timezone: "Europe/London"      # <- your timezone

transcripts:
  playwright:
    # Uses $LOCALAPPDATA env var — usually resolves automatically on Windows.
    # Override if your Edge profile is elsewhere.
    user_data_dir: "$LOCALAPPDATA/ms-playwright/mcp-msedge-profile"
```

The config supports environment variables (`$LOCALAPPDATA`, `$HOME`, `~`) in all string fields.

### Run

```bash
# Generate a daily digest (transcripts + inbox + RSS feeds)
python src/main.py --mode digest --once

# Collect meeting transcripts from Teams (opens browser)
python src/main.py --mode transcripts --once

# Run monitoring triage (WorkIQ queries only)
python src/main.py --mode monitor --once

# Run a deep research mission from the task queue
python src/main.py --mode research --once

# Standalone external intel brief
python src/main.py --mode intel --once

# Daemon mode — runs on a loop (default 30m interval)
python src/main.py --mode digest
```

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
  Output
  ├── output/digests/2026-02-16.md     Daily digest (30-50 lines)
  ├── output/intel/2026-02-16.md       External intel brief
  ├── output/monitoring-*.md           Triage reports
  └── logs/2026-02-16.jsonl            Structured audit trail
```

## Modes

### Digest (`--mode digest`)

The primary mode. Runs three phases:

1. **Local collection** — Scans `input/` folders for transcripts, documents, emails. Extracts text from `.txt`, `.md`, `.vtt`, `.docx`, `.pptx`, `.pdf`, `.xlsx`, `.csv`, `.eml`. Tracks what's already been processed for incremental runs.
2. **RSS feeds** — Fetches 8 configured feeds (Google News, TechCrunch, The Verge, Hacker News, Google AI Blog). Deduplicates via content hash. Filters by recency (48h lookback).
3. **LLM analysis** — Sends collected content + RSS articles to a GHCP SDK session. The agent also queries WorkIQ for your inbox and Teams messages, cross-references against what you've already handled, and generates a filtered digest.

Output: `output/digests/YYYY-MM-DD.md`

### Transcripts (`--mode transcripts`)

Deterministic Playwright script (no LLM involved in navigation):

1. Launches Edge with persistent auth profile
2. Navigates to Teams Calendar, goes to previous week
3. Finds meetings with "View recap" buttons
4. Opens each recap, navigates to the Transcript tab
5. Scrolls through the virtualized list (Fluent UI `ms-List`), collecting all entries
6. Saves each transcript as a `.txt` file to `input/transcripts/`

This is needed because Teams transcripts exist only in the cloud — they don't sync as text files to local storage.

### Monitor (`--mode monitor`)

Multi-step WorkIQ triage. The agent makes 5-10+ separate WorkIQ queries to:
- Triage unread/recent emails
- Pull context for upcoming meetings
- Scan Teams messages and threads
- Check for overdue follow-ups
- Write a comprehensive monitoring report

Output: `output/monitoring-YYYY-MM-DDTHH-MM.md`

### Research (`--mode research`)

Picks up tasks from `tasks/pending/*.yaml` and executes them autonomously. Each task gets its own GHCP SDK session with full access to WorkIQ, local files, and browser tools. Completed tasks are moved to `tasks/completed/`.

### Intel (`--mode intel`)

Standalone RSS feed analysis (also runs as Phase 1b of digest mode). Fetches feeds, sends articles to the agent for filtering and summarization.

Output: `output/intel/YYYY-MM-DD.md`

## Project Structure

```
├── README.md                          This file
├── AGENTS.md                          Agent behavior spec (contest requirement)
├── .mcp.json                          MCP server config (WorkIQ)
├── requirements.txt                   Python dependencies (pinned)
├── src/
│   ├── main.py                        CLI entrypoint — mode routing, daemon loop, signal handling
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
│   ├── standing-instructions.yaml     All behavior config (owner, priorities, models, feeds)
│   └── skills/                        GHCP SDK skill definitions
├── input/                             User content (gitignored)
│   ├── transcripts/                   Meeting transcripts (.txt)
│   ├── documents/                     Docs, presentations, spreadsheets
│   └── emails/                        Email exports (.eml)
├── output/                            Agent output (gitignored)
│   ├── digests/                       Daily digests
│   ├── intel/                         Intel briefs
│   ├── .digest-state.json             Incremental processing state
│   └── .intel-state.json              RSS deduplication state
├── tasks/
│   ├── pending/                       Queued research tasks (.yaml)
│   └── completed/                     Done tasks (gitignored)
└── logs/                              Structured JSONL audit logs (gitignored)
```

## Custom Tools

The agent has 5 custom tools registered via the GHCP SDK:

| Tool | Description |
|------|-------------|
| `log_action` | Write action + reasoning to `logs/YYYY-MM-DD.jsonl` (audit trail) |
| `write_output` | Write files to `output/` (path-traversal protected) |
| `queue_task` | Add a research task to `tasks/pending/` |
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
owner:
  name: "..."                   # Your display name
  email: "...@company.com"      # Your email
  timezone: "Europe/London"     # Used for scheduling context

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
- **Logging:** Python `logging` module → structured JSON lines
- **Config:** YAML with env var expansion

## Troubleshooting

**"Config not found"** — Make sure `config/standing-instructions.yaml` exists. Check the path from the project root.

**"Failed to connect to GitHub Copilot SDK"** — The Copilot CLI must be installed and authenticated. Run `github-copilot-cli auth` first.

**"CONFIG: still has placeholder value"** — Edit `config/standing-instructions.yaml` and replace `"Your Name"` and `"your.email@microsoft.com"` with your real details.

**Transcript collection fails** — The browser needs a logged-in Teams session. Open Edge manually, navigate to `teams.microsoft.com`, sign in, then try again. The `user_data_dir` in config must point to a valid Edge profile.

**WorkIQ queries return nothing** — Accept the WorkIQ EULA first. The MCP server must be installed and accessible on your PATH.

**charmap encoding errors on Windows** — All terminal output is ASCII-safe encoded. If you still see errors, check that your Python is 3.12+ and your terminal supports UTF-8.
