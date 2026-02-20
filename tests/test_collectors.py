"""Tests for collectors/ modules — extractors, feeds, transcript parsing, compressor."""

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from collectors.extractors import extract_text, EXTRACTORS
from collectors.transcripts.extraction import clean_transcript


# --- extractors ---

def test_extractor_registry():
    """All expected file types are registered."""
    expected = {".txt", ".md", ".vtt", ".csv", ".eml", ".docx", ".pptx", ".pdf", ".xlsx"}
    assert set(EXTRACTORS.keys()) == expected


def test_extract_plaintext():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "test.txt"
        p.write_text("hello world", encoding="utf-8")
        result = extract_text(p)
        assert result == "hello world"


def test_extract_markdown():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "test.md"
        p.write_text("# Title\nBody text", encoding="utf-8")
        result = extract_text(p)
        assert "Title" in result
        assert "Body text" in result


def test_extract_unknown_extension():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "test.xyz"
        p.write_text("data", encoding="utf-8")
        result = extract_text(p)
        assert result is None


# --- clean_transcript ---

def test_clean_transcript_basic():
    entries = [
        "Alice\n5 minutes 30 seconds\n0:13",
        "Hello everyone.",
        "Welcome to the meeting.",
        "Bob\n6 minutes 10 seconds\n1:05",
        "I have an update.",
    ]
    result = clean_transcript(entries)
    assert result is not None
    assert "Alice" in result
    assert "Bob" in result
    assert "[0:13]" in result
    assert "[1:05]" in result


def test_clean_transcript_empty():
    assert clean_transcript([]) is None


def test_clean_transcript_no_speakers():
    """Entries with no speaker headers — first entry treated as speaker."""
    entries = ["Just some text without any context."]
    # Should assign "Unknown" as speaker since no header detected
    result = clean_transcript(entries)
    assert result is not None
    assert "Unknown" in result


def test_clean_transcript_only_speakers():
    """Only speaker headers, no text entries — should return None."""
    entries = [
        "Alice\n5 minutes\n0:00",
        "Bob\n6 minutes\n0:30",
    ]
    result = clean_transcript(entries)
    assert result is None


# --- transcript compressor ---

from collectors.transcripts.compressor import compress_transcript, compress_existing_transcripts


async def test_compress_transcript_short_text_skipped():
    """Transcripts under 500 chars are too short to compress."""
    client = MagicMock()
    result = await compress_transcript(client, "short text", "Test Meeting")
    assert result is None
    client.create_session.assert_not_called()


async def test_compress_transcript_empty_text_skipped():
    """Empty transcript returns None."""
    client = MagicMock()
    result = await compress_transcript(client, "", "Test Meeting")
    assert result is None


async def test_compress_transcript_success():
    """Successful compression returns compressed text."""
    raw = "A" * 1000  # long enough to trigger compression

    # Mock SDK session + event handler
    mock_session = AsyncMock()

    # Simulate the event handler being set up and completing
    def fake_on(handler):
        # When session.on(handler) is called, set up the handler to complete
        handler.final_text = "## Meeting Summary\n- Discussed topic A"
        handler.done.set()
        return MagicMock()  # unsub function

    mock_session.on = fake_on

    client = MagicMock()
    client.create_session = AsyncMock(return_value=mock_session)

    result = await compress_transcript(client, raw, "Test Meeting")
    assert result is not None
    assert "Meeting Summary" in result
    client.create_session.assert_called_once()
    mock_session.destroy.assert_called_once()


async def test_compress_transcript_sdk_failure():
    """SDK session creation failure returns None (fallback to raw)."""
    client = MagicMock()
    client.create_session = AsyncMock(side_effect=Exception("CLI not available"))

    raw = "A" * 1000
    result = await compress_transcript(client, raw, "Test Meeting")
    assert result is None


async def test_compress_transcript_timeout():
    """Compression timeout returns None."""
    raw = "A" * 1000

    mock_session = AsyncMock()

    def fake_on(handler):
        # Don't set handler.done — simulates timeout
        return MagicMock()

    mock_session.on = fake_on

    client = MagicMock()
    client.create_session = AsyncMock(return_value=mock_session)

    # Patch the timeout to be very short
    with patch("collectors.transcripts.compressor.asyncio.wait_for", side_effect=asyncio.TimeoutError):
        result = await compress_transcript(client, raw, "Test Meeting")

    assert result is None
    mock_session.destroy.assert_called_once()


# --- batch compression ---


async def test_compress_existing_skips_already_compressed(tmp_path):
    """If .md already exists for a .txt, skip it."""
    txt = tmp_path / "2026-02-16_test-meeting.txt"
    md = tmp_path / "2026-02-16_test-meeting.md"
    txt.write_text("A" * 1000, encoding="utf-8")
    md.write_text("# Already compressed", encoding="utf-8")

    client = MagicMock()
    count = await compress_existing_transcripts(client, tmp_path)
    assert count == 0
    client.create_session.assert_not_called()
    # Original .txt should still exist (not deleted since we skipped)
    assert txt.exists()


async def test_compress_existing_replaces_txt_with_md(tmp_path):
    """Successful compression replaces .txt with .md and deletes .txt."""
    txt = tmp_path / "2026-02-16_test-meeting.txt"
    txt.write_text("A" * 1000, encoding="utf-8")

    mock_session = AsyncMock()

    def fake_on(handler):
        handler.final_text = "## Meeting Summary\n- Compressed"
        handler.done.set()
        return MagicMock()

    mock_session.on = fake_on

    client = MagicMock()
    client.create_session = AsyncMock(return_value=mock_session)

    count = await compress_existing_transcripts(client, tmp_path)
    assert count == 1
    # .txt should be gone, .md should exist
    assert not txt.exists()
    md = tmp_path / "2026-02-16_test-meeting.md"
    assert md.exists()
    content = md.read_text(encoding="utf-8")
    assert "Meeting Summary" in content
    assert "Original length" in content  # metadata header


async def test_compress_existing_keeps_txt_on_failure(tmp_path):
    """If compression fails, keep the raw .txt."""
    txt = tmp_path / "2026-02-16_test-meeting.txt"
    txt.write_text("A" * 1000, encoding="utf-8")

    client = MagicMock()
    client.create_session = AsyncMock(side_effect=Exception("SDK down"))

    count = await compress_existing_transcripts(client, tmp_path)
    assert count == 0
    # .txt should still be there
    assert txt.exists()
    assert not (tmp_path / "2026-02-16_test-meeting.md").exists()


async def test_compress_existing_empty_dir(tmp_path):
    """No .txt files -> returns 0."""
    client = MagicMock()
    count = await compress_existing_transcripts(client, tmp_path)
    assert count == 0


async def test_compress_existing_nonexistent_dir():
    """Nonexistent directory -> returns 0."""
    client = MagicMock()
    count = await compress_existing_transcripts(client, Path("/nonexistent"))
    assert count == 0
