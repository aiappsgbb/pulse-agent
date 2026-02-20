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
import signal
import sys

from dotenv import load_dotenv
load_dotenv()

from core.constants import PROJECT_ROOT
from core.config import load_config, validate_config, mark_task_completed
from core.logging import setup_logging, new_run_id, log


async def create_client():
    """Create and start a GHCP SDK CopilotClient."""
    from copilot import CopilotClient
    client = CopilotClient({"cwd": str(PROJECT_ROOT)})
    await client.start()
    return client


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
    parser.add_argument(
        "--config",
        default=None,
        help="Path to standing-instructions YAML (default: config/standing-instructions.yaml). Also settable via PULSE_CONFIG env var.",
    )
    args = parser.parse_args()

    # Set config override before anything calls load_config()
    if args.config:
        import os
        os.environ["PULSE_CONFIG"] = args.config

    run_id = new_run_id()
    setup_logging(run_id=run_id)

    try:
        config = load_config()
    except FileNotFoundError:
        log.error("Config not found: config/standing-instructions.yaml")
        sys.exit(1)
    except Exception as e:
        log.exception(f"Failed to load config: {e}")
        sys.exit(1)

    warnings = validate_config(config)
    for w in warnings:
        log.warning(f"CONFIG: {w}")

    # Startup diagnostics — preflight checks
    from core.diagnostics import run_diagnostics
    diag_warnings = run_diagnostics(config)
    for w in diag_warnings:
        log.warning(f"DIAG: {w}")

    log.info(f"Pulse Agent starting — run: {run_id}")

    # Start GHCP SDK client
    log.info("Connecting to GitHub Copilot SDK...")
    try:
        client = await create_client()
    except Exception as e:
        log.exception(f"Failed to connect to GitHub Copilot SDK: {e}")
        log.error("Make sure the Copilot CLI is installed and you have a valid subscription.")
        sys.exit(1)

    log.info(f"Connected. State: {client.get_state()}")

    # Start shared browser (single Edge instance for all Playwright consumers)
    # Uses dedicated daemon profile (pulse-daemon-profile) to avoid conflicts
    # with the user's Edge or Claude Code's Playwright MCP server.
    from core.browser import BrowserManager
    browser = BrowserManager()
    try:
        await browser.start()
    except Exception as e:
        log.warning(f"Shared browser failed to start: {e} — browser scans will be unavailable")
        browser = None

    # --once --mode X: run a single stage and exit (dev/debugging)
    if args.once and args.mode:
        from daemon.sync import sync_to_onedrive

        if args.mode == "transcripts":
            from collectors.transcripts import run_transcript_collection
            await run_transcript_collection(client, config)
        else:
            from sdk.runner import run_job
            await run_job(client, config, args.mode)
        sync_to_onedrive(config)
        if browser:
            await browser.stop()
        await client.stop()
        return

    # --once (no mode): run one triage + pending jobs and exit
    if args.once:
        from sdk.runner import run_job
        from daemon.sync import sync_jobs_from_onedrive, sync_to_onedrive

        job_queue = asyncio.Queue()
        job_queue.put_nowait({"type": "monitor", "_source": "cli"})
        sync_jobs_from_onedrive(config, job_queue)
        while not job_queue.empty():
            job = job_queue.get_nowait()
            job_type = job.get("type", "unknown")
            job_name = job.get("task", job_type)
            log.info(f"Running: [{job_type}] {job_name}")
            if job_type == "transcripts":
                from collectors.transcripts import run_transcript_collection
                await run_transcript_collection(client, config)
            elif job_type == "research":
                await run_job(client, config, "research", context={"task": job})
            elif job_type in ("digest", "monitor", "intel"):
                await run_job(client, config, job_type)
            else:
                log.warning(f"Unknown job type: {job_type}")
                continue
            if "_file" in job:
                mark_task_completed(job)
        sync_to_onedrive(config)
        if browser:
            await browser.stop()
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
    from tg.bot import start_telegram_bot, stop_telegram_bot
    telegram_app = await start_telegram_bot(config, job_queue)

    # Check for missed digests (runs before worker starts processing)
    from daemon.heartbeat import heartbeat, check_missed_digest
    check_missed_digest(job_queue)

    # Start worker, heartbeat, and scheduler
    from daemon.worker import job_worker
    from core.scheduler import scheduler_loop
    worker_task = asyncio.create_task(job_worker(client, config, job_queue, telegram_app))
    heartbeat_task = asyncio.create_task(heartbeat(config, job_queue, shutdown_event))
    scheduler_task = asyncio.create_task(scheduler_loop(job_queue, shutdown_event))

    log.info("Daemon running — Telegram + heartbeat + scheduler active. Ctrl+C to stop.")

    # Wait for shutdown
    await shutdown_event.wait()

    # Cleanup
    scheduler_task.cancel()
    heartbeat_task.cancel()
    worker_task.cancel()
    await stop_telegram_bot(telegram_app)
    if browser:
        await browser.stop()
    await client.stop()
    log.info("Pulse Agent stopped.")


if __name__ == "__main__":
    asyncio.run(main())
