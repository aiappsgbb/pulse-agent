"""Persistent scheduler — cron-like task scheduling with JSON state.

Supports schedule patterns:
  - "daily 07:00"         → every day at 07:00
  - "weekdays 07:00"      → Mon-Fri at 07:00
  - "every 6h"            → every 6 hours from last run
  - "every 30m"           → every 30 minutes from last run

State persists to scheduler.json so schedules survive restarts.
"""

import asyncio
import re
from datetime import datetime, timedelta

from core.constants import OUTPUT_DIR
from core.logging import log
from core.state import load_json_state, save_json_state

SCHEDULER_FILE = OUTPUT_DIR / ".scheduler.json"
MIN_INTERVAL_MINUTES = 5  # guard against runaway schedules


def _load_schedules() -> list[dict]:
    state = load_json_state(SCHEDULER_FILE, {"schedules": []})
    return state.get("schedules", [])


def _save_schedules(schedules: list[dict]):
    save_json_state(SCHEDULER_FILE, {"schedules": schedules})


def list_schedules() -> list[dict]:
    """Return all schedules (enabled and disabled)."""
    return _load_schedules()


def add_schedule(
    schedule_id: str,
    job_type: str,
    pattern: str,
    description: str = "",
) -> dict:
    """Add a new schedule. Returns the created entry."""
    if not validate_pattern(pattern):
        raise ValueError(f"Invalid schedule pattern: '{pattern}'")

    schedules = _load_schedules()

    # Check for duplicate ID
    if any(s["id"] == schedule_id for s in schedules):
        raise ValueError(f"Schedule '{schedule_id}' already exists")

    # Set last_run to now so the schedule doesn't fire immediately.
    # For daily/weekdays patterns this means it waits until the next occurrence.
    # For interval patterns it waits one full interval before first fire.
    entry = {
        "id": schedule_id,
        "type": job_type,
        "pattern": pattern,
        "description": description,
        "enabled": True,
        "created_at": datetime.now().isoformat(),
        "last_run": datetime.now().isoformat(),
    }
    schedules.append(entry)
    _save_schedules(schedules)
    return entry


def remove_schedule(schedule_id: str) -> bool:
    """Remove a schedule by ID. Returns True if found and removed."""
    schedules = _load_schedules()
    before = len(schedules)
    schedules = [s for s in schedules if s["id"] != schedule_id]
    if len(schedules) == before:
        return False
    _save_schedules(schedules)
    return True


def validate_pattern(pattern: str) -> bool:
    """Check if a pattern is valid."""
    return parse_pattern(pattern) is not None


def parse_pattern(pattern: str) -> dict | None:
    """Parse a schedule pattern into a structured dict.

    Returns None if invalid. Valid patterns:
      "daily HH:MM"      -> {"type": "daily", "hour": H, "minute": M}
      "weekdays HH:MM"   -> {"type": "weekdays", "hour": H, "minute": M}
      "every Nh"          -> {"type": "interval", "seconds": N*3600}
      "every Nm"          -> {"type": "interval", "seconds": N*60}
    """
    pattern = pattern.strip().lower()

    # daily HH:MM or weekdays HH:MM
    m = re.match(r"^(daily|weekdays)\s+(\d{1,2}):(\d{2})$", pattern)
    if m:
        kind, hour, minute = m.group(1), int(m.group(2)), int(m.group(3))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return {"type": kind, "hour": hour, "minute": minute}
        return None

    # every Nh or every Nm
    m = re.match(r"^every\s+(\d+)([hm])$", pattern)
    if m:
        value, unit = int(m.group(1)), m.group(2)
        seconds = value * 3600 if unit == "h" else value * 60
        if seconds >= MIN_INTERVAL_MINUTES * 60:
            return {"type": "interval", "seconds": seconds}
        return None

    return None


def is_due(schedule: dict, now: datetime | None = None) -> bool:
    """Check if a schedule is due to run."""
    if not schedule.get("enabled", True):
        return False

    now = now or datetime.now()
    parsed = parse_pattern(schedule["pattern"])
    if not parsed:
        return False

    last_run_str = schedule.get("last_run")
    last_run = datetime.fromisoformat(last_run_str) if last_run_str else None

    if parsed["type"] == "interval":
        if not last_run:
            return True  # never run → due immediately
        elapsed = (now - last_run).total_seconds()
        return elapsed >= parsed["seconds"]

    if parsed["type"] in ("daily", "weekdays"):
        # Only weekdays for "weekdays" pattern
        if parsed["type"] == "weekdays" and now.isoweekday() > 5:
            return False

        target_time = now.replace(
            hour=parsed["hour"], minute=parsed["minute"], second=0, microsecond=0
        )

        # Already ran today?
        if last_run and last_run.date() == now.date():
            return False

        # Past the target time today?
        return now >= target_time

    return False


def mark_run(schedule_id: str):
    """Record that a schedule just ran."""
    schedules = _load_schedules()
    for s in schedules:
        if s["id"] == schedule_id:
            s["last_run"] = datetime.now().isoformat()
            break
    _save_schedules(schedules)


async def scheduler_loop(
    config: dict,
    job_queue: asyncio.Queue,
    shutdown_event: asyncio.Event,
    check_interval: int = 60,
):
    """Background loop — checks schedules and syncs OneDrive every `check_interval` seconds.

    When a schedule is due, enqueues the job and marks it as run.
    Also pulls new job files from OneDrive each tick (inter-agent requests, etc.).
    """
    from tg.bot import get_proactive_chat_id
    from daemon.sync import sync_jobs_from_onedrive

    log.info(f"Scheduler started (checking every {check_interval}s)")

    while not shutdown_event.is_set():
        try:
            # Pull new job files from OneDrive (inter-agent requests, etc.)
            sync_jobs_from_onedrive(config, job_queue)

            schedules = _load_schedules()
            now = datetime.now()

            for schedule in schedules:
                if is_due(schedule, now):
                    chat_id = get_proactive_chat_id()
                    job = {
                        "type": schedule["type"],
                        "_source": f"schedule:{schedule['id']}",
                        "_chat_id": chat_id,
                    }
                    job_queue.put_nowait(job)
                    mark_run(schedule["id"])
                    log.info(f"Scheduler: fired '{schedule['id']}' ({schedule['pattern']})")

        except Exception:
            log.warning("Scheduler tick failed", exc_info=True)

        # Sleep in 1-second increments for responsive shutdown
        for _ in range(check_interval):
            if shutdown_event.is_set():
                return
            await asyncio.sleep(1)

    log.info("Scheduler stopped")
