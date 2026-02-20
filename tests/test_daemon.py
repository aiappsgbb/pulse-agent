"""Tests for daemon/ modules — heartbeat utilities, sync, worker helpers."""

import asyncio
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from daemon.heartbeat import parse_interval, is_office_hours, check_missed_digest
from daemon.worker import _write_agent_response


# --- parse_interval ---

def test_parse_minutes():
    assert parse_interval("30m") == 1800
    assert parse_interval("5m") == 300
    assert parse_interval("1m") == 60


def test_parse_hours():
    assert parse_interval("1h") == 3600
    assert parse_interval("2h") == 7200


def test_parse_seconds():
    assert parse_interval("10s") == 10
    assert parse_interval("90s") == 90


def test_parse_bare_number():
    assert parse_interval("120") == 120


def test_parse_with_whitespace():
    assert parse_interval("  30m  ") == 1800


def test_parse_invalid_defaults():
    assert parse_interval("bogus") == 1800


def test_parse_case_insensitive():
    assert parse_interval("30M") == 1800
    assert parse_interval("1H") == 3600


# --- is_office_hours ---

def test_office_hours_no_config():
    """No office hours configured = always on."""
    assert is_office_hours({}) is True
    assert is_office_hours({"monitoring": {}}) is True


def test_office_hours_with_config():
    """Just verify it doesn't crash with a valid config."""
    config = {
        "monitoring": {
            "office_hours": {
                "start": "08:00",
                "end": "18:00",
                "days": [1, 2, 3, 4, 5],
            }
        }
    }
    result = is_office_hours(config)
    assert isinstance(result, bool)


# --- check_missed_digest ---


def test_check_missed_digest_today_exists(tmp_dir):
    """If today's digest exists, no job is queued."""
    digests_dir = tmp_dir / "digests"
    digests_dir.mkdir()
    today = datetime.now().strftime("%Y-%m-%d")
    (digests_dir / f"{today}.md").write_text("digest")
    queue = asyncio.Queue()
    with patch("daemon.heartbeat.OUTPUT_DIR", tmp_dir), \
         patch("tg.bot.get_proactive_chat_id", return_value=12345):
        check_missed_digest(queue)
    assert queue.empty()


def test_check_missed_digest_yesterday_exists(tmp_dir):
    """If yesterday's digest exists, no job is queued."""
    digests_dir = tmp_dir / "digests"
    digests_dir.mkdir()
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    (digests_dir / f"{yesterday}.md").write_text("digest")
    queue = asyncio.Queue()
    with patch("daemon.heartbeat.OUTPUT_DIR", tmp_dir), \
         patch("tg.bot.get_proactive_chat_id", return_value=12345):
        check_missed_digest(queue)
    assert queue.empty()


def test_check_missed_digest_neither_exists_queues(tmp_dir):
    """If neither today nor yesterday has a digest, a catch-up is queued."""
    (tmp_dir / "digests").mkdir()
    queue = asyncio.Queue()
    with patch("daemon.heartbeat.OUTPUT_DIR", tmp_dir), \
         patch("tg.bot.get_proactive_chat_id", return_value=12345):
        check_missed_digest(queue)
    assert not queue.empty()
    job = queue.get_nowait()
    assert job["type"] == "digest"
    assert job["_source"] == "catch-up"


def test_check_missed_digest_no_dir_queues(tmp_dir):
    """If digests/ directory doesn't exist, queue catch-up."""
    queue = asyncio.Queue()
    with patch("daemon.heartbeat.OUTPUT_DIR", tmp_dir), \
         patch("tg.bot.get_proactive_chat_id", return_value=12345):
        check_missed_digest(queue)
    assert not queue.empty()


# --- _build_digest_keyboard (inline buttons on digest message) ---


