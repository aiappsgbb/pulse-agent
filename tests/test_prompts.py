"""Assertions about prompt file contents - guardrails against silent drift."""
from pathlib import Path

from core.constants import PROJECT_ROOT


def _read(rel_path: str) -> str:
    return (PROJECT_ROOT / rel_path).read_text(encoding="utf-8")


def test_digest_writer_has_team_enrichment_directive():
    text = _read("config/prompts/agents/digest-writer.md")
    assert "Team Enrichment" in text or "team enrichment" in text.lower()
    assert "broadcast_to_team" in text
    assert "last_team_enrichment" in text
    assert "questions" in text.lower()


def test_chat_has_broadcast_routing_instruction():
    text = _read("config/prompts/system/chat.md")
    assert "broadcast_to_team" in text
    assert "project_id" in text
