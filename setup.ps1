# ============================================================================
# Pulse Agent - One-command setup for Windows
# ============================================================================
# Usage:  Double-click install.bat (preferred)
#    or:  powershell -ExecutionPolicy Bypass -File setup.ps1
#
# What it does:
#   1. Auto-installs prerequisites via winget (Python, Node.js, GitHub CLI)
#   2. Installs WorkIQ MCP server (npm) and Copilot CLI extension (gh)
#   3. Creates a Python virtual environment + installs dependencies
#   4. Installs Playwright's Edge browser
#   5. Seeds the PULSE_HOME + Pulse-Team directory structure
#   6. Copies standing-instructions template (if missing)
#   7. Creates a "Start Pulse.bat" on your Desktop
#
# Designed for non-technical users. Just double-click install.bat.
# ============================================================================

$ErrorActionPreference = "Stop"

function Write-Step { param([string]$msg) Write-Host "`n>> $msg" -ForegroundColor Cyan }
function Write-Ok   { param([string]$msg) Write-Host "   OK: $msg" -ForegroundColor Green }
function Write-Warn { param([string]$msg) Write-Host "   WARN: $msg" -ForegroundColor Yellow }
function Write-Fail { param([string]$msg) Write-Host "   FAIL: $msg" -ForegroundColor Red }

# Helper: refresh PATH from registry so newly-installed programs are found
function Refresh-Path {
    $machinePath = [System.Environment]::GetEnvironmentVariable("Path", "Machine")
    $userPath    = [System.Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machinePath;$userPath"
}

$root = $PSScriptRoot
Push-Location $root

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  Pulse Agent Setup" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan

# Track whether we installed anything that needs a PATH refresh
$needsPathRefresh = $false

# -- 1. Check winget --------------------------------------------------------
Write-Step "Checking winget (Windows Package Manager)..."
$winget = Get-Command winget -ErrorAction SilentlyContinue
if (-not $winget) {
    Write-Fail "winget not found."
    Write-Host "   winget comes with Windows 11. If missing, install 'App Installer' from the Microsoft Store."
    Write-Host "   https://aka.ms/getwinget"
    Pop-Location; exit 1
}
Write-Ok "winget available"

# -- 2. Install Python (if missing) ----------------------------------------
Write-Step "Checking Python 3.12+..."
$py = Get-Command python -ErrorAction SilentlyContinue
$pyOk = $false

if ($py) {
    $pyVer = & python --version 2>&1
    if ($pyVer -match "(\d+)\.(\d+)") {
        $major = [int]$Matches[1]; $minor = [int]$Matches[2]
        if ($major -ge 3 -and $minor -ge 12) {
            $pyOk = $true
            Write-Ok "$pyVer"
        }
    }
}

if (-not $pyOk) {
    Write-Host "   Installing Python 3.12 via winget..." -ForegroundColor Yellow
    $wingetOut = winget install Python.Python.3.12 --accept-source-agreements --accept-package-agreements --silent 2>&1 | Out-String
    # winget returns non-zero for "already installed" or "no upgrade" -- check output
    if ($LASTEXITCODE -ne 0 -and $wingetOut -notmatch "already installed|No newer package|No available upgrade") {
        Write-Fail "Python install failed. Install manually from https://python.org (check 'Add to PATH')"
        Pop-Location; exit 1
    }
    $needsPathRefresh = $true
    Write-Ok "Python 3.12 installed"
}

# -- 3. Install Node.js (if missing) ---------------------------------------
Write-Step "Checking Node.js..."
$node = Get-Command node -ErrorAction SilentlyContinue

if (-not $node) {
    # Refresh PATH first in case Python install added to PATH
    if ($needsPathRefresh) { Refresh-Path }

    Write-Host "   Installing Node.js LTS via winget..." -ForegroundColor Yellow
    winget install OpenJS.NodeJS.LTS --accept-source-agreements --accept-package-agreements --silent
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "Node.js install failed - WorkIQ won't work, but Pulse can run without it."
    } else {
        $needsPathRefresh = $true
        Write-Ok "Node.js LTS installed"
    }
} else {
    Write-Ok "Node.js $(& node --version 2>&1)"
}

# -- 4. Install GitHub CLI (if missing) ------------------------------------
Write-Step "Checking GitHub CLI..."
$gh = Get-Command gh -ErrorAction SilentlyContinue