def test_build_digest_keyboard_returns_markup(tmp_dir):
    """_build_digest_keyboard builds InlineKeyboardMarkup from digest JSON."""
    import json
    from tg.bot import TelegramBot

    digest_data = {
        "date": "2026-02-20",
        "items": [
            {
                "id": "reply-esther-qbe",
                "type": "reply_needed",
                "priority": "urgent",
                "source": "Email from Esther",
                "title": "QBE Foundry Resolution",
                "suggested_actions": [
                    {"label": "Reply to Esther", "draft": "Hi Esther", "target": "Esther"}
                ],
            }
        ],
    }
    digests_dir = tmp_dir / "digests"
    digests_dir.mkdir()
    (digests_dir / "2026-02-20.json").write_text(json.dumps(digest_data), encoding="utf-8")

    bot = TelegramBot.__new__(TelegramBot)
    markup = bot._build_digest_keyboard(digests_dir)

    assert markup is not None
    # Row 0: action button, Row 1: dismiss button
    assert len(markup.inline_keyboard) == 2
    assert markup.inline_keyboard[0][0].text == "Reply to Esther"
    assert markup.inline_keyboard[0][0].callback_data == "action:reply-esther-qbe:0"
    assert "dismiss:" in markup.inline_keyboard[1][0].callback_data


def test_build_digest_keyboard_no_json(tmp_dir):
    """_build_digest_keyboard returns None when no digest JSON exists."""
    from tg.bot import TelegramBot

    digests_dir = tmp_dir / "digests"
    digests_dir.mkdir()

    bot = TelegramBot.__new__(TelegramBot)
    assert bot._build_digest_keyboard(digests_dir) is None


def test_build_digest_keyboard_no_actions(tmp_dir):
    """_build_digest_keyboard returns None when no items have suggested_actions."""
    import json
    from tg.bot import TelegramBot

    digest_data = {
        "date": "2026-02-20",
        "items": [
            {"id": "intel-foo", "type": "intel", "suggested_actions": []}
        ],
    }
    digests_dir = tmp_dir / "digests"
    digests_dir.mkdir()
    (digests_dir / "2026-02-20.json").write_text(json.dumps(digest_data), encoding="utf-8")

    bot = TelegramBot.__new__(TelegramBot)
    assert bot._build_digest_keyboard(digests_dir) is None


# --- _write_agent_response ---


def test_write_agent_response_creates_yaml(tmp_dir):
    """Response YAML is written to reply_to path with correct fields."""
    reply_dir = tmp_dir / "reply-jobs"
    reply_dir.mkdir()

    config = {"user": {"name": "Esther Barthel"}}
    job = {
        "type": "agent_request",
        "task": "What about Vodafone?",
        "from": "Artur Zielinski",
        "reply_to": str(reply_dir),
        "request_id": "abc-12345678",
    }

    _write_agent_response(config, job, "Vodafone deal is progressing well.")

    yaml_files = list(reply_dir.glob("*.yaml"))
    assert len(yaml_files) == 1
    data = yaml.safe_load(yaml_files[0].read_text())
    assert data["type"] == "agent_response"
    assert data["kind"] == "response"
    assert data["request_id"] == "abc-12345678"
    assert data["from"] == "Esther Barthel"
    assert "Vodafone" in data["result"]
    assert data["original_task"] == "What about Vodafone?"


def test_write_agent_response_no_reply_to(tmp_dir):
    """No crash and no file written when reply_to is empty."""
    config = {"user": {"name": "Esther"}}
    job = {"type": "agent_request", "task": "Test", "reply_to": ""}

    _write_agent_response(config, job, "Result")
    # Nothing should be written anywhere
    assert not list(tmp_dir.glob("**/*.yaml"))


def test_write_agent_response_creates_reply_dir(tmp_dir):
    """reply_to directory is created if it does not exist."""
    reply_dir = tmp_dir / "new-reply-dir"
    config = {"user": {"name": "Esther"}}
    job = {
        "type": "agent_request",
        "task": "Test",
        "reply_to": str(reply_dir),
        "request_id": "xyz-987",
    }

    _write_agent_response(config, job, "Answer here.")
    assert reply_dir.exists()
    assert len(list(reply_dir.glob("*.yaml"))) == 1
