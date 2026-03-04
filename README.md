<p align="center">
  <img src="https://img.shields.io/badge/Pulse_Agent-information_engine-0078D4?style=for-the-badge&logo=windows&logoColor=white" alt="Pulse Agent" />
</p>

<h1 align="center">Pulse Agent</h1>

<p align="center">
  <strong>An autonomous information processing engine for knowledge workers.</strong>
</p>

<p align="center">
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.12+-3776AB.svg?logo=python&logoColor=white" alt="Python 3.12+" /></a>
  <a href="https://nodejs.org/"><img src="https://img.shields.io/badge/node.js-20+-339933.svg?logo=nodedotjs&logoColor=white" alt="Node.js 20+" /></a>
  <a href="https://github.com/features/copilot"><img src="https://img.shields.io/badge/GitHub%20Copilot%20SDK-8957e5.svg?logo=github&logoColor=white" alt="GitHub Copilot SDK" /></a>
  <a href="https://www.microsoft.com/en-us/microsoft-365"><img src="https://img.shields.io/badge/Microsoft%20365-WorkIQ-D83B01.svg?logo=microsoft&logoColor=white" alt="WorkIQ" /></a>
  <a href="tests/"><img src="https://img.shields.io/badge/tests-690-brightgreen.svg?logo=pytest&logoColor=white" alt="690 Tests" /></a>
</p>

---

You have 8 meetings a day and retain 20% of what's said. You're CC'd on 50 email threads you'll never read. Competitors announce changes at 2 AM. Pulse Agent runs when you don't -- it consumes everything you can't and tells you only what matters.

> *"I had 8 meetings yesterday. I was distracted in half of them. At 7 AM, Pulse told me the 3 things that actually need my attention -- including an escalation I completely missed."*

<p align="center">
  <strong>&#9655; Watch the demo</strong><br/>
  <a href="https://youtu.be/E-IltXvRNkc">
    <img src="https://img.youtube.com/vi/E-IltXvRNkc/maxresdefault.jpg" alt="Watch the demo" width="560" />
  </a>
</p>

---

## Why Pulse Agent?

**Autonomous.** Runs on a schedule you control -- morning digests, 30-minute inbox triage, overnight knowledge mining. No prompting required.

**Cross-referencing.** Everything is checked against what you've already handled. Replied to that email? Gone. Attended a meeting with no open actions? Gone. A typical digest is 30 lines, not 400.

**Actionable.** Triage items include drafted replies. Press `r`, review the draft, hit Enter to send. Deterministic Playwright automation -- no LLM in the send path.

**Team-aware.** Multiple agents communicate via OneDrive. *"Ask Esther what context she has on the Vodafone deal"* -- her agent picks it up within 60 seconds and sends the answer back.

**Project memory.** Persistent per-engagement context with commitment tracking. Overdue items and approaching deadlines surface automatically.

**Intel.** RSS feeds filtered for your topics and competitors. Curated, not a firehose.

---

## What You Get

| Time | What happens |
|------|-------------|
| **7:00 AM** | Morning digest -- transcripts, emails, Teams messages, filtered to what's outstanding |
| **Every 30 min** | Inbox triage -- unread Teams + Outlook with drafted replies you can send in one tap |
| **9:00 AM** | Intel brief -- RSS feeds filtered for your topics and competitors |
| **Overnight** | Knowledge mining -- archives communications, enriches project memory |

### The TUI

Four tabs:

- **Inbox** -- merged triage + digest items, sorted by priority. `d` to dismiss, `r` to reply, `n` to add a note.
- **Projects** -- per-engagement memory with commitment tracking and deadlines.
- **Jobs** -- live view of running, pending, and completed jobs with activity logs.
- **Chat** -- ask anything. *"What did Fatos say about Vodafone?"* -- searches transcripts, emails, and M365 via WorkIQ.

Queue jobs from anywhere: `Ctrl+D` (digest), `Ctrl+T` (triage), `Ctrl+I` (intel), `Ctrl+X` (transcripts).

<!-- TODO: Add TUI screenshot
<p align="center">
  <img src="assets/screenshot-tui.png" alt="Pulse Agent TUI" width="700" />
</p>
-->

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

## Architecture

