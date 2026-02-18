"""Tests for collectors/ modules — extractors, feeds, transcript parsing."""

import tempfile
from pathlib import Path

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
