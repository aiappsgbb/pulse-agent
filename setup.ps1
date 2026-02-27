# ============================================================================
# Pulse Agent — One-command setup for Windows
# ============================================================================
# Usage:  powershell -ExecutionPolicy Bypass -File setup.ps1
#
# What it does:
#   1. Checks prerequisites (Python 3.12+, GitHub Copilot CLI, OneDrive)
#   2. Creates a Python virtual environment
#   3. Installs pip dependencies
#   4. Installs Playwright's Edge browser
#   5. Seeds the PULSE_HOME + Pulse-Team directory structure
#   6. Copies standing-instructions template to PULSE_HOME (if missing)
#   7. Prints next steps
#
# Zero-config paths: Pulse auto-detects OneDrive via the OneDriveCommercial
# env var. Every teammate's data lives at the same relative path — no manual
# PULSE_HOME or agent_path config needed.
# ============================================================================

$ErrorActionPreference = "Stop"

function Write-Step { param([string]$msg) Write-Host "`n>> $msg" -ForegroundColor Cyan }
function Write-Ok   { param([string]$msg) Write-Host "   OK: $msg" -ForegroundColor Green }
function Write-Warn { param([string]$msg) Write-Host "   WARN: $msg" -ForegroundColor Yellow }
function Write-Fail { param([string]$msg) Write-Host "   FAIL: $msg" -ForegroundColor Red }

$root = $PSScriptRoot
Push-Location $root

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  Pulse Agent Setup" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan

# ── 1. Check Python ──────────────────────────────────────────────────────────
Write-Step "Checking Python..."
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) {
    Write-Fail "Python not found on PATH."
    Write-Host "   Install Python 3.12+ from https://python.org and ensure it's on PATH."
    Pop-Location; exit 1
}
$pyVer = & python --version 2>&1
if ($pyVer -match "(\d+)\.(\d+)") {
    $major = [int]$Matches[1]; $minor = [int]$Matches[2]
    if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 12)) {
        Write-Fail "Python 3.12+ required (found $pyVer)"
        Pop-Location; exit 1
    }
    Write-Ok "$pyVer"
} else {
    Write-Warn "Could not parse Python version: $pyVer"
}

# ── 2. Check GitHub Copilot CLI ──────────────────────────────────────────────
Write-Step "Checking GitHub Copilot CLI..."
$ghCopilot = Get-Command github-copilot -ErrorAction SilentlyContinue
$copilot = Get-Command copilot -ErrorAction SilentlyContinue
if (-not $ghCopilot -and -not $copilot) {
    Write-Warn "Copilot CLI not found on PATH."
    Write-Host "   Install it:  gh extension install github/gh-copilot"
    Write-Host "   Continuing anyway — you'll need it before running the daemon."
} else {
    Write-Ok "Found"
}

# ── 3. Detect OneDrive for Business ─────────────────────────────────────────
Write-Step "Detecting OneDrive for Business..."
$oneDriveBiz = $env:OneDriveCommercial
if ($oneDriveBiz -and (Test-Path $oneDriveBiz)) {
    Write-Ok "Found: $oneDriveBiz"
    $pulseHome = Join-Path $oneDriveBiz "Documents\Pulse"
    $pulseTeam = Join-Path $oneDriveBiz "Documents\Pulse-Team"
} else {
    Write-Warn "OneDriveCommercial env var not set — is OneDrive for Business syncing?"
    Write-Host "   Falling back to default path. Set PULSE_HOME in .env if needed."
    $pulseHome = "$env:USERPROFILE\OneDrive - Microsoft\Documents\Pulse"
    $pulseTeam = "$env:USERPROFILE\OneDrive - Microsoft\Documents\Pulse-Team"
}

# ── 4. Create virtual environment ────────────────────────────────────────────
Write-Step "Setting up virtual environment..."
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
    Write-Warn "Could not activate venv — continuing with system Python"
}

# ── 5. Install pip dependencies ──────────────────────────────────────────────
Write-Step "Installing Python dependencies..."
& python -m pip install --upgrade pip --quiet 2>$null
& python -m pip install -r requirements.txt --quiet
if ($LASTEXITCODE -ne 0) {
    Write-Fail "pip install failed — check errors above"
    Pop-Location; exit 1
}
Write-Ok "All dependencies installed"

# ── 6. Install Playwright Edge browser ───────────────────────────────────────
Write-Step "Installing Playwright Edge browser..."
& python -m playwright install msedge 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Warn "Playwright browser install had issues — transcript collection may not work"
} else {
    Write-Ok "msedge installed"
}

# ── 7. Seed PULSE_HOME directory structure ───────────────────────────────────
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

# ── 8. Seed standing-instructions.yaml ───────────────────────────────────────
Write-Step "Checking standing-instructions..."
$siDest = Join-Path $pulseHome "standing-instructions.yaml"
$siTemplate = Join-Path $root "config\standing-instructions.template.yaml"
$siDefault = Join-Path $root "config\standing-instructions.yaml"

# Prefer template if it exists, otherwise fall back to default
$siSource = if (Test-Path $siTemplate) { $siTemplate } else { $siDefault }

if (-not (Test-Path $siDest)) {
    if (Test-Path $siSource) {
        Copy-Item $siSource $siDest
        Write-Ok "Copied template to: $siDest"
        Write-Host "   IMPORTANT: Edit this file to set your name, role, and team aliases."
    } else {
        Write-Warn "No template found — create standing-instructions.yaml manually"
    }
} else {
    Write-Ok "standing-instructions.yaml already exists in PULSE_HOME"
}

# ── 9. Create .env (only if needed for overrides) ───────────────────────────
Write-Step "Checking .env file..."
if (-not (Test-Path ".env")) {
    # Only create if OneDrive wasn't auto-detected (otherwise no .env needed)
    if (-not $oneDriveBiz) {
        Copy-Item ".env.example" ".env"
        Write-Ok "Created .env — edit PULSE_HOME since OneDrive wasn't auto-detected"
    } else {
        Write-Ok "No .env needed — OneDrive auto-detected. Copy .env.example if you need overrides."
    }
} else {
    Write-Ok ".env already exists"
}

# ── Done ─────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "============================================" -ForegroundColor Green
Write-Host "  Setup complete!" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Green
Write-Host ""
Write-Host "Paths (auto-detected from OneDrive):" -ForegroundColor White
Write-Host "  PULSE_HOME:  $pulseHome" -ForegroundColor DarkGray
Write-Host "  Pulse-Team:  $pulseTeam" -ForegroundColor DarkGray
Write-Host ""
Write-Host "Next steps:" -ForegroundColor White
Write-Host "  1. Activate the venv:     .venv\Scripts\activate" -ForegroundColor White
Write-Host "  2. Log into Teams in Edge once (for transcript/inbox scans)" -ForegroundColor White
Write-Host "  3. Start Pulse:           python src/pulse.py" -ForegroundColor White
Write-Host "     - Daemon + TUI launch together in one command" -ForegroundColor DarkGray
Write-Host "     - First run? The Chat tab will guide you through setup" -ForegroundColor DarkGray
Write-Host ""

Pop-Location
