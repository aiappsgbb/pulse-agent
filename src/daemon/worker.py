"""Job worker — processes jobs from the asyncio queue one at a time."""

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from core.constants import PULSE_HOME, JOBS_DIR
from core.config import mark_task_completed
from core.logging import log
from core.notify import notify_desktop, build_toast_summary


_PROXY_RETRY_DELAY = 300       # 5 minutes between retries
_PROXY_MAX_RETRIES = 48        # 4 hours max (48 × 5 min)


def _requeue_with_delay(job: dict, retry_count: int, delay_seconds: int = _PROXY_RETRY_DELAY):
    """Write a retry job YAML to jobs/pending/ with a retry_after timestamp.

    Strips internal fields (_file, _schedule_id, _chat_id) so the requeued job
    is treated as a clean file-based job picked up by sync_jobs_from_onedrive.
    """
    retry_after = (datetime.now() + timedelta(seconds=delay_seconds)).isoformat()
    retry_job = {k: v for k, v in job.items() if not k.startswith("_")}
    retry_job["_retry_count"] = retry_count
    retry_job["_retry_after"] = retry_after
    retry_job["_retry_reason"] = "ProxyResponseError"

    pending_dir = JOBS_DIR / "pending"
    pending_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    job_type = retry_job.get("type", "job")
    file_path = pending_dir / f"{timestamp}-retry-{job_type}-{retry_count}.yaml"
    with open(file_path, "w") as f:
        yaml.dump(retry_job, f, default_flow_style=False)
    log.info(f"  Retry job written: {file_path.name} (due after {retry_after})")


# --- Persistent chat session (infinite_sessions) ---
# Reused across messages so the SDK maintains conversation context natively.
# Each message gets its own EventHandler for completion tracking.

_chat_session = None
_onboarding_sent = False  # Once True, never re-inject onboarding prompt


async def _get_chat_session(client, config):
    """Get or create the persistent chat session with infinite_sessions enabled."""
    global _chat_session

    if _chat_session is not None:
        return _chat_session

    from sdk.tools import get_tools
    from sdk.session import build_session_config
    from core.browser import get_browser_manager

    mgr = get_browser_manager()
    cdp_endpoint = mgr.cdp_endpoint if mgr else None

    session_config = build_session_config(
        config, mode="chat", tools=get_tools(),
        cdp_endpoint=cdp_endpoint,
    )
    # Enable infinite sessions — SDK auto-compacts context when it fills up
    session_config["infinite_sessions"] = {"enabled": True}

    from sdk.session import MAX_SESSION_RETRIES
    for attempt in range(1, MAX_SESSION_RETRIES + 1):
        try:
            _chat_session = await client.create_session(session_config)
            log.info("  Persistent chat session created (infinite_sessions=True)")
            return _chat_session
        except Exception as e:
            if attempt == MAX_SESSION_RETRIES:
                raise
            log.warning(f"  Chat session creation failed (attempt {attempt}): {e}")
            await asyncio.sleep(2 ** attempt)


async def destroy_chat_session():
    """Destroy the persistent chat session. Called on daemon shutdown."""
    global _chat_session
    if _chat_session is not None:
        try:
            await _chat_session.destroy()
        except Exception:
            pass
        _chat_session = None


