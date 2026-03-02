# Pulse Agent

**An autonomous information processing engine for knowledge workers.**

You have 8 meetings a day and retain 20% of what's said. You're CC'd on 50 email threads you'll never read. Competitors announce changes at 2 AM. Pulse Agent runs when you don't -- it consumes everything you can't and tells you only what matters.

> "I had 8 meetings yesterday. I was distracted in half of them. At 7 AM, Pulse told me the 3 things that actually need my attention -- including an escalation I completely missed."

[![Watch the demo](https://img.shields.io/badge/Watch%20Demo-YouTube-red?style=for-the-badge&logo=youtube)](https://youtu.be/E-IltXvRNkc)

---

## Quick Start

### Option A: AI-assisted setup (recommended)

Open **GitHub Copilot Chat**, **Claude Code**, or any AI coding assistant and paste this:

> Follow the instructions at https://github.com/aiappsgbb/pulse-agent/blob/main/SETUP.md to set up Pulse Agent on my machine.

Your AI will clone the repo, install all prerequisites, set up the environment, and verify everything works. No manual steps -- it handles Python, Node.js, GitHub CLI, WorkIQ, everything.

### Option B: Double-click installer

Double-click **`install.bat`**. The installer automatically:

- Installs Python, Node.js, and GitHub CLI via [winget](https://learn.microsoft.com/en-us/windows/package-manager/winget/)
- Installs the WorkIQ MCP server and Copilot CLI extension
- Creates a Python virtual environment and installs all dependencies
- Sets up Playwright Edge for browser automation
- Seeds the data directory on OneDrive
- Creates a **"Start Pulse"** shortcut on your Desktop

### After install

1. Open Edge and sign into [teams.microsoft.com](https://teams.microsoft.com) (one time, for transcript/inbox scanning)
2. Double-click **"Start Pulse"** on your Desktop
3. On first run, the Chat tab walks you through personalization -- name, role, priorities, team members

<details>
<summary>Option C: Manual install</summary>

**Prerequisites:** Python 3.12+, Node.js, GitHub Copilot CLI authenticated, OneDrive for Business syncing.

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
playwright install msedge
npm install -g @microsoft/workiq
python src/pulse.py
```

Or run the setup script directly:

```powershell
powershell -ExecutionPolicy Bypass -File setup.ps1
```

</details>

---

## What You Get

Pulse runs on a schedule you control. By default:

| Time | What happens |
|------|-------------|
| **7:00 AM** | Morning digest -- transcripts, emails, Teams messages, filtered to what's outstanding |
| **Every 30 min** | Inbox triage -- unread Teams + Outlook with drafted replies you can send in one tap |
| **9:00 AM** | Intel brief -- RSS feeds filtered for your topics and competitors |
| **Overnight** | Knowledge mining -- archives communications, enriches project memory |

Everything cross-references against what you've already handled. Replied to that email? Gone. Attended a meeting with no open actions? Gone. A typical digest is 30 lines, not 400.

### The TUI

Three tabs:

- **Inbox** -- merged triage + digest items, sorted by priority. `d` to snooze, `r` to reply, `n` to add a note.
- **Projects** -- per-engagement memory with commitment tracking and deadlines.
- **Chat** -- ask anything. "What did Fatos say about Vodafone?" -- searches transcripts, emails, and M365 via WorkIQ.

Queue jobs from anywhere: `Ctrl+D` (digest), `Ctrl+T` (triage), `Ctrl+I` (intel), `Ctrl+X` (transcripts).

### Reply Flow

Triage items with unread messages include drafted replies. Press `r`, review the draft, hit Enter to send. The message is delivered via Playwright (deterministic browser automation, no LLM in the send path). You always review before anything is sent.

---

## How It Works

```
  Data Collection (Playwright, no LLM)          Pulse Agent (Python daemon)
  ─────────────────────────────────────          ──────────────────────────
  Teams Transcripts ─── browser scraping   ──>   Scheduler (cron-like patterns)
  Teams Inbox ───────── unread scan        ──>   Job Worker (one at a time)
  Outlook Inbox ─────── unread scan        ──>   TUI Backend (status, chat, toasts)
  Calendar ──────────── event scan         ──>        |
  Local Files ───────── .docx .pdf .pptx   ──>        v
  RSS Feeds ─────────── feedparser         ──>   GitHub Copilot SDK
                                                      |
                                                 ┌────┴────────────────────┐
                                                 │ WorkIQ MCP (M365 data)  │
                                                 │ 14 custom tools         │
                                                 │ 4 session hooks         │
                                                 │ 6 sub-agents            │
                                                 │ Multi-model routing     │
                                                 └────┬────────────────────┘
                                                      |
                                                      v
                                              $PULSE_HOME (OneDrive)
                                              digests/, intel/, projects/,
                                              transcripts/, logs/
```

The daemon runs three concurrent tasks:

1. **Scheduler** -- fires jobs on cron-like patterns, syncs OneDrive job files every 60s
2. **Worker** -- processes one job at a time via GitHub Copilot SDK sessions
3. **TUI backend** -- status updates, chat polling, streaming deltas, Windows toast notifications

All output goes to `$PULSE_HOME` (auto-detected from OneDrive for Business). Everything syncs via OneDrive -- no cloud backend, no external services.

---

## Modes

| Mode | Command | What It Does |
|------|---------|-------------|
| Transcript Collection | `--mode transcripts` | Scrapes Teams Calendar for recorded meetings, compresses transcripts to structured notes |
| Digest | `--mode digest` | Scans all sources, generates a filtered digest with action buttons |
| Triage | `--mode monitor` | Inbox triage with one-tap reply/dismiss/note actions |
| Research | `--mode research` | Autonomous deep research (60 min timeout) |
| Intel | `--mode intel` | RSS feeds filtered for relevance, concise brief |
| Chat | TUI chat tab | Natural language queries with streaming replies |
| Knowledge Mining | `--mode knowledge` | Overnight: archive communications, enrich project memory |

Run any mode standalone: `python src/pulse.py --mode digest --once`

---

## Configuration

Pulse auto-detects its data directory from OneDrive for Business. No `.env` file needed.

On first run, the Chat tab guides you through setup. Your config is saved to `$PULSE_HOME/standing-instructions.yaml`. You can also edit it directly:

```yaml
user:
  name: "Your Name"
  role: "Your Role"

monitoring:
  priorities: ["Customer escalations", "Deal blockers"]
  vip_contacts: ["Alice", "Bob"]

schedule:
  - id: morning-digest
    type: digest
    pattern: "daily 07:00"
  - id: triage
    type: monitor
    pattern: "every 30m"
    office_hours_only: true
```

**Config resolution:** `--config` flag > `PULSE_CONFIG` env var > `$PULSE_HOME/standing-instructions.yaml` > `config/standing-instructions.yaml` (template fallback)

---

## Inter-Agent Communication

Multiple team members can run Pulse Agent and send tasks to each other via OneDrive. No infrastructure needed -- just file-based messaging.

```yaml
# In standing-instructions.yaml
team:
  - name: "Esther Barthel"
    alias: "esther"
  - name: "Fatos Ismali"
    alias: "fatos"
```

From chat: *"Ask Esther what context she has on the Vodafone deal"* -- Pulse writes a task YAML to Esther's OneDrive folder, her agent picks it up within 60 seconds, processes it, and sends the response back.

---

## Security & Responsible AI

- **Draft-first** -- outbound messages always shown for review before sending. The agent never auto-sends.
- **Full audit trail** -- every tool call auto-logged to `logs/YYYY-MM-DD.jsonl` via session hooks (100% coverage, not optional)
- **Path guardrails** -- defense-in-depth validation at both hook and handler level blocks path traversal
- **Scoped access** -- WorkIQ sees only your M365 data, Playwright uses your authenticated browser session
- **Microsoft-tenant processing** -- content is processed through GitHub Copilot SDK (Microsoft cloud). No third-party services. Data stays within the Microsoft ecosystem but is NOT processed purely locally.

See [docs/RAI.md](docs/RAI.md) for full details and honest limitations.

---

## Testing

690 tests covering real browser selectors, prompt-to-code contracts, TTL/staleness, streaming IPC, and full reply round-trips.

```bash
python -m pytest tests/ -q          # Run all
python -m pytest tests/ -x --tb=short  # Stop on first failure
python -m pytest tests/ -k "reply"  # Filter by name
```

Key test suites:
- **Browser selectors** (57 tests) -- real JavaScript against real DOM in headless Chromium
- **Reply flow** (43 tests) -- full TUI -> YAML -> worker round-trip
- **Contracts** (15 tests) -- prompt schema matches code that parses LLM output
- **TTL & IPC** (45 tests) -- carry-forward staleness, dismiss expiry, streaming chat

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Language | Python 3.12 |
| Agent runtime | GitHub Copilot SDK -> Copilot CLI (JSON-RPC) |
| M365 integration | WorkIQ MCP server |
| Browser automation | Playwright (Edge) |
| User interface | Textual TUI + winotify (Windows toasts) |
| External intel | feedparser (RSS) |
| Document extraction | python-docx, python-pptx, PyPDF2, openpyxl |
| Data sync | OneDrive for Business |
| Config | YAML with env var expansion |

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Config not found | Run `python src/pulse.py` -- first-run onboarding creates it |
| Copilot SDK won't connect | Run `github-copilot-cli auth` to authenticate |
| Transcript collection fails | Open Edge, sign into `teams.microsoft.com`, then retry |
| WorkIQ returns nothing | Accept the WorkIQ EULA first via the MCP tool |
| Encoding errors on Windows | Ensure Python 3.12+ and UTF-8 terminal |

---

## Further Reading

- [CLAUDE.md](CLAUDE.md) -- full architecture, technical deep-dives, design decisions
- [AGENTS.md](AGENTS.md) -- agent behavior instructions
- [docs/RAI.md](docs/RAI.md) -- responsible AI notes
- [docs/SDK-FEEDBACK.md](docs/SDK-FEEDBACK.md) -- GitHub Copilot SDK product feedback
