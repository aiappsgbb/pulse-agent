# Pulse Agent — Setup

You are an AI agent installing Pulse Agent on a Windows 11 machine. Execute these steps in order. Run commands yourself — do not ask the user to copy-paste them. Only pause for steps marked **USER ACTION REQUIRED**.

**Shell note**: Commands below use PowerShell syntax. If your terminal is bash (Git Bash, WSL), adapt: `$env:VAR` becomes `$VAR`, backslash paths become forward-slash, `.venv\Scripts\Activate.ps1` becomes `source .venv/Scripts/activate`. The tool commands themselves (winget, python, pip, npm, gh) are identical across shells.

---

## What You're Installing

Pulse Agent is a local-first autonomous agent that processes meeting transcripts, Teams messages, Outlook emails, and external news — then delivers a structured digest of what needs attention. It runs as a background daemon with a terminal dashboard (TUI).

**Components**: Python runtime, GitHub Copilot SDK (agent brain), Playwright + Edge (browser automation), WorkIQ (Microsoft 365 data access), OneDrive (data storage).

**Time**: ~10 minutes. Three steps require the user to interact (GitHub login, Teams login, personalization questions).

---

## Step 0: Repository

Check if you're already inside the repo: look for `src/`, `config/`, `requirements.txt` in the current directory.

**Already in the repo?** Skip to Step 1.

**In a parent folder (e.g., `C:\Dev`)?** Clone and cd into it:
```
git clone https://github.com/aiappsgbb/pulse-agent.git
cd pulse-agent
```

**In a `pulse-agent` subfolder exists but you're one level up?** Just cd into it:
```
cd pulse-agent
```

If `git` is not found:
```
winget install Git.Git --accept-source-agreements --accept-package-agreements
```
After installing Git, the user must **close and reopen their terminal** (or VS Code) for Git to appear on PATH. Tell them this, then retry the clone.

**Verify**: You should see `src/`, `config/`, `requirements.txt`, `SETUP.md` in the current directory.

**Important — reopen VS Code in the project folder**: If the user cloned from a parent folder (e.g. `C:\Dev`), VS Code is still rooted there. Reopen it in the cloned folder so the user has proper file explorer context:
```
code pulse-agent
```
This opens a new VS Code window in the project. The user can close the old window. All remaining steps run from the repo root.

**All remaining steps run from the repo root.**

---

## Step 1: System Dependencies

Install these in order. After ALL winget installs are done, refresh PATH once (Step 1.4).

### 1.1 Python 3.12+

```
python --version
```

If not found, or version is below 3.12:
```
winget install Python.Python.3.12 --accept-source-agreements --accept-package-agreements
```

**Common issue**: winget returns a non-zero exit code even on "already installed" or "no upgrade available" — this is normal, not a failure. Check the output text.

**Common issue**: After install, `python` still not found. The user must close and reopen their terminal for PATH changes to take effect. In PowerShell you can try refreshing PATH in-place:
```powershell
$env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
```

**Verify**: `python --version` prints `Python 3.12.x` or higher.

### 1.2 Node.js LTS

```
node --version
```

If not found:
```
winget install OpenJS.NodeJS.LTS --accept-source-agreements --accept-package-agreements
```

**This is optional** — Pulse works without it, but WorkIQ (Microsoft 365 data access) requires npm. If install fails, warn the user and continue.

**Verify**: `node --version` prints `v20.x` or higher. `npm --version` returns a version.

### 1.3 GitHub CLI

```
gh --version
```

If not found:
```
winget install GitHub.cli --accept-source-agreements --accept-package-agreements
```

**Verify**: `gh --version` prints a version.

### 1.4 Refresh PATH

If you installed anything in 1.1-1.3, refresh PATH so the new tools are found:

**PowerShell**:
```powershell
$env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
```

**Bash**:
```bash
export PATH=$(powershell.exe -NoProfile -Command '[System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")' | tr -d '\r')
```