async def run_chat_query(client, config: dict, prompt: str, on_delta=None) -> str:
    """Run a conversational query via GHCP SDK. Returns the response text.

    Uses a persistent session with infinite_sessions so the SDK maintains
    conversation context natively (auto-compacts when context fills up).
    """
    global _chat_session

    log.info(f"  Chat query: {prompt[:80]}...")

    # Get persistent session (creates if needed)
    try:
        session = await _get_chat_session(client, config)
    except Exception as e:
        log.error(f"  Failed to create chat session: {e}")
        return f"Agent error: could not create session — {e}"

    # Each message gets its own handler for completion tracking
    from sdk.event_handler import EventHandler
    handler = EventHandler(on_delta=on_delta)
    unsub = session.on(handler)

    try:
        await session.send({"prompt": prompt})
        await asyncio.wait_for(handler.done.wait(), timeout=1800)
    except asyncio.TimeoutError:
        log.warning("Chat query timed out after 1800s")
        return handler.final_text or "Agent is still working — response timed out."
    except Exception as e:
        # Session died — destroy it so next message recreates
        log.warning(f"  Chat session error, will recreate: {e}")
        _chat_session = None
        try:
            await session.destroy()
        except Exception:
            pass
        return f"Agent error: {e}"
    finally:
        if unsub:
            try:
                unsub()
            except Exception:
                pass

    if handler.error:
        log.error(f"Chat session error: {handler.error}")
        # If session errored, destroy so next message gets a fresh one
        _chat_session = None
        try:
            await session.destroy()
        except Exception:
            pass
        return f"Agent error: {handler.error}"

    return handler.final_text or "No response from agent."


async def job_worker(client, config: dict, job_queue: asyncio.Queue):
    """Process jobs from the queue as they arrive.

    Uses an asyncio.Lock to prevent concurrent SDK access (safety net
    even though the queue is processed sequentially).
    """
    from sdk.runner import run_job

    lock = asyncio.Lock()

    while True:
        job = await job_queue.get()
        job_type = job.get("type", "unknown")
        job_name = job.get("task", job_type)
        job_file = job.get("_file")

        log.info(f"=== Job: [{job_type}] {job_name} ===")

        try:
            async with lock:
                if job_type == "chat":
                    prompt = job.get("prompt", "")
                    request_id = job.get("_request_id", "")

                    # Onboarding: inject context exactly ONCE per daemon lifetime.
                    # The SDK session maintains conversation history — follow-up
                    # messages need no special handling. The flag survives session
                    # crashes so we never re-inject even if the session is recreated.
                    global _onboarding_sent
                    from core.onboarding import is_first_run
                    if not _onboarding_sent and (job.get("_onboarding") or is_first_run(config)):
                        prompt = _build_onboarding_prompt(config, prompt)
                        _onboarding_sent = True

                    # File-based streaming for TUI — on_delta writes to .chat-stream.jsonl
                    from tui.ipc import (
                        write_chat_delta, finish_chat_stream, clear_chat_stream,
                    )
                    clear_chat_stream()

                    def _tui_delta(text: str) -> None:
                        write_chat_delta(text, request_id)

                    await run_chat_query(client, config, prompt, on_delta=_tui_delta)
                    finish_chat_stream(request_id)
                    # Mark file-based chat jobs as completed so they aren't re-enqueued
                    if "_file" in job:
                        mark_task_completed(job)
                    # Process any browser actions the agent queued
                    await process_pending_actions()

                elif job_type == "research":
                    context = {"task": job}
                    await run_job(client, config, "research", context=context)
                    if "_file" in job:
                        mark_task_completed(job)
                    notify_desktop("Pulse — Research", f"Research complete: {job_name}")

                elif job_type == "transcripts":
                    # Standalone mode — no SDK session, uses Playwright directly
                    from collectors.transcripts import run_transcript_collection
                    await run_transcript_collection(client, config)
                    if "_file" in job:
                        mark_task_completed(job)
                    notify_desktop("Pulse — Transcripts", "Transcript collection complete.")

                elif job_type == "knowledge":
                    # Pipeline mode — archive + per-project enrichment sessions
                    from sdk.runner import run_knowledge_pipeline
                    await run_knowledge_pipeline(client, config)
                    if "_file" in job:
                        mark_task_completed(job)
                    notify_desktop("Pulse — Knowledge", "Knowledge mining complete.")

                elif job_type in ("digest", "monitor", "intel"):
                    await run_job(client, config, job_type)
                    if "_file" in job:
                        mark_task_completed(job)
                    toast_title, toast_body = build_toast_summary(job_type, PULSE_HOME)
                    urgency = "urgent" if job_type == "monitor" else "normal"
                    notify_desktop(toast_title, toast_body, urgency=urgency)

                elif job_type == "agent_request":
                    result_text = await _handle_agent_request(client, config, job)
                    if "_file" in job:
                        mark_task_completed(job)
                    _write_agent_response(config, job, result_text)

                elif job_type == "agent_response":
                    from_name = job.get("from", "Unknown")
                    original_task = job.get("original_task", "")
                    log.info(f"  Agent response from {from_name} (req: {job.get('request_id', '?')[:8]})")
                    if "_file" in job:
                        mark_task_completed(job)
                    notify_desktop(
                        f"Pulse — Response from {from_name}",
                        f"Re: {original_task[:80]}",
                        urgency="urgent",
                    )

                elif job_type == "teams_send":
                    result = await _execute_teams_send(job)
                    status = "Sent" if result.get("success") else "Failed"
                    log.info(f"  Teams {status}: {result.get('detail', '')}")

                elif job_type == "email_reply":
                    result = await _execute_email_reply(job)
                    status = "Sent" if result.get("success") else "Failed"
                    log.info(f"  Email reply {status}: {result.get('detail', '')}")

                else:
                    log.warning(f"  Unknown job type: {job_type}")

        except Exception as e:
            from sdk.runner import ProxyError
            if isinstance(e, ProxyError):
                retry_count = job.get("_retry_count", 0) + 1
                if retry_count > _PROXY_MAX_RETRIES:
                    log.error(f"  Proxy retry limit reached for {job_name} ({retry_count} attempts)")
                else:
                    _requeue_with_delay(job, retry_count=retry_count)
                    log.warning(f"  Proxy 502 — requeued {job_name} (attempt {retry_count}, retry in 5 min)")
            else:
                log.exception(f"  Job failed: {job_name} — {e}")
                # Reset schedule so it retries instead of waiting until next day
                schedule_id = job.get("_schedule_id")
                if schedule_id:
                    from core.scheduler import reset_run
                    reset_run(schedule_id)

        finally:
            job_queue.task_done()
            if job_file:
                enqueued_files = getattr(job_queue, "_enqueued_files", None)
                if isinstance(enqueued_files, set):
                    enqueued_files.discard(job_file)
            from daemon.sync import sync_to_onedrive
            sync_to_onedrive(config)

        log.info(f"=== Job done: [{job_type}] {job_name} ===")


