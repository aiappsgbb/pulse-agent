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

Check if Git is installed:

```powershell
git --version
```

If Git is not found, install it:

```powershell
winget install Git.Git --accept-source-agreements --accept-package-agreements
```

After install, the user must **close and reopen their terminal** for Git to be on PATH.

Clone the repo (skip if it already exists):

```powershell
mkdir -Force "$env:USERPROFILE\dev" | Out-Null
cd "$env:USERPROFILE\dev"
git clone https://github.com/aiappsgbb/pulse-agent.git
cd pulse-agent
```

**All remaining steps run from the repo root** (`pulse-agent/`).

---

## Phase 2: Run the Automated Installer

The installer handles Python, Node.js, GitHub CLI, WorkIQ, virtual environment, Playwright, data directories, config template, and Desktop shortcut — all in one script.

```powershell
powershell -ExecutionPolicy Bypass -File setup.ps1
```

Watch the output. The script reports OK/WARN/FAIL for each step. Common outcomes:

| Output | Meaning | Action needed? |
|--------|---------|----------------|
| All green `OK` | Everything installed | No |
| Yellow `WARN: GitHub CLI not authenticated` | Expected — we'll handle it next | No |
| Yellow `WARN: WorkIQ installed but not on PATH` | PATH needs refresh | Close and reopen terminal |
| Red `FAIL: Python install failed` | Rare — install manually from python.org | Yes |

If setup.ps1 reports any FAIL, troubleshoot that specific step before continuing.

---

## Phase 3: GitHub CLI Authentication

This is interactive — the user needs to complete a browser sign-in flow.

Check current auth status:

```powershell
gh auth status
```

If not authenticated:

**ASK THE USER**: "GitHub CLI needs to authenticate with your GitHub account. This is needed for the Copilot agent runtime. I'll start the login flow — a browser window will open for you to sign in."

```powershell
gh auth login
```

Walk the user through the prompts:
1. **Account type**: GitHub.com
2. **Preferred protocol**: HTTPS
3. **Authenticate**: Login with a web browser
4. Copy the one-time code shown in the terminal, paste it in the browser, and authorize.

After auth succeeds, install the Copilot CLI extension:

```powershell
gh extension install github/gh-copilot
```

Verify:

```powershell
gh auth status
gh copilot --version 2>$null || echo "Copilot CLI extension not found"
```

---

## Phase 4: Browser Authentication

**This is the most important step that people get wrong.** Pulse uses its own dedicated browser profile (separate from the user's normal Edge). Signing into Teams in the user's regular Edge browser does NOT work — the auth must happen in Pulse's profile.

Explain to the user:

> "Pulse needs to read your Teams inbox and meeting transcripts using browser automation. It uses a dedicated Edge profile that's separate from your normal browser. I need to open that profile so you can sign into Microsoft Teams in it. This is a one-time step."

Activate the virtual environment first (if not already active):

```powershell
.venv\Scripts\activate
```

Then run the health check, which will detect the missing auth and offer to open the browser:

```powershell
python src/pulse.py --health-check
```

The health check will:
1. Validate all installed components
2. Detect that Teams auth is missing in the daemon profile
3. Ask: "Open browser to sign in now? [Y/n]"
4. Open a visible Edge window using Pulse's dedicated profile
5. The user signs into `teams.microsoft.com` with their work account
6. The user closes the browser window when done
7. The health check verifies auth succeeded

**If the user prefers to do this manually later**, that's fine — Pulse will work for everything except transcript collection and inbox scanning until they complete this step.

**IMPORTANT**: If the health check shows `[FAIL] Playwright Edge`, the user needs to run:

```powershell
python -m playwright install msedge
```

Then re-run `--health-check`.

---

## Phase 5: Verify Everything

If you didn't already run the health check in Phase 4, run it now:

```powershell
python src/pulse.py --health-check
```

All checks should pass. The key ones to look for:

| Check | Must pass? | Notes |
|-------|-----------|-------|
| Python version | Yes | 3.12+ required |
| Playwright Edge | Yes | Browser automation |
| GitHub CLI auth | Yes | Agent runtime |
| Copilot CLI extension | Yes | Agent runtime |
| PULSE_HOME | Yes | Data storage |
| Browser: Teams auth | Recommended | Transcript + inbox scanning |
| WorkIQ MCP server | Optional | M365 data queries |
| Config: user identity | No (next step) | Onboarding will set this |

Optionally run the test suite for extra confidence:

```powershell
python -m pytest tests/ -q --tb=line
```

---

## Phase 6: First Run + Onboarding

Launch Pulse with the setup flag to force the onboarding conversation:

```powershell
python src/pulse.py --setup
```

This starts the full Pulse TUI (terminal dashboard). The Chat tab will automatically activate and walk the user through:

1. **Identity** — name, email, role, organization
2. **Focus** — what they work on day-to-day
3. **What matters vs. noise** — what should surface in digests
4. **Schedule** — digest time, triage frequency, office hours
5. **Team** (optional) — colleagues also running Pulse
6. **Intelligence** (optional) — topics and competitors to watch

The agent asks one question at a time. Defaults are offered in brackets — the user can accept them or customize.

After all questions are answered, the agent saves the config and confirms. The user can re-run `--setup` anytime to change their preferences.

**Tell the user**: "From now on, just double-click 'Start Pulse' on your Desktop. Pulse runs automatically — morning digest at 7 AM, triage every 30 minutes, intel brief at 9 AM. Press `?` in the TUI for keyboard shortcuts."

---

## Setup Complete

Summarize what was set up:

| Component | What it does |
|-----------|-------------|
| Python + venv | Pulse runtime |
| GitHub CLI + Copilot | Agent brain (LLM via GitHub Copilot SDK) |
| Playwright + Edge | Browser automation (transcripts, inbox, sending) |
| WorkIQ | Microsoft 365 data access (calendar, email, people) |
| PULSE_HOME (OneDrive) | All your data — syncs automatically |
| Desktop shortcut | Double-click to start |
| Standing instructions | Your preferences and schedule |

Your data lives entirely on OneDrive. No cloud backend, no third-party services beyond GitHub Copilot and Microsoft 365.

---

## Upgrading

### AI-Assisted (Recommended)

Open a terminal in the repo folder and tell your AI assistant:

> "Pull the latest Pulse Agent code, update dependencies, and verify everything works."

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

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `winget` not found | Install "App Installer" from Microsoft Store |
| `git` not found | `winget install Git.Git` then reopen terminal |
| Python not on PATH | Close and reopen terminal |
| `npm install -g` permission error | Run terminal as Administrator |
| `gh auth login` fails | Ensure GitHub account has Copilot access |
| `playwright install msedge` fails | Update Edge: `edge://settings/help` |
| Tests fail with import errors | Activate venv: `.venv\Scripts\activate` |
| `OneDriveCommercial` not set | Open OneDrive settings, sign in with work account |
| Transcript collection finds nothing | Re-run `--health-check` to verify browser auth |
| "Browser launch failed" errors | Kill orphan Edge: `taskkill /F /IM msedge.exe` then retry |
