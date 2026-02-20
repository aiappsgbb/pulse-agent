"""Heartbeat — periodic triage scheduling and missed-digest catch-up."""

import asyncio
from datetime import datetime, timedelta

from core.constants import OUTPUT_DIR
from core.logging import log


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
        log.warning(f"Invalid interval '{interval}', defaulting to 30m")
        return 1800


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
    start_h, start_m = map(int, start.split(":"))
    end_h, end_m = map(int, end.split(":"))

    now_mins = now.hour * 60 + now.minute
    return (start_h * 60 + start_m) <= now_mins < (end_h * 60 + end_m)


def check_missed_digest(job_queue: asyncio.Queue):
    """Queue a catch-up digest only if neither today nor yesterday has one."""
    digests_dir = OUTPUT_DIR / "digests"
    today = datetime.now().strftime("%Y-%m-%d")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    today_exists = (digests_dir / f"{today}.md").exists()
    yesterday_exists = (digests_dir / f"{yesterday}.md").exists()

    if today_exists or yesterday_exists:
        log.info("Digest up to date — skipping catch-up")
        return

    from tg.bot import get_proactive_chat_id
    chat_id = get_proactive_chat_id()
    job_queue.put_nowait({
        "type": "digest",
        "_source": "catch-up",
        "_chat_id": chat_id,
    })
    log.info("Catch-up: no recent digest found — queued")


async def heartbeat(config: dict, job_queue: asyncio.Queue, shutdown_event: asyncio.Event):
    """Periodic heartbeat — enqueues triage during office hours."""
    from tg.bot import get_proactive_chat_id

    interval = config["monitoring"].get("interval", "30m")
    seconds = parse_interval(interval)

    # Delay first heartbeat so chat messages aren't blocked on startup
    log.info(f"First heartbeat in {interval} (chat messages processed immediately)")
    for _ in range(seconds):
        if shutdown_event.is_set():
            return
        await asyncio.sleep(1)

    while not shutdown_event.is_set():
        # Triage only during office hours
        if is_office_hours(config):
            chat_id = get_proactive_chat_id()
            job_queue.put_nowait({
                "type": "monitor",
                "_source": "heartbeat",
                "_chat_id": chat_id,
            })
            log.info("Heartbeat: triage queued (office hours)")
        else:
            log.info("Heartbeat: outside office hours, skipping triage")

        # Sleep until next cycle
        log.info(f"Next heartbeat in {interval}")
        for _ in range(seconds):
            if shutdown_event.is_set():
                return
            await asyncio.sleep(1)
