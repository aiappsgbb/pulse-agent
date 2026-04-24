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
  <a href="tests/"><img src="https://img.shields.io/badge/tests-854-brightgreen.svg?logo=pytest&logoColor=white" alt="854 Tests" /></a>
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

**Cross-referencing.** Everything is checked against what you've already handled via WorkIQ. Replied to that email? Gone. Meeting with no open actions? Gone. A typical digest is 30 lines, not 400.

**Actionable.** Triage items include AI-drafted replies. Press `r`, review the draft, hit Enter to send. Deterministic Playwright automation -- no LLM in the send path. Then sweep your inbox clean with one command.

**Multi-agent collaboration.** 20+ team agents communicate autonomously via OneDrive. *"Ask Esther what context she has on the Vodafone deal"* -- her agent searches her transcripts and projects, and sends the answer back within 60 seconds. No infrastructure, no APIs -- just file-based messaging across OneDrive.

**Project memory.** Auto-discovered per-engagement context with commitment tracking. Projects are only created when a customer has 3+ mentions across 2+ source types with an actionable element -- not from every meeting or CC. Stale observer projects auto-archive after 14 days. CRM deals link to existing projects but don't auto-create new ones. The result: a clean list of projects you're actually working on.

**Intel.** RSS feeds filtered for your topics and competitors. Product launches, pricing changes, regulatory moves -- curated, not a firehose.

---

## What You Get

| Time | What happens |
|------|-------------|
| **7:00 AM** | Morning digest -- transcripts, emails, Teams, docs, RSS, filtered to what's outstanding |
| **Every 30 min** | Inbox triage -- unread Teams + Outlook with AI-drafted replies you can send in one tap |
| **9:00 AM** | Intel brief -- RSS feeds filtered for your topics and competitors |
| **2:00 AM** | Knowledge mining -- archives communications, enriches project memory with commitments |
| **On demand** | Inbox sweep -- clears FYI/low-priority items so Pulse becomes your single source of truth |
| **Anytime** | Chat -- *"What did Fatos say about Vodafone?"* -- searches your transcripts, emails, and M365 |

### The TUI

Five-tab dashboard:

