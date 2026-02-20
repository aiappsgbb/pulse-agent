"""Tests for tg/ modules — bot utilities, message splitting, confirmations."""

import asyncio
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tg.bot import md_to_telegram_html, split_message, TelegramBot
from tg.confirmations import has_pending_confirmation, resolve_confirmation, wait_for_confirmation


# --- md_to_telegram_html ---

def test_bold():
    assert "<b>bold</b>" in md_to_telegram_html("**bold**")


def test_italic():
    assert "<i>italic</i>" in md_to_telegram_html("*italic*")


def test_inline_code():
    assert "<code>code</code>" in md_to_telegram_html("`code`")


def test_code_block():
    result = md_to_telegram_html("```python\nprint('hi')\n```")
    assert "<pre>" in result


def test_header():
    result = md_to_telegram_html("## My Header")
    assert "<b>My Header</b>" in result


def test_html_escaping():
    result = md_to_telegram_html("<script>alert('xss')</script>")
    assert "<script>" not in result
    assert "&lt;script&gt;" in result


def test_mixed():
    result = md_to_telegram_html("**bold** and *italic* and `code`")
    assert "<b>bold</b>" in result
    assert "<i>italic</i>" in result
    assert "<code>code</code>" in result


# --- split_message ---

def test_split_short():
    """Short text returns as-is in a single chunk."""
    assert split_message("hello") == ["hello"]


def test_split_exact_limit():
    """Text exactly at the limit stays in one chunk."""
    text = "a" * 4000
    assert split_message(text) == [text]


def test_split_at_newline():
    """Text splits at newline boundary when possible."""
    text = "a" * 3000 + "\n" + "b" * 2000
    chunks = split_message(text)
    assert len(chunks) == 2
    assert chunks[0] == "a" * 3000
    assert chunks[1] == "b" * 2000


def test_split_at_space():
    """Falls back to space boundary when no newline is near the limit."""
    text = "word " * 900  # ~4500 chars
    chunks = split_message(text)
    assert len(chunks) >= 2
    for chunk in chunks:
        assert len(chunk) <= 4000


def test_split_hard_cut():
    """Hard cut when no newlines or spaces exist."""
    text = "a" * 8000
    chunks = split_message(text)
    assert len(chunks) == 2
    assert len(chunks[0]) == 4000
    assert len(chunks[1]) == 4000


def test_split_custom_max():
    """Custom max_len is respected."""
    text = "Hello world this is a test"
    chunks = split_message(text, max_len=12)
    assert len(chunks) >= 2
    for chunk in chunks:
        assert len(chunk) <= 12


def test_split_empty():
    """Empty text returns single empty chunk."""
    assert split_message("") == [""]


# --- confirmations ---

def test_has_pending_empty():
    assert has_pending_confirmation({}, 12345) is False


def test_has_pending_with_entry():
    pending = {12345: asyncio.Future()}
    assert has_pending_confirmation(pending, 12345) is True
    assert has_pending_confirmation(pending, 99999) is False


def test_resolve_confirmation():
    loop = asyncio.new_event_loop()
    fut = loop.create_future()
    pending = {12345: fut}
    resolve_confirmation(pending, 12345, "yes")
    assert 12345 not in pending


@pytest.mark.asyncio
async def test_wait_for_confirmation_resolved():
    pending = {}

    async def resolve_after_delay():
        await asyncio.sleep(0.05)
        from tg.confirmations import resolve_confirmation as _resolve
        _resolve(pending, 12345, "approved")

    asyncio.create_task(resolve_after_delay())
    result = await wait_for_confirmation(pending, 12345, timeout=2)
    assert result == "approved"


@pytest.mark.asyncio
async def test_wait_for_confirmation_timeout():
    pending = {}
    with pytest.raises(asyncio.TimeoutError):
        await wait_for_confirmation(pending, 12345, timeout=0.05)


# --- TelegramBot._find_action_draft ---


def test_find_action_draft_from_json(tmp_dir):
    """_find_action_draft reads the latest monitoring JSON and finds the action."""
    from unittest.mock import patch
    import json

    triage_data = {
        "timestamp": "2026-02-20T10:00",
        "items": [
            {
                "id": "reply-sarah-havas",
                "type": "reply_needed",
                "priority": "high",
                "source": "Teams: Sarah",
                "summary": "Asking about Havas",
                "context": "Yesterday's dry-run",
                "suggested_actions": [
                    {
                        "label": "Draft: We pivoted to Y",
                        "action_type": "draft_teams_reply",
                        "draft": "Hey Sarah, we pivoted to Y.",
                        "target": "Sarah",
                    }
                ],
            }
        ],
    }
    (tmp_dir / "monitoring-2026-02-20T10-00.json").write_text(json.dumps(triage_data), encoding="utf-8")

    bot = TelegramBot.__new__(TelegramBot)
    with patch("tg.bot.OUTPUT_DIR", tmp_dir):
        result = bot._find_action_draft("reply-sarah-havas", 0)
    assert result is not None
    assert result["draft"] == "Hey Sarah, we pivoted to Y."
    assert result["target"] == "Sarah"


