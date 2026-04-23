"""Persistent scheduler — cron-like task scheduling with JSON state.

Supports schedule patterns:
  - "daily 07:00"         → every day at 07:00
  - "weekdays 07:00"      → Mon-Fri at 07:00
  - "every 6h"            → every 6 hours from last run
  - "every 30m"           → every 30 minutes from last run

Default schedules are defined in standing-instructions.yaml under `schedule:`.
On daemon startup, `ensure_default_schedules()` syncs them into the state file.
Config is authoritative for patterns/descriptions; state preserves last_run/enabled.

State persists to scheduler.json so schedules survive restarts.
"""

import asyncio
import re
from datetime import datetime, timedelta

from core.constants import PULSE_HOME
from core.logging import log
from core.state import load_json_state, save_json_state

SCHEDULER_FILE = PULSE_HOME / ".scheduler.json"
MIN_INTERVAL_MINUTES = 5  # guard against runaway schedules


def _load_schedules() -> list[dict]:
    state = load_json_state(SCHEDULER_FILE, {"schedules": []})
    return state.get("schedules", [])


def _save_schedules(schedules: list[dict]):
    save_json_state(SCHEDULER_FILE, {"schedules": schedules})


def is_office_hours(config: dict) -> bool:
    """Check if current time is within configured office hours."""
    office = config.get("monitoring", {}).get("office_hours", {})
    if not office:
        return True  # No office hours configured = always on

    now = datetime.now()
    allowed_days = office.get("days", [1, 2, 3, 4, 5])
    if now.isoweekday() not in allowed_days:
        return False

    start = office.get("start", "08:00")
    end = office.get("end", "18:00")
    try:
        start_parts = str(start).split(":")
        end_parts = str(end).split(":")
        start_h, start_m = int(start_parts[0]), int(start_parts[1]) if len(start_parts) > 1 else 0
        end_h, end_m = int(end_parts[0]), int(end_parts[1]) if len(end_parts) > 1 else 0
    except (ValueError, IndexError):
        log.warning(f"  Malformed office hours (start={start!r}, end={end!r}) — defaulting to always-on")
        return True

    now_mins = now.hour * 60 + now.minute
    return (start_h * 60 + start_m) <= now_mins < (end_h * 60 + end_m)


def ensure_default_schedules(config: dict):
    """Sync default schedules from config into the persistent scheduler state.

    Config schedules (standing-instructions.yaml `schedule:` section) are
    authoritative for pattern, type, description, and office_hours_only.
    The state file preserves last_run and enabled status across restarts.
    Agent-created schedules (not in config) are left untouched.
    """
    config_schedules = config.get("schedule", [])
    if not config_schedules:
        return

    current = _load_schedules()
    current_by_id = {s["id"]: s for s in current}
    changed = False

    for cs in config_schedules:
        sid = cs.get("id", "")
        if not sid:
            continue

        pattern = cs.get("pattern", "")
        if not validate_pattern(pattern):
            log.warning(f"Scheduler: invalid pattern '{pattern}' for '{sid}', skipping")
            continue

        if sid in current_by_id:
            # Update pattern/description from config, preserve state
            existing = current_by_id[sid]
            if (existing.get("pattern") != pattern
                    or existing.get("type") != cs.get("type")
                    or existing.get("description") != cs.get("description", "")
                    or existing.get("office_hours_only") != cs.get("office_hours_only", False)):
                existing["pattern"] = pattern
                existing["type"] = cs["type"]
                existing["description"] = cs.get("description", "")
                existing["office_hours_only"] = cs.get("office_hours_only", False)
                changed = True
                log.info(f"Scheduler: updated '{sid}' from config ({pattern})")
        else:
            # New schedule — seed with last_run=None so catch-up fires naturally
            entry = {
                "id": sid,
                "type": cs["type"],
                "pattern": pattern,
                "description": cs.get("description", ""),
                "enabled": True,
                "created_at": datetime.now().isoformat(),
                "last_run": None,
                "office_hours_only": cs.get("office_hours_only", False),
            }
            current.append(entry)
            changed = True
            log.info(f"Scheduler: added '{sid}' from config ({pattern})")

    if changed:
        _save_schedules(current)


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


def update_schedule(schedule_id: str, pattern: str = "", description: str = "", enabled: bool = True) -> dict | None:
    """Update an existing schedule. Returns the updated entry, or None if not found."""
    schedules = _load_schedules()
    for s in schedules:
        if s["id"] == schedule_id:
            if pattern:
                if not validate_pattern(pattern):
                    raise ValueError(f"Invalid schedule pattern: '{pattern}'")
                s["pattern"] = pattern
            if description:
                s["description"] = description
            s["enabled"] = enabled
            _save_schedules(schedules)
            return s
    return None


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


def is_due(schedule: dict, now: datetime | None = None, config: dict | None = None) -> bool:
    """Check if a schedule is due to run.

    Args:
        schedule: Schedule entry dict.
        now: Override current time (for testing).
        config: Standing instructions config — needed for office_hours_only check.
    """
    if not schedule.get("enabled", True):
        return False

    # Office hours gate — skip if outside configured hours
    if schedule.get("office_hours_only", False) and config:
        if not is_office_hours(config):
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


def reset_run(schedule_id: str):
    """Reset last_run so the schedule fires again on the next tick.

    Called by the worker when a scheduled job fails — ensures the job
    will be retried instead of waiting until the next scheduled time.
    """
    schedules = _load_schedules()
    for s in schedules:
        if s["id"] == schedule_id:
            s["last_run"] = None
            log.info(f"Scheduler: reset '{schedule_id}' for retry")
            break
    _save_schedules(schedules)


async def scheduler_loop(
    config: dict,
    job_queue,
    shutdown_event: asyncio.Event,
    check_interval: int = 30,
):
    """Background loop — checks schedules and syncs OneDrive every `check_interval` seconds.

    When a schedule is due, enqueues the job and marks it as run.
    Also pulls new job files from OneDrive each tick (inter-agent requests, etc.).

    ``job_queue`` is an ``asyncio.PriorityQueue`` — jobs are enqueued via
    ``enqueue_job()`` from ``daemon.worker`` so they respect priority ordering.
    """
    from daemon.sync import sync_jobs_from_onedrive
    from daemon.worker import enqueue_job
    from tui.ipc import cleanup_orphaned_jobs

    log.info(f"Scheduler started (checking every {check_interval}s)")
    _cleanup_counter = 0
    _CLEANUP_EVERY_N_TICKS = 5  # every 5 ticks (~5 min at 60s interval)

    while not shutdown_event.is_set():
        try:
            # Pull new job files from OneDrive (inter-agent requests, etc.)
            sync_jobs_from_onedrive(config, job_queue)

            # Periodically clean up stale "running" jobs
            _cleanup_counter += 1
            if _cleanup_counter >= _CLEANUP_EVERY_N_TICKS:
                _cleanup_counter = 0
                try:
                    cleaned = cleanup_orphaned_jobs()
                    if cleaned:
                        log.info(f"Scheduler: cleaned {cleaned} stale running job(s)")
                except Exception:
                    log.warning("Scheduler: orphaned job cleanup failed", exc_info=True)

            schedules = _load_schedules()
            now = datetime.now()

            for schedule in schedules:
                if is_due(schedule, now, config=config):
                    job = {
                        "type": schedule["type"],
                        "_source": f"schedule:{schedule['id']}",
                        "_schedule_id": schedule["id"],
                    }
                    enqueue_job(job_queue, job, config)
                    # Mark as run immediately to prevent re-fire while job runs.
                    # On failure, the worker resets last_run to None for retry.
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
