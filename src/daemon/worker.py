"""Job worker — processes jobs from the asyncio queue one at a time."""

import asyncio
import json
from datetime import datetime
from pathlib import Path

import yaml

from core.constants import OUTPUT_DIR
from core.config import mark_task_completed
from core.logging import log


async def run_chat_query(client, config: dict, prompt: str,
                        telegram_app=None, chat_id: int | None = None,
                        on_delta=None) -> str:
    """Run a conversational query via GHCP SDK. Returns the response text.

    For Telegram chats, streams deltas progressively into the message.
    """
    from sdk.tools import get_tools
    from sdk.session import agent_session

    log.info(f"  Chat query: {prompt[:80]}...")

    # Set up streaming reply for Telegram
    streaming_reply = None
    delta_cb = on_delta
    if chat_id and not on_delta:
        from tg.bot import create_streaming_reply
        streaming_reply = await create_streaming_reply(chat_id)
        if streaming_reply:
            delta_cb = streaming_reply.on_delta

    async with agent_session(client, config, "chat", tools=get_tools(),
                             telegram_app=telegram_app, chat_id=chat_id,
                             on_delta=delta_cb) as (session, handler):
        await session.send({"prompt": prompt})
        try:
            await asyncio.wait_for(handler.done.wait(), timeout=1800)
        except asyncio.TimeoutError:
            log.warning("Chat query timed out after 1800s")
            if streaming_reply:
                await streaming_reply.finish()
            if handler.final_text:
                return handler.final_text
            return "Agent is still working — response timed out."

        if handler.error:
            log.error(f"Chat session error: {handler.error}")
            if streaming_reply:
                await streaming_reply.finish()
            return f"Agent error: {handler.error}"

        # Finalize streaming reply with formatted HTML
        if streaming_reply:
            await streaming_reply.finish()
            return handler.final_text or "No response from agent."

        return handler.final_text or "No response from agent."


async def job_worker(client, config: dict, job_queue: asyncio.Queue, telegram_app):
    """Process jobs from the queue as they arrive.

    Uses an asyncio.Lock to prevent concurrent SDK access (safety net
    even though the queue is processed sequentially).
    """
    from sdk.runner import run_job

    lock = asyncio.Lock()

    while True:
        job = await job_queue.get()
        job_type = job.get("type", "unknown")
        chat_id = job.get("_chat_id")
        job_name = job.get("task", job_type)
        job_file = job.get("_file")

        log.info(f"=== Job: [{job_type}] {job_name} ===")

        # Ensure typing indicator is active for Telegram-sourced jobs
        if chat_id:
            from tg.bot import start_typing
            start_typing(chat_id)

        try:
            async with lock:
                if job_type == "chat":
                    prompt = job.get("prompt", "")
                    # StreamingReply handles Telegram delivery when chat_id is set
                    await run_chat_query(client, config, prompt,
                                         telegram_app=telegram_app, chat_id=chat_id)
                    # Process any browser actions the agent queued via tools
                    await process_pending_actions(telegram_app, chat_id)

                elif job_type == "research":
                    context = {"task": job}
                    await run_job(client, config, "research", context=context,
                                  telegram_app=telegram_app, chat_id=chat_id)
                    if "_file" in job:
                        mark_task_completed(job)
                    if chat_id:
                        await _notify(telegram_app, chat_id, f"Research complete: {job_name}")

                elif job_type == "transcripts":
                    # Standalone mode — no SDK session, uses Playwright directly
                    from collectors.transcripts import run_transcript_collection
                    await run_transcript_collection(client, config)
                    if "_file" in job:
                        mark_task_completed(job)
                    if chat_id:
                        await _notify(telegram_app, chat_id, "Transcripts complete.")

                elif job_type in ("digest", "monitor", "intel"):
                    await run_job(client, config, job_type,
                                  telegram_app=telegram_app, chat_id=chat_id)
                    if "_file" in job:
                        mark_task_completed(job)
                    if chat_id:
                        await _post_job_notify(telegram_app, chat_id, job_type)

                elif job_type == "agent_request":
                    result_text = await _handle_agent_request(
                        client, config, job, telegram_app, chat_id
                    )
                    if "_file" in job:
                        mark_task_completed(job)
                    _write_agent_response(config, job, result_text)
                    if chat_id:
                        from_name = job.get("from", "Unknown agent")
                        await _notify(telegram_app, chat_id,
                                      f"Processed request from {from_name}: {job_name}")

                elif job_type == "agent_response":
                    from_name = job.get("from", "Unknown")
                    original_task = job.get("original_task", "")
                    result_text = job.get("result", "No content in response.")
                    log.info(f"  Agent response from {from_name} (req: {job.get('request_id', '?')[:8]})")
                    if "_file" in job:
                        mark_task_completed(job)
                    notify_id = chat_id
                    if not notify_id:
                        from tg.bot import get_proactive_chat_id
                        notify_id = get_proactive_chat_id()
                    if notify_id:
                        notification = (
                            f"Response from {from_name}'s agent:\n\n"
                            f"Re: {original_task[:100]}\n\n"
                            f"{result_text}"
                        )
                        await _notify(telegram_app, notify_id, notification)

                elif job_type == "teams_send":
                    result = await _execute_teams_send(job)
                    if chat_id:
                        status = "Sent" if result.get("success") else "Failed"
                        await _notify(telegram_app, chat_id,
                                      f"Teams {status}: {result.get('detail', '')}")

                elif job_type == "email_reply":
                    result = await _execute_email_reply(job)
                    if chat_id:
                        status = "Sent" if result.get("success") else "Failed"
                        await _notify(telegram_app, chat_id,
                                      f"Email reply {status}: {result.get('detail', '')}")

                else:
                    log.warning(f"  Unknown job type: {job_type}")

        except Exception as e:
            log.exception(f"  Job failed: {job_name} — {e}")
            if chat_id:
                from tg.bot import stop_typing
                stop_typing(chat_id)
                await _notify(telegram_app, chat_id, f"Failed: {job_name}\n{e}")

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


