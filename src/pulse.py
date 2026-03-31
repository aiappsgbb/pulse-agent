"""Pulse Agent — single unified entry point.

Launches both the daemon (scheduler, job worker, SDK client, browser)
and the Textual TUI in one process.

Usage:
    python src/pulse.py              # daemon + TUI (default)
    python src/pulse.py --once       # single cycle, exit (no TUI)
    python src/pulse.py --mode X     # run specific mode, exit (no TUI)
    python src/pulse.py --setup      # force re-run onboarding in chat
    python src/pulse.py --no-tui     # daemon only, headless

Architecture:
    Main thread  → Textual TUI (requires main thread for terminal I/O)
    Daemon thread → asyncio event loop (SDK client, scheduler, worker, browser)
    IPC          → file-based (proven, same as before)
"""

import argparse
import asyncio
import signal
import sys
import threading
from datetime import datetime
from pathlib import Path

# Add src/ to path for clean imports
_src = Path(__file__).parent
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from dotenv import load_dotenv
load_dotenv()


def main():
    parser = argparse.ArgumentParser(description="Pulse Agent")
    parser.add_argument(
        "--mode",
        choices=["monitor", "digest", "research", "transcripts", "intel", "knowledge"],
        default=None,
        help="Run a specific stage (CLI mode, no TUI).",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single cycle then exit (no TUI)",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to standing-instructions YAML",
    )
    parser.add_argument(
        "--setup",
        action="store_true",
        help="Force the onboarding wizard in Chat",
    )
    parser.add_argument(
        "--no-tui",
        action="store_true",
        help="Run daemon without TUI (headless mode)",
    )
    parser.add_argument(
        "--health-check",
        action="store_true",
        help="Run comprehensive installation health check and exit",
    )
    args = parser.parse_args()

    # Set config override before anything calls load_config()
    if args.config:
        import os
        os.environ["PULSE_CONFIG"] = args.config

    # --- Health check mode ---
    if args.health_check:
        asyncio.run(_health_check_main())
        return

    # --- CLI mode: --once and/or --mode → run and exit, no TUI ---
    if args.once or args.mode:
        asyncio.run(_cli_main(args))
        return

    # --- Load config and detect first run ---
    from core.config import load_config
    from core.onboarding import is_first_run

    try:
        config = load_config()
    except FileNotFoundError:
        config = None

    needs_onboarding = args.setup or is_first_run(config)

    # --- Headless daemon mode (--no-tui) ---
    if args.no_tui:
        asyncio.run(_daemon_main_headless())
        return

    # --- Default: daemon + TUI ---
    shutdown_event = threading.Event()

    # Start daemon in background thread
    daemon_thread = threading.Thread(
        target=_run_daemon_thread,
        args=(shutdown_event,),
        daemon=True,
        name="pulse-daemon",
    )
    daemon_thread.start()

    # Run TUI in main thread
    try:
        from tui.app import PulseApp

        app = PulseApp()
        app.needs_onboarding = needs_onboarding
        app.run()
    except Exception as e:
        print(f"TUI error: {e}", file=sys.stderr)
    finally:
        # TUI exited — signal daemon to shut down
        shutdown_event.set()
        daemon_thread.join(timeout=15)
        if daemon_thread.is_alive():
            print("Daemon thread did not exit cleanly.", file=sys.stderr)


# ---------------------------------------------------------------------------
# Health check mode — validates full installation
# ---------------------------------------------------------------------------

