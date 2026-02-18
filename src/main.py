"""Pulse Agent — Autonomous Digital Employee

Always-on daemon with Telegram interface.
Architecture: asyncio.Queue → worker processes jobs immediately.

- Telegram messages land on the queue instantly
- Heartbeat puts triage on the queue every 30 minutes
- OneDrive job files are pulled each cycle
- Worker executes jobs one at a time (no SDK concurrency issues)
"""

import asyncio
import argparse
import shutil
import signal
import sys
from pathlib import Path

from config import load_config, load_pending_tasks, mark_task_completed, validate_config
from utils import setup_logging, new_run_id, log

PROJECT_ROOT = Path(__file__).parent.parent
OUTPUT_DIR = PROJECT_ROOT / "output"
TASKS_DIR = PROJECT_ROOT / "tasks"


async def create_client():
    """Create and start a GHCP SDK CopilotClient."""
    from copilot import CopilotClient
    client = CopilotClient({"cwd": str(PROJECT_ROOT)})
    await client.start()
    return client


async def run_stage(client, config: dict, stage: str):
    """Run a single pipeline stage."""
    if stage == "monitor":
        from monitor import run_monitoring_cycle
        await run_monitoring_cycle(client, config)
    elif stage == "digest":
        from digest import run_digest
        await run_digest(client, config)
    elif stage == "transcripts":
        from transcripts import run_transcript_collection
        await run_transcript_collection(client, config)
    elif stage == "intel":
        from intel import run_intel
        await run_intel(client, config)


async def run_single_research(client, config: dict, task: dict):
    """Run a single research task."""
    from tools import get_tools
    from utils import agent_session

    task_name = task.get("task", "unnamed")
    description = task.get("description", task_name)
    output_config = task.get("output", {})
    local_path = output_config.get("local", "./output/")

    log.info(f"  Research mission: {task_name}")

    async with agent_session(client, config, "research", tools=get_tools()) as session:
        prompt = f"""Execute this research mission:

## Task
{task_name}

## Description
{description}

## Output
Write all findings and deliverables to: {local_path}
Use markdown format. Create one file per logical section if the output is large.
When complete, provide a summary of your research and key findings.
"""
        response = await session.send_and_wait({"prompt": prompt}, timeout=3600)
        if not response:
            log.warning("  No response from agent (may have timed out).")


async def run_chat_query(client, config: dict, prompt: str,
                        telegram_app=None, chat_id: int | None = None) -> str:
    """Run a conversational query via GHCP SDK. Returns the response text."""
    from tools import get_tools
    from utils import agent_session

    log.info(f"  Chat query: {prompt[:80]}...")

    async with agent_session(client, config, "chat", tools=get_tools(),
                             telegram_app=telegram_app, chat_id=chat_id) as session:
        response = await session.send_and_wait({"prompt": prompt}, timeout=180)
        if response and response.data and response.data.content:
            return response.data.content
        return "No response from agent."


async def job_worker(client, config: dict, job_queue: asyncio.Queue, telegram_app):
    """Process jobs from the queue as they arrive."""
    from telegram_bot import notify

    while True:
        job = await job_queue.get()
        job_type = job.get("type", "unknown")
        chat_id = job.get("_chat_id")
        job_name = job.get("task", job_type)

        log.info(f"=== Job: [{job_type}] {job_name} ===")

        try:
            if job_type == "chat":
                # Conversational query — respond directly
                prompt = job.get("prompt", "")
                reply = await run_chat_query(client, config, prompt,
                                             telegram_app=telegram_app, chat_id=chat_id)
                if chat_id:
                    await notify(telegram_app, chat_id, reply)

            elif job_type == "research":
                await run_single_research(client, config, job)
                # If it came from a file-based job, mark completed
                if "_file" in job:
                    mark_task_completed(job)
                if chat_id:
                    await notify(telegram_app, chat_id, f"Research complete: {job_name}")

            elif job_type in ("digest", "monitor", "transcripts", "intel"):
                await run_stage(client, config, job_type)
                if "_file" in job:
                    mark_task_completed(job)
                if chat_id:
                    if job_type == "digest":
                        from telegram_bot import send_latest_digest
                        await notify(telegram_app, chat_id, "Digest complete:")
                        await send_latest_digest(chat_id, telegram_app)
                    elif job_type == "monitor":
                        # Send the latest monitoring report
                        report = _get_latest_monitoring_report()
                        if report:
                            await notify(telegram_app, chat_id, report)
                    else:
                        label = {"intel": "Intel brief", "transcripts": "Transcripts"}
                        await notify(telegram_app, chat_id, f"{label.get(job_type, job_type)} complete.")

            else:
                log.warning(f"  Unknown job type: {job_type}")

        except Exception as e:
            log.error(f"  Job failed: {job_name} — {e}")
            if chat_id:
                await notify(telegram_app, chat_id, f"Failed: {job_name}\n{e}")

        finally:
            job_queue.task_done()
            sync_to_onedrive(config)

        log.info(f"=== Job done: [{job_type}] {job_name} ===")