async def _execute_teams_send(job: dict) -> dict:
    """Execute a Teams message send using the deterministic Playwright sender."""
    from collectors.teams_sender import send_teams_message, reply_to_chat

    recipient = job.get("recipient", "")
    message = job.get("message", "")
    chat_name = job.get("chat_name", "")

    if not message:
        return {"success": False, "detail": "No message provided"}

    if chat_name:
        log.info(f"  Sending Teams reply to chat: {chat_name}")
        return await reply_to_chat(chat_name, message)
    elif recipient:
        log.info(f"  Sending Teams message to: {recipient}")
        return await send_teams_message(recipient, message)
    else:
        return {"success": False, "detail": "No recipient or chat_name provided"}


async def _execute_email_reply(job: dict) -> dict:
    """Execute an email reply using the deterministic Playwright sender."""
    from collectors.outlook_sender import reply_to_email

    search_query = job.get("search_query", "")
    message = job.get("message", "")

    if not message:
        return {"success": False, "detail": "No message provided"}
    if not search_query:
        return {"success": False, "detail": "No search_query provided"}

    log.info(f"  Replying to email matching: {search_query}")
    return await reply_to_email(search_query, message)


async def process_pending_actions():
    """Process any pending browser actions queued by SDK tools.

    Called after each chat session completes. Picks up .json files from
    .pending-actions/ and executes them via the deterministic senders.
    """
    from sdk.tools import PENDING_ACTIONS_DIR

    if not PENDING_ACTIONS_DIR.exists():
        return

    action_files = sorted(PENDING_ACTIONS_DIR.glob("*.json"))
    if not action_files:
        return

    log.info(f"Processing {len(action_files)} pending browser action(s)...")
    for action_file in action_files:
        try:
            action = json.loads(action_file.read_text(encoding="utf-8"))
            action_type = action.get("type", "")

            if action_type == "teams_send":
                result = await _execute_teams_send(action)
            elif action_type == "email_reply":
                result = await _execute_email_reply(action)
            else:
                log.warning(f"  Unknown action type: {action_type}")
                result = {"success": False, "detail": f"Unknown action: {action_type}"}

            status = "OK" if result.get("success") else "FAILED"
            log.info(f"  Action {action_type} {status}: {result.get('detail', '')}")

        except Exception as e:
            log.error(f"  Pending action failed: {e}")
        finally:
            # Always remove the action file (processed or failed)
            try:
                action_file.unlink()
            except Exception:
                pass


