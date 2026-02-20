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
    schedule_task,
    list_schedules_tool,
    cancel_schedule,
    search_local_files,
    send_teams_message,
    send_email_reply,
    send_task_to_agent,
    PENDING_ACTIONS_DIR,
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


def test_get_tools_returns_all():
    tools = get_tools()
    assert len(tools) == 12
    names = {t.name for t in tools}
    assert names == {
        "log_action", "write_output", "queue_task", "dismiss_item", "add_note",
        "schedule_task", "list_schedules", "cancel_schedule",
        "search_local_files", "send_teams_message", "send_email_reply",
        "send_task_to_agent",
    }


# --- schedule_task ---


async def test_schedule_task_creates(tmp_dir):
    sched_file = tmp_dir / ".scheduler.json"
    with patch("core.scheduler.SCHEDULER_FILE", sched_file):
        result = await schedule_task.handler({"arguments": {
            "id": "morning-digest", "type": "digest",
            "pattern": "weekdays 07:00", "description": "Morning digest",
        }})
    assert result["resultType"] == "success"
    assert "Scheduled" in result["textResultForLlm"]


async def test_schedule_task_invalid_pattern(tmp_dir):
    sched_file = tmp_dir / ".scheduler.json"
    with patch("core.scheduler.SCHEDULER_FILE", sched_file):
        result = await schedule_task.handler({"arguments": {
            "id": "bad", "type": "digest", "pattern": "nope",
        }})
    assert "ERROR" in result["textResultForLlm"]


# --- list_schedules ---


async def test_list_schedules_empty(tmp_dir):
    sched_file = tmp_dir / ".scheduler.json"
    with patch("core.scheduler.SCHEDULER_FILE", sched_file):
        result = await list_schedules_tool.handler({"arguments": {}})
    assert "No schedules" in result["textResultForLlm"]


async def test_list_schedules_with_entries(tmp_dir):
    sched_file = tmp_dir / ".scheduler.json"
    with patch("core.scheduler.SCHEDULER_FILE", sched_file):
        await schedule_task.handler({"arguments": {
            "id": "test-sched", "type": "intel", "pattern": "daily 18:00",
        }})
        result = await list_schedules_tool.handler({"arguments": {}})
    assert "test-sched" in result["textResultForLlm"]
    assert "daily 18:00" in result["textResultForLlm"]


# --- cancel_schedule ---


async def test_cancel_schedule_exists(tmp_dir):
    sched_file = tmp_dir / ".scheduler.json"
    with patch("core.scheduler.SCHEDULER_FILE", sched_file):
        await schedule_task.handler({"arguments": {
            "id": "to-cancel", "type": "digest", "pattern": "every 6h",
        }})
        result = await cancel_schedule.handler({"arguments": {"id": "to-cancel"}})
    assert "Cancelled" in result["textResultForLlm"]


async def test_cancel_schedule_not_found(tmp_dir):
    sched_file = tmp_dir / ".scheduler.json"
    with patch("core.scheduler.SCHEDULER_FILE", sched_file):
        result = await cancel_schedule.handler({"arguments": {"id": "nope"}})
    assert "not found" in result["textResultForLlm"]


# --- search_local_files ---


async def test_search_local_files_finds_match(tmp_dir):
    input_dir = tmp_dir / "input" / "transcripts"
    input_dir.mkdir(parents=True)
    (input_dir / "meeting.txt").write_text("Alice discussed the Havas project timeline.\nBob agreed.", encoding="utf-8")
    with patch("core.constants.INPUT_DIR", tmp_dir / "input"):
        result = await search_local_files.handler({"arguments": {"query": "Havas", "file_pattern": "*.txt"}})
    assert result["resultType"] == "success"
    assert "Havas" in result["textResultForLlm"]
    assert "meeting.txt" in result["textResultForLlm"]


async def test_search_local_files_no_match(tmp_dir):
    input_dir = tmp_dir / "input" / "transcripts"
    input_dir.mkdir(parents=True)
    (input_dir / "meeting.txt").write_text("Nothing relevant here.", encoding="utf-8")
    with patch("core.constants.INPUT_DIR", tmp_dir / "input"):
        result = await search_local_files.handler({"arguments": {"query": "Havas", "file_pattern": "*.txt"}})
    assert "No matches" in result["textResultForLlm"]


async def test_search_local_files_no_input_dir(tmp_dir):
    with patch("core.constants.INPUT_DIR", tmp_dir / "nonexistent"):
        result = await search_local_files.handler({"arguments": {"query": "test"}})
    assert "No input directory" in result["textResultForLlm"]


async def test_search_local_files_path_traversal_blocked(tmp_dir):
    input_dir = tmp_dir / "input"
    input_dir.mkdir(parents=True)
    with patch("core.constants.INPUT_DIR", input_dir):
        result = await search_local_files.handler({"arguments": {"query": "test", "file_pattern": "../../*.txt"}})
    assert "ERROR" in result["textResultForLlm"]


async def test_search_local_files_context_lines(tmp_dir):
    input_dir = tmp_dir / "input"
    input_dir.mkdir(parents=True)
    lines = ["line1", "line2", "line3 has TARGET word", "line4", "line5", "line6"]
    (input_dir / "doc.txt").write_text("\n".join(lines), encoding="utf-8")
    with patch("core.constants.INPUT_DIR", input_dir):
        result = await search_local_files.handler({"arguments": {"query": "TARGET"}})
    text = result["textResultForLlm"]
    assert "line2" in text  # context before
    assert "line4" in text  # context after


# --- send_teams_message ---