def test_find_action_draft_missing_item(tmp_dir):
    """Returns None when item ID doesn't match."""
    from unittest.mock import patch
    import json

    triage_data = {"items": [{"id": "other-item", "suggested_actions": []}]}
    (tmp_dir / "monitoring-2026-02-20T10-00.json").write_text(json.dumps(triage_data), encoding="utf-8")

    bot = TelegramBot.__new__(TelegramBot)
    with patch("tg.bot.OUTPUT_DIR", tmp_dir):
        result = bot._find_action_draft("nonexistent", 0)
    assert result is None


def test_find_action_draft_no_json(tmp_dir):
    """Returns None when no monitoring or digest JSON exists."""
    from unittest.mock import patch

    bot = TelegramBot.__new__(TelegramBot)
    with patch("tg.bot.OUTPUT_DIR", tmp_dir):
        result = bot._find_action_draft("any-id", 0)
    assert result is None


def test_find_action_draft_from_digest_json(tmp_dir):
    """_find_action_draft falls back to digest JSON when monitoring has no match."""
    from unittest.mock import patch
    import json

    digest_data = {
        "date": "2026-02-20",
        "items": [
            {
                "id": "reply-esther-qbe",
                "type": "reply_needed",
                "priority": "urgent",
                "source": "Email from Esther",
                "title": "QBE Foundry",
                "summary": "Needs prioritization",
                "suggested_actions": [
                    {
                        "label": "Reply to Esther",
                        "action_type": "send_email_reply",
                        "draft": "Hi Esther, I'll prioritize this today.",
                        "target": "Esther Dediashvili",
                    }
                ],
            }
        ],
    }
    digests_dir = tmp_dir / "digests"
    digests_dir.mkdir()
    (digests_dir / "2026-02-20.json").write_text(json.dumps(digest_data), encoding="utf-8")

    bot = TelegramBot.__new__(TelegramBot)
    with patch("tg.bot.OUTPUT_DIR", tmp_dir):
        result = bot._find_action_draft("reply-esther-qbe", 0)
    assert result is not None
    assert result["draft"] == "Hi Esther, I'll prioritize this today."
    assert result["action_type"] == "send_email_reply"


def test_find_action_draft_monitoring_takes_precedence(tmp_dir):
    """Monitoring JSON is searched before digest JSON."""
    from unittest.mock import patch
    import json

    monitoring_data = {
        "items": [{
            "id": "reply-bob",
            "suggested_actions": [{"draft": "from monitoring", "target": "Bob"}],
        }],
    }
    digest_data = {
        "items": [{
            "id": "reply-bob",
            "suggested_actions": [{"draft": "from digest", "target": "Bob"}],
        }],
    }
    (tmp_dir / "monitoring-2026-02-20T10-00.json").write_text(json.dumps(monitoring_data), encoding="utf-8")
    digests_dir = tmp_dir / "digests"
    digests_dir.mkdir()
    (digests_dir / "2026-02-20.json").write_text(json.dumps(digest_data), encoding="utf-8")

    bot = TelegramBot.__new__(TelegramBot)
    with patch("tg.bot.OUTPUT_DIR", tmp_dir):
        result = bot._find_action_draft("reply-bob", 0)
    assert result["draft"] == "from monitoring"


# --- _build_action_prompt ---


def test_build_action_prompt_teams_reply():
    prompt, status = TelegramBot._build_action_prompt(
        "draft_teams_reply", "Alice", "Hey, sounds good!", ""
    )
    assert "Teams message" in prompt
    assert "Alice" in prompt
    assert "Hey, sounds good!" in prompt
    assert "Alice" in status


def test_build_action_prompt_email_reply():
    prompt, status = TelegramBot._build_action_prompt(
        "send_email_reply", "Bob", "Thanks for the update.", ""
    )
    assert "Reply to the email" in prompt
    assert "Bob" in prompt
    assert "Thanks for the update." in prompt
    assert "email" in status.lower()


def test_build_action_prompt_schedule_meeting():
    prompt, status = TelegramBot._build_action_prompt(
        "schedule_meeting", "Charlie", "", "30min with Charlie about Q3 planning"
    )
    assert "Schedule a meeting" in prompt
    assert "30min with Charlie" in prompt
    assert "Copilot" in status


def test_build_action_prompt_schedule_meeting_uses_draft_fallback():
    """When metadata is empty, falls back to draft for meeting details."""
    prompt, _ = TelegramBot._build_action_prompt(
        "schedule_meeting", "Dave", "Meet with Dave about review", ""
    )
    assert "Meet with Dave about review" in prompt


def test_build_action_prompt_unknown_type_defaults_to_teams():
    """Unrecognized action types default to Teams reply."""
    prompt, status = TelegramBot._build_action_prompt(
        "some_future_type", "Eve", "Hello!", ""
    )
    assert "Teams message" in prompt
    assert "Eve" in status