After refreshing, verify all three: `python --version`, `node --version`, `gh --version`.

If any tool is still not found after PATH refresh, tell the user to close and reopen their terminal, then resume from this point.

---

## Step 2: GitHub Authentication

### **USER ACTION REQUIRED**

GitHub CLI must be authenticated before installing the Copilot CLI extension. Check current auth:

```
gh auth status
```

If the output says "Logged in to github.com" — skip to Step 3.

If not authenticated, tell the user:

> "I need you to sign into GitHub. A browser window will open — sign in with the GitHub account that has Copilot access. For Microsoft employees, this is typically your Microsoft-linked GitHub account."

Then run:
```
gh auth login
```

This is interactive — the terminal will prompt for:
1. **Where do you use GitHub?** → GitHub.com
2. **Preferred protocol** → HTTPS
3. **Authenticate** → Login with a web browser

A device code appears in the terminal and the browser opens. The user enters the code and authorizes. Wait for the terminal to show "Logged in as USERNAME".

**Verify**:
```
gh auth status
```
Must show "Logged in to github.com as USERNAME".

**Common issue**: "Could not authenticate" — the user's GitHub account may not have Copilot access. They need a GitHub Copilot license (Individual, Business, or Enterprise). Microsoft employees get this through their corporate GitHub account.

---

## Step 3: Python Environment

### 3.1 Virtual Environment

Create and activate a venv:

```
python -m venv .venv
```

Activate it:

**PowerShell**: `.venv\Scripts\Activate.ps1`
**cmd**: `.venv\Scripts\activate.bat`
**Bash**: `source .venv/Scripts/activate`

