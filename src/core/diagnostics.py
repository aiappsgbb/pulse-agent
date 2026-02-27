"""Startup diagnostics — preflight checks before daemon starts."""

import os
import shutil
import sys
from pathlib import Path

from core.constants import (
    PULSE_HOME, DIGESTS_DIR, LOGS_DIR, TRANSCRIPTS_DIR, PROJECTS_DIR,
    JOBS_DIR, INTEL_DIR, SIGNALS_DIR,
)
from core.logging import log


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