async def _health_check_main():
    """Run comprehensive health check, optionally fix browser auth."""
    from core.config import load_config
    from core.diagnostics import (
        run_health_check_async, print_health_report,
        verify_browser_auth, open_browser_for_login,
    )

    try:
        config = load_config()
    except Exception:
        config = None

    print("\nRunning health checks...")
    checks = await run_health_check_async(config)
    print_health_report(checks)

    # If browser auth failed, offer to open browser for login
    browser_auth = next((c for c in checks if c.name == "Browser: Teams auth"), None)
    if browser_auth and not browser_auth.ok:
        print("  Browser authentication is required for transcript collection")
        print("  and inbox scanning. Pulse will open a browser window using")
        print("  its dedicated profile so you can sign into Microsoft Teams.")
        print()
        try:
            answer = input("  Open browser to sign in now? [Y/n] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = "n"

        if answer in ("", "y", "yes"):
            print("\n  Opening browser...")
            await open_browser_for_login()

            # Verify auth worked
            print("  Verifying authentication...")
            result = await verify_browser_auth(headless=True)
            if result["ok"]:
                print("  Authentication successful! Teams loaded correctly.\n")
            elif result["needs_login"]:
                print("  Still not authenticated. Please try again or sign in")
                print("  manually by running: python src/pulse.py --health-check\n")
            else:
                print(f"  Verification error: {result.get('error', 'unknown')}\n")

    # If config needs setup, mention it
    config_check = next((c for c in checks if "Config" in c.name and not c.ok), None)
    if config_check:
        print("  To complete configuration, run: python src/pulse.py --setup")
        print()


# ---------------------------------------------------------------------------
# CLI mode (--once / --mode) — pure asyncio, no TUI, no threads
# ---------------------------------------------------------------------------

async def _cli_main(args):
    """Run a specific mode or single cycle, then exit.

    This is the same flow as the old main.py --once/--mode paths.
    """
    from core.constants import PROJECT_ROOT
    from core.config import load_config, validate_config, mark_task_completed
    from core.logging import setup_logging, new_run_id, log

    run_id = new_run_id()
    setup_logging(run_id=run_id)

    try:
        config = load_config()
    except FileNotFoundError:
        log.error("Config not found")
        sys.exit(1)
    except Exception as e:
        log.exception(f"Failed to load config: {e}")
        sys.exit(1)

    warnings = validate_config(config)
    for w in warnings:
        log.warning(f"CONFIG: {w}")

    from core.diagnostics import run_diagnostics
    for w in run_diagnostics(config):
        log.warning(f"DIAG: {w}")

    log.info(f"Pulse Agent starting — run: {run_id}")

    # Start SDK client
    from copilot import CopilotClient
    try:
        client = CopilotClient({"cwd": str(PROJECT_ROOT)})
        await client.start()
    except Exception as e:
        log.exception(f"Failed to connect to SDK: {e}")
        sys.exit(1)

    log.info(f"Connected. State: {client.get_state()}")

    # Browser is now lazy — starts on first use, auto-stops after idle.

    # --once --mode X: run a single stage
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

    # --once (no mode): run one triage + pending jobs
    elif args.once:
        from sdk.runner import run_job
        from daemon.sync import sync_jobs_from_onedrive, sync_to_onedrive

        from daemon.worker import enqueue_job, dequeue_job
        job_queue = asyncio.PriorityQueue()
        enqueue_job(job_queue, {"type": "monitor", "_source": "cli"}, config)
        sync_jobs_from_onedrive(config, job_queue)
        while not job_queue.empty():
            _pri, _seq, job = job_queue.get_nowait()
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

    # --mode X (no --once): run single stage
    elif args.mode:
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

    # Cleanup — stop lazy browser if it was started
    from core.browser import get_browser_manager
    browser = get_browser_manager()
    if browser:
        await browser.stop()
    try:
        await asyncio.wait_for(client.stop(), timeout=10)
    except asyncio.TimeoutError:
        await client.force_stop()


# ---------------------------------------------------------------------------
# Headless daemon (--no-tui) — same as old main.py daemon mode
# ---------------------------------------------------------------------------

async def _daemon_main_headless():
    """Run the daemon without TUI — old main.py daemon mode."""
    from core.constants import PROJECT_ROOT
    from core.config import load_config, validate_config
    from core.logging import setup_logging, new_run_id, log

    run_id = new_run_id()
    setup_logging(run_id=run_id)

    try:
        config = load_config()
    except Exception as e:
        log.exception(f"Config load failed: {e}")
        sys.exit(1)

    for w in validate_config(config):
        log.warning(f"CONFIG: {w}")

    from core.diagnostics import run_diagnostics
    for w in run_diagnostics(config):
        log.warning(f"DIAG: {w}")

    log.info(f"Pulse daemon (headless) starting — run: {run_id}")

    from copilot import CopilotClient
    try:
        client = CopilotClient({"cwd": str(PROJECT_ROOT)})
        await client.start()
        log.info(f"SDK connected. State: {client.get_state()}")
    except Exception as e:
        log.exception(f"SDK connection failed: {e}")
        sys.exit(1)

    try:
        auth = await client.get_auth_status()
        log.info(f"Auth: {auth}")
    except Exception as e:
        log.warning(f"Auth check failed (non-fatal): {e}")

    # Browser is now lazy — starts on first use, auto-stops after idle.
    # No eager start here. See core/browser.py ensure_browser().

    shutdown_event = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, shutdown_event.set)
        except NotImplementedError:
            pass  # Windows

    boot_time = datetime.now()
    job_queue = asyncio.Queue()

    # Clean up orphaned "running" jobs from previous daemon instance
    from tui.ipc import cleanup_orphaned_jobs
    orphans = cleanup_orphaned_jobs()
    if orphans:
        log.info(f"Cleaned up {orphans} orphaned running job(s) from previous session")

    from daemon.sync import sync_jobs_from_onedrive
    sync_jobs_from_onedrive(config, job_queue)

    from core.scheduler import ensure_default_schedules, scheduler_loop
    ensure_default_schedules(config)

    from daemon.worker import job_worker
    from daemon.tasks import write_daemon_status_loop, poll_tui_chat_requests

    worker_task = asyncio.create_task(job_worker(client, config, job_queue))
    scheduler_task = asyncio.create_task(scheduler_loop(config, job_queue, shutdown_event))
    status_task = asyncio.create_task(write_daemon_status_loop(job_queue, boot_time, shutdown_event))
    chat_poll_task = asyncio.create_task(poll_tui_chat_requests(client, config, shutdown_event))

    log.info("Daemon running (headless). Ctrl+C to stop.")
    await shutdown_event.wait()

    for t in (scheduler_task, worker_task, status_task, chat_poll_task):
        t.cancel()

    from daemon.worker import destroy_chat_session
    await destroy_chat_session()

    # Stop lazy browser if it's running
    from core.browser import get_browser_manager
    browser = get_browser_manager()
    if browser:
        await browser.stop()

    try:
        await asyncio.wait_for(client.stop(), timeout=10)
    except asyncio.TimeoutError:
        log.warning("client.stop() hung — forcing")
        await client.force_stop()

    log.info("Daemon stopped.")


