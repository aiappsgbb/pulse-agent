"""Job worker — concurrent worker pool with priority queue.

Multiple workers pull from a single PriorityQueue, each running its own
SDK session.  Urgent jobs (triage, sends) get high priority and are never
blocked by long-running jobs (knowledge enrichment, research).

Knowledge Phase 2 is split into individual ``knowledge-project`` jobs
queued at low priority so they naturally interleave with triage cycles.
"""

import asyncio
import json
import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from core.constants import PULSE_HOME, JOBS_DIR, LOGS_DIR, PROJECTS_DIR
from core.config import mark_task_completed
from core.logging import log
from core.notify import notify_desktop, build_toast_summary
from tui.ipc import write_job_notification, append_job_event


_PROXY_RETRY_DELAY = 300       # 5 minutes between retries
_PROXY_MAX_RETRIES = 48        # 4 hours max (48 × 5 min)
_BROWSER_JOB_TIMEOUT = 180    # 3 minute timeout for Playwright-based jobs

# ---------------------------------------------------------------------------
# Priority queue helpers — lower number = higher priority.
# Default priorities; can be overridden via modes.yaml ``job_priorities:``.
# ---------------------------------------------------------------------------
DEFAULT_JOB_PRIORITIES: dict[str, int] = {
    "monitor": 1,
    "teams_send": 1,
    "email_reply": 1,
    "mark_read_teams": 1,
    "mark_read_outlook": 1,
    "inbox_sweep": 2,
    "digest": 3,
    "intel": 4,
    "agent_request": 4,
    "agent_response": 4,
    "research": 5,
    "transcripts": 6,
    "knowledge-init": 6,
    "housekeeping": 7,
    "knowledge-project": 8,
}
_DEFAULT_PRIORITY = 5

# Monotonic counter — preserves FIFO within the same priority level.
_enqueue_seq = 0

# Resolved priorities (updated on first call to enqueue_job with config).
_resolved_priorities: dict[str, int] | None = None


def _get_priorities(config: dict | None = None) -> dict[str, int]:
    """Return merged job priorities (config overrides defaults)."""
    global _resolved_priorities
    if _resolved_priorities is not None:
        return _resolved_priorities

    merged = dict(DEFAULT_JOB_PRIORITIES)
    if config:
        overrides = config.get("job_priorities", {})
        if isinstance(overrides, dict):
            merged.update(overrides)
    _resolved_priorities = merged
    return merged


def enqueue_job(
    queue: asyncio.PriorityQueue,
    job: dict,
    config: dict | None = None,
) -> None:
    """Put a job onto the priority queue with correct ordering."""
    global _enqueue_seq
    _enqueue_seq += 1
    priorities = _get_priorities(config)
    priority = priorities.get(job.get("type", ""), _DEFAULT_PRIORITY)
    queue.put_nowait((priority, _enqueue_seq, job))


async def dequeue_job(queue: asyncio.PriorityQueue) -> dict:
    """Blocking get — returns the job dict (strips priority wrapper)."""
    _pri, _seq, job = await queue.get()
    return job


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
    from core.browser import ensure_browser

    mgr = await ensure_browser()
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