if (-not $gh) {
    if ($needsPathRefresh) { Refresh-Path }

    Write-Host "   Installing GitHub CLI via winget..." -ForegroundColor Yellow
    winget install GitHub.cli --accept-source-agreements --accept-package-agreements --silent
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "GitHub CLI install failed - Copilot CLI won't work."
    } else {
        $needsPathRefresh = $true
        Write-Ok "GitHub CLI installed"
    }
} else {
    Write-Ok "GitHub CLI found"
}

# -- Refresh PATH after all winget installs ---------------------------------
if ($needsPathRefresh) {
    Write-Step "Refreshing PATH..."
    Refresh-Path
    Write-Ok "PATH refreshed"
}

# -- 5. Install WorkIQ MCP server (npm) ------------------------------------
Write-Step "Installing WorkIQ MCP server..."
$npm = Get-Command npm -ErrorAction SilentlyContinue
if ($npm) {
    & npm install -g @microsoft/workiq --silent 2>&1 | Out-Null
    $workiq = Get-Command workiq -ErrorAction SilentlyContinue
    if ($workiq) {
        Write-Ok "WorkIQ installed (npm global)"
    } else {
        # npm global bin might not be on PATH yet
        Refresh-Path
        $workiq = Get-Command workiq -ErrorAction SilentlyContinue
        if ($workiq) {
            Write-Ok "WorkIQ installed (npm global)"
        } else {
            Write-Warn "WorkIQ installed but 'workiq' not found on PATH - you may need to restart your terminal"
        }
    }
} else {
    Write-Warn "npm not found - skipping WorkIQ. Install Node.js and re-run setup."
}

# -- 6. Install GitHub Copilot CLI extension --------------------------------
Write-Step "Checking GitHub Copilot CLI..."
$gh = Get-Command gh -ErrorAction SilentlyContinue
if ($gh) {
    # Check if already authenticated
    $authStatus = & gh auth status 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "GitHub CLI not authenticated."
        Write-Host "   After setup, run:  gh auth login" -ForegroundColor Yellow
        Write-Host "   Then re-run install.bat to finish Copilot CLI setup." -ForegroundColor Yellow
    } else {
        Write-Ok "GitHub CLI authenticated"
        # Install Copilot extension (stderr warning if already installed is normal)
        try {
            $null = & gh extension install github/gh-copilot 2>&1
        } catch {
            # gh writes to stderr when extension already exists -- not a real error
        }
        Write-Ok "Copilot CLI extension installed"
    }
} else {
    Write-Warn "GitHub CLI not found - Copilot agent runtime won't work."
    Write-Host "   Re-run install.bat after installing GitHub CLI."
}

# -- 7. Detect OneDrive for Business ---------------------------------------
Write-Step "Detecting OneDrive for Business..."
$oneDriveBiz = $env:OneDriveCommercial
if ($oneDriveBiz -and (Test-Path $oneDriveBiz)) {
    Write-Ok "Found: $oneDriveBiz"
    $pulseHome = Join-Path $oneDriveBiz "Documents\Pulse"
    $pulseTeam = Join-Path $oneDriveBiz "Documents\Pulse-Team"
} else {
    Write-Warn "OneDriveCommercial env var not set - is OneDrive for Business syncing?"
    Write-Host "   Falling back to default path. Set PULSE_HOME in .env if needed."
    $pulseHome = "$env:USERPROFILE\OneDrive - Microsoft\Documents\Pulse"
    $pulseTeam = "$env:USERPROFILE\OneDrive - Microsoft\Documents\Pulse-Team"
}

# -- 8. Create virtual environment -----------------------------------------
Write-Step "Setting up Python virtual environment..."
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    Write-Fail "Python still not found on PATH after install. Please restart your terminal and re-run install.bat."
    Pop-Location; exit 1
}

if (-not (Test-Path ".venv")) {
    & python -m venv .venv
    Write-Ok "Created .venv/"
} else {
    Write-Ok ".venv/ already exists"
}

# Activate
$activateScript = ".venv\Scripts\Activate.ps1"
if (Test-Path $activateScript) {
    & $activateScript
} else {
    Write-Warn "Could not activate venv - continuing with system Python"
}

# -- 9. Install pip dependencies --------------------------------------------
Write-Step "Installing Python dependencies..."
& python -m pip install --upgrade pip --quiet 2>$null
& python -m pip install -r requirements.txt --quiet
if ($LASTEXITCODE -ne 0) {
    Write-Fail "pip install failed - check errors above"
    Pop-Location; exit 1
}
Write-Ok "All dependencies installed"

# -- 10. Install Playwright Edge browser ------------------------------------
Write-Step "Installing Playwright Edge browser..."
& python -m playwright install msedge 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Warn "Playwright browser install had issues - transcript collection may not work"
} else {
    Write-Ok "msedge installed"
}

