"""Job worker — processes jobs from the asyncio queue one at a time."""

import asyncio
import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from core.constants import PULSE_HOME, JOBS_DIR, LOGS_DIR
from core.config import mark_task_completed
from core.logging import log
from core.notify import notify_desktop, build_toast_summary
from tui.ipc import write_job_notification, append_job_event


_PROXY_RETRY_DELAY = 300       # 5 minutes between retries
_PROXY_MAX_RETRIES = 48        # 4 hours max (48 × 5 min)
_BROWSER_JOB_TIMEOUT = 180    # 3 minute timeout for Playwright-based jobs


def _write_job_log(log_file: str | None, entry_type: str, **kwargs) -> None:
    """Append a progress entry to the per-job activity log.

    Used for Playwright-based jobs (teams_send, email_reply) that don't
    have an EventHandler writing to the log.
    """
    if not log_file:
        return
    try:
        entry = {"ts": datetime.now().isoformat(), "type": entry_type, **kwargs}
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


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


async def run_chat_query(client, config: dict, prompt: str, on_delta=None, on_status=None) -> str:
    """Run a conversational query via GHCP SDK. Returns the response text.

    Uses a persistent session with infinite_sessions so the SDK maintains
    conversation context natively (auto-compacts when context fills up).
    Auto-retries once on transient errors (fetch failed, MCP timeout).
    """
    global _chat_session

    log.info(f"  Chat query: {prompt[:80]}...")

    for attempt in range(2):  # max 1 retry
        # Get persistent session (creates if needed)
        try:
            session = await _get_chat_session(client, config)
        except Exception as e:
            log.error(f"  Failed to create chat session: {e}")
            if attempt == 0 and _is_transient_error(str(e)):
                log.info("  Transient error — retrying with fresh session...")
                _chat_session = None
                await asyncio.sleep(2)
                continue
            return f"Agent error: could not create session — {e}"

        # Each message gets its own handler for completion tracking
        from sdk.event_handler import EventHandler
        handler = EventHandler(on_delta=on_delta, on_status=on_status)
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
            if attempt == 0 and _is_transient_error(str(e)):
                log.info("  Transient error — retrying with fresh session...")
                await asyncio.sleep(2)
                continue
            return f"Agent error: {e}"
        finally:
            if unsub:
                try:
                    unsub()
                except Exception:
                    pass

        if handler.error:
            error_str = str(handler.error)
            log.error(f"Chat session error: {error_str}")
            # If session errored, destroy so next message gets a fresh one
            _chat_session = None
            try:
                await session.destroy()
            except Exception:
                pass
            if attempt == 0 and _is_transient_error(error_str):
                log.info("  Transient error — retrying with fresh session...")
                await asyncio.sleep(2)
                continue
            return f"Agent error: {handler.error}"

        return handler.final_text or "No response from agent."

    return "Agent error: all retries exhausted"


def _is_transient_error(error: str) -> bool:
    """Check if an error is transient and worth retrying."""
    transient_patterns = [
        "fetch failed",           # MCP server HTTP fetch failed
        "Something went wrong",   # Generic Copilot CLI transient error
        "Request timed out",      # MCP tool execution timeout
        "ECONNREFUSED",           # MCP server not running
        "ECONNRESET",             # Connection dropped
        "ProxyResponseError",     # Proxy/firewall 502
        "Session not found",      # Server-side session expired/evicted
    ]
    return any(p.lower() in error.lower() for p in transient_patterns)