**Common issue — PowerShell execution policy**: If `.venv\Scripts\Activate.ps1` fails with "running scripts is disabled on this system":
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```
Then retry activation.

**Verify**: `python -c "import sys; print(sys.prefix)"` should print a path ending in `.venv`.

### 3.2 Install Python Dependencies

```
python -m pip install --upgrade pip --quiet
python -m pip install -r requirements.txt --quiet
```

**Common issue**: If the install fails with compilation errors, ensure you're on Python 3.12+ (not 3.11 or earlier). All dependencies in requirements.txt are pure Python — no C compilation should be needed.

**Verify**: `python -c "import copilot; import textual; import playwright; print('OK')"` prints `OK`.

### 3.3 Install Playwright Edge Browser

Playwright needs Edge browser binaries for automation:

```
python -m playwright install msedge
```

This downloads Microsoft Edge for Playwright (~150 MB). It does NOT affect the user's installed Edge.

**Common issue**: "ERROR: Failed to download" — corporate proxy may block the download. The user should try from a non-VPN network or download Edge binaries manually per Playwright docs.

**Verify**: Run this Python one-liner — it should launch and close Edge without errors:
```
python -c "from playwright.sync_api import sync_playwright; p = sync_playwright().start(); b = p.chromium.launch(channel='msedge', headless=True); b.close(); p.stop(); print('Edge OK')"
```

---

## Step 4: Agent Runtime

### 4.1 GitHub Copilot CLI Extension

This is the backend that the GitHub Copilot SDK talks to:

```
gh extension install github/gh-copilot
```

**Common issue**: stderr output like "already installed" is normal — not an error.

**Common issue**: If `gh auth status` failed in Step 2, this install silently succeeds but the extension won't work. Always verify Step 2 first.

Now trigger the binary download (the extension is lazy — it downloads the actual `copilot` binary on first use):

```
gh copilot --version
```

**Common issue**: First run may take 30-60 seconds to download. If it hangs longer than 2 minutes, Ctrl+C and retry — it may be a network issue.

**Verify**: One of these should return a version:
```
gh copilot --version
```

If the above works, the Copilot CLI is ready.

### 4.2 WorkIQ MCP Server (Optional)

WorkIQ provides Microsoft 365 data access (calendar, email, Teams messages, people). Skip if Node.js wasn't installed.

```
npm install -g @microsoft/workiq
```

**Common issue — permission error**: `npm install -g` on Windows may fail with EACCES/EPERM if npm's global prefix is in a protected directory. Fix: run the terminal as Administrator, or change npm's global prefix:
```
npm config set prefix "%APPDATA%\npm"
```
Then retry the install.

**Common issue — not on PATH after install**: Close and reopen the terminal, or add npm's global bin to PATH:
```powershell
$env:Path += ";$env:APPDATA\npm"
```

**Verify**: `workiq --version` prints a version number.

---

## Step 5: Data Storage

### 5.1 Detect OneDrive for Business

Pulse stores all data (transcripts, digests, projects) in an OneDrive-synced folder. Check if OneDrive for Business is active:

**PowerShell**:
```powershell
echo $env:OneDriveCommercial
```

**Bash**:
```bash
echo "$OneDriveCommercial"
```

If this prints a path (like `C:\Users\alice\OneDrive - Microsoft`), OneDrive is active. Set PULSE_HOME:

**PowerShell**:
```powershell
$pulseHome = "$env:OneDriveCommercial\Documents\Pulse"
```

**Bash**:
```bash
pulseHome="$OneDriveCommercial/Documents/Pulse"
```

If the variable is empty, tell the user:

> "OneDrive for Business isn't syncing. Open OneDrive settings (system tray icon → gear icon) and add your work account. After OneDrive finishes syncing, close and reopen your terminal so the environment variable gets set. I'll wait."

If the user can't set up OneDrive right now, use a local fallback:

**PowerShell**:
```powershell
$pulseHome = "$env:USERPROFILE\Documents\Pulse"
```

And create a `.env` file in the repo root:
```
PULSE_HOME=C:\Users\USERNAME\Documents\Pulse
```

Replace `USERNAME` with the actual username. This works but data won't sync across devices.

### 5.2 Create Directory Structure

Create all required directories under PULSE_HOME:

**PowerShell**:
```powershell
$dirs = @("transcripts", "documents", "emails", "teams-messages", "digests", "intel", "projects", "pulse-signals", "jobs/pending", "jobs/completed", "logs", "Agent Instructions")
foreach ($d in $dirs) { New-Item -ItemType Directory -Path "$pulseHome\$d" -Force | Out-Null }
```

**Bash**:
```bash
mkdir -p "$pulseHome"/{transcripts,documents,emails,teams-messages,digests,intel,projects,pulse-signals,jobs/pending,jobs/completed,logs,"Agent Instructions"}
```

Also create the Pulse-Team directory (for inter-agent communication):

**PowerShell**:
```powershell
$pulseTeam = (Split-Path $pulseHome) + "\Pulse-Team"
New-Item -ItemType Directory -Path $pulseTeam -Force | Out-Null
```

**Bash**:
```bash
mkdir -p "$(dirname "$pulseHome")/Pulse-Team"
```

**Verify**: The directories exist under `$pulseHome`.

### 5.3 Copy Standing Instructions Template

If no config exists yet, copy the template:

**PowerShell**:
```powershell
$configDest = "$pulseHome\standing-instructions.yaml"
if (-not (Test-Path $configDest)) {
    Copy-Item "config\standing-instructions.template.yaml" $configDest
}
```

**Bash**:
```bash
configDest="$pulseHome/standing-instructions.yaml"
[ ! -f "$configDest" ] && cp config/standing-instructions.template.yaml "$configDest"
```

Do NOT edit this file now — the onboarding step (Step 8) will fill it in via a chat conversation.

---

## Step 6: Browser Authentication

### **USER ACTION REQUIRED**

Pulse uses a **dedicated Edge browser profile** for automation — separate from the user's regular Edge. The user must sign into Microsoft Teams in this profile once. This is the step that people most commonly get wrong.

Tell the user:

> "I'm going to run a health check that will open a special Pulse browser window. It looks like a regular Edge window but it's a separate profile just for Pulse. Sign into Microsoft Teams with your work account, then close the browser window. This is a one-time step — Pulse will remember the login."

Make sure the venv is activated, then run:

```
python src/pulse.py --health-check
```

The health check will:
1. Run through all component checks (Python, imports, CLI tools, etc.)
2. Try to launch Edge with Pulse's profile and navigate to Teams
3. If Teams shows a login page → prompt: "Open browser to sign in now? [Y/n]"
4. Answer `Y` — a visible Edge window opens to teams.microsoft.com
5. **The user signs in with their work Microsoft account**
6. **The user closes the browser window** (or it auto-closes after auth succeeds)
7. Health check re-verifies auth

**Common issue — "Playwright Edge FAIL"**: On Windows 11 Edge is always preinstalled at `C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe`, and Playwright's `channel="msedge"` will use it. If this check fails, the cause is almost never a missing Edge. Check the error message first:
- "User data directory is already in use" / profile lock: close any Edge windows using the Pulse profile and retry.
- Redirected to login / Teams auth error: re-run `python src/pulse.py --health-check` and sign in when the window opens.
- "Executable doesn't exist": only then run `python -m playwright install msedge` (Step 3.3). On managed/corporate devices that step runs Microsoft's Edge MSI and may prompt UAC or be blocked by Intune/Defender ASR — safe to skip if Edge is already on disk.

**Common issue — browser window opens but immediately shows an error**: An orphan Edge process may be locking the profile. Kill it:
```
taskkill /F /IM msedge.exe
```
Then retry the health check.

**Common issue — user signed into regular Edge instead of the Pulse window**: The Pulse browser window has a distinct clean profile (no extensions, no bookmarks). If they signed in to their regular Edge, it doesn't count. Re-run `--health-check` and make sure they sign into the window that Pulse opens.

**Common issue — Edge window asks for MFA/conditional access**: This is normal for corporate accounts. The user completes MFA as usual. The credentials persist in Pulse's profile.

**Verify**: The health check output should show `[OK] Browser: Teams auth` (or similar pass indicator).

---

## Step 7: Verify Installation

Run the full health check one more time to confirm everything:

```
python src/pulse.py --health-check
```

Check the output. Here's what each result means:

| Check | Required? | If it fails |
|-------|-----------|-------------|
| Python version | Yes | Reinstall Python 3.12+ (Step 1.1) |
| Python imports | Yes | `pip install -r requirements.txt` (Step 3.2) |
| GitHub CLI | Yes | `winget install GitHub.cli` (Step 1.3) |
| GitHub CLI auth | Yes | `gh auth login` (Step 2) |
| Copilot CLI extension | Yes | `gh extension install github/gh-copilot` (Step 4.1) |
| PULSE_HOME | Yes | OneDrive detection (Step 5.1) |
| Playwright Edge | Yes | Verify Edge is installed (it ships with Windows 11). Only run `python -m playwright install msedge` if truly missing — see Step 6 troubleshooting. |
| Browser: Teams auth | Recommended | Re-run `--health-check` with browser login (Step 6) |
| WorkIQ MCP server | Optional | `npm install -g @microsoft/workiq` (Step 4.2) |
| Config: user identity | No | Will be set in Step 8 |

**All "Yes" checks must pass before continuing.**

If you want extra confidence, run the test suite:
```
python -m pytest tests/ -q --tb=line
```
800+ tests should pass. A handful may skip (browser-dependent tests skip without a live browser). Zero failures expected.

---

## Step 8: Personalization

### **USER ACTION REQUIRED**

Launch the onboarding flow. This starts the TUI with a chat conversation that asks the user about their role, preferences, and schedule.

**Make sure the venv is activated** (same as Step 6). If in doubt, re-activate:

**PowerShell**: `.venv\Scripts\Activate.ps1`
**Bash**: `source .venv/Scripts/activate`

Then run:
```
python src/pulse.py --setup
```

Tell the user:

> "Pulse is going to ask you a few questions to set up your profile — your name, role, what kind of information matters to you, when you want your daily digest. Just answer naturally in the Chat tab. It takes about 2 minutes."

The chat agent will ask about:
1. **Identity** — name, email, role, organization
2. **Focus** — what they work on, what customers/topics matter
3. **Signal vs noise** — what should surface in digests, what to filter
4. **Schedule** — when to deliver the daily digest, how often to triage
5. **Team** (optional) — colleagues also running Pulse for inter-agent messaging
6. **Intelligence** (optional) — competitors, topics, RSS feeds to monitor

After answering, the agent saves the config automatically to `$PULSE_HOME/standing-instructions.yaml`.

**Common issue — chat doesn't respond**: The Copilot SDK couldn't connect. Check that `gh auth status` shows authenticated and `gh copilot --version` works. If both are fine, it may be a transient network issue — exit (press `q`) and retry `python src/pulse.py --setup`.

**Common issue — onboarding runs again on next launch**: The config didn't save. Check that `$PULSE_HOME/standing-instructions.yaml` exists and the `user.name` field is not "TODO". If the file is missing, re-run `--setup`.

---

## Step 9: Team Folder (Optional)

### **USER ACTION REQUIRED**

Skip this step if the user is running solo (no teammates on Pulse).

Pulse uses OneDrive-shared folders for cross-agent messaging. Your agent drops task YAMLs into a teammate's shared folder; their agent drops answers back into yours. Only small YAML files move — nothing from `$PULSE_HOME` is ever shared.

### 9.1 Read the alias from config

The alias was set during onboarding. Read it out of the saved config:

**PowerShell**:
```powershell
$alias = (Select-String -Path "$pulseHome\standing-instructions.yaml" -Pattern '^\s*alias:\s*"?([^"\r\n]+)"?' | Select-Object -First 1).Matches.Groups[1].Value.Trim()
echo "Your alias is: $alias"
```

**Verify**: `$alias` is a non-empty lowercase slug (e.g., `artur`, `riccardo`). If empty, re-run Step 8 onboarding.

### 9.2 Create your team mailbox

**PowerShell**:
```powershell
$pulseTeam = (Split-Path $pulseHome) + "\Pulse-Team"
$myMailbox = "$pulseTeam\$alias\jobs\pending"
New-Item -ItemType Directory -Path $myMailbox -Force | Out-Null
New-Item -ItemType Directory -Path "$pulseTeam\$alias\jobs\completed" -Force | Out-Null
echo "Created: $pulseTeam\$alias\jobs\"
```

**Verify**: The folder `Pulse-Team\{alias}\jobs\pending\` exists and is empty.

### 9.3 Share your `jobs/` folder with teammates

Tell the user:

> "Open File Explorer and navigate to `OneDrive - Microsoft\Documents\Pulse-Team\{your-alias}\`. Right-click the `jobs` folder → **OneDrive** → **Share**. Add each teammate's work email with **Can edit** permission. Share ONLY the `jobs` folder — not the `{alias}` parent, not `Pulse`. Your private data stays on your machine."
>
> "Ask each teammate to share their `Pulse-Team\{their-alias}\jobs\` folder back with you the same way."

**Why edit permission**: teammates' agents need to *write* request YAMLs into your `jobs/pending/`. View-only breaks the messaging loop.

### 9.4 Accept teammates' shared folders

For each `jobs` folder a teammate shares with you:

1. The user opens the share email or goes to OneDrive web (`onedrive.live.com`) → **Shared** → **Shared with you**.
2. Click the teammate's `jobs` folder → **Add shortcut to My files**.
3. OneDrive syncs the shortcut to the local disk.

**Important — OneDrive maps shared folders to its root, not under Pulse-Team.** When you add a shortcut to a teammate's shared folder, OneDrive places it at:

```
C:\Users\{you}\OneDrive - Microsoft\{Teammate Name}'s files - {their-alias}\jobs\
```

