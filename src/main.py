"""Pulse Agent — Autonomous Digital Employee

Local daemon entrypoint. Runs two modes:
1. Always-on monitoring loop (standing instructions)
2. Deep research mission runner (task queue)
"""

import asyncio
import argparse
from copilot import CopilotClient

from config import load_config
from monitor import run_monitoring_cycle
from researcher import run_pending_tasks
from transcripts import run_transcript_collection


async def create_client() -> CopilotClient:
    """Create and start a GHCP SDK CopilotClient."""
    client = CopilotClient()
    await client.start()
    return client


async def main():
    parser = argparse.ArgumentParser(description="Pulse Agent")
    parser.add_argument(
        "--mode",
        choices=["monitor", "research", "transcripts", "both"],
        default="both",
        help="Run mode: monitor, research, transcripts, or both (monitor+research)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single cycle then exit (no loop)",
    )
    args = parser.parse_args()

    config = load_config()
    owner = config["owner"]["name"]
    print(f"Pulse Agent starting — mode: {args.mode}, owner: {owner}")

    # Start GHCP SDK client
    print("Connecting to GitHub Copilot SDK...")
    client = await create_client()
    print(f"Connected. State: {client.get_state()}")

    try:
        if args.once:
            if args.mode in ("monitor", "both"):
                await run_monitoring_cycle(client, config)
            if args.mode in ("research", "both"):
                await run_pending_tasks(client, config)
            if args.mode == "transcripts":
                await run_transcript_collection(client, config)
            return

        # Continuous daemon loop
        while True:
            if args.mode in ("monitor", "both"):
                await run_monitoring_cycle(client, config)
            if args.mode in ("research", "both"):
                await run_pending_tasks(client, config)
            if args.mode == "transcripts":
                await run_transcript_collection(client, config)

            interval = config["monitoring"].get("interval", "30m")
            seconds = _parse_interval(interval)
            print(f"\nSleeping {interval} until next cycle...")
            await asyncio.sleep(seconds)

    finally:
        print("Shutting down Pulse Agent...")
        await client.stop()


def _parse_interval(interval: str) -> int:
    """Parse interval string like '30m', '1h', '5m' to seconds."""
    interval = interval.strip().lower()
    if interval.endswith("h"):
        return int(interval[:-1]) * 3600
    if interval.endswith("m"):
        return int(interval[:-1]) * 60
    if interval.endswith("s"):
        return int(interval[:-1])
    return int(interval)


if __name__ == "__main__":
    asyncio.run(main())
