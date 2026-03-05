# Pulse Agent - Setup Instructions

You are setting up Pulse Agent on a Windows 11 machine. Follow these instructions step by step. Run each command, check the output, and handle errors before moving on. Ask the user for input when indicated.

## Environment

- **OS**: Windows 11
- **Shell**: PowerShell
- **Required**: OneDrive for Business must be syncing (check that `$env:OneDriveCommercial` env var exists)

---

## Phase 1: Clone the Repository

First, make sure Git is installed:

```powershell
git --version
```

If Git is not found:

```powershell
winget install Git.Git --accept-source-agreements --accept-package-agreements
```

After install, **close and reopen the terminal**, then verify `git --version` works.

Now clone the repo and navigate into it:

```powershell
mkdir -Force "$env:USERPROFILE\dev" | Out-Null
cd "$env:USERPROFILE\dev"
git clone https://github.com/aiappsgbb/pulse-agent.git
cd pulse-agent
```

If the repo already exists at a known path, just `cd` into it instead.

**IMPORTANT**: All remaining commands run from the repo root directory (`pulse-agent/`).

---

## Phase 2: Install Prerequisites

Check and install each tool. Use `winget` (built into Windows 11) for missing software.

### 2.1 Python 3.12+

```powershell
python --version
```

If Python is not found or version is below 3.12:

```powershell
winget install Python.Python.3.12 --accept-source-agreements --accept-package-agreements
```

After install, **close and reopen the terminal** so Python is on PATH, then verify:

```powershell
python --version
```

### 2.2 Node.js

```powershell
node --version
```

If Node.js is not found:

```powershell
winget install OpenJS.NodeJS.LTS --accept-source-agreements --accept-package-agreements
```

After install, **close and reopen the terminal**, then verify:

```powershell
node --version
npm --version
```

### 2.3 GitHub CLI

```powershell
gh --version
```

If `gh` is not found:

```powershell
winget install GitHub.cli --accept-source-agreements --accept-package-agreements
```

After install, **close and reopen the terminal**, then verify:

```powershell
gh --version
```

### 2.4 Authenticate GitHub CLI

```powershell
gh auth status
```

If not authenticated, run:

```powershell
gh auth login
```

**ASK THE USER**: "GitHub CLI needs authentication. Run `gh auth login` in your terminal. It will open a browser for you to sign in with your GitHub account. Let me know when you're done."

After auth, install the Copilot CLI extension:

```powershell
gh extension install github/gh-copilot
```

### 2.5 WorkIQ MCP Server

```powershell
npm install -g @microsoft/workiq
```

Verify:

```powershell
workiq --version
```

If `workiq` is not found after install, the npm global bin directory may not be on PATH. Run:

```powershell
npm config get prefix
```

And add `{prefix}` to the user's PATH if needed.

---

## Phase 3: Python Environment

### 3.1 Create virtual environment

```powershell
python -m venv .venv
```

### 3.2 Activate it

```powershell
.venv\Scripts\activate
```

You should see `(.venv)` in the prompt.

### 3.3 Install dependencies

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### 3.4 Install Playwright Edge browser

```powershell
python -m playwright install msedge
```

---

## Phase 4: Data Directory Setup

Pulse stores all data on OneDrive. Detect the path:

```powershell
echo $env:OneDriveCommercial
```

If this prints a valid path (e.g., `C:\Users\username\OneDrive - Microsoft`), create the Pulse data directories:

```powershell
$pulseHome = Join-Path $env:OneDriveCommercial "Documents\Pulse"
$pulseTeam = Join-Path $env:OneDriveCommercial "Documents\Pulse-Team"

$dirs = @(
    "transcripts", "documents", "emails", "teams-messages",
    "digests", "intel", "projects", "pulse-signals",
    "jobs/pending", "jobs/completed", "logs", "Agent Instructions"
)

foreach ($d in $dirs) {
    New-Item -ItemType Directory -Path (Join-Path $pulseHome $d) -Force | Out-Null
}
New-Item -ItemType Directory -Path $pulseTeam -Force | Out-Null

Write-Host "PULSE_HOME: $pulseHome"
Write-Host "Pulse-Team: $pulseTeam"
```

If `OneDriveCommercial` is empty, **ASK THE USER**: "OneDrive for Business doesn't seem to be syncing. Is your OneDrive path different? I need to know where to store Pulse data."

### 4.1 Copy config template

