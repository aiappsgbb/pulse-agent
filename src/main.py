"""Pulse Agent — Autonomous Digital Employee

Local daemon entrypoint. Two primary pipelines:
1. Overnight: transcripts → digest (+ RSS + WorkIQ) → research
2. Daytime: lightweight monitoring loop via WorkIQ
"""

import asyncio
import argparse
import shutil
import signal
import sys
from pathlib import Path

from config import load_config, validate_config
from utils import setup_logging, new_run_id, log

PROJECT_ROOT = Path(__file__).parent.parent
OUTPUT_DIR = PROJECT_ROOT / "output"


async def create_client():
    """Create and start a GHCP SDK CopilotClient."""
    from copilot import CopilotClient
    client = CopilotClient()
    await client.start()
    return client


async def run_cycle(client, config: dict, mode: str):
    """Run a single cycle for the given mode."""
    if mode == "overnight":
        from transcripts import run_transcript_collection
        from digest import run_digest
        from researcher import run_pending_tasks
        await run_transcript_collection(client, config)
        await run_digest(client, config)
        await run_pending_tasks(client, config)
    elif mode == "monitor":
        from monitor import run_monitoring_cycle
        await run_monitoring_cycle(client, config)
    elif mode == "digest":
        from digest import run_digest
        await run_digest(client, config)
    elif mode == "research":
        from researcher import run_pending_tasks
        await run_pending_tasks(client, config)
    elif mode == "transcripts":
        from transcripts import run_transcript_collection
        await run_transcript_collection(client, config)
    elif mode == "intel":
        from intel import run_intel
        await run_intel(client, config)


def sync_to_onedrive(config: dict):
    """Copy output files to OneDrive so M365 Copilot can read them."""
    onedrive_cfg = config.get("onedrive", {})
    if not onedrive_cfg.get("sync_enabled", False):
        return

    dest_root = Path(onedrive_cfg.get("path", ""))
    if not dest_root or str(dest_root) == ".":
        log.warning("OneDrive sync enabled but no path configured")
        return

    # Sync each output subdirectory
    synced = 0
    for subdir in ("digests", "intel", "pulse-signals"):
        src = OUTPUT_DIR / subdir
        if not src.exists():
            continue
        dest = dest_root / subdir
        dest.mkdir(parents=True, exist_ok=True)
        for f in src.iterdir():
            if f.is_file() and not f.name.startswith("."):
                dest_file = dest / f.name
                # Only copy if newer or missing
                if not dest_file.exists() or f.stat().st_mtime > dest_file.stat().st_mtime:
                    shutil.copy2(f, dest_file)
                    synced += 1

    # Also sync monitoring reports from output/ root
    for f in OUTPUT_DIR.glob("monitoring-*.md"):
        dest_file = dest_root / f.name
        if not dest_file.exists() or f.stat().st_mtime > dest_file.stat().st_mtime:
            shutil.copy2(f, dest_file)
            synced += 1

    # Seed Agent Instructions to OneDrive (local defaults → OneDrive, never overwrite)
    instructions_src = PROJECT_ROOT / "config" / "instructions"
    instructions_dest = dest_root / "Agent Instructions"
    if instructions_src.exists():
        instructions_dest.mkdir(parents=True, exist_ok=True)
        for f in instructions_src.glob("*.md"):
            dest_file = instructions_dest / f.name
            if not dest_file.exists():
                # Only seed defaults — never overwrite user edits
                shutil.copy2(f, dest_file)
                synced += 1

    if synced:
        log.info(f"Synced {synced} file(s) to OneDrive: {dest_root}")


async def main():
    parser = argparse.ArgumentParser(description="Pulse Agent")
    parser.add_argument(
        "--mode",
        choices=["overnight", "monitor", "digest", "research", "transcripts", "intel"],
        default="overnight",
        help=(
            "overnight: full pipeline (transcripts, digest, research). "
            "monitor: lightweight daytime triage. "
            "digest/research/transcripts/intel: run a single stage."
        ),
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single cycle then exit (no loop)",
    )
    args = parser.parse_args()

    # Set up structured logging with a unique run ID
    run_id = new_run_id()
    setup_logging(run_id=run_id)

    # Load config with validation
    try:
        config = load_config()
    except FileNotFoundError:
        log.error("Config not found: config/standing-instructions.yaml")
        log.error("Copy config/standing-instructions.example.yaml and fill in your details.")
        sys.exit(1)
    except Exception as e:
        log.error(f"Failed to load config: {e}")
        sys.exit(1)

    # Validate config — warn but don't block
    warnings = validate_config(config)
    for w in warnings:
        log.warning(f"CONFIG: {w}")

    log.info(f"Pulse Agent starting — mode: {args.mode}, run: {run_id}")

    # Start GHCP SDK client
    log.info("Connecting to GitHub Copilot SDK...")
    try:
        client = await create_client()
    except Exception as e:
        log.error(f"Failed to connect to GitHub Copilot SDK: {e}")
        log.error("Make sure the Copilot CLI is installed and you have a valid subscription.")
        sys.exit(1)

    log.info(f"Connected. State: {client.get_state()}")

    # Graceful shutdown handler
    shutdown_event = asyncio.Event()

    def _signal_handler():
        log.info("Shutdown signal received — finishing current cycle...")
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler for SIGTERM
            pass

    try:
        if args.once:
            await run_cycle(client, config, args.mode)
            sync_to_onedrive(config)
            return

        # Continuous daemon loop
        while not shutdown_event.is_set():
            await run_cycle(client, config, args.mode)
            sync_to_onedrive(config)

            interval = config["monitoring"].get("interval", "30m")
            seconds = _parse_interval(interval)
            log.info(f"Sleeping {interval} until next cycle...")

            # Sleep in 1-second chunks so shutdown signal is responsive
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