async def job_worker(client, config: dict, job_queue: asyncio.Queue):
    """Process jobs from the queue as they arrive.

    Uses an asyncio.Lock to prevent concurrent SDK access (safety net
    even though the queue is processed sequentially).
    """
    from sdk.runner import run_job

    lock = asyncio.Lock()

    # Shared state for TUI status bar — imported from tasks.py
    from daemon.tasks import current_job

    while True:
        job = await job_queue.get()
        job_type = job.get("type", "unknown")
        job_name = job.get("task", job_type)
        job_file = job.get("_file")

        # Generate unique job ID and per-job activity log
        job_id = job.get("_job_id") or f"{job_type}-{datetime.now().strftime('%H%M%S')}-{uuid.uuid4().hex[:6]}"
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        job_log_file = str(LOGS_DIR / f"job-{job_id}.jsonl")

        log.info(f"=== Job: [{job_type}] {job_name} (id={job_id}) ===")

        # Track current job for status bar
        current_job["type"] = job_type
        current_job["started"] = datetime.now().isoformat()
        current_job["job_id"] = job_id

        append_job_event(job_id, job_type, "running", job_name, log_file=job_log_file)

        try:
            async with lock:
                if job_type == "research":
                    context = {"task": job}
                    await run_job(client, config, "research", context=context, job_log_file=job_log_file)
                    if "_file" in job:
                        mark_task_completed(job)
                    notify_desktop("Pulse — Research", f"Research complete: {job_name}")
                    write_job_notification("research", f"Research complete: {job_name}")

                elif job_type == "transcripts":
                    # Standalone mode — no SDK session, uses Playwright directly
                    from collectors.transcripts import run_transcript_collection
                    await run_transcript_collection(client, config)
                    if "_file" in job:
                        mark_task_completed(job)
                    notify_desktop("Pulse — Transcripts", "Transcript collection complete.")
                    write_job_notification("transcripts", "Transcript collection complete.")

                elif job_type == "knowledge":
                    # Pipeline mode — archive + per-project enrichment sessions
                    from sdk.runner import run_knowledge_pipeline
                    await run_knowledge_pipeline(client, config, job_log_file=job_log_file)
                    if "_file" in job:
                        mark_task_completed(job)
                    notify_desktop("Pulse — Knowledge", "Knowledge mining complete.")
                    write_job_notification("knowledge", "Knowledge mining complete.")

                elif job_type in ("digest", "monitor", "intel"):
                    await run_job(client, config, job_type, job_log_file=job_log_file)
                    if "_file" in job:
                        mark_task_completed(job)
                    toast_title, toast_body = build_toast_summary(job_type, PULSE_HOME)
                    urgency = "urgent" if job_type == "monitor" else "normal"
                    notify_desktop(toast_title, toast_body, urgency=urgency)
                    write_job_notification(job_type, toast_body)

                    # Post-triage auto-sweep: mark FYI/low items as read
                    if job_type == "monitor":
                        sweep_cfg = config.get("monitoring", {}).get("sweep", {})
                        if sweep_cfg.get("enabled", False):
                            try:
                                sweep_result = await asyncio.wait_for(
                                    _execute_inbox_sweep(config, full_sweep=False),
                                    timeout=_BROWSER_JOB_TIMEOUT,
                                )
                                summary = sweep_result.get("summary", "Sweep done")
                                log.info(f"  Auto-sweep: {summary}")
                            except Exception as e:
                                log.warning(f"  Auto-sweep failed: {e}")

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
                    recipient = job.get("chat_name") or job.get("recipient") or ""
                    _write_job_log(job_log_file, "tool_start", tool="teams_send", target=recipient)
                    try:
                        result = await asyncio.wait_for(
                            _execute_teams_send(job), timeout=_BROWSER_JOB_TIMEOUT
                        )
                    except asyncio.TimeoutError:
                        result = {"success": False, "detail": f"Timed out after {_BROWSER_JOB_TIMEOUT}s"}
                    ok = result.get("success")
                    status = "Sent" if ok else "Failed"
                    detail = result.get("detail", "")
                    _write_job_log(job_log_file, "tool_result", tool="teams_send", status=status, detail=detail)
                    log.info(f"  Teams {status}: {detail}")
                    if "_file" in job:
                        mark_task_completed(job)
                    summary = f"Teams reply {status.lower()}: {recipient}" + (f" — {detail}" if not ok else "")
                    notify_desktop("Pulse — Teams Reply", summary)
                    write_job_notification("teams_send", summary)

                elif job_type == "email_reply":
                    query = job.get("search_query", "")
                    _write_job_log(job_log_file, "tool_start", tool="email_reply", target=query)
                    try:
                        result = await asyncio.wait_for(
                            _execute_email_reply(job), timeout=_BROWSER_JOB_TIMEOUT
                        )
                    except asyncio.TimeoutError:
                        result = {"success": False, "detail": f"Timed out after {_BROWSER_JOB_TIMEOUT}s"}
                    ok = result.get("success")
                    status = "Sent" if ok else "Failed"
                    detail = result.get("detail", "")
                    _write_job_log(job_log_file, "tool_result", tool="email_reply", status=status, detail=detail)
                    log.info(f"  Email reply {status}: {detail}")
                    if "_file" in job:
                        mark_task_completed(job)
                    summary = f"Email reply {status.lower()}: {query}" + (f" — {detail}" if not ok else "")
                    notify_desktop("Pulse — Email Reply", summary)
                    write_job_notification("email_reply", summary)

                elif job_type == "inbox_sweep":
                    full = job.get("full_sweep", False)
                    _write_job_log(job_log_file, "tool_start", tool="inbox_sweep", full_sweep=full)
                    try:
                        result = await asyncio.wait_for(
                            _execute_inbox_sweep(config, full_sweep=full),
                            timeout=_BROWSER_JOB_TIMEOUT,
                        )
                    except asyncio.TimeoutError:
                        result = {"success": False, "summary": f"Timed out after {_BROWSER_JOB_TIMEOUT}s"}
                    summary = result.get("summary", "Sweep complete")
                    _write_job_log(job_log_file, "tool_result", tool="inbox_sweep", summary=summary)
                    log.info(f"  {summary}")
                    if "_file" in job:
                        mark_task_completed(job)
                    notify_desktop("Pulse — Inbox Sweep", summary)
                    write_job_notification("inbox_sweep", summary)

                elif job_type == "mark_read_teams":
                    chat_name = job.get("chat_name", "")
                    _write_job_log(job_log_file, "tool_start", tool="mark_read_teams", target=chat_name)
                    try:
                        from collectors.teams_marker import mark_teams_chats_read
                        result = await asyncio.wait_for(
                            mark_teams_chats_read([chat_name]),
                            timeout=_BROWSER_JOB_TIMEOUT,
                        )
                    except asyncio.TimeoutError:
                        result = {"success": False, "marked": 0, "details": ["Timed out"]}
                    ok = result.get("marked", 0) > 0
                    status = "Done" if ok else "Failed"
                    _write_job_log(job_log_file, "tool_result", tool="mark_read_teams", status=status)
                    if "_file" in job:
                        mark_task_completed(job)
                    summary = f"Teams mark-read {status.lower()}: {chat_name}"
                    notify_desktop("Pulse — Mark Read", summary)
                    write_job_notification("mark_read_teams", summary)

                elif job_type == "mark_read_outlook":
                    sender = job.get("sender", "")
                    _write_job_log(job_log_file, "tool_start", tool="mark_read_outlook", target=sender)
                    try:
                        from collectors.outlook_marker import mark_outlook_emails_read
                        result = await asyncio.wait_for(
                            mark_outlook_emails_read([{
                                "conv_id": job.get("conv_id", ""),
                                "sender": sender,
                                "subject": job.get("subject", ""),
                            }]),
                            timeout=_BROWSER_JOB_TIMEOUT,
                        )
                    except asyncio.TimeoutError:
                        result = {"success": False, "marked": 0, "details": ["Timed out"]}
                    ok = result.get("marked", 0) > 0
                    status = "Done" if ok else "Failed"
                    _write_job_log(job_log_file, "tool_result", tool="mark_read_outlook", status=status)
                    if "_file" in job:
                        mark_task_completed(job)
                    summary = f"Outlook mark-read {status.lower()}: {sender}"
                    notify_desktop("Pulse — Mark Read", summary)
                    write_job_notification("mark_read_outlook", summary)

                elif job_type == "housekeeping":
                    from core.housekeeping import run_housekeeping
                    result = run_housekeeping(config)
                    total = sum(result.values())
                    if "_file" in job:
                        mark_task_completed(job)
                    summary = f"Housekeeping: cleaned {total} items"
                    log.info(f"  {summary}")
                    write_job_notification("housekeeping", summary)

                else:
                    log.warning(f"  Unknown job type: {job_type}")

            # Record success
            append_job_event(job_id, job_type, "completed", job_name, log_file=job_log_file)

        except Exception as e:
            append_job_event(job_id, job_type, "failed", str(e)[:200], log_file=job_log_file)
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
                # Notify user of failure via TUI and desktop toast
                try:
                    write_job_notification(job_type, f"FAILED: {str(e)[:100]}")
                except Exception:
                    pass
                try:
                    notify_desktop("Pulse — Job Failed", f"{job_name}: {str(e)[:80]}")
                except Exception:
                    pass
                # Reset schedule so it retries instead of waiting until next day
                schedule_id = job.get("_schedule_id")
                if schedule_id:
                    from core.scheduler import reset_run
                    reset_run(schedule_id)

        finally:
            current_job["type"] = None
            current_job["started"] = None
            current_job["job_id"] = None
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

    Dedup: tracks (type, target, message) to skip duplicate sends within
    a single batch — prevents the LLM calling the tool multiple times
    from sending the same message repeatedly.
    """
    from sdk.tools import PENDING_ACTIONS_DIR

    if not PENDING_ACTIONS_DIR.exists():
        return

    action_files = sorted(PENDING_ACTIONS_DIR.glob("*.json"))
    if not action_files:
        return

    log.info(f"Processing {len(action_files)} pending browser action(s)...")
    seen: set[tuple[str, str, str]] = set()  # (type, target, message_hash)

    for action_file in action_files:
        try:
            action = json.loads(action_file.read_text(encoding="utf-8"))
            action_type = action.get("type", "")
            message = action.get("message", "").strip()

            # Build dedup key
            if action_type == "teams_send":
                target = (action.get("chat_name") or action.get("recipient", "")).lower()
            elif action_type == "email_reply":
                target = action.get("search_query", "").lower()
            else:
                target = ""

            dedup_key = (action_type, target, message)
            if dedup_key in seen:
                log.warning(f"  Skipping duplicate {action_type} to {target}")
                continue
            seen.add(dedup_key)

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


async def _execute_inbox_sweep(config: dict, full_sweep: bool = False) -> dict:
    """Execute an inbox sweep — marks items as read in Teams and Outlook."""
    from collectors.sweep import execute_sweep
    return await execute_sweep(config, full_sweep=full_sweep)


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
