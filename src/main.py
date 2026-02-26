"""Pulse Agent — Autonomous Digital Employee

Always-on daemon with TUI + desktop notification interfaces.
Architecture: asyncio.Queue → worker processes jobs immediately.

- Heartbeat puts triage on the queue every 30 minutes
- OneDrive job files are pulled each cycle
- Worker executes jobs one at a time (no SDK concurrency issues)
- TUI chat requests polled every 5s; status file written every 60s
"""

import asyncio
import argparse
import json
import signal
import sys
from datetime import datetime

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
        choices=["monitor", "digest", "research", "transcripts", "intel", "knowledge"],
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

    # Preflight: verify auth is valid before entering the main loop
    try:
        auth = await client.get_auth_status()
        log.info(f"Auth status: {auth}")
    except Exception as e:
        log.warning(f"Auth check failed (non-fatal): {e}")

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
        elif args.mode == "knowledge":
            from sdk.runner import run_knowledge_pipeline
            await run_knowledge_pipeline(client, config)
        else:
            from sdk.runner import run_job
            await run_job(client, config, args.mode)
        sync_to_onedrive(config)
        if browser:
            await browser.stop()
        try:
            await asyncio.wait_for(client.stop(), timeout=10)
        except asyncio.TimeoutError:
            await client.force_stop()
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
        try:
            await asyncio.wait_for(client.stop(), timeout=10)
        except asyncio.TimeoutError:
            await client.force_stop()
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

    boot_time = datetime.now()
    job_queue = asyncio.Queue()

    # Pull any pending jobs from OneDrive (picks up inter-agent requests immediately)
    from daemon.sync import sync_jobs_from_onedrive
    sync_jobs_from_onedrive(config, job_queue)

    # Sync default schedules from config (digest, triage, intel patterns)
    # Catch-up fires naturally: new schedules have last_run=None, so is_due()
    # returns True for any daily schedule whose target time has already passed.
    from core.scheduler import ensure_default_schedules, scheduler_loop
    ensure_default_schedules(config)

    # Start worker and scheduler (scheduler handles all periodic jobs)
    from daemon.worker import job_worker
    worker_task = asyncio.create_task(job_worker(client, config, job_queue))
    scheduler_task = asyncio.create_task(scheduler_loop(config, job_queue, shutdown_event))

    # TUI support: status file writer + chat request poller
    status_task = asyncio.create_task(
        _write_daemon_status_loop(job_queue, boot_time, shutdown_event)
    )
    chat_poll_task = asyncio.create_task(
        _poll_tui_chat_requests(client, config, job_queue, shutdown_event)
    )

    log.info("Daemon running — TUI + scheduler active. Ctrl+C to stop.")

    # Wait for shutdown
    await shutdown_event.wait()

    # Cleanup
    scheduler_task.cancel()
    worker_task.cancel()
    status_task.cancel()
    chat_poll_task.cancel()
    from daemon.worker import destroy_chat_session
    await destroy_chat_session()
    if browser:
        await browser.stop()
    try:
        await asyncio.wait_for(client.stop(), timeout=10)
    except asyncio.TimeoutError:
        log.warning("client.stop() hung — forcing shutdown")
        await client.force_stop()
    log.info("Pulse Agent stopped.")


async def _write_daemon_status_loop(
    job_queue: asyncio.Queue,
    boot_time: datetime,
    shutdown_event: asyncio.Event,
) -> None:
    """Write .daemon-status.json every 60s for TUI status bar."""
    from core.constants import PULSE_HOME

    status_file = PULSE_HOME / ".daemon-status.json"

    while not shutdown_event.is_set():
        try:
            uptime_s = int((datetime.now() - boot_time).total_seconds())
            status = {
                "boot_time": boot_time.isoformat(),
                "uptime_s": uptime_s,
                "queue_size": job_queue.qsize(),
                "updated_at": datetime.now().isoformat(),
            }
            status_file.write_text(json.dumps(status), encoding="utf-8")
        except Exception:
            pass
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=60)
        except asyncio.TimeoutError:
            pass


async def _poll_tui_chat_requests(
    client,
    config: dict,
    job_queue: asyncio.Queue,
    shutdown_event: asyncio.Event,
) -> None:
    """Poll .chat-request.json every 5s and enqueue chat jobs for the TUI.

    When the TUI sends a chat request, this picks it up and puts a chat job
    on the queue. The worker handles it with file-based streaming (on_delta
    writes to .chat-stream.jsonl).
    """
    from core.constants import PULSE_HOME
    from core.logging import log

    request_file = PULSE_HOME / ".chat-request.json"

    while not shutdown_event.is_set():
        try:
            if request_file.exists():
                data = json.loads(request_file.read_text(encoding="utf-8"))
                prompt = data.get("prompt", "")
                request_id = data.get("request_id", "")
                if prompt:
                    # Delete first so TUI doesn't see duplicate on next poll
                    request_file.unlink(missing_ok=True)
                    job_queue.put_nowait({
                        "type": "chat",
                        "prompt": prompt,
                        "_request_id": request_id,
                        "_from_tui": True,
                    })
                    log.info(f"TUI chat request queued (id={request_id[:8]}): {prompt[:60]}...")
        except Exception as e:
            log.debug(f"TUI chat poll error: {e}")

        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=5)
        except asyncio.TimeoutError:
            pass


if __name__ == "__main__":
    asyncio.run(main())
