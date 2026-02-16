"""Pulse Agent — Autonomous Digital Employee

Local daemon entrypoint. Runs two modes:
1. Always-on monitoring loop (standing instructions)
2. Deep research mission runner (task queue)
"""

import asyncio
import argparse
import signal
import sys

from copilot import CopilotClient

from config import load_config, validate_config
from digest import run_digest
from intel import run_intel
from monitor import run_monitoring_cycle
from researcher import run_pending_tasks
from transcripts import run_transcript_collection
from utils import setup_logging, new_run_id, log


async def create_client() -> CopilotClient:
    """Create and start a GHCP SDK CopilotClient."""
    client = CopilotClient()
    await client.start()
    return client


async def run_cycle(client: CopilotClient, config: dict, mode: str):
    """Run a single cycle for the given mode. Shared by --once and daemon loop."""
    if mode in ("monitor", "both"):
        await run_monitoring_cycle(client, config)
    if mode == "digest":
        await run_digest(client, config)
    if mode in ("research", "both"):
        await run_pending_tasks(client, config)
    if mode == "transcripts":
        await run_transcript_collection(client, config)
    if mode == "intel":
        await run_intel(client, config)


async def main():
    parser = argparse.ArgumentParser(description="Pulse Agent")
    parser.add_argument(
        "--mode",
        choices=["monitor", "digest", "research", "transcripts", "intel", "both"],
        default="both",
        help="Run mode: monitor, digest, research, transcripts, intel, or both (monitor+research)",
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

    owner = config["owner"]["name"]
    log.info(f"Pulse Agent starting — mode: {args.mode}, owner: {owner}, run: {run_id}")

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
            return

        # Continuous daemon loop
        while not shutdown_event.is_set():
            await run_cycle(client, config, args.mode)

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
