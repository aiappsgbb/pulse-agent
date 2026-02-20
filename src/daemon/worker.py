"""Job worker — processes jobs from the asyncio queue one at a time."""

import asyncio

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


async def _post_job_notify(telegram_app, chat_id: int, job_type: str):
    """Send job-specific completion notification."""
    if job_type == "digest":
        from tg.bot import send_latest_digest
        await _notify(telegram_app, chat_id, "Digest complete:")
        await send_latest_digest(chat_id)
        await _send_digest_actions(chat_id)
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


async def _send_digest_actions(chat_id: int):
    """Send inline action buttons from the latest digest JSON."""
    import json
    digests = sorted(OUTPUT_DIR.glob("digests/*.json"), reverse=True)
    if not digests:
        return
    try:
        data = json.loads(digests[0].read_text(encoding="utf-8"))
        from tg.bot import send_triage_actions
        await send_triage_actions(chat_id, data)
    except Exception:
        log.warning("Failed to send digest action buttons", exc_info=True)


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