def _get_latest_monitoring_report() -> str | None:
    """Read the most recent monitoring report."""
    reports = sorted(OUTPUT_DIR.glob("monitoring-*.md"), reverse=True)
    if not reports:
        return None
    try:
        content = reports[0].read_text(encoding="utf-8")
        # Truncate for Telegram (keep it useful, not overwhelming)
        if len(content) > 3500:
            content = content[:3500] + "\n\n... (truncated)"
        return content
    except Exception:
        return None


def sync_jobs_from_onedrive(config: dict, job_queue: asyncio.Queue):
    """Pull new job files from OneDrive Jobs/ into tasks/pending/ and enqueue them."""
    onedrive_cfg = config.get("onedrive", {})
    if not onedrive_cfg.get("sync_enabled", False):
        return

    dest_root = Path(onedrive_cfg.get("path", ""))
    if not dest_root or str(dest_root) == ".":
        return

    jobs_src = dest_root / "Jobs"
    if not jobs_src.exists():
        jobs_src.mkdir(parents=True, exist_ok=True)
        return

    pending_dir = TASKS_DIR / "pending"
    pending_dir.mkdir(parents=True, exist_ok=True)
    pulled = 0

    for f in jobs_src.glob("*.yaml"):
        dest_file = pending_dir / f.name
        if not dest_file.exists():
            shutil.copy2(f, dest_file)
            pulled += 1

    if pulled:
        log.info(f"Pulled {pulled} new job(s) from OneDrive")

    # Enqueue any pending file-based jobs
    for job in load_pending_tasks():
        job_queue.put_nowait(job)


def sync_to_onedrive(config: dict):
    """Copy output files to OneDrive so M365 Copilot can read them."""
    onedrive_cfg = config.get("onedrive", {})
    if not onedrive_cfg.get("sync_enabled", False):
        return

    dest_root = Path(onedrive_cfg.get("path", ""))
    if not dest_root or str(dest_root) == ".":
        log.warning("OneDrive sync enabled but no path configured")
        return

    synced = 0

    # Sync output subdirectories
    for subdir in ("digests", "intel", "pulse-signals"):
        src = OUTPUT_DIR / subdir
        if not src.exists():
            continue
        dest = dest_root / subdir
        dest.mkdir(parents=True, exist_ok=True)
        for f in src.iterdir():
            if f.is_file() and not f.name.startswith("."):
                dest_file = dest / f.name
                if not dest_file.exists() or f.stat().st_mtime > dest_file.stat().st_mtime:
                    shutil.copy2(f, dest_file)
                    synced += 1

    # Sync monitoring reports
    for f in OUTPUT_DIR.glob("monitoring-*.md"):
        dest_file = dest_root / f.name
        if not dest_file.exists() or f.stat().st_mtime > dest_file.stat().st_mtime:
            shutil.copy2(f, dest_file)
            synced += 1

    # Seed Agent Instructions (local defaults -> OneDrive, never overwrite)
    instructions_src = PROJECT_ROOT / "config" / "instructions"
    instructions_dest = dest_root / "Agent Instructions"
    if instructions_src.exists():
        instructions_dest.mkdir(parents=True, exist_ok=True)
        for f in instructions_src.glob("*.md"):
            dest_file = instructions_dest / f.name
            if not dest_file.exists():
                shutil.copy2(f, dest_file)
                synced += 1

    # Clean up completed jobs from OneDrive Jobs/ folder
    jobs_onedrive = dest_root / "Jobs"
    completed_dir = TASKS_DIR / "completed"
    if jobs_onedrive.exists() and completed_dir.exists():
        for f in list(jobs_onedrive.glob("*.yaml")):
            if (completed_dir / f.name).exists():
                f.unlink()
                synced += 1

    if synced:
        log.info(f"Synced {synced} file(s) to OneDrive: {dest_root}")


def _check_missed_digest(job_queue: asyncio.Queue):
    """Queue a digest if today's or yesterday's digest is missing (catch-up on startup)."""
    from datetime import datetime, timedelta

    digests_dir = OUTPUT_DIR / "digests"
    today = datetime.now().strftime("%Y-%m-%d")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    today_exists = (digests_dir / f"{today}.md").exists()
    yesterday_exists = (digests_dir / f"{yesterday}.md").exists()

    if not today_exists or not yesterday_exists:
        from telegram_bot import get_proactive_chat_id
        chat_id = get_proactive_chat_id()
        job_queue.put_nowait({
            "type": "digest",
            "_source": "catch-up",
            "_chat_id": chat_id,
        })
        missing = []
        if not yesterday_exists:
            missing.append(yesterday)
        if not today_exists:
            missing.append(today)
        log.info(f"Catch-up: digest missing for {', '.join(missing)} — queued")
    else:
        log.info("Digest up to date (today + yesterday exist)")


