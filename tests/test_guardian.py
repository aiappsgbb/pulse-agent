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


# --- Guardian session flow ---

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml


@pytest.mark.asyncio
async def test_handle_agent_request_writes_response_yaml(tmp_path, monkeypatch):
    """Guardian session flow: fake LLM returns structured JSON, worker writes YAML."""
    from daemon.worker import _handle_agent_request

    reply_to = tmp_path / "reply"
    reply_to.mkdir()

    job = {
        "type": "agent_request",
        "kind": "broadcast",
        "task": "Any context on Fabric-on-SAP?",
        "project_id": "fabric-sap-engagement",
        "from": "Artur Zielinski",
        "from_alias": "artur",
        "reply_to": str(reply_to),
        "request_id": "test-req-123",
        "created_at": "2026-04-23T10:00:00",
    }
    config = {"user": {"name": "Beta User", "alias": "beta"}}

    # Mock the Guardian session to return a happy-path answer
    fake_output = '''```json
{"status": "answered", "result": "Found 2 POCs in my notes.", "sources": ["transcripts/a.md"]}
```'''
    fake_run = AsyncMock(return_value=fake_output)
    monkeypatch.setattr("daemon.worker._run_guardian_session", fake_run)

    client = MagicMock()
    await _handle_agent_request(client, config, job)

    yaml_files = list(reply_to.glob("*.yaml"))
    assert len(yaml_files) == 1
    data = yaml.safe_load(yaml_files[0].read_text())
    assert data["type"] == "agent_response"
    assert data["status"] == "answered"
    assert data["project_id"] == "fabric-sap-engagement"
    assert data["request_id"] == "test-req-123"
    assert data["from"] == "Beta User"
    assert data["result"] == "Found 2 POCs in my notes."
    assert data["sources"] == ["transcripts/a.md"]


@pytest.mark.asyncio
async def test_handle_agent_request_no_context_writes_minimal_response(tmp_path, monkeypatch):
    """no_context responses still write a YAML so the sender can log+dedup."""
    from daemon.worker import _handle_agent_request

    reply_to = tmp_path / "reply"
    reply_to.mkdir()
    job = {
        "type": "agent_request",
        "task": "something obscure",
        "project_id": "some-project",
        "from": "Artur",
        "from_alias": "artur",
        "reply_to": str(reply_to),
        "request_id": "test-req-456",
        "created_at": "2026-04-23T10:00:00",
    }
    config = {"user": {"name": "Beta", "alias": "beta"}}

    fake_run = AsyncMock(return_value='```json\n{"status": "no_context"}\n```')
    monkeypatch.setattr("daemon.worker._run_guardian_session", fake_run)

    await _handle_agent_request(MagicMock(), config, job)

    yaml_files = list(reply_to.glob("*.yaml"))
    assert len(yaml_files) == 1
    data = yaml.safe_load(yaml_files[0].read_text())
    assert data["status"] == "no_context"
    assert data["project_id"] == "some-project"
    assert data.get("result", "") == ""


@pytest.mark.asyncio
async def test_handle_agent_request_declined_preserves_reason(tmp_path, monkeypatch):
    """declined responses include the reason field in the written YAML."""
    from daemon.worker import _handle_agent_request

    reply_to = tmp_path / "reply"
    reply_to.mkdir()
    job = {
        "type": "agent_request",
        "task": "what's the customer name?",
        "project_id": "contoso-engagement",
        "from": "Artur",
        "from_alias": "artur",
        "reply_to": str(reply_to),
        "request_id": "test-req-declined",
        "created_at": "2026-04-23T10:00:00",
    }
    config = {"user": {"name": "Beta", "alias": "beta"}}

    fake_run = AsyncMock(return_value='```json\n{"status": "declined", "reason": "customer name is sensitive"}\n```')
    monkeypatch.setattr("daemon.worker._run_guardian_session", fake_run)

    await _handle_agent_request(MagicMock(), config, job)

    yaml_files = list(reply_to.glob("*.yaml"))
    assert len(yaml_files) == 1
    data = yaml.safe_load(yaml_files[0].read_text())
    assert data["status"] == "declined"
    assert data["reason"] == "customer name is sensitive"
    assert data["project_id"] == "contoso-engagement"