```powershell
$siDest = Join-Path $pulseHome "standing-instructions.yaml"
if (-not (Test-Path $siDest)) {
    Copy-Item "config\standing-instructions.template.yaml" $siDest
    Write-Host "Config template copied to: $siDest"
}
```

---

## Phase 5: Create Desktop Shortcut

Create a batch file on the Desktop so the user can double-click to start Pulse:

```powershell
$desktop = [System.Environment]::GetFolderPath("Desktop")
$repoRoot = (Get-Location).Path

$content = @"
@echo off
cd /d "$repoRoot"
call .venv\Scripts\activate.bat
python src\pulse.py
pause
"@

Set-Content -Path (Join-Path $desktop "Start Pulse.bat") -Value $content -Encoding ASCII
Write-Host "Created 'Start Pulse.bat' on Desktop"
```

---

## Phase 6: One-time Browser Setup

**ASK THE USER**: "Almost done -- open Microsoft Edge and sign into https://teams.microsoft.com with your work account. This is needed so Pulse can read your meeting transcripts and inbox. Let me know when you're signed in."

---

## Phase 7: Verify Installation

Run the test suite to confirm everything works:

```powershell
python -m pytest tests/ -q --tb=line
```

All tests should pass (690+). If any fail, investigate the errors.

Then start Pulse to verify it launches:

```powershell
python src/pulse.py --once --mode monitor
```

This runs a single triage cycle and exits. If it starts without errors, the install is good.

---

## Phase 8: First Run

Tell the user:

"**Setup is complete!** Here's how to use Pulse Agent:

1. **Double-click 'Start Pulse' on your Desktop** to launch
2. The first time, the **Chat tab** will ask you a few questions (your name, email, what topics matter to you)
3. After that, Pulse runs automatically -- morning digest at 7 AM, triage every 30 minutes, intel brief at 9 AM
4. Press `?` in the TUI for keyboard shortcuts

Your data lives on OneDrive and syncs automatically. No cloud backend, no external services."

---

## Upgrading Existing Installations

If Pulse Agent is already installed and you need to update to the latest version:

### AI-Assisted Upgrade (Recommended)

Open a terminal in the Pulse Agent repo folder, then paste this into GitHub Copilot Chat or any AI assistant with terminal access:

> "Pull the latest Pulse Agent code, update dependencies, and verify everything works. The repo is https://github.com/aiappsgbb/pulse-agent.git"

### Manual Upgrade

From the repo root (`pulse-agent/`):

```powershell
# 1. Pull latest code
git pull origin main

# 2. Activate the virtual environment
.venv\Scripts\activate

# 3. Update dependencies (in case new packages were added)
python -m pip install -r requirements.txt

# 4. Run tests to verify
python -m pytest tests/ -q --tb=line
```

That's it. Your data in `PULSE_HOME` (OneDrive) is untouched — only the code updates.

### What if something breaks?

| Problem | Fix |
|---------|-----|
| Merge conflicts on `git pull` | You shouldn't have local code changes. Run `git stash` then `git pull` |
| New dependencies fail to install | Delete `.venv/` and recreate: `python -m venv .venv` then reinstall |
| Tests fail after upgrade | Check the error — likely a missing dependency. Re-run `pip install -r requirements.txt` |
| Desktop shortcut stops working | Re-run Phase 5 from the setup instructions above |

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `winget` not found | Install "App Installer" from the Microsoft Store |
| `git` not found | `winget install Git.Git` then reopen terminal |
| Python not on PATH after install | Close and reopen terminal, or add manually via System Settings > Environment Variables |
| `npm install -g` permission error | Run terminal as Administrator |
| `gh auth login` fails | Ensure you have a GitHub account with Copilot access |
| `playwright install msedge` fails | Edge might need updating -- check edge://settings/help |
| Tests fail with import errors | Make sure the venv is activated (`.venv\Scripts\activate`) |
| `OneDriveCommercial` is empty | OneDrive for Business isn't syncing. Open OneDrive settings and sign in with your work account |

---

## What Was Installed

| Tool | Purpose | Installed via |
|------|---------|--------------|
| Git | Source control | winget |
| Python 3.12 | Core runtime | winget |
| Node.js LTS | Needed for WorkIQ MCP server | winget |
| GitHub CLI | Needed for Copilot CLI extension | winget |
| WorkIQ | Microsoft 365 data access (emails, calendar, Teams) | npm |
| GitHub Copilot CLI | Agent runtime (SDK server mode) | gh extension |
| Playwright + Edge | Browser automation for transcript/inbox scanning | pip + playwright install |
