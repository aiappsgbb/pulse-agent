"""Tests for Guardian Mode -- system prompt + structured response parser."""
from pathlib import Path

from core.constants import PROJECT_ROOT


GUARDIAN_PROMPT_PATH = PROJECT_ROOT / "config" / "prompts" / "system" / "guardian.md"


def test_guardian_prompt_file_exists():
    assert GUARDIAN_PROMPT_PATH.exists(), f"Missing Guardian prompt at {GUARDIAN_PROMPT_PATH}"


def test_guardian_prompt_contains_required_directives():
    """The Guardian prompt must clearly instruct: search, judge, output JSON.

    Failing these means the LLM has no scaffolding for safe-sharing behavior.
    """
    text = GUARDIAN_PROMPT_PATH.read_text(encoding="utf-8")

    # Must tell the LLM it is acting as the user's guardian
    assert "guardian" in text.lower()
    # Must require a JSON payload as the structured output
    assert '"status"' in text
    assert '"answered"' in text and '"no_context"' in text and '"declined"' in text
    # Must require source citations on answered responses
    assert '"sources"' in text
    # Must mention PII / personal / sensitive as judgment criteria
    assert any(word in text.lower() for word in ("pii", "personal", "sensitive"))
    # Must instruct to search local files first
    assert "search_local_files" in text


from daemon.worker import _parse_guardian_output


def test_parse_guardian_output_answered():
    text = '''Some prose before.
```json
{"status": "answered", "result": "3 POCs tried. Licensing was the main objection.", "sources": ["transcripts/2026-01-15.md"]}
```
Trailing prose.'''
    result = _parse_guardian_output(text)
    assert result["status"] == "answered"
    assert "POCs" in result["result"]
    assert result["sources"] == ["transcripts/2026-01-15.md"]


def test_parse_guardian_output_no_context():
    text = '```json\n{"status": "no_context"}\n```'
    result = _parse_guardian_output(text)
    assert result["status"] == "no_context"
    assert result.get("result", "") == ""
    assert result.get("sources", []) == []


def test_parse_guardian_output_declined():
    text = '```json\n{"status": "declined", "reason": "too sensitive"}\n```'
    result = _parse_guardian_output(text)
    assert result["status"] == "declined"
    assert result["reason"] == "too sensitive"


def test_parse_guardian_output_no_json_block():
    """No fenced JSON -> fall back to no_context (defensive default)."""
    text = "The LLM forgot to produce JSON, just wrote prose."
    result = _parse_guardian_output(text)
    assert result["status"] == "no_context"


def test_parse_guardian_output_malformed_json():
    """Malformed JSON -> fall back to no_context, do not crash."""
    text = '```json\n{"status": "answered", "result":\n```'
    result = _parse_guardian_output(text)
    assert result["status"] == "no_context"


def test_parse_guardian_output_bare_json_no_fence():
    """Accept raw JSON without fence as a fallback."""
    text = '{"status": "answered", "result": "answer", "sources": ["a.md"]}'
    result = _parse_guardian_output(text)
    assert result["status"] == "answered"
    assert result["result"] == "answer"
