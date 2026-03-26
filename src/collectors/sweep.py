"""Inbox sweep — classify and mark unimportant items as read.

Two sweep modes:
1. Smart sweep (post-triage): Uses triage output to identify FYI/low items
2. Full sweep (ad-hoc "clear my messages"): Marks ALL unread items as read

Sweep rules are configurable via standing-instructions.yaml:
  monitoring.sweep.enabled          — auto-sweep after triage (default: false)
  monitoring.sweep.sweep_types      — item types to sweep (default: ["fyi"])
  monitoring.sweep.max_priority     — max priority to sweep (default: "low")
  monitoring.sweep.never_sweep      — types that are never swept
"""

import json
import re
from pathlib import Path

from core.constants import PULSE_HOME
from core.logging import log


# Priority ordering (lower index = higher priority)
_PRIORITY_ORDER = ["urgent", "high", "medium", "low"]

# Default sweep config
DEFAULT_SWEEP_CONFIG = {
    "enabled": False,
    "sweep_types": ["fyi"],
    "max_priority": "low",
    "never_sweep": ["reply_needed", "escalation"],
}


def get_sweep_config(config: dict) -> dict:
    """Extract sweep config from standing instructions, with defaults."""
    sweep = config.get("monitoring", {}).get("sweep", {})
    result = dict(DEFAULT_SWEEP_CONFIG)
    result.update({k: v for k, v in sweep.items() if v is not None})
    return result


def parse_source_name(source: str) -> tuple[str, str]:
    """Extract (platform, name) from a triage/digest item source field.

    Examples:
        "Teams: Fatos Ismali"        -> ("teams", "Fatos Ismali")
        "Email: Bob Wilson"          -> ("outlook", "Bob Wilson")
        "Email from Bob Wilson"      -> ("outlook", "Bob Wilson")
        "Teams: Project Planning"    -> ("teams", "Project Planning")
        "Calendar: Standup"          -> ("calendar", "Standup")
        "RSS: TechCrunch"           -> ("rss", "TechCrunch")

    Returns ("unknown", source) if pattern not recognized.
    """
    if not source:
        return ("unknown", "")

    # "Teams: Name" or "Email: Name"
    match = re.match(r"^(Teams|Email|Calendar|RSS)\s*:\s*(.+)", source, re.IGNORECASE)
    if match:
        platform = match.group(1).lower()
        name = match.group(2).strip()
        if platform == "email":
            platform = "outlook"
        return (platform, name)

    # "Email from Name"
    match = re.match(r"^Email\s+from\s+(.+)", source, re.IGNORECASE)
    if match:
        return ("outlook", match.group(1).strip())

    return ("unknown", source)


def classify_for_sweep(
    triage_items: list[dict],
    sweep_config: dict,
) -> tuple[list[str], list[dict]]:
    """Classify triage items into sweepable Teams chats and Outlook emails.

    Returns:
        (teams_names, outlook_items) where:
        - teams_names: list of chat names to mark as read
        - outlook_items: list of dicts with conv_id/sender/subject for Outlook
    """
    sweep_types = set(sweep_config.get("sweep_types", ["fyi"]))
    max_priority = sweep_config.get("max_priority", "low")
    never_sweep = set(sweep_config.get("never_sweep", ["reply_needed", "escalation"]))

    # Determine the priority threshold
    try:
        max_priority_idx = _PRIORITY_ORDER.index(max_priority)
    except ValueError:
        max_priority_idx = len(_PRIORITY_ORDER) - 1  # default to lowest

    teams_names = []
    outlook_items = []

    for item in triage_items:
        item_type = item.get("type", "")
        priority = item.get("priority", "medium")
        source = item.get("source", "")

        # Never sweep these types
        if item_type in never_sweep:
            continue

        # Check type filter
        if item_type not in sweep_types:
            continue

        # Check priority threshold
        try:
            item_priority_idx = _PRIORITY_ORDER.index(priority)
        except ValueError:
            item_priority_idx = 2  # default to medium

        if item_priority_idx < max_priority_idx:
            # Item has HIGHER priority than threshold — don't sweep
            continue

        # Classify by platform
        platform, name = parse_source_name(source)
        if platform == "teams" and name:
            teams_names.append(name)
        elif platform == "outlook" and name:
            outlook_items.append({
                "conv_id": item.get("conv_id", ""),
                "sender": name,
                "subject": item.get("title", ""),
            })

    return teams_names, outlook_items


def load_latest_triage_items() -> list[dict]:
    """Load items from the most recent monitoring JSON file."""
    reports = sorted(PULSE_HOME.glob("monitoring-*.json"), reverse=True)
    if not reports:
        return []

    try:
        data = json.loads(reports[0].read_text(encoding="utf-8"))
        return data.get("items", [])
    except Exception:
        log.warning("Failed to load latest triage report", exc_info=True)
        return []


async def execute_sweep(
    config: dict,
    full_sweep: bool = False,
) -> dict:
    """Execute an inbox sweep — marks items as read in Teams and Outlook.

    Args:
        config: Standing instructions config
        full_sweep: If True, marks ALL unread items as read (no classification).
                    If False, uses triage data for smart classification.

    Returns:
        {success, teams_result, outlook_result, summary}
    """
    from collectors.teams_marker import mark_teams_chats_read
    from collectors.outlook_marker import mark_outlook_emails_read

    teams_result = {"marked": 0, "failed": 0}
    outlook_result = {"marked": 0, "failed": 0}

    if full_sweep:
        # Full sweep — mark everything as read
        log.info("Running full inbox sweep (all unread items)...")

        teams_result = await mark_teams_chats_read(chat_names=None)
        outlook_result = await mark_outlook_emails_read(items=None)
    else:
        # Smart sweep — use triage classification
        triage_items = load_latest_triage_items()
        if not triage_items:
            log.info("No triage data available — running full sweep instead")
            teams_result = await mark_teams_chats_read(chat_names=None)
            outlook_result = await mark_outlook_emails_read(items=None)
        else:
            sweep_config = get_sweep_config(config)
            teams_names, outlook_items = classify_for_sweep(triage_items, sweep_config)

            log.info(
                f"Smart sweep: {len(teams_names)} Teams chats, "
                f"{len(outlook_items)} Outlook emails to mark as read"
            )

            if teams_names:
                teams_result = await mark_teams_chats_read(chat_names=teams_names)
            if outlook_items:
                outlook_result = await mark_outlook_emails_read(items=outlook_items)

    teams_marked = teams_result.get("marked", 0)
    outlook_marked = outlook_result.get("marked", 0)
    total = teams_marked + outlook_marked

    summary = f"Sweep complete: {total} items marked as read"
    if teams_marked:
        summary += f" ({teams_marked} Teams"
    if outlook_marked:
        summary += f", {outlook_marked} Outlook" if teams_marked else f" ({outlook_marked} Outlook"
    if teams_marked or outlook_marked:
        summary += ")"

    log.info(f"  {summary}")

    return {
        "success": True,
        "teams_result": teams_result,
        "outlook_result": outlook_result,
        "summary": summary,
    }
