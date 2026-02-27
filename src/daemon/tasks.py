"""Background daemon tasks — status writer and TUI chat poller.

Extracted from main.py so they can be imported by the unified entry point
(pulse.py) without pulling in the full CLI/argparse machinery.
"""

import asyncio
import json
from datetime import datetime

from core.constants import PULSE_HOME
from core.logging import log


async def write_daemon_status_loop(
    job_queue: asyncio.Queue,
    boot_time: datetime,
    shutdown_event: asyncio.Event,
) -> None:
    """Write .daemon-status.json every 60s for TUI status bar."""
    status_file = PULSE_HOME / ".daemon-status.json"

    def _write_status():
        uptime_s = int((datetime.now() - boot_time).total_seconds())
        status = {
            "boot_time": boot_time.isoformat(),
            "uptime_s": uptime_s,
            "queue_size": job_queue.qsize(),
            "updated_at": datetime.now().isoformat(),
        }
        status_file.write_text(json.dumps(status), encoding="utf-8")

    # Write immediately so TUI sees "online" right away
    try:
        _write_status()
    except Exception:
        pass

    while not shutdown_event.is_set():
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=60)
        except asyncio.TimeoutError:
            pass
        try:
            _write_status()
        except Exception:
            pass


async def poll_tui_chat_requests(
    client,
    config: dict,
    job_queue: asyncio.Queue,
    shutdown_event: asyncio.Event,
) -> None:
    """Poll .chat-request.json every 5s and enqueue chat jobs for the TUI.

    When the TUI sends a chat request, this picks it up and puts a chat job
    on the queue.  The worker handles it with file-based streaming (on_delta
    writes to .chat-stream.jsonl).
    """
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