This is **outside** the `Documents\Pulse-Team\` directory. The convention-based path (`Pulse-Team\{alias}\jobs\pending\`) will NOT find it. You **must** add an `agent_path:` override in `standing-instructions.yaml` for every shared teammate (see Step 9.5).

**Common issue — "shortcut not syncing"**: If the shortcut shows a cloud icon for more than a few minutes, right-click → **Always keep on this device** to force sync.

### 9.5 List teammates in standing-instructions.yaml

Open `$pulseHome\standing-instructions.yaml` and add each teammate to the `team:` section. Every shared teammate needs `agent_path:` pointing to the actual OneDrive-synced path:

```yaml
team:
  - name: "Artur Zielinski"
    alias: "artur"
    agent_path: "C:/Users/USERNAME/OneDrive - Microsoft/Artur Zielinski's files - artur"
  - name: "Esther Barthel"
    alias: "esther"
    agent_path: "C:/Users/USERNAME/OneDrive - Microsoft/Esther Barthel's files - esther"
```

Replace `USERNAME` with your Windows username. The `agent_path` must point to the folder whose child is `jobs/pending/`.

> **Why `agent_path` is required:** OneDrive sharing places shortcuts at its root (`OneDrive - Microsoft\{Name}'s files - {alias}\`), not under `Documents\Pulse-Team\{alias}\`. Without `agent_path`, the tool looks in `Pulse-Team\{alias}\` which doesn't contain the synced content. Only your **own** mailbox lives under `Pulse-Team\` — teammates' shared folders always need the override.

The alias MUST match exactly what the teammate set during their own onboarding (case-sensitive). If unsure, ask them to paste the `alias:` line from their own `standing-instructions.yaml`.

### 9.6 Verify the loop

After the user finishes the mutual share with at least one teammate, test it end-to-end. In the TUI's Chat tab, type:

> "ask the team what context they have on [any topic]"

Within ~60 seconds (30s OneDrive sync + 30s teammate's job poll), a toast should fire on the user's machine when the response lands. If nothing arrives after a few minutes:

- Check the teammate's daemon is running (`python src/pulse.py` open on their machine)
- Check the teammate's jobs folder on your disk — if the YAML you sent is still there, their OneDrive hasn't synced yet
- Check `logs\YYYY-MM-DD.jsonl` on the user's machine for `broadcast_to_team` errors

---

## Step 10: Desktop Shortcut

Create a shortcut so the user can launch Pulse with a double-click:

**PowerShell** (run from repo root):
```powershell
$desktop = [System.Environment]::GetFolderPath("Desktop")
$repoRoot = (Get-Location).Path
$bat = @"
@echo off
cd /d "$repoRoot"
call .venv\Scripts\activate.bat
python src\pulse.py
pause
"@
Set-Content -Path "$desktop\Start Pulse.bat" -Value $bat -Encoding ASCII
```

**Bash**:
```bash
desktop="$(powershell.exe -NoProfile -Command '[System.Environment]::GetFolderPath("Desktop")' | tr -d '\r')"
repoRoot="$(pwd)"
cat > "$desktop/Start Pulse.bat" << 'ENDOFBAT'
@echo off
cd /d "REPO_ROOT_PLACEHOLDER"
call .venv\Scripts\activate.bat
python src\pulse.py
pause
ENDOFBAT
sed -i "s|REPO_ROOT_PLACEHOLDER|$(cygpath -w "$repoRoot")|" "$desktop/Start Pulse.bat"
```

**Verify**: A file called `Start Pulse.bat` exists on the user's Desktop.

---

## Done

Tell the user:

> "Pulse Agent is installed and configured. Here's how to use it:
>
> - **Double-click 'Start Pulse'** on your Desktop to launch
> - The dashboard has tabs: **Today** (schedule + commitments), **Inbox** (triage items), **Projects**, **Jobs** (background tasks), and **Chat** (ask questions)
> - Press **?** for keyboard shortcuts
> - Your daily digest runs automatically at the time you configured
> - Triage scans your inbox every 30 minutes during office hours
>
> Your data is in your OneDrive under Documents/Pulse — it syncs automatically."

---

## Upgrading

Open the `pulse-agent` folder in **VS Code** (the same folder you installed into), then paste this into your AI chat:

> Pull the latest Pulse Agent code with `git pull`, update Python dependencies with `pip install -r requirements.txt`, and run `python src/pulse.py --health-check` to verify everything still works. Activate the venv first.

Your data in OneDrive is untouched — only the code updates.

---

## Troubleshooting Reference

### System Dependencies

| Symptom | Cause | Fix |
|---------|-------|-----|
| `winget` not found | Windows App Installer missing | Install "App Installer" from Microsoft Store |
| `python` not found after install | PATH not updated | Close and reopen terminal |
| `node` / `npm` not found after install | PATH not updated | Close and reopen terminal |
| `gh` not found after install | PATH not updated | Close and reopen terminal |
| winget returns error on install | Already installed (normal) | Check output text — "already installed" or "no upgrade" is fine |

### Authentication

| Symptom | Cause | Fix |
|---------|-------|-----|
| `gh auth login` browser doesn't open | Firewall/proxy blocking | Use `gh auth login --with-token` with a personal access token instead |
| "Could not authenticate" during gh login | No Copilot license on this GitHub account | Use a GitHub account with Copilot access |
| `gh copilot --version` hangs forever | Network issue downloading binary | Ctrl+C, check network/VPN, retry |
| Teams login in health check shows wrong account | Multiple Microsoft accounts | Sign out of all accounts in the Pulse Edge window first, then sign in with work account |
| Browser auth succeeds but health check still fails | Orphan Edge process from previous run | `taskkill /F /IM msedge.exe` then retry |

### Python Environment

| Symptom | Cause | Fix |
|---------|-------|-----|
| `pip install` fails with permission errors | Installing to system Python | Make sure venv is activated (Step 3.1) |
| `import copilot` fails | Wrong Python / venv not activated | Activate venv, verify with `python -c "import sys; print(sys.prefix)"` |
| `playwright install msedge` fails or prompts UAC | It runs Microsoft's Edge MSI, not a bundled browser. Blocked by Intune/Defender ASR on managed devices. | Safe to skip if Edge is already installed (it is on Windows 11). Playwright uses the system Edge via `channel="msedge"`. |
| Edge launch test fails | Usually profile lock or Teams auth, not a missing browser | Close orphan Edge processes (`taskkill /F /IM msedge.exe`), then re-run `python src/pulse.py --health-check`. Only install Edge if `where msedge` returns nothing. |

### Runtime

| Symptom | Cause | Fix |
|---------|-------|-----|
| Chat doesn't respond in onboarding | SDK can't connect to Copilot CLI | Verify: `gh auth status` + `gh copilot --version` |
| "Browser launch failed" at runtime | Profile locked by orphan process | `taskkill /F /IM msedge.exe` then restart Pulse |
| `OneDriveCommercial` empty | OneDrive for Business not syncing | Open OneDrive settings, add work account, restart terminal |
| Onboarding keeps repeating | Config not saved / user.name still "TODO" | Check `$PULSE_HOME/standing-instructions.yaml` exists and has real values |
| WorkIQ errors at runtime | EULA not accepted | Run `workiq mcp` once manually — it opens a browser for EULA acceptance |
| npm global tool not found | npm bin not on PATH | Add `%APPDATA%\npm` to PATH, or restart terminal |

### CRM Plugin (Microsoft Internal Only)

| Symptom | Cause | Fix |
|---------|-------|-----|
| `copilot plugin install` fails | Not in mcaps-microsoft GitHub org | Must be Microsoft employee with org access |
| CRM queries fail at runtime | Azure CLI not authenticated | Run `az login` |
| `copilot` command not found | Copilot CLI binary not downloaded | Run `gh copilot` once to trigger download |
