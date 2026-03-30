"""Background daemon tasks — status writer and TUI chat poller.

Extracted from main.py so they can be imported by the unified entry point
(pulse.py) without pulling in the full CLI/argparse machinery.
"""

import asyncio
import json
from datetime import datetime

from core.constants import PULSE_HOME, JOBS_DIR
from core.logging import log


# Shared state — workers write, status writer reads.  Same asyncio loop, no lock needed.
# Maps worker_id -> {"type": str, "started": str, "job_id": str}
active_workers: dict[int, dict] = {}

# Legacy alias — some code still reads current_job["type"].
# Returns the first active worker's info, or empty dict.
current_job: dict = {"type": None, "started": None}


def _sync_current_job():
    """Keep legacy ``current_job`` dict in sync with ``active_workers``."""
    if active_workers:
        first = next(iter(active_workers.values()))
        current_job["type"] = first.get("type")
        current_job["started"] = first.get("started")
    else:
        current_job["type"] = None
        current_job["started"] = None


async def write_daemon_status_loop(
    job_queue,
    boot_time: datetime,
    shutdown_event: asyncio.Event,
) -> None:
    """Write .daemon-status.json every 10s for TUI status bar."""
    status_file = PULSE_HOME / ".daemon-status.json"

    def _count_pending_files() -> int:
        """Count job files on disk not yet enqueued to in-memory queue."""
        pending_dir = JOBS_DIR / "pending"
        if not pending_dir.exists():
            return 0
        try:
            return sum(1 for f in pending_dir.iterdir() if f.suffix in (".yaml", ".yml"))
        except Exception:
            return 0

    def _write_status():
        _sync_current_job()
        uptime_s = int((datetime.now() - boot_time).total_seconds())
        in_memory = job_queue.qsize()
        on_disk = _count_pending_files()
        status = {
            "boot_time": boot_time.isoformat(),
            "uptime_s": uptime_s,
            "queue_size": in_memory + on_disk,
            "updated_at": datetime.now().isoformat(),
            "max_workers": getattr(job_queue, "_max_workers", 2),
        }
        # Show all active workers
        workers = []
        for wid, info in sorted(active_workers.items()):
            workers.append({
                "worker_id": wid,
                "job_type": info.get("type"),
                "started": info.get("started"),
            })
        if workers:
            status["active_workers"] = workers
            # Legacy fields — first active worker
            status["current_job"] = workers[0]["job_type"]
            status["current_job_started"] = workers[0]["started"]
        status_file.write_text(json.dumps(status), encoding="utf-8")

    # Write immediately so TUI sees "online" right away
    try:
        _write_status()
    except Exception:
        pass

    while not shutdown_event.is_set():
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=10)
        except asyncio.TimeoutError:
            pass
        try:
            _write_status()
        except Exception:
            pass


async def poll_tui_chat_requests(
    client,
    config: dict,
    shutdown_event: asyncio.Event,
) -> None:
    """Poll .chat-request.json every 5s and process chat directly.

    Chat runs in its own async task — NEVER blocked by heavy jobs
    (digest, knowledge, transcripts, intel) in the job queue.
    """
    request_file = PULSE_HOME / ".chat-request.json"

    while not shutdown_event.is_set():
        try:
            if request_file.exists():
                data = json.loads(request_file.read_text(encoding="utf-8"))
                prompt = data.get("prompt", "")
                request_id = data.get("request_id", "")
                if prompt:
                    # Delete first so we don't re-process on next poll
                    request_file.unlink(missing_ok=True)
                    log.info(f"TUI chat (fast-lane, id={request_id[:8]}): {prompt[:60]}...")
                    # Process directly — no queue, no waiting
                    await _handle_chat_request(client, config, prompt, request_id)
        except Exception as e:
            log.debug(f"TUI chat poll error: {e}")

        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=5)
        except asyncio.TimeoutError:
            pass


async def _handle_chat_request(client, config: dict, prompt: str, request_id: str):
    """Process a TUI chat request directly (fast-lane, bypasses job queue)."""
    from daemon.worker import run_chat_query, process_pending_actions
    from tui.ipc import write_chat_delta, write_chat_status, finish_chat_stream, clear_chat_stream

    # Onboarding: inject context exactly ONCE per daemon lifetime.
    # Use the canonical flag from worker.py to share state across modules.
    import daemon.worker as _worker
    from core.onboarding import is_first_run
    if not _worker._onboarding_sent and is_first_run(config):
        prompt = _build_onboarding_prompt(config, prompt)
        _worker._onboarding_sent = True

    # File-based streaming for TUI
    clear_chat_stream()

    _delta_written = False

    def _tui_delta(text: str) -> None:
        nonlocal _delta_written
        _delta_written = True
        write_chat_delta(text, request_id)

    def _tui_status(text: str) -> None:
        write_chat_status(text, request_id)

    try:
        result = await run_chat_query(client, config, prompt, on_delta=_tui_delta, on_status=_tui_status)
        # If agent returned text but no deltas were streamed (error or fallback),
        # write the result so the TUI shows it instead of "(no response)"
        if result and not _delta_written:
            write_chat_delta(result + "\n", request_id)
    except Exception as e:
        log.error(f"  Chat fast-lane error: {e}")
        write_chat_delta(f"Error: {e}\n", request_id)
    finally:
        finish_chat_stream(request_id)

    # Process any browser actions the agent queued
    await process_pending_actions()


def _build_onboarding_prompt(config: dict, user_prompt: str) -> str:
    """Build onboarding prompt — same logic as worker._build_onboarding_prompt."""
    import yaml as _yaml
    try:
        from sdk.prompts import load_prompt
        current_config_str = _yaml.dump(config, default_flow_style=False, sort_keys=False)
        onboarding_text = load_prompt(
            "config/prompts/triggers/onboarding.md",
            {"current_config": current_config_str},
        )
        return f"{onboarding_text}\n\nUser: {user_prompt}"
    except Exception as e:
        log.warning(f"Failed to load onboarding prompt: {e}")
        return user_prompt
