# Pulse Agent — Setup Guide

You are an AI assistant helping a user set up Pulse Agent on their Windows 11 machine. This guide orchestrates the process — explain what's happening at each step, handle errors, and guide the user through interactive parts.

**Philosophy**: The automated installer (`setup.ps1`) handles everything it can. This guide handles what the script can't: Git auth, browser auth, and the onboarding conversation. Your job is to make the whole experience feel like one continuous flow.

---

## Before You Start

Explain to the user what Pulse Agent is and what setup will do:

> "Pulse Agent is a local-first information processing engine that runs on your machine. It reads your meeting transcripts, scans your Teams and Outlook inbox, and delivers a daily digest of what needs your attention — all without you having to ask.
>
> Setup takes about 10 minutes. Here's what will happen:
> 1. **Install tools** — Python, Node.js, GitHub CLI (automated)
> 2. **Authenticate** — GitHub CLI + Microsoft Teams (you'll sign in twice)
> 3. **Configure** — Pulse will ask you a few questions about your role and preferences
> 4. **Verify** — A health check confirms everything works
>
> Ready?"

Wait for the user to confirm before proceeding.

---

## Phase 1: Clone the Repository

Check if Git is installed. If not, install it automatically:

```powershell
git --version
# If not found:
winget install Git.Git --accept-source-agreements --accept-package-agreements
```

After install, the user needs to **close and reopen their terminal** for Git to be on PATH. Tell them this — it's the one thing you can't do for them.

Clone the repo (skip if it already exists):

```powershell
mkdir -Force "$env:USERPROFILE\dev" | Out-Null
cd "$env:USERPROFILE\dev"
git clone https://github.com/aiappsgbb/pulse-agent.git
cd pulse-agent
```

**All remaining steps run from the repo root** (`pulse-agent/`). Run everything yourself — don't paste commands for the user to copy.

---

## Phase 2: GitHub CLI Authentication

Do this BEFORE running the installer — the installer needs an authenticated `gh` to install the Copilot CLI extension. Without this, the installer will warn and the user has to re-run it.

Check if GitHub CLI is installed and authenticated:

```powershell
gh auth status
```

If `gh` is not found, install it first:

```powershell
winget install GitHub.cli --accept-source-agreements --accept-package-agreements
```

If not authenticated, start login:

```powershell
gh auth login
```

**Tell the user**: "A browser window will open — sign in with your GitHub account that has Copilot access. For Microsoft employees, this is typically your @microsoft-linked GitHub account. I'll wait."

The `gh auth login` prompts will appear in the terminal. Select:
1. **Account type**: GitHub.com
2. **Preferred protocol**: HTTPS
3. **Authenticate**: Login with a web browser

Verify auth succeeded:

```powershell
gh auth status
```

---

## Phase 3: Run the Automated Installer

Now run the installer — it handles Python, Node.js, WorkIQ, Copilot CLI extension, virtual environment, Playwright, data directories, config template, and Desktop shortcut:

```powershell
powershell -ExecutionPolicy Bypass -File setup.ps1
```

Check the output. The script reports OK/WARN/FAIL for each step. Common outcomes:

| Output | Meaning | Action needed? |
|--------|---------|----------------|
| All green `OK` | Everything installed | No |
| Yellow `WARN: WorkIQ installed but not on PATH` | PATH needs refresh | Close and reopen terminal |
| Red `FAIL: Python install failed` | Rare — install manually from python.org | Yes |
| `npm install -g` permission error | npm global install needs admin | Run terminal as Administrator and re-run `npm install -g @microsoft/workiq` |

If setup.ps1 reports any FAIL, troubleshoot that specific step before continuing.

**Important**: The installer detects OneDrive for Business via the `OneDriveCommercial` environment variable (set automatically by the OneDrive sync client). If this isn't set, OneDrive for Business isn't syncing — the user must open OneDrive settings and sign in with their work account first, then restart the terminal.

---

## Phase 4: Browser Authentication

**This is the most important step that people get wrong.** Pulse uses a dedicated Edge browser profile (separate from the user's normal Edge). Signing into Teams in the user's regular browser does NOT work — Pulse won't see that auth.

**Tell the user**: "I'm going to run a health check that opens a special Pulse browser window. Sign into Microsoft Teams with your work account when it opens, then close the window. This is a one-time step."

Activate the venv and run the health check — it detects missing auth and opens the browser automatically:

```powershell
.venv\Scripts\activate
python src/pulse.py --health-check
```

The health check will:
1. Validate all installed components
2. Detect missing Teams auth in Pulse's dedicated browser profile
3. Ask: "Open browser to sign in now? [Y/n]" — accept
4. Open a visible Edge window → user signs into Teams → closes window
5. Verify auth succeeded

If `[FAIL] Playwright Edge` appears, fix it first:

```powershell
python -m playwright install msedge
```

Then re-run `--health-check`.

---

## Phase 5: CRM Plugin (Optional)

Pulse can integrate with CRM/pipeline data (deals, accounts, milestones) via a Copilot CLI plugin.

**Microsoft internal staff**: Install the MSX-MCP plugin for Dataverse access:

```powershell
copilot plugin install mcaps-microsoft/MSX-MCP
```

This requires access to the `mcaps-microsoft` GitHub organization (Microsoft employees only). The `copilot` command is a standalone binary installed by `gh copilot` — if it's not found, run `gh copilot` once to trigger the download, then retry.

After install, authenticate with Azure CLI and verify:

```powershell
az login
copilot plugin list
```

**External users**: Skip this step — Pulse works fine without CRM integration. If your organization has a compatible Copilot CLI CRM plugin, install it per your internal docs.

Pulse auto-detects installed CRM plugins at startup — no config changes needed.

---

## Phase 6: Verify Everything

Run the health check — do NOT ask the user to run it, run it yourself:

```powershell
python src/pulse.py --health-check
```

All checks should pass. The key ones:

| Check | Must pass? | Notes |
|-------|-----------|-------|
| Python version | Yes | 3.12+ required |
| Playwright Edge | Yes | Browser automation |
| GitHub CLI auth | Yes | Agent runtime |
| Copilot CLI extension | Yes | Agent runtime |
| PULSE_HOME | Yes | Data storage |
| Browser: Teams auth | Recommended | Transcript + inbox scanning |
| WorkIQ MCP server | Optional | M365 data queries |
| CRM plugin | Optional | Pipeline/deal queries |
| Config: user identity | No (next step) | Onboarding will set this |

If any required check fails, fix it before moving on. Run the test suite for extra confidence:

```powershell
python -m pytest tests/ -q --tb=line
```

---

## Phase 7: First Run + Onboarding

**Prerequisite**: GitHub CLI auth and Copilot CLI extension must be working (Phase 6 health check passed). Onboarding uses the Copilot SDK — if auth is broken, the chat conversation won't work.

Launch Pulse with the setup flag — this starts the TUI and the onboarding conversation automatically:

```powershell
python src/pulse.py --setup
```

The Chat tab activates and walks the user through configuration — one question at a time, defaults offered. The user just answers:

1. **Identity** — name, email, role, organization
2. **Focus** — what they work on day-to-day
3. **What matters vs. noise** — what should surface in digests
4. **Schedule** — digest time, triage frequency, office hours
5. **Team** (optional) — colleagues also running Pulse
6. **Intelligence** (optional) — topics and competitors to watch

After all answers, the agent saves config automatically. The user can re-run `--setup` anytime.

**Tell the user**: "You're all set. From now on, double-click 'Start Pulse' on your Desktop. Press `?` for keyboard shortcuts."

---

## Setup Complete

Summarize what was set up:

| Component | What it does |
|-----------|-------------|
| Python + venv | Pulse runtime |
| GitHub CLI + Copilot | Agent brain (LLM via GitHub Copilot SDK) |
| Playwright + Edge | Browser automation (transcripts, inbox, sending) |
| WorkIQ | Microsoft 365 data access (calendar, email, people) |
| CRM plugin (optional) | Pipeline, deals, accounts, milestones |
| PULSE_HOME (OneDrive) | All your data — syncs automatically |
| Desktop shortcut | Double-click to start |
| Standing instructions | Your preferences and schedule |

Your data lives entirely on OneDrive. No cloud backend, no third-party services beyond GitHub Copilot and Microsoft 365.

---

## Upgrading

### AI-Assisted (Recommended)

Open a terminal in the repo folder and tell your AI assistant:

> "Pull the latest Pulse Agent code, update dependencies, and verify everything works. Then run the health check."

The assistant will handle `git pull`, `pip install`, and `--health-check` automatically.

### Manual

```powershell
cd path\to\pulse-agent
git pull origin main
.venv\Scripts\activate
python -m pip install -r requirements.txt
python src/pulse.py --health-check
```

Data in PULSE_HOME is untouched — only code updates.

### What if something breaks?

| Problem | Fix |
|---------|-----|
| Merge conflicts | `git stash` then `git pull` |
| New deps fail | Delete `.venv/`, recreate: `python -m venv .venv`, reinstall |
| Tests fail after upgrade | Re-run `pip install -r requirements.txt` |
| Desktop shortcut broken | Re-run `setup.ps1` |
| Browser auth expired | `python src/pulse.py --health-check` (will offer re-login) |
| CRM tools not showing | `copilot plugin install mcaps-microsoft/MSX-MCP` (MS internal) + restart daemon |

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `winget` not found | Install "App Installer" from Microsoft Store |
| `git` not found | `winget install Git.Git` then reopen terminal |
| Python not on PATH | Close and reopen terminal |
| `npm install -g` permission error | Run terminal as Administrator |
| `gh auth login` fails | Ensure GitHub account has Copilot access |
| `copilot` command not found | Run `gh copilot` once to download the Copilot CLI binary |
| `playwright install msedge` fails | Update Edge: `edge://settings/help` |
| Tests fail with import errors | Activate venv: `.venv\Scripts\activate` |
| `OneDriveCommercial` not set | Open OneDrive settings, sign in with work account, restart terminal |
| Transcript collection finds nothing | Re-run `--health-check` to verify browser auth |
| "Browser launch failed" errors | Kill orphan Edge: `taskkill /F /IM msedge.exe` then retry |
| Browser signed in but Pulse can't see auth | You signed into regular Edge, not Pulse's profile. Run `--health-check` to open the correct browser |