async def job_worker(client, config: dict, job_queue: asyncio.PriorityQueue, worker_id: int = 0):
    """Single worker coroutine — multiple instances run concurrently.

    Each worker pulls the highest-priority job from the shared queue,
    creates its own SDK session (via run_job), and processes it.  The old
    asyncio.Lock is removed — concurrent SDK sessions are safe (proven by
    the chat fast-lane which already runs alongside the job worker).

    ``knowledge`` jobs are handled specially: Phase 0+1 runs as a single
    ``knowledge-init`` step, then individual ``knowledge-project`` jobs
    are queued at low priority so other workers can interleave triage.
    """
    from sdk.runner import run_job

    tag = f"[W{worker_id}]"

    # Shared state for TUI status bar — imported from tasks.py
    from daemon.tasks import active_workers

    while True:
        job = await dequeue_job(job_queue)
        job_type = job.get("type", "unknown")
        job_name = job.get("task", job_type)
        job_file = job.get("_file")

        # Generate unique job ID and per-job activity log
        job_id = job.get("_job_id") or f"{job_type}-{datetime.now().strftime('%H%M%S')}-{uuid.uuid4().hex[:6]}"
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        job_log_file = str(LOGS_DIR / f"job-{job_id}.jsonl")

        log.info(f"=== {tag} Job: [{job_type}] {job_name} (id={job_id}) ===")

        # Track this worker's current job for status bar
        active_workers[worker_id] = {
            "type": job_type,
            "started": datetime.now().isoformat(),
            "job_id": job_id,
        }

        append_job_event(job_id, job_type, "running", job_name, log_file=job_log_file)

        # Browser-based jobs must serialize — Teams SPA won't load if
        # another tab is already navigating it in the same Edge instance.
        _BROWSER_JOB_TYPES = {
            "teams_send", "email_reply", "inbox_sweep",
            "mark_read_teams", "mark_read_outlook",
            "monitor",  # pre_process scans Teams/Outlook/Calendar via browser
        }
        browser_lock = None
        if job_type in _BROWSER_JOB_TYPES:
            from core.browser import get_browser_use_lock
            browser_lock = get_browser_use_lock()
            await browser_lock.acquire()
            log.info(f"  {tag} Browser lock acquired for {job_type}")

        try:
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
                # Split into knowledge-init + N knowledge-project jobs.
                # Phase 0+1 runs here; Phase 2 projects are queued individually.
                await _run_knowledge_init(client, config, job_queue, job_log_file)
                if "_file" in job:
                    mark_task_completed(job)

            elif job_type == "knowledge-init":
                # Explicit init (from file-based job or re-queue)
                await _run_knowledge_init(client, config, job_queue, job_log_file)
                if "_file" in job:
                    mark_task_completed(job)

            elif job_type == "knowledge-project":
                # Single project enrichment — runs its own SDK session
                project_context = job.get("_context", {})
                pname = project_context.get("project_name", "?")
                log.info(f"  {tag} Enriching: {pname}...")
                try:
                    await asyncio.wait_for(
                        run_job(client, config, "knowledge-project",
                                context=project_context, job_log_file=job_log_file),
                        timeout=600,
                    )
                    log.info(f"  {tag} Done: {pname}")
                except asyncio.TimeoutError:
                    log.warning(f"  {tag} Timeout enriching {pname} (10 min cap)")
                # Check if this was the last project in the batch
                _knowledge_project_done(job)

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
                            summary = sweep_result.get("summary", "Sweep done") if isinstance(sweep_result, dict) else "Sweep done"
                            log.info(f"  Auto-sweep: {summary}")
                        except Exception as e:
                            log.warning(f"  Auto-sweep failed: {e}")

            elif job_type == "agent_request":
                await _handle_agent_request(client, config, job)
                if "_file" in job:
                    mark_task_completed(job)

            elif job_type == "agent_response":
                from_name = job.get("from", "Unknown")
                project_id = job.get("project_id", "")
                log.info(f"  Agent response from {from_name} (project: {project_id or 'n/a'}, req: {str(job.get('request_id') or '?')[:8]})")
                _ingest_agent_response(job)
                if "_file" in job:
                    mark_task_completed(job)
                if job.get("status") == "answered":
                    original_task = job.get("original_task", "")
                    notify_desktop(
                        f"Pulse — {from_name} contributed",
                        f"Project: {project_id or 'n/a'} | Re: {original_task[:60]}",
                        urgency="normal",
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
                if not ok:
                    raise RuntimeError(f"Teams send failed: {detail}")

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
                if not ok:
                    raise RuntimeError(f"Email reply failed: {detail}")

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
                log.exception(f"  {tag} Job failed: {job_name} — {e}")
                try:
                    write_job_notification(job_type, f"FAILED: {str(e)[:100]}")
                except Exception:
                    pass
                try:
                    notify_desktop("Pulse — Job Failed", f"{job_name}: {str(e)[:80]}")
                except Exception:
                    pass
                schedule_id = job.get("_schedule_id")
                if schedule_id:
                    from core.scheduler import reset_run
                    reset_run(schedule_id)

        finally:
            if browser_lock and browser_lock.locked():
                browser_lock.release()
            active_workers.pop(worker_id, None)
            job_queue.task_done()
            if job_file:
                enqueued_files = getattr(job_queue, "_enqueued_files", None)
                if isinstance(enqueued_files, set):
                    enqueued_files.discard(job_file)
            try:
                from daemon.sync import sync_to_onedrive
                sync_to_onedrive(config)
            except Exception as e:
                log.warning(f"  Post-job sync failed: {e}")

        log.info(f"=== {tag} Job done: [{job_type}] {job_name} ===")


# ---------------------------------------------------------------------------
# Knowledge init — runs Phase 0+1, then fans out Phase 2 as individual jobs
# ---------------------------------------------------------------------------

# Track outstanding knowledge-project jobs for completion notification
_knowledge_batch_remaining = 0
_knowledge_batch_total = 0
_knowledge_batch_lock = asyncio.Lock()


async def _run_knowledge_init(
    client, config: dict, job_queue: asyncio.PriorityQueue, job_log_file: str | None,
):
    """Run knowledge Phase 0 (transcripts) + Phase 1 (archive), then queue
    individual knowledge-project jobs for Phase 2.

    This replaces the old monolithic ``run_knowledge_pipeline()`` call.
    Phase 2 projects are queued at low priority so triage/digest can
    interleave naturally via the priority queue.
    """
    global _knowledge_batch_remaining, _knowledge_batch_total
    from sdk.runner import run_knowledge_init_phases, prepare_knowledge_projects

    log.info("=== Knowledge init: Phase 0 + 1 ===")

    # Phase 0 + 1
    await run_knowledge_init_phases(client, config, job_log_file=job_log_file)

    # Phase 2: queue individual project enrichments
    project_jobs = prepare_knowledge_projects(config)
    if not project_jobs:
        log.info("  No active projects to enrich. Knowledge init done.")
        notify_desktop("Pulse — Knowledge", "Knowledge mining complete (no projects).")
        write_job_notification("knowledge", "Knowledge mining complete (no projects).")
        return

    async with _knowledge_batch_lock:
        _knowledge_batch_total = len(project_jobs)
        _knowledge_batch_remaining = len(project_jobs)

    log.info(f"  Queuing {len(project_jobs)} knowledge-project jobs...")
    for pj in project_jobs:
        enqueue_job(job_queue, pj, config)

    log.info("  Knowledge init done — project enrichments queued.")


def _knowledge_project_done(job: dict):
    """Track completion of individual knowledge-project jobs.

    When the last project finishes, send the completion notification.
    Uses a simple counter — no lock needed (single asyncio thread).
    """
    global _knowledge_batch_remaining
    _knowledge_batch_remaining = max(0, _knowledge_batch_remaining - 1)
    remaining = _knowledge_batch_remaining
    total = _knowledge_batch_total

    if remaining == 0 and total > 0:
        log.info(f"=== Knowledge pipeline complete — all {total} projects enriched ===")
        notify_desktop("Pulse — Knowledge", "Knowledge mining complete.")
        write_job_notification("knowledge", "Knowledge mining complete.")


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
        result = await reply_to_chat(chat_name, message)
        if not result.get("success") and "not found in sidebar" in result.get("detail", ""):
            # Chat not visible in sidebar — fall back to new-chat search flow
            log.info(f"  Chat not in sidebar — falling back to new-chat with: {chat_name}")
            result = await send_teams_message(chat_name, message)
        return result
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


async def _run_guardian_session(client, config: dict, job: dict) -> str:
    """Open an SDK session in 'guardian' mode and return the final text.

    Returns an empty string on timeout or error; parser will default to no_context.
    """
    from sdk.session import agent_session
    from sdk.tools import get_tools

    task_text = job.get("task", "")
    from_name = job.get("from", "Unknown")
    project_id = job.get("project_id", "")

    user_prompt = (
        f"Teammate: {from_name}\n"
        f"Project context: {project_id or '(unspecified)'}\n"
        f"Question: {task_text}\n\n"
        f"Follow the Guardian Mode workflow. End with the structured JSON."
    )

    tools = get_tools()
    async with agent_session(client, config, "guardian", tools=tools) as (session, handler):
        await session.send({"prompt": user_prompt})
        try:
            await asyncio.wait_for(handler.done.wait(), timeout=120)
        except asyncio.TimeoutError:
            log.warning(f"  Guardian session timed out for req {str(job.get('request_id', '?'))[:8]}")
        if handler.error:
            log.warning(f"  Guardian session error for req {str(job.get('request_id', '?'))[:8]}: {handler.error}")
        return handler.final_text or ""


async def _handle_agent_request(client, config: dict, job: dict) -> None:
    """Process an incoming agent_request via Guardian Mode and write response YAML."""
    task_text = job.get("task", "")
    from_name = job.get("from", "Unknown")
    kind = job.get("kind", "question")

    log.info(f"  Guardian for {from_name} ({kind}): {task_text[:80]}...")

    output_text = await _run_guardian_session(client, config, job)
    parsed = _parse_guardian_output(output_text)
    _write_guardian_response(config, job, parsed)


def _parse_guardian_output(text: str) -> dict:
    """Extract the structured JSON payload the Guardian LLM emits.

    Accepts fenced ```json blocks (preferred) or raw JSON. Falls back to
    {"status": "no_context"} on any parse failure, which is the defensive
    default so a misbehaving session does not crash the worker.
    """
    import re as _re

    if not text:
        return {"status": "no_context"}

    # Prefer the last fenced json block
    fenced = _re.findall(r"```json\s*(\{.*?\})\s*```", text, flags=_re.DOTALL)
    if fenced:
        candidate = fenced[-1]
    else:
        # Fallback: largest-looking {...} span
        m = _re.search(r"\{.*\}", text, flags=_re.DOTALL)
        candidate = m.group(0) if m else ""

    if not candidate:
        return {"status": "no_context"}

    try:
        data = json.loads(candidate)
    except (json.JSONDecodeError, ValueError):
        return {"status": "no_context"}

    if not isinstance(data, dict) or "status" not in data:
        return {"status": "no_context"}

    status = data.get("status")
    if status not in ("answered", "no_context", "declined"):
        return {"status": "no_context"}

    return data


def _resolve_reply_dir(config: dict, original_job: dict) -> Path | None:
    """Resolve where to write a Guardian response on THIS machine.

    The sender's ``reply_to`` field is a path on the SENDER's machine
    (``C:\\Users\\<their-username>\\...``) and is almost never accessible on
    the receiver. Resolve against the receiver's own view of the sender's
    shared OneDrive folder instead.

    Matching strategy (strong signals only):

      1. Find the sender in ``config["team"]`` by matching ``from_alias`` OR
         ``from`` (display name), case-insensitive. This tolerates senders
         whose config has a placeholder alias (e.g. ``todo``) but a real name,
         or vice-versa.
      2. If a match has ``agent_path`` and that folder exists on disk, use
         ``agent_path/pending``.
      3. Else try convention ``PULSE_TEAM_DIR/{matched_alias}/jobs/pending``
         BUT only if ``PULSE_TEAM_DIR/{matched_alias}/jobs/`` already exists
         (meaning the shortcut is actually synced at the convention path).
      4. Only fall back to the raw ``reply_to`` if that path already exists
         — covers same-machine demos (alpha/beta personas).

    Never creates a directory tree outside a pre-existing shared folder. That
    was a bug in the previous version: ``mkdir(parents=True)`` on an
    unresolvable ``PULSE_TEAM_DIR/{unknown_alias}/...`` path silently created
    an orphan local folder that nobody is subscribed to, and the reply was
    stranded there forever.

    Returns None when no candidate resolves to a real shared folder.
    """
    from core.constants import PULSE_TEAM_DIR

    sender_alias = (original_job.get("from_alias") or "").strip().lower()
    sender_name = (original_job.get("from") or "").strip().lower()

    # Find team entry by alias OR by name (whichever matches first)
    matched: dict | None = None
    for member in config.get("team", []):
        m_alias = (member.get("alias") or "").strip().lower()
        m_name = (member.get("name") or "").strip().lower()
        if sender_alias and m_alias == sender_alias:
            matched = member
            break
        if sender_name and m_name and m_name == sender_name:
            matched = member
            break

    candidates: list[Path] = []

    if matched is not None:
        explicit = matched.get("agent_path")
        if explicit:
            root = Path(explicit)
            if root.exists():
                candidates.append(root / "pending")

        conv_alias = (matched.get("alias") or "").strip().lower()
        if conv_alias:
            conv_jobs = PULSE_TEAM_DIR / conv_alias / "jobs"
            if conv_jobs.exists():
                candidates.append(conv_jobs / "pending")

    # Same-machine demo: sender and receiver share filesystem. Accept only
    # if the raw reply_to path already exists.
    raw = (original_job.get("reply_to") or "").strip()
    if raw:
        raw_path = Path(raw)
        if raw_path.exists():
            candidates.append(raw_path)

    for candidate in candidates:
        try:
            # Only the pending/ leaf is ever created; the parent is required
            # to pre-exist (it's the real shared-folder root).
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
        except OSError as e:
            log.debug(f"  reply candidate {candidate} not usable: {e}")
            continue
    return None


def _write_guardian_response(config: dict, original_job: dict, parsed: dict) -> None:
    """Write a structured response YAML to the requester's shared mailbox.

    ``parsed`` is the Guardian LLM's output dict, at minimum containing 'status'.
    """
    reply_dir = _resolve_reply_dir(config, original_job)
    if reply_dir is None:
        log.error(
            "  Cannot resolve reply destination for agent_request (from_alias="
            f"{original_job.get('from_alias')!r}, reply_to={original_job.get('reply_to')!r}). "
            "Add the sender to your team config or ensure the shared OneDrive folder is synced."
        )
        return

    user_cfg = config.get("user", {})
    from_name = user_cfg.get("name", "Unknown")
    from_alias = user_cfg.get("alias") or re.sub(r"[^a-z0-9-]", "", (from_name or "unknown").lower().split()[0]) or "unknown"

    request_id = original_job.get("request_id", "unknown")
    project_id = original_job.get("project_id", "")
    timestamp = datetime.now().isoformat()
    date_str = datetime.now().strftime("%Y-%m-%d")
    slug = str(request_id)[:8]

    response_data = {
        "type": "agent_response",
        "kind": "response",
        "request_id": request_id,
        "project_id": project_id,
        "from": from_name,
        "from_alias": from_alias,
        "original_task": original_job.get("task", "")[:200],
        "status": parsed.get("status", "no_context"),
        "result": parsed.get("result", ""),
        "sources": parsed.get("sources", []),
        "created_at": timestamp,
    }
    if parsed.get("status") == "declined" and "reason" in parsed:
        response_data["reason"] = parsed["reason"]

    response_file = reply_dir / f"{date_str}-response-{from_alias}-{slug}.yaml"
    with open(response_file, "w", encoding="utf-8") as f:
        yaml.dump(response_data, f, default_flow_style=False)

    log.info(f"  Guardian response written: status={parsed.get('status')} to {response_file}")


def _ingest_agent_response(job: dict) -> None:
    """Fold an agent_response into its target project YAML's team_context[].

    Silent skip on:
      - status != "answered" (no_context / declined)
      - missing project_id
      - missing project YAML
      - duplicate request_id (already ingested)

    Atomic write: writes to a temp path and renames.
    """
    status = job.get("status", "")
    if status != "answered":
        log.info(f"  Ingest: skipping response with status='{status}' (req={str(job.get('request_id', '?'))[:8]})")
        return

    project_id = job.get("project_id", "")
    if not project_id:
        log.warning(f"  Ingest: response has no project_id, dropping (req={str(job.get('request_id', '?'))[:8]})")
        return

    project_path = PROJECTS_DIR / f"{project_id}.yaml"
    if not project_path.exists():
        log.warning(f"  Ingest: project '{project_id}' not found, dropping response")
        return

    try:
        with open(project_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception as e:
        log.error(f"  Ingest: cannot read project '{project_id}': {e}")
        return

    team_context = data.get("team_context")
    if not isinstance(team_context, list):
        team_context = []
    request_id = job.get("request_id", "")
    if any(entry.get("request_id") == request_id for entry in team_context):
        log.info(f"  Ingest: request_id {str(request_id)[:8]} already present, dedup skip")
        return

    entry = {
        "from": job.get("from", "Unknown"),
        "from_alias": job.get("from_alias", ""),
        "contributed_at": job.get("created_at", datetime.now().isoformat()),
        "question": job.get("original_task", "")[:200],
        "answer": job.get("result", ""),
        "sources": job.get("sources") or [],
        "request_id": request_id,
    }
    team_context.append(entry)
    data["team_context"] = team_context

    # Atomic write via temp file + rename
    try:
        tmp_path = project_path.with_suffix(".yaml.tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        tmp_path.replace(project_path)
    except Exception as e:
        log.error(f"  Ingest: failed to write project '{project_id}': {e}")
        return

    log.info(f"  Ingest: added team_context entry to project '{project_id}' from {entry['from']}")


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