def _is_office_hours(config: dict) -> bool:
    """Check if current time is within configured office hours."""
    from datetime import datetime
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


async def heartbeat(config: dict, job_queue: asyncio.Queue, shutdown_event: asyncio.Event):
    """Periodic heartbeat — enqueues triage during office hours + pulls OneDrive jobs."""
    from telegram_bot import get_proactive_chat_id

    interval = config["monitoring"].get("interval", "30m")
    seconds = _parse_interval(interval)

    # Delay first heartbeat so chat messages aren't blocked on startup
    log.info(f"First heartbeat in {interval} (chat messages processed immediately)")
    for _ in range(seconds):
        if shutdown_event.is_set():
            return
        await asyncio.sleep(1)

    while not shutdown_event.is_set():
        # Pull file-based jobs from OneDrive (always, even outside office hours)
        sync_jobs_from_onedrive(config, job_queue)

        # Triage only during office hours
        if _is_office_hours(config):
            chat_id = get_proactive_chat_id()
            job_queue.put_nowait({
                "type": "monitor",
                "_source": "heartbeat",
                "_chat_id": chat_id,
            })
            log.info(f"Heartbeat: triage queued (office hours)")
        else:
            log.info(f"Heartbeat: outside office hours, skipping triage")

        # Sleep until next cycle
        log.info(f"Next heartbeat in {interval}")
        for _ in range(seconds):
            if shutdown_event.is_set():
                return
            await asyncio.sleep(1)


async def main():
    parser = argparse.ArgumentParser(description="Pulse Agent")
    parser.add_argument(
        "--mode",
        choices=["monitor", "digest", "research", "transcripts", "intel"],
        default=None,
        help="Run a specific stage (for dev/debugging). Default: daemon mode.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single cycle then exit (no loop)",
    )
    args = parser.parse_args()

    run_id = new_run_id()
    setup_logging(run_id=run_id)

    try:
        config = load_config()
    except FileNotFoundError:
        log.error("Config not found: config/standing-instructions.yaml")
        sys.exit(1)
    except Exception as e:
        log.error(f"Failed to load config: {e}")
        sys.exit(1)

    warnings = validate_config(config)
    for w in warnings:
        log.warning(f"CONFIG: {w}")

    log.info(f"Pulse Agent starting — run: {run_id}")

    # Start GHCP SDK client
    log.info("Connecting to GitHub Copilot SDK...")
    try:
        client = await create_client()
    except Exception as e:
        log.error(f"Failed to connect to GitHub Copilot SDK: {e}")
        log.error("Make sure the Copilot CLI is installed and you have a valid subscription.")
        sys.exit(1)

    log.info(f"Connected. State: {client.get_state()}")

    # --once --mode X: run a single stage and exit (dev/debugging)
    if args.once and args.mode:
        await run_stage(client, config, args.mode)
        sync_to_onedrive(config)
        await client.stop()
        return

    # --once (no mode): run one triage + pending jobs and exit
    if args.once:
        job_queue = asyncio.Queue()
        job_queue.put_nowait({"type": "monitor", "_source": "cli"})
        sync_jobs_from_onedrive(config, job_queue)
        while not job_queue.empty():
            job = job_queue.get_nowait()
            job_type = job.get("type", "unknown")
            job_name = job.get("task", job_type)
            log.info(f"Running: [{job_type}] {job_name}")
            if job_type == "research":
                await run_single_research(client, config, job)
                if "_file" in job:
                    mark_task_completed(job)
            elif job_type in ("digest", "monitor", "transcripts", "intel"):
                await run_stage(client, config, job_type)
                if "_file" in job:
                    mark_task_completed(job)
        sync_to_onedrive(config)
        await client.stop()
        return

    # --- Daemon mode ---
    shutdown_event = asyncio.Event()

    def _signal_handler():
        log.info("Shutdown signal received — finishing current job...")
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            pass  # Windows

    job_queue = asyncio.Queue()

    # Start Telegram bot
    from telegram_bot import start_telegram_bot, stop_telegram_bot
    telegram_app = await start_telegram_bot(config, job_queue)

    # Check for missed digests (runs before worker starts processing)
    _check_missed_digest(job_queue)

    # Start worker and heartbeat
    worker_task = asyncio.create_task(job_worker(client, config, job_queue, telegram_app))
    heartbeat_task = asyncio.create_task(heartbeat(config, job_queue, shutdown_event))

    log.info("Daemon running — Telegram + heartbeat active. Ctrl+C to stop.")

    # Wait for shutdown
    await shutdown_event.wait()

    # Cleanup
    heartbeat_task.cancel()
    worker_task.cancel()
    await stop_telegram_bot(telegram_app)
    await client.stop()
    log.info("Pulse Agent stopped.")


def _parse_interval(interval: str) -> int:
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


if __name__ == "__main__":
    asyncio.run(main())
