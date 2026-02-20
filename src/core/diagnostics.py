"""Startup diagnostics — preflight checks before daemon starts."""

import shutil
from pathlib import Path

from core.constants import OUTPUT_DIR, LOGS_DIR
from core.logging import log


def run_diagnostics(config: dict) -> list[str]:
    """Run preflight checks. Returns list of warnings (empty = all good)."""
    warnings = []

    # Config completeness
    if not config.get("models"):
        warnings.append("No 'models' section in config")

    if not config.get("telegram", {}).get("bot_token"):
        warnings.append("No Telegram bot_token configured")

    if "monitoring" not in config:
        warnings.append("No 'monitoring' section in config")

    # Copilot CLI availability
    if not shutil.which("copilot") and not shutil.which("github-copilot"):
        warnings.append("Copilot CLI not found on PATH")

    # WorkIQ availability (optional)
    if not shutil.which("workiq"):
        warnings.append("WorkIQ MCP server not found on PATH (optional)")

    # Output directories
    for d in [OUTPUT_DIR, LOGS_DIR]:
        try:
            d.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            warnings.append(f"Cannot create directory {d}: {e}")

    # Browser profile (for transcript collection)
    playwright_cfg = config.get("transcripts", {}).get("playwright", {})
    user_data_dir = playwright_cfg.get("user_data_dir")
    if user_data_dir and not Path(user_data_dir).exists():
        warnings.append(f"Browser profile not found: {user_data_dir}")

    return warnings