async def test_send_teams_message_queues_action(tmp_dir):
    pending = tmp_dir / ".pending-actions"
    with patch("sdk.tools.PENDING_ACTIONS_DIR", pending):
        result = await send_teams_message.handler({"arguments": {
            "recipient": "Alice", "message": "Hello there",
        }})
    assert result["resultType"] == "success"
    assert "queued" in result["textResultForLlm"].lower()
    assert "Alice" in result["textResultForLlm"]

    # Verify the action file was created
    action_files = list(pending.glob("teams-send-*.json"))
    assert len(action_files) == 1
    data = json.loads(action_files[0].read_text())
    assert data["type"] == "teams_send"
    assert data["recipient"] == "Alice"
    assert data["message"] == "Hello there"


async def test_send_teams_message_with_chat_name(tmp_dir):
    pending = tmp_dir / ".pending-actions"
    with patch("sdk.tools.PENDING_ACTIONS_DIR", pending):
        result = await send_teams_message.handler({"arguments": {
            "recipient": "", "message": "Reply text",
            "chat_name": "Project Discussion",
        }})
    assert "Project Discussion" in result["textResultForLlm"]
    data = json.loads(list(pending.glob("*.json"))[0].read_text())
    assert data["chat_name"] == "Project Discussion"


# --- send_email_reply ---


async def test_send_email_reply_queues_action(tmp_dir):
    pending = tmp_dir / ".pending-actions"
    with patch("sdk.tools.PENDING_ACTIONS_DIR", pending):
        result = await send_email_reply.handler({"arguments": {
            "search_query": "Bob budget review", "message": "Approved, thanks!",
        }})
    assert result["resultType"] == "success"
    assert "queued" in result["textResultForLlm"].lower()

    action_files = list(pending.glob("email-reply-*.json"))
    assert len(action_files) == 1
    data = json.loads(action_files[0].read_text())
    assert data["type"] == "email_reply"
    assert data["search_query"] == "Bob budget review"
    assert data["message"] == "Approved, thanks!"


# --- send_task_to_agent ---


async def test_send_task_to_agent_success(tmp_dir):
    (tmp_dir / "alice-onedrive").mkdir()
    team_config = {
        "team": [
            {"name": "Alice Test", "alias": "alice", "agent_path": str(tmp_dir / "alice-onedrive")},
        ],
        "user": {"name": "Artur Zielinski"},
        "onedrive": {"path": str(tmp_dir / "my-onedrive")},
    }
    with patch("core.config.load_config", return_value=team_config):
        result = await send_task_to_agent.handler({"arguments": {
            "agent": "alice",
            "task": "What do you know about Vodafone?",
            "kind": "question",
        }})
    assert result["resultType"] == "success"
    assert "Alice Test" in result["textResultForLlm"]
    assert "Request ID" in result["textResultForLlm"]

    # Verify YAML was written to alice's Jobs folder
    jobs_dir = tmp_dir / "alice-onedrive" / "Jobs"
    yaml_files = list(jobs_dir.glob("*.yaml"))
    assert len(yaml_files) == 1
    data = yaml.safe_load(yaml_files[0].read_text())
    assert data["type"] == "agent_request"
    assert data["kind"] == "question"
    assert data["from"] == "Artur Zielinski"
    assert data["reply_to"]  # non-empty
    assert data["request_id"]  # UUID present
    assert "Vodafone" in data["task"]


async def test_send_task_to_agent_unknown_agent(tmp_dir):
    team_config = {
        "team": [
            {"name": "Alice Test", "alias": "alice", "agent_path": str(tmp_dir / "alice")},
        ],
    }
    with patch("core.config.load_config", return_value=team_config):
        result = await send_task_to_agent.handler({"arguments": {
            "agent": "nobody",
            "task": "Hello?",
        }})
    assert "ERROR" in result["textResultForLlm"]
    assert "not found" in result["textResultForLlm"]
    assert "alice" in result["textResultForLlm"]


async def test_send_task_to_agent_no_team_config(tmp_dir):
    with patch("core.config.load_config", return_value={"team": []}):
        result = await send_task_to_agent.handler({"arguments": {
            "agent": "anyone",
            "task": "Hello",
        }})
    assert "ERROR" in result["textResultForLlm"]
    assert "not found" in result["textResultForLlm"]


async def test_send_task_to_agent_path_not_accessible(tmp_dir):
    team_config = {
        "team": [
            {"name": "Alice", "alias": "alice", "agent_path": str(tmp_dir / "nonexistent-path")},
        ],
    }
    with patch("core.config.load_config", return_value=team_config):
        result = await send_task_to_agent.handler({"arguments": {
            "agent": "alice",
            "task": "Hello",
        }})
    assert "ERROR" in result["textResultForLlm"]
    assert "not accessible" in result["textResultForLlm"]


async def test_send_task_to_agent_case_insensitive(tmp_dir):
    (tmp_dir / "alice").mkdir()
    team_config = {
        "team": [
            {"name": "Alice Test", "alias": "alice", "agent_path": str(tmp_dir / "alice")},
        ],
        "user": {"name": "Artur"},
        "onedrive": {"path": str(tmp_dir)},
    }
    with patch("core.config.load_config", return_value=team_config):
        result = await send_task_to_agent.handler({"arguments": {
            "agent": "Alice",
            "task": "Test case insensitivity",
        }})
    assert result["resultType"] == "success"


async def test_send_task_to_agent_match_by_name(tmp_dir):
    (tmp_dir / "alice").mkdir()
    team_config = {
        "team": [
            {"name": "Alice Test", "alias": "alice", "agent_path": str(tmp_dir / "alice")},
        ],
        "user": {"name": "Artur"},
        "onedrive": {"path": str(tmp_dir)},
    }
    with patch("core.config.load_config", return_value=team_config):
        result = await send_task_to_agent.handler({"arguments": {
            "agent": "alice test",
            "task": "Test name matching",
        }})
    assert result["resultType"] == "success"
