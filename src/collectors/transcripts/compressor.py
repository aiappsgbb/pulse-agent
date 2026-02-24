"""Compress raw transcript text via GHCP SDK before saving.

Sends a raw transcript through a lightweight SDK session (no tools, no MCP)
to extract structured notes: TLDR, decisions, action items, key quotes.
Falls back to raw text if compression fails.
"""

import asyncio
from pathlib import Path

from copilot import CopilotClient

from core.logging import log, safe_encode
from sdk.session import auto_approve_handler

COMPRESS_PROMPT = """\
You are a meeting transcript compressor. Your job is to extract the signal from noise.

Given a raw meeting transcript, produce a compressed version with ONLY:

## Meeting Summary
3-5 bullet points covering what was discussed and decided.

## Decisions Made
- Each concrete decision (who decided what). Omit if none.

## Action Items
- WHO will do WHAT by WHEN. Be specific. Omit if none.

## Key Quotes
Direct quotes that capture important positions, commitments, or concerns.
Only include quotes that would be lost without the transcript. Max 5.

## Participants
List of speakers identified in the transcript.

Rules:
- Be SPECIFIC — names, dates, numbers. No vague summaries.
- Strip all filler (greetings, "can you hear me", "that makes sense", etc.)
- Target: compress to ~10% of original length
- If the transcript is mostly filler with no substance, say so in 2 lines
- Output plain markdown, no code blocks
"""


async def _send_and_collect(session, prompt: str, timeout: float = 120) -> str | None:
    """Send a prompt to an existing session and collect the response."""
    from sdk.event_handler import EventHandler
    handler = EventHandler()
    unsub = session.on(handler)
    try:
        await session.send({"prompt": prompt})
        await asyncio.wait_for(handler.done.wait(), timeout=timeout)
        if handler.error:
            log.warning(f"  Session error: {handler.error}")
            return None
        return handler.final_text
    except asyncio.TimeoutError:
        log.warning("  Compression timed out")
        return None
    finally:
        if unsub:
            try:
                unsub()
            except Exception:
                pass


async def compress_transcript(
    client: CopilotClient,
    raw_text: str,
    meeting_name: str,
    model: str = "claude-sonnet",
) -> str | None:
    """Compress a raw transcript via GHCP SDK.

    Args:
        client: GHCP SDK client (already started)
        raw_text: Raw transcript text from Playwright extraction
        meeting_name: Meeting title for logging
        model: Model to use for compression

    Returns:
        Compressed text, or None if compression fails (caller saves raw).
    """
    if not raw_text or len(raw_text) < 500:
        return None

    log.info(f"  Compressing transcript: {safe_encode(meeting_name[:50])} ({len(raw_text)} chars)")

    session = None
    try:
        session = await client.create_session({
            "model": model,
            "system_message": {"mode": "replace", "content": COMPRESS_PROMPT},
            "streaming": True,
            "on_permission_request": auto_approve_handler,
        })

        prompt = (
            f"Compress this transcript from the meeting: **{meeting_name}**\n\n"
            f"---\n{raw_text}\n---"
        )

        compressed = await _send_and_collect(session, prompt)
        if not compressed:
            return None

        ratio = len(compressed) / len(raw_text) * 100
        log.info(f"  Compressed: {len(raw_text)} -> {len(compressed)} chars ({ratio:.0f}%)")
        return compressed

    except Exception as e:
        log.warning(f"  Compression failed for {safe_encode(meeting_name[:50])}: {e}")
        return None
    finally:
        if session:
            try:
                await session.destroy()
            except Exception:
                pass


async def compress_existing_transcripts(
    client: CopilotClient,
    transcripts_dir: Path,
    model: str = "claude-sonnet",
) -> int:
    """Batch-compress raw .txt transcripts that haven't been compressed yet.

    Reuses a single SDK session for all transcripts (cheaper than 1 session each).
    Falls back to per-transcript sessions if the shared session fails mid-batch.

    Args:
        client: GHCP SDK client (already started)
        transcripts_dir: Directory containing transcript files
        model: Model to use for compression

    Returns:
        Number of transcripts compressed.
    """
    if not transcripts_dir.exists():
        return 0

    raw_files = sorted(transcripts_dir.glob("*.txt"))
    if not raw_files:
        return 0

    # Filter to files that need compression
    to_compress = []
    for txt_path in raw_files:
        md_path = txt_path.with_suffix(".md")
        if md_path.exists():
            log.info(f"  SKIP (already compressed): {txt_path.name}")
            continue
        to_compress.append(txt_path)

    if not to_compress:
        return 0

    log.info(f"  Found {len(to_compress)} raw transcripts to compress")
    compressed_count = 0

    # Create one session for the whole batch
    session = None
    try:
        session = await client.create_session({
            "model": model,
            "system_message": {"mode": "replace", "content": COMPRESS_PROMPT},
            "streaming": True,
            "on_permission_request": auto_approve_handler,
        })

        for txt_path in to_compress:
            raw_text = txt_path.read_text(encoding="utf-8")
            if len(raw_text) < 500:
                log.info(f"  SKIP (too short): {txt_path.name}")
                continue

            meeting_name = txt_path.stem.split("_", 1)[-1].replace("-", " ").title()
            log.info(f"  Compressing: {safe_encode(meeting_name[:50])} ({len(raw_text)} chars)")

            prompt = (
                f"Compress this transcript from the meeting: **{meeting_name}**\n\n"
                f"---\n{raw_text}\n---"
            )

            try:
                compressed = await _send_and_collect(session, prompt)
            except Exception as e:
                log.warning(f"  Batch session error on {txt_path.name}: {e} — skipping rest")
                break

            if compressed:
                date_part = txt_path.stem.split("_", 1)[0]
                header = (
                    f"# {meeting_name}\n"
                    f"**Date**: {date_part} | "
                    f"**Original length**: {len(raw_text)} chars | "
                    f"**Compressed**: {len(compressed)} chars\n\n"
                )
                md_path = txt_path.with_suffix(".md")
                md_path.write_text(header + compressed, encoding="utf-8")
                txt_path.unlink()
                log.info(f"  Replaced {txt_path.name} -> {md_path.name}")
                compressed_count += 1
            else:
                log.warning(f"  Could not compress {txt_path.name} — keeping raw")

    except Exception as e:
        log.warning(f"  Batch compression session failed: {e}")
    finally:
        if session:
            try:
                await session.destroy()
            except Exception:
                pass

    return compressed_count
