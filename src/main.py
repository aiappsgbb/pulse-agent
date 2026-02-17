"""Pulse Agent — Autonomous Digital Employee

Always-on daemon that runs on a heartbeat (default 30min).
Each cycle: triage (WorkIQ) + process pending jobs + sync to OneDrive.

Jobs are YAML files in tasks/pending/ (synced to OneDrive).
Drop a job file to trigger digest, research, transcripts, or intel.
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
    client = CopilotClient()
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
    """Run a single research task (not the whole queue)."""
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


async def process_jobs(client, config: dict):
    """Process all pending jobs from the queue.

    Job types: digest, research, transcripts, intel.
    Each job is a YAML file in tasks/pending/.
    """
    jobs = load_pending_tasks()
    if not jobs:
        return

    log.info(f"Found {len(jobs)} pending job(s)")

    for job in jobs:
        job_type = job.get("type", "research")
        job_name = job.get("task", job.get("type", "unnamed"))
        log.info(f"  Processing job: [{job_type}] {job_name}")

        try:
            if job_type == "research":
                await run_single_research(client, config, job)
            elif job_type in ("digest", "transcripts", "intel"):
                await run_stage(client, config, job_type)
            else:
                log.warning(f"  Unknown job type: {job_type} — skipping")
                continue

            mark_task_completed(job)
            log.info(f"  Job complete: {job_name}")
        except Exception as e:
            log.error(f"  Job failed: {job_name} — {e}")


def sync_jobs_from_onedrive(config: dict):
    """Pull new job files from OneDrive Jobs/ into local tasks/pending/."""
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

    # Graceful shutdown
    shutdown_event = asyncio.Event()

    def _signal_handler():
        log.info("Shutdown signal received — finishing current cycle...")
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            pass  # Windows

    try:
        # --once --mode X: run a single stage and exit (dev/debugging)
        if args.once and args.mode:
            await run_stage(client, config, args.mode)
            sync_to_onedrive(config)
            return

        # --once (no mode): run one daemon cycle and exit
        if args.once:
            sync_jobs_from_onedrive(config)
            await run_stage(client, config, "monitor")
            await process_jobs(client, config)
            sync_to_onedrive(config)
            return

        # Default: daemon mode — triage + jobs every interval
        interval = config["monitoring"].get("interval", "30m")
        seconds = _parse_interval(interval)
        log.info(f"Daemon mode — cycle every {interval} (triage + pending jobs)")

        while not shutdown_event.is_set():
            sync_jobs_from_onedrive(config)
            await run_stage(client, config, "monitor")
            await process_jobs(client, config)
            sync_to_onedrive(config)

            log.info(f"Sleeping {interval} until next cycle...")
            for _ in range(seconds):
                if shutdown_event.is_set():
                    break
                await asyncio.sleep(1)

    finally:
        log.info("Shutting down Pulse Agent...")
        await client.stop()


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
