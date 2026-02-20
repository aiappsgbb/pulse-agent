"""Tests for daemon/ modules — heartbeat utilities, sync."""

import asyncio
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from daemon.heartbeat import parse_interval, is_office_hours, check_missed_digest


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


# --- _send_digest_actions ---


@pytest.mark.asyncio
async def test_send_digest_actions_calls_triage_buttons(tmp_dir):
    """_send_digest_actions loads digest JSON and sends action buttons."""
    import json
    from unittest.mock import AsyncMock

    digest_data = {
        "date": "2026-02-20",
        "items": [
            {
                "id": "reply-esther-qbe",
                "type": "reply_needed",
                "priority": "urgent",
                "source": "Email from Esther",
                "suggested_actions": [
                    {"label": "Reply", "draft": "Hi Esther", "target": "Esther"}
                ],
            }
        ],
    }
    digests_dir = tmp_dir / "digests"
    digests_dir.mkdir()
    (digests_dir / "2026-02-20.json").write_text(json.dumps(digest_data), encoding="utf-8")

    mock_send = AsyncMock()
    with patch("daemon.worker.OUTPUT_DIR", tmp_dir), \
         patch("tg.bot.send_triage_actions", mock_send):
        from daemon.worker import _send_digest_actions
        await _send_digest_actions(12345)

    mock_send.assert_called_once()
    call_args = mock_send.call_args
    assert call_args[0][0] == 12345  # chat_id
    assert call_args[0][1]["items"][0]["id"] == "reply-esther-qbe"


@pytest.mark.asyncio
async def test_send_digest_actions_no_json(tmp_dir):
    """_send_digest_actions does nothing when no digest JSON exists."""
    from unittest.mock import AsyncMock

    mock_send = AsyncMock()
    with patch("daemon.worker.OUTPUT_DIR", tmp_dir), \
         patch("tg.bot.send_triage_actions", mock_send):
        from daemon.worker import _send_digest_actions
        await _send_digest_actions(12345)

    mock_send.assert_not_called()