# -- 11. Seed PULSE_HOME directory structure --------------------------------
Write-Step "Seeding data directories..."

$pulseDirs = @(
    "transcripts", "documents", "emails", "teams-messages",
    "digests", "intel", "projects", "pulse-signals",
    "jobs/pending", "jobs/completed", "logs", "Agent Instructions"
)

foreach ($sub in $pulseDirs) {
    $dir = Join-Path $pulseHome $sub
    if (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
}
Write-Ok "PULSE_HOME: $pulseHome"

# Seed Pulse-Team directory (for inter-agent communication)
if (-not (Test-Path $pulseTeam)) {
    New-Item -ItemType Directory -Path $pulseTeam -Force | Out-Null
}
Write-Ok "Pulse-Team: $pulseTeam"

# -- 12. Seed standing-instructions.yaml ------------------------------------
Write-Step "Checking standing-instructions..."
$siDest = Join-Path $pulseHome "standing-instructions.yaml"
$siTemplate = Join-Path $root "config\standing-instructions.template.yaml"

$siSource = $siTemplate

if (-not (Test-Path $siDest)) {
    if (Test-Path $siSource) {
        Copy-Item $siSource $siDest
        Write-Ok "Copied template to: $siDest"
    } else {
        Write-Warn "No template found - config will be created on first run"
    }
} else {
    Write-Ok "standing-instructions.yaml already exists"
}

# -- 13. Create .env (only if needed for overrides) ------------------------
Write-Step "Checking .env file..."
if (-not (Test-Path ".env")) {
    if (-not $oneDriveBiz) {
        if (Test-Path ".env.example") {
            Copy-Item ".env.example" ".env"
            Write-Ok "Created .env - edit PULSE_HOME since OneDrive wasn't auto-detected"
        }
    } else {
        Write-Ok "No .env needed - OneDrive auto-detected"
    }
} else {
    Write-Ok ".env already exists"
}

# -- 14. Create Desktop shortcut -------------------------------------------
Write-Step "Creating Desktop shortcut..."
$desktopPath = [System.Environment]::GetFolderPath("Desktop")
$shortcutBat = Join-Path $desktopPath "Start Pulse.bat"

$batContent = @"
@echo off
:: ============================================================================
:: Pulse Agent - Double-click to start
:: ============================================================================
cd /d "$root"
call .venv\Scripts\activate.bat
python src\pulse.py
pause
"@

Set-Content -Path $shortcutBat -Value $batContent -Encoding ASCII
Write-Ok "Created: $shortcutBat"

# -- Done ------------------------------------------------------------------
Write-Host ""
Write-Host "============================================" -ForegroundColor Green
Write-Host "  Setup complete!" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Green
Write-Host ""
Write-Host "What was installed:" -ForegroundColor White
Write-Host "  Python 3.12       - language runtime" -ForegroundColor DarkGray
Write-Host "  Node.js           - needed for WorkIQ" -ForegroundColor DarkGray
Write-Host "  GitHub CLI        - needed for Copilot agent" -ForegroundColor DarkGray
Write-Host "  WorkIQ            - Microsoft 365 data access" -ForegroundColor DarkGray
Write-Host "  Playwright Edge   - browser automation" -ForegroundColor DarkGray
Write-Host ""
Write-Host "Paths (auto-detected from OneDrive):" -ForegroundColor White
Write-Host "  PULSE_HOME:  $pulseHome" -ForegroundColor DarkGray
Write-Host "  Pulse-Team:  $pulseTeam" -ForegroundColor DarkGray
Write-Host ""
Write-Host "Next steps:" -ForegroundColor White
Write-Host "  1. Run:  .venv\Scripts\activate && python src\pulse.py --health-check" -ForegroundColor White
Write-Host "     (This opens Pulse's browser profile -- sign into Teams there, not regular Edge)" -ForegroundColor Yellow
Write-Host "  2. Double-click 'Start Pulse' on your Desktop" -ForegroundColor White
Write-Host "  3. The Chat tab will walk you through final setup" -ForegroundColor White
Write-Host ""

# Check for things that still need manual steps
$gh = Get-Command gh -ErrorAction SilentlyContinue
if ($gh) {
    $authStatus = & gh auth status 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  NOTE: GitHub CLI needs authentication." -ForegroundColor Yellow
        Write-Host "  Run this command, then re-run install.bat:" -ForegroundColor Yellow
        Write-Host "    gh auth login" -ForegroundColor White
        Write-Host ""
    }
}

Pop-Location
