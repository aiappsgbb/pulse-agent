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