async def _handle_agent_request(client, config: dict, job: dict) -> str:
    """Process an incoming agent_request — runs a chat query to answer."""
    from sdk.runner import run_job

    task_text = job.get("task", "")
    from_name = job.get("from", "Unknown")
    kind = job.get("kind", "question")

    log.info(f"  Agent request from {from_name} ({kind}): {task_text[:80]}...")

    if kind == "research":
        context = {"task": job}
        result = await run_job(client, config, "research", context=context)
    else:
        prompt = (
            f"A colleague ({from_name}) sent this request to your agent:\n\n"
            f"**Request ({kind}):** {task_text}\n\n"
            f"Search your local files (transcripts, documents, emails) for relevant "
            f"context and provide a thorough answer. Include specific details from "
            f"meetings and documents if available."
        )
        result = await run_chat_query(client, config, prompt)

    return result or "No response generated."


def _write_agent_response(config: dict, original_job: dict, result_text: str):
    """Write a response YAML to the requesting agent's reply_to path."""
    reply_to = original_job.get("reply_to", "")
    if not reply_to:
        log.warning("  Agent request has no reply_to — cannot send response")
        return

    reply_dir = Path(reply_to)
    if not reply_dir.exists():
        try:
            reply_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            log.error(f"  Cannot create reply_to path: {e}")
            return

    user_cfg = config.get("user", {})
    from_name = user_cfg.get("name", "Unknown")
    from_alias = from_name.lower().split()[0] if from_name else "unknown"

    request_id = original_job.get("request_id", "unknown")
    timestamp = datetime.now().isoformat()
    date_str = datetime.now().strftime("%Y-%m-%d")
    slug = request_id[:8]

    response_data = {
        "type": "agent_response",
        "kind": "response",
        "request_id": request_id,
        "from": from_name,
        "from_alias": from_alias,
        "original_task": original_job.get("task", "")[:200],
        "result": result_text,
        "created_at": timestamp,
    }

    response_file = reply_dir / f"{date_str}-response-{from_alias}-{slug}.yaml"

    with open(response_file, "w") as f:
        yaml.dump(response_data, f, default_flow_style=False)

    log.info(f"  Response written to: {response_file}")


def _build_onboarding_prompt(config: dict, user_prompt: str) -> str:
    """Build onboarding prompt for a fresh chat session.

    Only called when _chat_session is None (fresh start or recreation).
    Always loads the full onboarding template so the agent has complete
    instructions. Follow-up messages on an existing session are passed
    through without modification — the SDK session has conversation history.
    """
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


def get_latest_monitoring_report() -> str | None:
    """Read the most recent monitoring report."""
    reports = sorted(PULSE_HOME.glob("monitoring-*.md"), reverse=True)
    if not reports:
        return None
    try:
        content = reports[0].read_text(encoding="utf-8")
        if len(content) > 3500:
            content = content[:3500] + "\n\n... (truncated)"
        return content
    except Exception:
        log.warning("Failed reading latest monitoring report", exc_info=True)
        return None