# ---------------------------------------------------------------------------
# Daemon thread (default mode — runs alongside TUI)
# ---------------------------------------------------------------------------

def _run_daemon_thread(shutdown_event: threading.Event):
    """Run the daemon's asyncio event loop in a background thread.

    Redirects stdout/stderr to devnull so neither our logs nor the
    Copilot CLI subprocess output bleed into the Textual TUI.
    """
    import os
    devnull = open(os.devnull, "w")
    sys.stdout = devnull
    sys.stderr = devnull
    try:
        asyncio.run(_daemon_main_threaded(shutdown_event))
    except Exception as e:
        import logging
        logging.getLogger("pulse").error(f"Daemon thread crashed: {e}", exc_info=True)
    finally:
        devnull.close()


async def _daemon_main_threaded(shutdown_event: threading.Event):
    """Daemon's async entry point when running alongside TUI.

    Same as headless mode but uses a threading.Event for cross-thread
    shutdown signaling (bridged to asyncio.Event internally).
    """
    from core.constants import PROJECT_ROOT
    from core.config import load_config, validate_config
    from core.logging import setup_logging, new_run_id, log

    run_id = new_run_id()
    setup_logging(run_id=run_id, console=False)  # No console output — TUI owns the terminal

    try:
        config = load_config()
    except Exception as e:
        log.error(f"Daemon: config load failed: {e}")
        return

    for w in validate_config(config):
        log.warning(f"CONFIG: {w}")

    from core.diagnostics import run_diagnostics
    for w in run_diagnostics(config):
        log.warning(f"DIAG: {w}")

    log.info(f"Pulse daemon starting — run: {run_id}")

    # Start SDK client
    from copilot import CopilotClient
    try:
        client = CopilotClient({"cwd": str(PROJECT_ROOT)})
        await client.start()
        log.info(f"SDK connected. State: {client.get_state()}")
    except Exception as e:
        log.error(f"SDK connection failed: {e} — daemon will wait for shutdown")
        # TUI still works for browsing data
        shutdown_event.wait()
        return

    try:
        auth = await client.get_auth_status()
        log.info(f"Auth: {auth}")
    except Exception as e:
        log.warning(f"Auth check failed (non-fatal): {e}")

    # Browser is now lazy — starts on first use, auto-stops after idle.
    # No eager start here. See core/browser.py ensure_browser().

    # Bridge threading.Event → asyncio.Event
    aio_shutdown = asyncio.Event()

    async def _bridge_shutdown():
        while not shutdown_event.is_set():
            await asyncio.sleep(0.5)
        aio_shutdown.set()

    bridge_task = asyncio.create_task(_bridge_shutdown())

    boot_time = datetime.now()

    # Concurrent worker pool — N workers pulling from one PriorityQueue.
    # max_workers is config-driven (default 2): triage + knowledge can run
    # simultaneously on separate SDK sessions.
    max_workers = config.get("max_workers", 2)
    job_queue = asyncio.PriorityQueue()
    # Stash max_workers on the queue so status writer can display it
    job_queue._max_workers = max_workers  # type: ignore[attr-defined]

    # Clean up orphaned "running" jobs from previous daemon instance
    from tui.ipc import cleanup_orphaned_jobs
    orphans = cleanup_orphaned_jobs()
    if orphans:
        log.info(f"Cleaned up {orphans} orphaned running job(s) from previous session")

    from daemon.sync import sync_jobs_from_onedrive
    sync_jobs_from_onedrive(config, job_queue)

    from core.scheduler import ensure_default_schedules, scheduler_loop
    ensure_default_schedules(config)

    from daemon.worker import job_worker
    from daemon.tasks import write_daemon_status_loop, poll_tui_chat_requests

    # Spawn N worker coroutines — each pulls from the same PriorityQueue
    worker_tasks = []
    for i in range(max_workers):
        t = asyncio.create_task(job_worker(client, config, job_queue, worker_id=i))
        worker_tasks.append(t)
    log.info(f"Spawned {max_workers} worker(s)")

    scheduler_task = asyncio.create_task(scheduler_loop(config, job_queue, aio_shutdown))
    status_task = asyncio.create_task(write_daemon_status_loop(job_queue, boot_time, aio_shutdown))
    chat_poll_task = asyncio.create_task(poll_tui_chat_requests(client, config, aio_shutdown))

    log.info("Daemon running — scheduler active.")

    # Wait for shutdown signal
    await aio_shutdown.wait()

    # Cleanup
    all_tasks = [bridge_task, scheduler_task, status_task, chat_poll_task] + worker_tasks
    for t in all_tasks:
        t.cancel()

    from daemon.worker import destroy_chat_session
    await destroy_chat_session()

    # Stop lazy browser if it's running
    from core.browser import get_browser_manager
    browser = get_browser_manager()
    if browser:
        await browser.stop()

    try:
        await asyncio.wait_for(client.stop(), timeout=10)
    except asyncio.TimeoutError:
        log.warning("client.stop() hung — forcing")
        await client.force_stop()

    log.info("Daemon stopped.")


if __name__ == "__main__":
    main()
