"""Compress raw transcript text via GHCP SDK before saving.

Sends a raw transcript through a lightweight SDK session (no tools, no MCP)
to extract structured notes: TLDR, decisions, action items, key quotes.
Falls back to raw text if compression fails.
"""

import asyncio
from pathlib import Path

from copilot import CopilotClient, PermissionRequest, PermissionRequestResult

from core.logging import log, safe_encode

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
        # Too short to bother compressing
        return None

    log.info(f"  Compressing transcript: {safe_encode(meeting_name[:50])} ({len(raw_text)} chars)")

    def _auto_approve(request: PermissionRequest, context: dict) -> PermissionRequestResult:
        return PermissionRequestResult(kind="approved", rules=[])

    session = None
    try:
        session = await client.create_session({
            "model": model,
            "system_message": {"mode": "replace", "content": COMPRESS_PROMPT},
            "streaming": True,
            "on_permission_request": _auto_approve,
            # No tools, no MCP, no agents — pure prompt-response
        })

        prompt = (
            f"Compress this transcript from the meeting: **{meeting_name}**\n\n"
            f"---\n{raw_text}\n---"
        )

        # Collect the response via event handler
        from sdk.event_handler import EventHandler
        handler = EventHandler()
        unsub = session.on(handler)

        await session.send({"prompt": prompt})

        try:
            await asyncio.wait_for(handler.done.wait(), timeout=120)
        except asyncio.TimeoutError:
            log.warning(f"  Compression timed out for {safe_encode(meeting_name[:50])}")
            return None

        if handler.error:
            log.warning(f"  Compression error: {handler.error}")
            return None

        compressed = handler.final_text
        if not compressed:
            log.warning("  Compression returned empty text")
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

    Finds all .txt files in transcripts_dir, compresses each via SDK,
    replaces with .md file, and deletes the raw .txt.

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

    log.info(f"  Found {len(raw_files)} raw transcripts to compress")
    compressed_count = 0

    for txt_path in raw_files:
        # Check if compressed version already exists
        md_path = txt_path.with_suffix(".md")
        if md_path.exists():
            log.info(f"  SKIP (already compressed): {txt_path.name}")
            continue

        raw_text = txt_path.read_text(encoding="utf-8")
        meeting_name = txt_path.stem.split("_", 1)[-1].replace("-", " ").title()

        compressed = await compress_transcript(client, raw_text, meeting_name, model=model)

        if compressed:
            # Save compressed .md with metadata header
            date_part = txt_path.stem.split("_", 1)[0]
            header = (
                f"# {meeting_name}\n"
                f"**Date**: {date_part} | "
                f"**Original length**: {len(raw_text)} chars | "
                f"**Compressed**: {len(compressed)} chars\n\n"
            )
            md_path.write_text(header + compressed, encoding="utf-8")
            txt_path.unlink()
            log.info(f"  Replaced {txt_path.name} -> {md_path.name}")
            compressed_count += 1
        else:
            log.warning(f"  Could not compress {txt_path.name} — keeping raw")

    return compressed_count
