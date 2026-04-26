"""Heartbeat — DEPRECATED. Scheduling is now config-driven via core.scheduler.

All periodic scheduling (triage, digest, intel) is handled by the scheduler.
Default schedules are defined in standing-instructions.yaml under `schedule:`.
Office hours checking is in core.scheduler.is_office_hours().

This module is kept only for backward compatibility of existing imports.
"""

# Re-export from scheduler for any existing callers
from core.scheduler import is_office_hours  # noqa: F401


def parse_interval(interval: str) -> int:
    """Parse interval string like '30m', '1h', '5m' to seconds."""
    interval = interval.strip().lower()
    try:
        if interval.endswith("h"):
            return int(interval[:-1]) * 3600
        if interval.endswith("m"):
            return int(interval[:-1]) * 60
        if interval.endswith("s"):
            return int(interval[:-1])
        return int(interval)
    except ValueError:
        return 1800
