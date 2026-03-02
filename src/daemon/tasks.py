"""Background daemon tasks — status writer and TUI chat poller.

Extracted from main.py so they can be imported by the unified entry point
(pulse.py) without pulling in the full CLI/argparse machinery.
"""

import asyncio
import json
from datetime import datetime

from core.constants import PULSE_HOME
from core.logging import log


# Shared state — worker writes, status writer reads.  Same asyncio loop, no lock needed.
current_job: dict = {"type": None, "started": None}


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
        # Include current job info if one is running
        if current_job["type"]:
            status["current_job"] = current_job["type"]
            status["current_job_started"] = current_job["started"]
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
    from tui.ipc import write_chat_delta, finish_chat_stream, clear_chat_stream

    # Onboarding: inject context exactly ONCE per daemon lifetime.
    global _onboarding_sent
    from core.onboarding import is_first_run
    if not _onboarding_sent and is_first_run(config):
        prompt = _build_onboarding_prompt(config, prompt)
        _onboarding_sent = True

    # File-based streaming for TUI
    clear_chat_stream()

    def _tui_delta(text: str) -> None:
        write_chat_delta(text, request_id)

    try:
        await run_chat_query(client, config, prompt, on_delta=_tui_delta)
    except Exception as e:
        log.error(f"  Chat fast-lane error: {e}")
    finally:
        finish_chat_stream(request_id)

    # Process any browser actions the agent queued
    await process_pending_actions()


# Module-level onboarding flag — mirrors the one in worker.py but for the fast-lane
_onboarding_sent = False


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
