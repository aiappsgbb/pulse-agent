"""Tests for sdk/tools.py — custom GHCP SDK tool handlers."""

import json
from unittest.mock import patch

import pytest
import yaml

from sdk.tools import (
    get_tools,
    load_actions,
    log_action,
    write_output,
    queue_task,
    dismiss_item,
    add_note,
)


# --- load_actions ---


def test_load_actions_missing_file(tmp_dir):
    with patch("sdk.tools.ACTIONS_FILE", tmp_dir / "missing.json"):
        result = load_actions()
    assert result == {"dismissed": [], "notes": {}}


def test_load_actions_existing_file(tmp_dir):
    actions_file = tmp_dir / ".digest-actions.json"
    actions_file.write_text(json.dumps({
        "dismissed": [{"item": "x"}],
        "notes": {"y": {"note": "z"}},
    }))
    with patch("sdk.tools.ACTIONS_FILE", actions_file):
        result = load_actions()
    assert len(result["dismissed"]) == 1
    assert result["notes"]["y"]["note"] == "z"


# --- log_action ---


async def test_log_action_writes_jsonl(tmp_dir):
    with patch("sdk.tools.LOGS_DIR", tmp_dir):
        result = await log_action.handler({"arguments": {"action": "tested", "reasoning": "because tests", "category": "test"}})
    assert result["resultType"] == "success"
    assert "Logged" in result["textResultForLlm"]
    log_files = list(tmp_dir.glob("*.jsonl"))
    assert len(log_files) == 1
    entry = json.loads(log_files[0].read_text().strip())
    assert entry["action"] == "tested"


# --- write_output ---


async def test_write_output_creates_file(tmp_dir):
    with patch("sdk.tools.OUTPUT_DIR", tmp_dir):
        result = await write_output.handler({"arguments": {"filename": "test.md", "content": "# Hello"}})
    assert result["resultType"] == "success"
    assert (tmp_dir / "test.md").read_text() == "# Hello"


async def test_write_output_creates_subdirectory(tmp_dir):
    with patch("sdk.tools.OUTPUT_DIR", tmp_dir):
        await write_output.handler({"arguments": {"filename": "digests/2026-02-18.md", "content": "# Digest"}})
    assert (tmp_dir / "digests" / "2026-02-18.md").exists()


async def test_write_output_path_traversal_blocked(tmp_dir):
    with patch("sdk.tools.OUTPUT_DIR", tmp_dir):
        result = await write_output.handler({"arguments": {"filename": "../../etc/passwd", "content": "pwned"}})
    assert "ERROR" in result["textResultForLlm"]
    assert not (tmp_dir.parent.parent / "etc" / "passwd").exists()


# --- queue_task ---


async def test_queue_task_creates_yaml(tmp_dir):
    with patch("sdk.tools.TASKS_DIR", tmp_dir):
        result = await queue_task.handler({"arguments": {"type": "research", "task": "Test Research", "description": "A test"}})
    assert result["resultType"] == "success"
    pending = tmp_dir / "pending"
    yaml_files = list(pending.glob("*.yaml"))
    assert len(yaml_files) == 1
    data = yaml.safe_load(yaml_files[0].read_text())
    assert data["type"] == "research"
    assert data["task"] == "Test Research"


# --- dismiss_item ---


async def test_dismiss_item_persists(tmp_dir):
    actions_file = tmp_dir / ".digest-actions.json"
    with patch("sdk.tools.ACTIONS_FILE", actions_file):
        result = await dismiss_item.handler({"arguments": {"item": "reply-alice-budget", "reason": "already replied"}})
        assert "Dismissed" in result["textResultForLlm"]
        actions = load_actions()
    assert len(actions["dismissed"]) == 1
    assert actions["dismissed"][0]["item"] == "reply-alice-budget"


# --- add_note ---


async def test_add_note_persists(tmp_dir):
    actions_file = tmp_dir / ".digest-actions.json"
    with patch("sdk.tools.ACTIONS_FILE", actions_file):
        result = await add_note.handler({"arguments": {"item": "escalation-vodafone", "note": "Waiting on PM response"}})
        assert "Note added" in result["textResultForLlm"]
        actions = load_actions()
    assert "escalation-vodafone" in actions["notes"]
    assert actions["notes"]["escalation-vodafone"]["note"] == "Waiting on PM response"


# --- get_tools ---


def test_get_tools_returns_five():
    tools = get_tools()
    assert len(tools) == 5
    names = {t.name for t in tools}
    assert names == {"log_action", "write_output", "queue_task", "dismiss_item", "add_note"}
