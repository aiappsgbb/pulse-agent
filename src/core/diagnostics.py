"""Startup diagnostics — preflight checks and health check.

``run_diagnostics`` — lightweight preflight (runs every daemon start).
``run_health_check`` / ``run_health_check_async`` — comprehensive post-install
validation invoked via ``--health-check``.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

from core.constants import (
    PULSE_HOME, DIGESTS_DIR, LOGS_DIR, TRANSCRIPTS_DIR, PROJECTS_DIR,
    JOBS_DIR, INTEL_DIR, SIGNALS_DIR,
)
from core.logging import log


# ---------------------------------------------------------------------------
# Preflight diagnostics (daemon startup)
# ---------------------------------------------------------------------------

def run_diagnostics(config: dict) -> list[str]:
    """Run preflight checks. Returns list of warnings (empty = all good).

    Warnings are non-fatal — the daemon starts regardless. But they give
    the user actionable messages about what to fix.
    """
    warnings = []

    # ── PULSE_HOME ───────────────────────────────────────────────────────
    pulse_env = os.environ.get("PULSE_HOME", "")
    if not pulse_env:
        warnings.append(
            "PULSE_HOME not set — using project root for data storage. "
            "Set PULSE_HOME in .env to an OneDrive folder for production use. "
            "See .env.example for details."
        )
    elif not PULSE_HOME.exists():
        warnings.append(
            f"PULSE_HOME path does not exist: {PULSE_HOME} — "
            "run setup.ps1 or create the directory manually."
        )

    # ── Config completeness ──────────────────────────────────────────────
    if not config.get("models"):
        warnings.append(
            "No 'models' section in config — "
            "add model routing (triage, digest, chat, etc.) to standing-instructions.yaml"
        )

    if "monitoring" not in config:
        warnings.append(
            "No 'monitoring' section in config — "
            "triage scheduling and office hours won't work"
        )

    user = config.get("user", {})
    if not user.get("name"):
        warnings.append(
            "No 'user.name' in config — "
            "set your name in standing-instructions.yaml so digests and inter-agent messages identify you"
        )

    # Check for unresolved TODO placeholders
    todo_fields = [
        k for k, v in user.items()
        if isinstance(v, str) and "TODO" in v.upper()
    ]
    if todo_fields:
        warnings.append(
            f"Config has TODO placeholders in user.{', user.'.join(todo_fields)} — "
            "use Chat to complete setup or run with --setup"
        )

    # ── Copilot CLI ──────────────────────────────────────────────────────
    if not shutil.which("copilot") and not shutil.which("github-copilot"):
        warnings.append(
            "Copilot CLI not found on PATH — "
            "install via: gh extension install github/gh-copilot"
        )

    # ── WorkIQ (optional) ────────────────────────────────────────────────
    if not shutil.which("workiq"):
        warnings.append("WorkIQ MCP server not found on PATH (optional — M365 queries will be unavailable)")

    # ── Data directories — create if missing ─────────────────────────────
    essential_dirs = [
        DIGESTS_DIR, LOGS_DIR, TRANSCRIPTS_DIR, PROJECTS_DIR,
        JOBS_DIR / "pending", JOBS_DIR / "completed",
        INTEL_DIR, SIGNALS_DIR,
    ]
    for d in essential_dirs:
        try:
            d.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            warnings.append(f"Cannot create directory {d}: {e}")

    # ── Browser profile (transcript collection) ──────────────────────────
    playwright_cfg = config.get("transcripts", {}).get("playwright", {})
    user_data_dir = playwright_cfg.get("user_data_dir")
    if user_data_dir:
        expanded = Path(os.path.expandvars(user_data_dir))
        if not expanded.exists():
            warnings.append(
                f"Browser profile not found: {expanded} — "
                "transcript collection and inbox scans need an authenticated Edge session. "
                "Run 'playwright install msedge' and log into Teams once."
            )

    # ── Team config (inter-agent) ────────────────────────────────────────
    team = config.get("team", [])
    if not team:
        warnings.append(
            "No 'team' section in config (optional — inter-agent communication disabled)"
        )
    else:
        for member in team:
            if not member.get("alias"):
                warnings.append(f"Team member '{member.get('name', '?')}' has no alias")

    # ── Pulse-Team directory (inter-agent convention path) ────────────────
    from core.constants import PULSE_TEAM_DIR
    if team and not PULSE_TEAM_DIR.exists():
        warnings.append(
            f"Pulse-Team directory not found: {PULSE_TEAM_DIR} — "
            "inter-agent communication needs this shared OneDrive folder. "
            "Create it or share it from a teammate."
        )

    return warnings


# ---------------------------------------------------------------------------
# Browser auth verification
# ---------------------------------------------------------------------------

TEAMS_URL = "https://teams.microsoft.com/v2/"
AUTH_TIMEOUT = 15  # seconds to wait for page load

_LOGIN_INDICATORS = [
    "login.microsoftonline.com",
    "login.live.com",
    "login.microsoft.com",
    "/oauth2/",
    "/common/login",
]


def _is_login_page(url: str) -> bool:
    """Check whether a URL looks like a Microsoft login redirect."""
    lower = url.lower()
    return any(ind in lower for ind in _LOGIN_INDICATORS)


async def _launch_edge(headless: bool = True):
    """Launch Edge with the daemon profile. Returns (pw, context) or raises.

    Caller is responsible for cleanup via _close_edge().
    """
    from playwright.async_api import async_playwright
    from core.browser import _default_profile_dir

    pw = await async_playwright().__aenter__()
    try:
        context = await pw.chromium.launch_persistent_context(
            _default_profile_dir(),
            channel="msedge",
            headless=headless,
            viewport={"width": 1280, "height": 900},
        )
        return pw, context
    except Exception:
        await pw.__aexit__(None, None, None)
        raise


async def _close_edge(pw, context):
    """Cleanup helper — close context and playwright, swallow errors."""
    if context:
        try:
            await context.close()
        except Exception:
            pass
    if pw:
        try:
            await pw.__aexit__(None, None, None)
        except Exception:
            pass


async def verify_browser_auth(headless: bool = True) -> dict:
    """Launch the daemon's Edge profile and check Teams authentication.

    Returns dict with: ok, url, title, error, needs_login, profile_dir.
    """
    from core.browser import _default_profile_dir

    result = {
        "ok": False, "url": "", "title": "",
        "error": None, "needs_login": False,
        "profile_dir": _default_profile_dir(),
    }

    pw = context = None
    try:
        pw, context = await _launch_edge(headless=headless)
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(TEAMS_URL, wait_until="domcontentloaded", timeout=AUTH_TIMEOUT * 1000)

        # Wait for redirects to settle
        try:
            await page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass  # networkidle can time out on heavy SPAs

        result["url"] = page.url
        result["title"] = await page.title()
        result["needs_login"] = _is_login_page(page.url)
        result["ok"] = not result["needs_login"]

    except Exception as e:
        result["error"] = str(e)
    finally:
        await _close_edge(pw, context)

    return result


async def open_browser_for_login() -> dict:
    """Open a visible Edge window for the user to sign into Teams.

    Uses the same daemon profile that Pulse uses at runtime.
    Auth persists after the user closes the browser.
    """
    from core.browser import _default_profile_dir

    result = {
        "ok": False, "url": "", "title": "",
        "error": None, "needs_login": False,
        "profile_dir": _default_profile_dir(),
    }

    pw = context = None
    try:
        pw, context = await _launch_edge(headless=False)
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(TEAMS_URL, wait_until="domcontentloaded", timeout=30000)

        print("\n  A browser window has opened. Please sign into Microsoft Teams.")
        print("  Close the browser window when you're done.\n")
        try:
            await page.wait_for_event("close", timeout=300000)  # 5 min max
        except Exception:
            pass

        result["ok"] = True
        result["url"] = TEAMS_URL

    except Exception as e:
        result["error"] = str(e)
    finally:
        await _close_edge(pw, context)

    return result


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

class HealthCheck(NamedTuple):
    name: str
    ok: bool
    detail: str = ""
    fix: str = ""

    def __repr__(self):
        return f"[{'PASS' if self.ok else 'FAIL'}] {self.name}: {self.detail}"


# (name_or_callable, ok_test, detail, fix) — declarative checks
_IMPORT_CHECKS = [
    ("playwright", "pip install playwright && python -m playwright install msedge"),
    ("yaml", "pip install pyyaml"),
    ("dotenv", "pip install python-dotenv"),
    ("textual", "pip install textual"),
]

_CLI_CHECKS = [
    ("gh", "winget install GitHub.cli"),
    ("node", "winget install OpenJS.NodeJS.LTS"),
    ("npm", "winget install OpenJS.NodeJS.LTS"),
]


def run_health_check(config: dict | None = None) -> list[HealthCheck]:
    """Comprehensive post-install validation (synchronous checks only).

    For async checks (browser auth), use run_health_check_async().
    """
    checks: list[HealthCheck] = []

    # Python version
    v = sys.version_info
    checks.append(HealthCheck(
        "Python version", v.major >= 3 and v.minor >= 12,
        f"{v.major}.{v.minor}.{v.micro}",
        "Install Python 3.12+: winget install Python.Python.3.12",
    ))

    # Key imports
    for mod_name, fix in _IMPORT_CHECKS:
        try:
            __import__(mod_name)
            checks.append(HealthCheck(f"Import {mod_name}", True, "available"))
        except ImportError:
            checks.append(HealthCheck(f"Import {mod_name}", False, "missing", fix))

    # CLI tools
    for tool, fix in _CLI_CHECKS:
        path = shutil.which(tool)
        checks.append(HealthCheck(f"CLI: {tool}", path is not None, path or "not found", fix))

    # GitHub auth
    checks.append(_check_gh_auth())

    # Copilot CLI extension
    found = shutil.which("copilot") or shutil.which("github-copilot")
    checks.append(HealthCheck(
        "Copilot CLI extension", found is not None,
        "found" if found else "not found",
        "gh extension install github/gh-copilot",
    ))

    # WorkIQ (optional)
    found = shutil.which("workiq")
    checks.append(HealthCheck(
        "WorkIQ MCP server", found is not None,
        "found" if found else "not found (optional)",
        "npm install -g @microsoft/workiq",
    ))

    # MSX-MCP (optional — MSX/Dataverse/CRM tools)
    msx_found = _check_msx_mcp_plugin()
    checks.append(HealthCheck(
        "MSX-MCP plugin", msx_found,
        "installed" if msx_found else "not found (optional — MSX pipeline queries unavailable)",
        "copilot plugin install mcaps-microsoft/MSX-MCP",
    ))

    # PULSE_HOME
    checks.append(_check_pulse_home())

    # Playwright Edge
    checks.append(_check_playwright_edge())

    # Config
    checks.extend(_check_config(config))

    # Virtual environment
    in_venv = sys.prefix != sys.base_prefix
    checks.append(HealthCheck(
        "Virtual environment", in_venv,
        "active" if in_venv else "not active (using system Python)",
        "python -m venv .venv && .venv\\Scripts\\activate",
    ))

    return checks


def _check_msx_mcp_plugin() -> bool:
    """Check if MSX-MCP plugin is installed in Copilot CLI."""
    from sdk.agents import is_msx_available
    return is_msx_available()


def _check_gh_auth() -> HealthCheck:
    if not shutil.which("gh"):
        return HealthCheck("GitHub CLI auth", False, "gh not installed", "winget install GitHub.cli")
    try:
        r = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True, timeout=10)
        return HealthCheck(
            "GitHub CLI auth", r.returncode == 0,
            "authenticated" if r.returncode == 0 else "not authenticated",
            "Run: gh auth login",
        )
    except Exception as e:
        return HealthCheck("GitHub CLI auth", False, str(e), "Run: gh auth login")


def _check_pulse_home() -> HealthCheck:
    pulse_env = os.environ.get("PULSE_HOME", "")
    onedrive_env = os.environ.get("OneDriveCommercial", "")
    if pulse_env:
        exists = Path(os.path.expandvars(pulse_env)).exists()
        return HealthCheck(
            "PULSE_HOME", exists,
            pulse_env if exists else f"{pulse_env} (does not exist)",
            "Run setup.ps1 or create the directory",
        )
    if onedrive_env:
        auto = Path(onedrive_env) / "Documents" / "Pulse"
        return HealthCheck(
            "PULSE_HOME (auto-detected)", auto.exists(),
            str(auto) if auto.exists() else f"{auto} (does not exist)",
            "Run setup.ps1 to create directories",
        )
    return HealthCheck(
        "PULSE_HOME", False,
        "Not set, OneDriveCommercial not found either",
        "Sign into OneDrive for Business, or set PULSE_HOME in .env",
    )


def _check_playwright_edge() -> HealthCheck:
    """Check if Playwright can launch Edge.

    Skips the actual browser launch if we're inside an asyncio event loop
    (sync_playwright can't run there). In that case, just checks that the
    playwright package is importable — the async browser auth check covers
    the actual launch test.
    """
    try:
        import playwright  # noqa: F401
    except ImportError:
        return HealthCheck("Playwright Edge", False, "playwright not installed", "pip install playwright")

    # If we're inside an event loop, can't use sync API — defer to async check
    import asyncio
    try:
        asyncio.get_running_loop()
        return HealthCheck("Playwright Edge", True, "installed (launch test deferred to browser auth check)")
    except RuntimeError:
        pass  # No event loop — safe to use sync API

    try:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().__enter__()
        try:
            browser = pw.chromium.launch(channel="msedge", headless=True)
            browser.close()
            return HealthCheck("Playwright Edge", True, "launches OK")
        except Exception as e:
            return HealthCheck("Playwright Edge", False, str(e), _edge_launch_remediation(e))
        finally:
            pw.__exit__(None, None, None)
    except Exception as e:
        return HealthCheck("Playwright Edge", False, str(e), _edge_launch_remediation(e))


def _edge_launch_remediation(err: Exception) -> str:
    """Pick remediation based on why Edge failed to launch.

    `playwright install msedge` is only correct when Edge isn't on disk. Profile
    locks, auth redirects, or transient crashes have nothing to do with the
    installer — sending users there is a corporate-IT rabbit hole.
    """
    msg = str(err).lower()
    if "executable doesn't exist" in msg or "please run the following command" in msg:
        return "Install Edge from microsoft.com/edge, or run: python -m playwright install msedge"
    if "user data directory is already in use" in msg or ("profile" in msg and "lock" in msg):
        return "Close other Edge windows using Pulse's profile, then retry"
    return "Run: python src/pulse.py --health-check (and verify Edge is installed)"


def _check_config(config: dict | None) -> list[HealthCheck]:
    if not config:
        return [HealthCheck("Config", False, "No config file found", "Run setup.ps1 or python src/pulse.py --setup")]
    checks = []
    user = config.get("user", {})
    name_ok = bool(user.get("name")) and "TODO" not in str(user.get("name", "")).upper()
    email_ok = bool(user.get("email")) and "TODO" not in str(user.get("email", "")).upper()
    checks.append(HealthCheck(
        "Config: user identity", name_ok and email_ok,
        f"name={'set' if name_ok else 'missing'}, email={'set' if email_ok else 'missing'}",
        "Run: python src/pulse.py --setup",
    ))
    checks.append(HealthCheck(
        "Config: models", bool(config.get("models")),
        "configured" if config.get("models") else "missing",
        "Add 'models' section to standing-instructions.yaml",
    ))
    return checks


# ---------------------------------------------------------------------------
# Async health check (adds browser auth)
# ---------------------------------------------------------------------------

async def run_health_check_async(config: dict | None = None) -> list[HealthCheck]:
    """All health checks including async browser auth verification."""
    checks = run_health_check(config)

    try:
        auth = await verify_browser_auth(headless=True)
        if auth["error"]:
            checks.append(HealthCheck(
                "Browser: Teams auth", False, auth["error"],
                "Run: python src/pulse.py --health-check",
            ))
        elif auth["needs_login"]:
            checks.append(HealthCheck(
                "Browser: Teams auth", False,
                "Not signed in — redirected to login page",
                "Run: python src/pulse.py --health-check",
            ))
        else:
            checks.append(HealthCheck(
                "Browser: Teams auth", True,
                f"Authenticated ({auth['url'][:60]})",
            ))
    except Exception as e:
        checks.append(HealthCheck(
            "Browser: Teams auth", False, str(e),
            "Run: python src/pulse.py --health-check (opens Edge for Teams sign-in)",
        ))

    return checks


# ---------------------------------------------------------------------------
# Report printer
# ---------------------------------------------------------------------------

def print_health_report(checks: list[HealthCheck]):
    """Print a formatted health check report to stdout."""
    print()
    print("=" * 60)
    print("  Pulse Agent — Health Check")
    print("=" * 60)
    print()

    passed = sum(1 for c in checks if c.ok)
    total = len(checks)

    for c in checks:
        icon = "  OK" if c.ok else "FAIL"
        print(f"  [{icon}]  {c.name}")
        print(f"         {c.detail}")
        if not c.ok and c.fix:
            print(f"         Fix: {c.fix}")
        print()

    print("-" * 60)
    print(f"  {passed}/{total} checks passed")
    if passed == total:
        print("  All good! Pulse is ready to run.")
    else:
        critical = [c for c in checks if not c.ok and c.name != "WorkIQ MCP server"]
        if critical:
            print(f"  {len(critical)} issue(s) need attention before Pulse can run reliably.")
        else:
            print("  Only optional components missing — Pulse can run with reduced functionality.")
    print("=" * 60)
    print()