- **Today** -- your meetings, your commitments (only yours, not other people's tasks), digest/intel briefing summary. Completed items shown with strikethrough.
- **Inbox** -- merged triage + digest items, sorted by priority. `d` to dismiss, `r` to reply with AI-drafted response, `n` to add a note.
- **Projects** -- auto-discovered per-engagement memory with stakeholders, commitments, risk levels, and deadlines. Sortable, filterable.
- **Jobs** -- live view of running, pending, and completed jobs with per-job activity logs.
- **Chat** -- natural language queries with streaming replies. Access to WorkIQ, local file search, and browser actions.

Queue jobs from anywhere: `Ctrl+D` (digest), `Ctrl+T` (triage), `Ctrl+I` (intel), `Ctrl+X` (transcripts), `Ctrl+M` (inbox sweep).


---

## Quick Start

**Total time:** ~10 minutes.

**1.** Create a folder where you want the code to live (e.g. `C:\Dev\pulse-agent`) and open it in **VS Code** via **File > Open Folder**.

**2.** Open the AI chat panel (**Copilot Chat**, **Claude Code**, etc.) and paste:

> Install Pulse Agent on my machine from https://github.com/aiappsgbb/pulse-agent.git — follow SETUP.md step by step. Run all commands yourself — only pause when a step says USER ACTION REQUIRED.

Your AI handles everything: git, Python, Node.js, GitHub CLI, WorkIQ, Playwright, browser auth, personalization, and a desktop shortcut. Double-click **"Start Pulse"** when it finishes.

<details>
<summary>Alternative: double-click installer (no AI needed)</summary>

If you prefer not to use an AI assistant, clone the repo manually and double-click **`install.bat`** inside it. The installer handles everything above automatically. After install, run `python src/pulse.py --health-check` to sign into Teams.

</details>

<details>
<summary>Alternative: manual install</summary>

**Prerequisites:** Python 3.12+, Node.js, GitHub Copilot CLI authenticated, OneDrive for Business syncing.

```bash
git clone https://github.com/aiappsgbb/pulse-agent.git
cd pulse-agent
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
playwright install msedge
npm install -g @microsoft/workiq
python src/pulse.py --health-check
python src/pulse.py
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
                  |  Transcripts | Inbox scan          |      |
                  |  Calendar | Sweep | Send/Reply     |      |
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
                  |  15 custom tools | 6 sub-agents    |      |
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
                  |  Today | Inbox | Projects | Jobs | Chat       |
                  |  + winotify toast alerts + inter-agent comms   |
                  +----------------------------------------------+
```

**Three layers, one daemon:**

| Layer | What | Technology |
|-------|------|-----------|
| **Collection** | Scrapes Teams transcripts, scans inboxes + calendar, sends replies, sweeps read items -- real-time state that APIs can't provide | Playwright + Edge (your authenticated session) |
| **Intelligence** | Triages, cross-references via WorkIQ, drafts replies, tracks commitments, mines knowledge, discovers projects | GitHub Copilot SDK + WorkIQ MCP + Dataverse MCP + 15 custom tools |
| **Delivery** | 5-tab TUI (Today/Inbox/Projects/Jobs/Chat), streaming chat, Windows toasts, inter-agent collaboration | Textual + winotify + OneDrive for Business |

Everything runs as a single Python daemon with three concurrent tasks: **scheduler** (cron-like patterns + OneDrive job sync), **worker** (one SDK session at a time), and **TUI backend** (status, chat, toasts). All output to `$PULSE_HOME` on OneDrive -- no cloud backend, no external services beyond Microsoft.

---

## Modes

| Mode | Command | What It Does |
|------|---------|-------------|
| Transcript Collection | `--mode transcripts` | Scrapes Teams Calendar for recorded meetings, compresses 20K-char transcripts to 2K structured notes |
| Digest | `--mode digest` | Scans transcripts, emails, Teams, docs, RSS -- generates filtered digest with 1-tap action buttons |
| Triage | `--mode monitor` | 30-min inbox triage: scans Teams + Outlook, drafts replies, produces actionable JSON |
| Research | `--mode research` | Autonomous deep research with full local + M365 access (60 min timeout) |
| Intel | `--mode intel` | RSS feeds filtered for relevance, generates concise competitive intelligence brief |
| Chat | TUI chat tab | Natural language queries with streaming replies, local search + WorkIQ |
| Knowledge Mining | `--mode knowledge` | Overnight pipeline: archive emails/Teams, discover projects, enrich per-engagement memory |

Run any mode standalone: `python src/pulse.py --mode digest --once`

### Project Lifecycle

Projects are the core unit of context in Pulse. They track stakeholders, commitments, deadlines, and risk levels per customer engagement.

**Discovery** — a project is only created when ALL three conditions are met:
1. **3+ mentions** of the customer/initiative across your data
2. **2+ different source types** (e.g., transcript + email — not two emails from the same thread)
3. **At least one actionable element** — a commitment, deliverable, meeting series, or explicit ask directed at you

One-off meetings, CC'd emails, and passing mentions do NOT create projects. CRM deals link to existing projects but don't auto-create new ones — deal team membership alone isn't enough signal.

**Curation** — projects are kept concise: max 6 stakeholders, 10 timeline entries, 6 artifacts, 4 tags. The knowledge miner prunes on each run rather than appending forever.

**Auto-archive** — observer-involvement projects with no activity in 14+ days are automatically archived. If you're not actively working it and nothing is happening, it's not a project.

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

## Multi-Agent Team Collaboration

Multiple team members run their own Pulse Agent daemons. Agents communicate autonomously via OneDrive -- no servers, no APIs, no infrastructure. Just file-based async messaging with ~60 second latency.

```yaml
# In standing-instructions.yaml
team:
  - name: "Esther Barthel"
    alias: "esther"
    agent_path: "C:/Users/USERNAME/OneDrive - Microsoft/Esther Barthel's files - esther"
  - name: "Fatos Ismali"
    alias: "fatos"
    agent_path: "C:/Users/USERNAME/OneDrive - Microsoft/Fatos Ismali's files - fatos"
```

> **OneDrive sharing note:** When you accept a teammate's shared `jobs` folder, OneDrive maps it to its root (e.g., `OneDrive - Microsoft\{Name}'s files - {alias}\`), not under `Documents\Pulse-Team\`. The `agent_path` override is **required** for every shared teammate so Pulse can find their synced folder.
>
> **Auto-detect on install/upgrade:** You don't have to hand-write these entries. SETUP.md Step 9.5 instructs your install/upgrade agent (Copilot CLI, Claude Code, etc.) to scan your OneDrive root for `{Name}'s files - {alias}/jobs/` shortcuts and offer to add them to your `team:` list. Re-run whenever a new teammate shares their folder with you.

**From chat:** *"Ask Esther what context she has on the Vodafone deal"* -- Pulse writes a task YAML to Esther's OneDrive folder. Her agent picks it up, searches her transcripts and project memory, and sends the answer back. You get a toast notification with the result.

**Supported task types:** questions (instant chat query), research (deep 60-min investigation), intel (competitive brief), review (document/proposal feedback).

**Graceful degradation:** If a teammate's laptop is off, the task YAML sits in their OneDrive folder until they come back online. No timeouts, no connection errors.

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

854 tests covering real browser selectors, prompt-to-code contracts, TTL/staleness, streaming IPC, and full reply round-trips.

```bash
python -m pytest tests/ -q          # Run all
python -m pytest tests/ -x --tb=short  # Stop on first failure
python -m pytest tests/ -k "reply"  # Filter by name
```

| Suite | Tests | What it validates |
|-------|-------|-------------------|
| Today view | 59 | Commitment filtering, done visibility, briefing loaders |
| Browser selectors | 57 | Real JavaScript against real DOM in headless Chromium |
| Tools | 62 | All 15 tool handlers + path traversal security + inter-agent |
| Runner | 49 | Trigger variables, carry-forward, pre-process, project loading |
| Transcript collection | 46 | Polling, tri-state extraction, slug pruning, date parsing |
| Mark-as-read / Sweep | 45 | Teams + Outlook mark-read + inbox sweep orchestration |
| TTL & IPC | 30 | Carry-forward staleness, dismiss expiry, streaming chat |
| Reply flow | 43 | Full TUI -> YAML -> worker round-trip |
| Hooks | 40 | Audit trail, guardrails, error recovery, session metrics |
| Scheduler | 39 | Config-driven patterns, office hours, CRUD operations |
| Projects pane | 35 | TUI project display, sorting, filtering, status changes |
| Jobs tab | 35 | Job history, activity logs, completion notifications |
| Hardening | 32 | Atomic writes, auth detection, crash recovery |
| Dedup defense | 25 | 3-layer dedup + CKEditor draft contamination |
| Onboarding | 25 | First-run detection, chat flow, config save |
| Contracts | 15 | Prompt schema matches code that parses LLM output |

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

- [3-minute demo video](https://youtu.be/E-IltXvRNkc)
- [AGENTS.md](AGENTS.md) -- agent behavior instructions
- [docs/RAI.md](docs/RAI.md) -- responsible AI notes
- [docs/SDK-FEEDBACK.md](docs/SDK-FEEDBACK.md) -- GitHub Copilot SDK product feedback
- [docs/SUMMARY.md](docs/SUMMARY.md) -- 150-word project summary