<p align="center">
  <img src="https://img.shields.io/badge/Microsoft_Teams-6264A7?style=flat-square&logo=microsoftteams&logoColor=white" alt="Teams" />
  <img src="https://img.shields.io/badge/Outlook-0078D4?style=flat-square&logo=microsoftoutlook&logoColor=white" alt="Outlook" />
  <img src="https://img.shields.io/badge/OneDrive-0078D4?style=flat-square&logo=microsoftonedrive&logoColor=white" alt="OneDrive" />
  <img src="https://img.shields.io/badge/Edge-0078D4?style=flat-square&logo=microsoftedge&logoColor=white" alt="Edge" />
  <img src="https://img.shields.io/badge/Playwright-2EAD33?style=flat-square&logo=playwright&logoColor=white" alt="Playwright" />
  <img src="https://img.shields.io/badge/GitHub_Copilot_SDK-8957e5?style=flat-square&logo=github&logoColor=white" alt="Copilot SDK" />
  <img src="https://img.shields.io/badge/WorkIQ_MCP-D83B01?style=flat-square&logo=microsoft&logoColor=white" alt="WorkIQ" />
  <img src="https://img.shields.io/badge/Dataverse_MCP-742774?style=flat-square&logo=dynamics365&logoColor=white" alt="Dataverse" />
  <img src="https://img.shields.io/badge/Python_3.12-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python" />
</p>

```
                        +-------------------------------------------+
                        |          Microsoft 365 (your account)      |
                        |  Teams  |  Outlook  |  Calendar  |  OneDrive  |
                        +----+--------+------------+----------+------+
                             |        |            |          |
                    Playwright (Edge browser automation)      |
                             |        |            |          |
                  +----------v--------v------------v---+      |
                  |        Data Collection (no LLM)    |      |
                  |  Transcripts | Inbox | Calendar    |      |
                  |  Unread scan | Event scan          |      |
                  +----------------+-------------------+      |
                                   |                          |
                  +----------------v-------------------+      |
                  |       GitHub Copilot SDK            |      |
                  |  +-------------------------------+  |      |
                  |  | WorkIQ MCP (M365 data layer)  |  |      |
                  |  +-------------------------------+  |      |
                  |  +-------------------------------+  |      |
                  |  | Dataverse MCP (CRM, optional) |  |      |
                  |  +-------------------------------+  |      |
                  |  14 custom tools | 6 sub-agents    |      |
                  |  4 session hooks | multi-model      |      |
                  +----------------+-------------------+      |
                                   |                          |
                  +----------------v--------------------------v--+
                  |          $PULSE_HOME (OneDrive sync)          |
                  |  digests/ | intel/ | projects/ | transcripts/ |
                  |  jobs/ | logs/ | pulse-signals/               |
                  +--------------------+-------------------------+
                                       |
                  +--------------------v-------------------------+
                  |              Textual TUI                      |
                  |  Inbox | Projects | Jobs | Chat               |
                  |  + winotify (Windows toast notifications)      |
                  +----------------------------------------------+
```

**Three layers, one daemon:**

| Layer | What | Technology |
|-------|------|-----------|
| **Collection** | Scrapes Teams transcripts, inboxes, calendar -- real-time state that APIs can't provide | Playwright + Edge (your authenticated session) |
| **Intelligence** | Triages, cross-references, drafts replies, tracks commitments, mines knowledge | GitHub Copilot SDK + WorkIQ MCP + 14 custom tools |
| **Delivery** | 4-tab TUI, streaming chat, Windows toasts, OneDrive sync across team | Textual + winotify + OneDrive for Business |

Everything runs as a single Python daemon with three concurrent tasks: **scheduler** (cron-like patterns + OneDrive job sync), **worker** (one SDK session at a time), and **TUI backend** (status, chat, toasts). All output to `$PULSE_HOME` on OneDrive -- no cloud backend, no external services beyond Microsoft.

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

mcp_servers:
  dataverse:
    url: "https://your-org.crm.dynamics.com/api/mcp"  # optional, skip if no CRM

schedule:
  - id: morning-digest
    type: digest
    pattern: "daily 07:00"
  - id: triage
    type: monitor
    pattern: "every 30m"
    office_hours_only: true
```

**MCP servers:** All modes inherit `default_mcp_servers` from `modes.yaml`. Instance-specific settings (like Dataverse URL) go in your standing instructions. Unconfigured servers are skipped gracefully — no crashes.

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

| Suite | Tests | What it validates |
|-------|-------|-------------------|
| Browser selectors | 57 | Real JavaScript against real DOM in headless Chromium |
| Reply flow | 43 | Full TUI -> YAML -> worker round-trip |
| Dedup defense | 25 | 3-layer dedup + CKEditor draft contamination |
| Contracts | 15 | Prompt schema matches code that parses LLM output |
| TTL & IPC | 45 | Carry-forward staleness, dismiss expiry, streaming chat |

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Language | Python 3.12 |
| Agent runtime | GitHub Copilot SDK -> Copilot CLI (JSON-RPC) |
| M365 integration | WorkIQ MCP server |
| CRM integration | Dataverse MCP (Dynamics 365, optional, config-driven) |
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