async def process_pending_actions(telegram_app, chat_id: int | None = None):
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

            # Notify via Telegram
            notify_chat = chat_id
            if notify_chat:
                status = "Sent" if result.get("success") else "Failed"
                detail = result.get("detail", "")
                await _notify(telegram_app, notify_chat, f"{status}: {detail}")

        except Exception as e:
            log.error(f"  Pending action failed: {e}")
        finally:
            # Always remove the action file (processed or failed)
            try:
                action_file.unlink()
            except Exception:
                pass


async def _post_job_notify(telegram_app, chat_id: int, job_type: str):
    """Send job-specific completion notification."""
    if job_type == "digest":
        from tg.bot import send_latest_digest
        await _notify(telegram_app, chat_id, "Digest complete:")
        await send_latest_digest(chat_id)
    elif job_type == "monitor":
        report = get_latest_monitoring_report()
        if report:
            await _notify(telegram_app, chat_id, report)
        # Send action buttons from the structured triage JSON
        await _send_triage_actions(chat_id)
    else:
        label = {"intel": "Intel brief", "transcripts": "Transcripts"}
        await _notify(telegram_app, chat_id, f"{label.get(job_type, job_type)} complete.")


async def _notify(telegram_app, chat_id: int, text: str):
    """Notify via Telegram (lazy import to avoid circular deps)."""
    from tg.bot import notify
    await notify(telegram_app, chat_id, text)


async def _send_triage_actions(chat_id: int):
    """Send inline action buttons from the latest triage JSON."""
    import json
    reports = sorted(OUTPUT_DIR.glob("monitoring-*.json"), reverse=True)
    if not reports:
        return
    try:
        triage_data = json.loads(reports[0].read_text(encoding="utf-8"))
        from tg.bot import send_triage_actions
        await send_triage_actions(chat_id, triage_data)
    except Exception:
        log.warning("Failed to send triage action buttons", exc_info=True)


async def _handle_agent_request(client, config: dict, job: dict,
                                 telegram_app, chat_id: int | None) -> str:
    """Process an incoming agent_request — runs a chat query to answer."""
    from sdk.runner import run_job

    task_text = job.get("task", "")
    from_name = job.get("from", "Unknown")
    kind = job.get("kind", "question")

    log.info(f"  Agent request from {from_name} ({kind}): {task_text[:80]}...")

    if chat_id:
        await _notify(telegram_app, chat_id,
                      f"Incoming request from {from_name}: {task_text[:100]}")

    if kind == "research":
        context = {"task": job}
        result = await run_job(client, config, "research", context=context,
                               telegram_app=telegram_app, chat_id=chat_id)
    else:
        prompt = (
            f"A colleague ({from_name}) sent this request to your agent:\n\n"
            f"**Request ({kind}):** {task_text}\n\n"
            f"Search your local files (transcripts, documents, emails) for relevant "
            f"context and provide a thorough answer. Include specific details from "
            f"meetings and documents if available."
        )
        result = await run_chat_query(client, config, prompt,
                                       telegram_app=telegram_app, chat_id=chat_id)

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


def get_latest_monitoring_report() -> str | None:
    """Read the most recent monitoring report."""
    reports = sorted(OUTPUT_DIR.glob("monitoring-*.md"), reverse=True)
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
