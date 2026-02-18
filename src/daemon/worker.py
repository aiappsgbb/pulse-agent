"""Job worker — processes jobs from the asyncio queue one at a time."""

import asyncio

from core.constants import OUTPUT_DIR
from core.config import mark_task_completed
from core.logging import log


async def run_chat_query(client, config: dict, prompt: str,
                        telegram_app=None, chat_id: int | None = None) -> str:
    """Run a conversational query via GHCP SDK. Returns the response text."""
    from sdk.tools import get_tools
    from sdk.session import agent_session

    log.info(f"  Chat query: {prompt[:80]}...")

    async with agent_session(client, config, "chat", tools=get_tools(),
                             telegram_app=telegram_app, chat_id=chat_id) as session:
        response = await session.send_and_wait({"prompt": prompt}, timeout=600)
        if response and response.data and response.data.content:
            return response.data.content
        return "No response from agent."


async def job_worker(client, config: dict, job_queue: asyncio.Queue, telegram_app):
    """Process jobs from the queue as they arrive."""
    from sdk.runner import run_job

    while True:
        job = await job_queue.get()
        job_type = job.get("type", "unknown")
        chat_id = job.get("_chat_id")
        job_name = job.get("task", job_type)

        log.info(f"=== Job: [{job_type}] {job_name} ===")

        try:
            if job_type == "chat":
                prompt = job.get("prompt", "")
                reply = await run_chat_query(client, config, prompt,
                                             telegram_app=telegram_app, chat_id=chat_id)
                if chat_id:
                    await _notify(telegram_app, chat_id, reply)

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
            log.error(f"  Job failed: {job_name} — {e}")
            if chat_id:
                await _notify(telegram_app, chat_id, f"Failed: {job_name}\n{e}")

        finally:
            job_queue.task_done()
            from daemon.sync import sync_to_onedrive
            sync_to_onedrive(config)

        log.info(f"=== Job done: [{job_type}] {job_name} ===")


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
    else:
        label = {"intel": "Intel brief", "transcripts": "Transcripts"}
        await _notify(telegram_app, chat_id, f"{label.get(job_type, job_type)} complete.")


async def _notify(telegram_app, chat_id: int, text: str):
    """Notify via Telegram (lazy import to avoid circular deps)."""
    from tg.bot import notify
    await notify(telegram_app, chat_id, text)


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
        return None
