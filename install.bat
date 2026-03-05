@echo off
:: ============================================================================
:: Pulse Agent - Double-click Installer
:: ============================================================================
:: Just double-click this file. It handles everything:
::   - Installs Python, Node.js, GitHub CLI (via winget)
::   - Installs WorkIQ, Copilot CLI
::   - Sets up Python environment and dependencies
::   - Creates a "Start Pulse" shortcut on your Desktop
::
:: Prerequisites: Windows 11 with OneDrive for Business syncing.
:: ============================================================================

echo.
echo ============================================
echo   Pulse Agent - Easy Installer
echo ============================================
echo.
echo This will install everything you need to run Pulse Agent.
echo It may take 5-10 minutes on the first run.
echo.
pause

:: Run the real setup via PowerShell (handles execution policy automatically)
powershell -ExecutionPolicy Bypass -NoProfile -File "%~dp0setup.ps1"

if %ERRORLEVEL% neq 0 (
    echo.
    echo ============================================
    echo   Setup encountered errors. See above.
    echo ============================================
    echo.
    pause
    exit /b 1
)

echo.
echo ============================================
echo   All done! Look for "Start Pulse" on
echo   your Desktop to launch Pulse Agent.
echo ============================================
echo.
pause
