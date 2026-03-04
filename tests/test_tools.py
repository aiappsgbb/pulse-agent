"""Tests for sdk/tools.py — custom GHCP SDK tool handlers."""

import json
from unittest.mock import patch

import pytest
import yaml

from sdk.tools import (
    get_tools,
    load_actions,
    write_output,
    queue_task,
    dismiss_item,
    add_note,
    schedule_task,
    list_schedules_tool,
    update_schedule_tool,
    cancel_schedule,
    search_local_files,
    update_project,
    _find_similar_projects,
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
    with patch("sdk.tools.JOBS_DIR", tmp_dir):
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
        assert "Archived" in result["textResultForLlm"]
        actions = load_actions()
    assert len(actions["dismissed"]) == 1
    assert actions["dismissed"][0]["item"] == "reply-alice-budget"
    assert actions["dismissed"][0]["status"] == "archived"


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
    assert len(tools) == 15
    names = {t.name for t in tools}
    assert names == {
        "write_output", "queue_task", "dismiss_item", "add_note",
        "schedule_task", "list_schedules", "update_schedule", "cancel_schedule",
        "search_local_files", "update_project",
        "send_teams_message", "send_email_reply",
        "send_task_to_agent", "save_config",
        "sweep_inbox",
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


# --- update_schedule ---


async def test_update_schedule_changes_pattern(tmp_dir):
    sched_file = tmp_dir / ".scheduler.json"
    with patch("core.scheduler.SCHEDULER_FILE", sched_file):
        await schedule_task.handler({"arguments": {
            "id": "triage", "type": "monitor", "pattern": "every 30m",
        }})
        result = await update_schedule_tool.handler({"arguments": {
            "id": "triage", "pattern": "every 15m",
        }})
    assert "Updated" in result["textResultForLlm"]
    assert "every 15m" in result["textResultForLlm"]


async def test_update_schedule_not_found(tmp_dir):
    sched_file = tmp_dir / ".scheduler.json"
    with patch("core.scheduler.SCHEDULER_FILE", sched_file):
        result = await update_schedule_tool.handler({"arguments": {
            "id": "nope", "pattern": "every 15m",
        }})
    assert "not found" in result["textResultForLlm"]


async def test_update_schedule_invalid_pattern(tmp_dir):
    sched_file = tmp_dir / ".scheduler.json"
    with patch("core.scheduler.SCHEDULER_FILE", sched_file):
        await schedule_task.handler({"arguments": {
            "id": "triage", "type": "monitor", "pattern": "every 30m",
        }})
        result = await update_schedule_tool.handler({"arguments": {
            "id": "triage", "pattern": "nope",
        }})
    assert "ERROR" in result["textResultForLlm"]


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


def _search_patches(tmp_dir, **overrides):
    """Build patch context managers for search_local_files tests.

    All dirs default to nonexistent.  Pass dir_name=path to override.
    Always patches PULSE_HOME to nonexistent unless explicitly provided.
    """
    nonexistent = tmp_dir / "nonexistent"
    dirs = {
        "TRANSCRIPTS_DIR": nonexistent,
        "DOCUMENTS_DIR": nonexistent,
        "EMAILS_DIR": nonexistent,
        "TEAMS_MESSAGES_DIR": nonexistent,
        "DIGESTS_DIR": nonexistent,
        "INTEL_DIR": nonexistent,
        "PROJECTS_DIR": nonexistent,
        "PULSE_HOME": nonexistent,
    }
    dirs.update(overrides)
    from contextlib import ExitStack
    stack = ExitStack()
    for name, path in dirs.items():
        stack.enter_context(patch(f"sdk.tools.{name}", path))
    return stack


async def test_search_local_files_finds_match(tmp_dir):
    transcripts_dir = tmp_dir / "transcripts"
    transcripts_dir.mkdir(parents=True)
    (transcripts_dir / "meeting.txt").write_text("Alice discussed the Havas project timeline.\nBob agreed.", encoding="utf-8")
    with _search_patches(tmp_dir, TRANSCRIPTS_DIR=transcripts_dir):
        result = await search_local_files.handler({"arguments": {"query": "Havas", "file_pattern": "*.txt"}})
    assert result["resultType"] == "success"
    assert "Havas" in result["textResultForLlm"]
    assert "meeting.txt" in result["textResultForLlm"]


async def test_search_local_files_no_match(tmp_dir):
    transcripts_dir = tmp_dir / "transcripts"
    transcripts_dir.mkdir(parents=True)
    (transcripts_dir / "meeting.txt").write_text("Nothing relevant here.", encoding="utf-8")
    with _search_patches(tmp_dir, TRANSCRIPTS_DIR=transcripts_dir):
        result = await search_local_files.handler({"arguments": {"query": "Havas", "file_pattern": "*.txt"}})
    assert "No matches" in result["textResultForLlm"]


async def test_search_local_files_no_match_has_workiq_hint(tmp_dir):
    """When no results found, response should hint to try WorkIQ."""
    transcripts_dir = tmp_dir / "transcripts"
    transcripts_dir.mkdir(parents=True)
    (transcripts_dir / "meeting.txt").write_text("Nothing relevant here.", encoding="utf-8")
    with _search_patches(tmp_dir, TRANSCRIPTS_DIR=transcripts_dir):
        result = await search_local_files.handler({"arguments": {"query": "nonexistent topic"}})
    text = result["textResultForLlm"]
    assert "No matches" in text
    assert "WorkIQ" in text


async def test_search_local_files_no_dirs(tmp_dir):
    nonexistent = tmp_dir / "nonexistent"
    with _search_patches(tmp_dir, PULSE_HOME=nonexistent / "ph"):
        result = await search_local_files.handler({"arguments": {"query": "test"}})
    assert "No data directories" in result["textResultForLlm"]


async def test_search_local_files_path_traversal_blocked(tmp_dir):
    transcripts_dir = tmp_dir / "transcripts"
    transcripts_dir.mkdir(parents=True)
    with _search_patches(tmp_dir, TRANSCRIPTS_DIR=transcripts_dir):
        result = await search_local_files.handler({"arguments": {"query": "test", "file_pattern": "../../*.txt"}})
    assert "ERROR" in result["textResultForLlm"]


async def test_search_local_files_context_lines(tmp_dir):
    transcripts_dir = tmp_dir / "transcripts"
    transcripts_dir.mkdir(parents=True)
    lines = ["line1", "line2", "line3 has TARGET word", "line4", "line5", "line6"]
    (transcripts_dir / "doc.txt").write_text("\n".join(lines), encoding="utf-8")
    with _search_patches(tmp_dir, TRANSCRIPTS_DIR=transcripts_dir):
        result = await search_local_files.handler({"arguments": {"query": "TARGET"}})
    text = result["textResultForLlm"]
    assert "line2" in text  # context before
    assert "line4" in text  # context after


async def test_search_local_files_finds_md_by_default(tmp_dir):
    """Default pattern (*.*) should find .md files — transcripts are .md."""
    transcripts_dir = tmp_dir / "transcripts"
    transcripts_dir.mkdir(parents=True)
    (transcripts_dir / "meeting.md").write_text("Claude security launch announced today.", encoding="utf-8")
    with _search_patches(tmp_dir, TRANSCRIPTS_DIR=transcripts_dir):
        result = await search_local_files.handler({"arguments": {"query": "Claude security"}})
    assert result["resultType"] == "success"
    assert "Claude security" in result["textResultForLlm"]
    assert "meeting.md" in result["textResultForLlm"]


async def test_search_local_files_skips_binary(tmp_dir):
    """Binary files (.pptx, .pdf, etc.) should be skipped even with *.*."""
    documents_dir = tmp_dir / "documents"
    documents_dir.mkdir(parents=True)
    (documents_dir / "deck.pptx").write_bytes(b"\x00\x01binary content with keyword")
    (documents_dir / "notes.md").write_text("The keyword is here.", encoding="utf-8")
    with _search_patches(tmp_dir, DOCUMENTS_DIR=documents_dir):
        result = await search_local_files.handler({"arguments": {"query": "keyword"}})
    text = result["textResultForLlm"]
    assert "notes.md" in text
    assert "deck.pptx" not in text


async def test_search_local_files_searches_digests_dir(tmp_dir):
    """Tool should search digests dir — digest files live there."""
    digests_dir = tmp_dir / "digests"
    digests_dir.mkdir(parents=True)
    (digests_dir / "2026-02-23.md").write_text("# Digest\nQBE Foundry resolution plan urgent.", encoding="utf-8")
    with _search_patches(tmp_dir, DIGESTS_DIR=digests_dir):
        result = await search_local_files.handler({"arguments": {"query": "QBE Foundry"}})
    assert result["resultType"] == "success"
    assert "QBE Foundry" in result["textResultForLlm"]
    assert "digests/" in result["textResultForLlm"]


async def test_search_local_files_finds_monitoring_reports(tmp_dir):
    """Monitoring/triage reports in PULSE_HOME root should be searchable."""
    # Create monitoring files at PULSE_HOME root (not in a subdir)
    (tmp_dir / "monitoring-2026-02-27T10-33.json").write_text(
        '{"items": [{"source": "Teams: az prototype", "summary": "Joshua confirmed the approach"}]}',
        encoding="utf-8",
    )
    (tmp_dir / "monitoring-2026-02-27T10-33.md").write_text(
        "# Monitoring\n- az prototype: Joshua confirmed the prototype approach",
        encoding="utf-8",
    )
    with _search_patches(tmp_dir, PULSE_HOME=tmp_dir):
        result = await search_local_files.handler({"arguments": {"query": "az prototype"}})
    text = result["textResultForLlm"]
    assert result["resultType"] == "success"
    assert "az prototype" in text
    assert "reports/" in text


async def test_search_local_files_skips_dotfiles_in_root(tmp_dir):
    """Dot-files (.scheduler.json, etc.) in PULSE_HOME root should be skipped."""
    (tmp_dir / ".scheduler.json").write_text('{"keyword": "secret state"}', encoding="utf-8")
    (tmp_dir / "monitoring-2026-02-27.md").write_text("No keyword here.", encoding="utf-8")
    with _search_patches(tmp_dir, PULSE_HOME=tmp_dir):
        result = await search_local_files.handler({"arguments": {"query": "secret state"}})
    assert "No matches" in result["textResultForLlm"]


async def test_search_local_files_root_and_subdirs_combined(tmp_dir):
    """Search should find results from both subdirs and PULSE_HOME root."""
    transcripts_dir = tmp_dir / "transcripts"
    transcripts_dir.mkdir(parents=True)
    (transcripts_dir / "meeting.md").write_text("Discussed az prototype timeline.", encoding="utf-8")
    (tmp_dir / "monitoring-2026-02-27.json").write_text(
        '{"items": [{"source": "Teams: az prototype"}]}',
        encoding="utf-8",
    )
    with _search_patches(tmp_dir, TRANSCRIPTS_DIR=transcripts_dir, PULSE_HOME=tmp_dir):
        result = await search_local_files.handler({"arguments": {"query": "az prototype"}})
    text = result["textResultForLlm"]
    assert "transcripts/" in text
    assert "reports/" in text


# --- update_project ---


async def test_update_project_creates_file(tmp_dir):
    yaml_content = "project: Test Project\nstatus: active\ncommitments: []\n"
    with patch("sdk.tools.PROJECTS_DIR", tmp_dir):
        result = await update_project.handler({"arguments": {
            "project_id": "test-project",
            "yaml_content": yaml_content,
        }})
    assert result["resultType"] == "success"
    assert "updated" in result["textResultForLlm"].lower()
    project_file = tmp_dir / "test-project.yaml"
    assert project_file.exists()
    data = yaml.safe_load(project_file.read_text())
    assert data["project"] == "Test Project"
    assert data["status"] == "active"
    assert "updated_at" in data


async def test_update_project_overwrites_existing(tmp_dir):
    existing = tmp_dir / "existing.yaml"
    existing.write_text("project: Old\nstatus: stalled\n")
    with patch("sdk.tools.PROJECTS_DIR", tmp_dir):
        result = await update_project.handler({"arguments": {
            "project_id": "existing",
            "yaml_content": "project: Updated\nstatus: active\nrisk_level: high\n",
        }})
    assert result["resultType"] == "success"
    data = yaml.safe_load(existing.read_text())
    assert data["project"] == "Updated"
    assert data["status"] == "active"
    assert data["risk_level"] == "high"


async def test_update_project_invalid_id_path_traversal(tmp_dir):
    with patch("sdk.tools.PROJECTS_DIR", tmp_dir):
        result = await update_project.handler({"arguments": {
            "project_id": "../../etc/passwd",
            "yaml_content": "project: Evil\n",
        }})
    assert "ERROR" in result["textResultForLlm"]


async def test_update_project_invalid_id_uppercase(tmp_dir):
    with patch("sdk.tools.PROJECTS_DIR", tmp_dir):
        result = await update_project.handler({"arguments": {
            "project_id": "MyProject",
            "yaml_content": "project: Test\n",
        }})
    assert "ERROR" in result["textResultForLlm"]


async def test_update_project_invalid_yaml(tmp_dir):
    with patch("sdk.tools.PROJECTS_DIR", tmp_dir):
        result = await update_project.handler({"arguments": {
            "project_id": "bad-yaml",
            "yaml_content": "{{invalid yaml: [unclosed",
        }})
    assert "ERROR" in result["textResultForLlm"]
    assert "Invalid YAML" in result["textResultForLlm"]


async def test_update_project_yaml_must_be_dict(tmp_dir):
    with patch("sdk.tools.PROJECTS_DIR", tmp_dir):
        result = await update_project.handler({"arguments": {
            "project_id": "list-test",
            "yaml_content": "- item1\n- item2\n",
        }})
    assert "ERROR" in result["textResultForLlm"]
    assert "mapping" in result["textResultForLlm"]


async def test_update_project_auto_sets_updated_at(tmp_dir):
    from datetime import datetime
    with patch("sdk.tools.PROJECTS_DIR", tmp_dir):
        await update_project.handler({"arguments": {
            "project_id": "ts-test",
            "yaml_content": "project: Timestamp Test\n",
        }})
    data = yaml.safe_load((tmp_dir / "ts-test.yaml").read_text())
    assert "updated_at" in data
    datetime.fromisoformat(data["updated_at"])  # validates format


async def test_update_project_with_commitments(tmp_dir):
    yaml_content = (
        "project: QBE Migration\n"
        "status: active\n"
        "commitments:\n"
        "  - id: commit-send-plan\n"
        "    what: Send resolution plan\n"
        "    to: Esther\n"
        '    due: "2026-02-21"\n'
        "    status: open\n"
    )
    with patch("sdk.tools.PROJECTS_DIR", tmp_dir):
        result = await update_project.handler({"arguments": {
            "project_id": "qbe-migration",
            "yaml_content": yaml_content,
        }})
    assert result["resultType"] == "success"
    data = yaml.safe_load((tmp_dir / "qbe-migration.yaml").read_text())
    assert len(data["commitments"]) == 1
    assert data["commitments"][0]["id"] == "commit-send-plan"


# --- project dedup ---


def test_find_similar_projects_empty_dir(tmp_dir):
    with patch("sdk.tools.PROJECTS_DIR", tmp_dir):
        assert _find_similar_projects("vodafone-frontier") == []


def test_find_similar_projects_no_overlap(tmp_dir):
    (tmp_dir / "contoso-migration.yaml").write_text("project: x")
    with patch("sdk.tools.PROJECTS_DIR", tmp_dir):
        assert _find_similar_projects("vodafone-frontier") == []


def test_find_similar_projects_one_token_overlap_ignored(tmp_dir):
    """One shared token is not enough — needs 2+."""
    (tmp_dir / "vodafone-billing.yaml").write_text("project: x")
    with patch("sdk.tools.PROJECTS_DIR", tmp_dir):
        # Only "vodafone" overlaps — not enough (need 2+)
        assert _find_similar_projects("vodafone-frontier") == []


def test_find_similar_projects_two_token_overlap_detected(tmp_dir):
    (tmp_dir / "gsk-investigations.yaml").write_text("project: x")
    with patch("sdk.tools.PROJECTS_DIR", tmp_dir):
        # "gsk" + "investigations" overlap
        assert _find_similar_projects("gsk-investigations-ai") == ["gsk-investigations"]


def test_find_similar_projects_skips_exact_match(tmp_dir):
    """Exact same slug = update, not a conflict."""
    (tmp_dir / "vodafone-frontier.yaml").write_text("project: x")
    with patch("sdk.tools.PROJECTS_DIR", tmp_dir):
        assert _find_similar_projects("vodafone-frontier") == []


def test_find_similar_projects_short_tokens_ignored(tmp_dir):
    """Single-char tokens (like 'ai') should not cause false positives."""
    (tmp_dir / "some-ai-project.yaml").write_text("project: x")
    with patch("sdk.tools.PROJECTS_DIR", tmp_dir):
        # "ai" is only 2 chars, but there's no second overlapping token
        assert _find_similar_projects("other-ai-thing") == []


def test_find_similar_projects_multiple_matches(tmp_dir):
    (tmp_dir / "qbe-foundry.yaml").write_text("project: x")
    (tmp_dir / "qbe-foundry-migration.yaml").write_text("project: x")
    with patch("sdk.tools.PROJECTS_DIR", tmp_dir):
        result = _find_similar_projects("qbe-foundry-escalation")
        assert "qbe-foundry" in result
        assert "qbe-foundry-migration" in result


@pytest.mark.asyncio
async def test_update_project_blocks_new_duplicate(tmp_dir):
    """Creating a new file with a similar slug should be blocked."""
    (tmp_dir / "gsk-investigations.yaml").write_text("project: GSK\nstatus: active\n")
    with patch("sdk.tools.PROJECTS_DIR", tmp_dir):
        result = await update_project.handler({"arguments": {
            "project_id": "gsk-investigations-ai",
            "yaml_content": "project: GSK AI\nstatus: active\n",
        }})
    assert result["resultType"] == "success"
    assert "BLOCKED" in result["textResultForLlm"]
    assert "gsk-investigations" in result["textResultForLlm"]
    # File should NOT have been created
    assert not (tmp_dir / "gsk-investigations-ai.yaml").exists()


@pytest.mark.asyncio
async def test_update_project_allows_updating_existing(tmp_dir):
    """Updating an existing file should NOT be blocked even if similar slugs exist."""
    (tmp_dir / "gsk-investigations.yaml").write_text("project: GSK\nstatus: active\n")
    (tmp_dir / "gsk-investigations-ai.yaml").write_text("project: GSK AI\nstatus: active\n")
    with patch("sdk.tools.PROJECTS_DIR", tmp_dir):
        result = await update_project.handler({"arguments": {
            "project_id": "gsk-investigations-ai",
            "yaml_content": "project: GSK AI Updated\nstatus: active\n",
        }})
    assert result["resultType"] == "success"
    assert "BLOCKED" not in result["textResultForLlm"]
    data = yaml.safe_load((tmp_dir / "gsk-investigations-ai.yaml").read_text())
    assert data["project"] == "GSK AI Updated"


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


# --- send_teams_message / send_email_reply dedup ---


async def test_send_teams_message_dedup_rejects_identical(tmp_dir):
    """Second call with same recipient + message should be rejected."""
    pending = tmp_dir / ".pending-actions"
    with patch("sdk.tools.PENDING_ACTIONS_DIR", pending):
        # First call — should succeed
        r1 = await send_teams_message.handler({"arguments": {
            "recipient": "Alice", "message": "Hello there",
        }})
        assert "queued" in r1["textResultForLlm"].lower()

        # Second call with same recipient + message — should be rejected
        r2 = await send_teams_message.handler({"arguments": {
            "recipient": "Alice", "message": "Hello there",
        }})
        assert "already queued" in r2["textResultForLlm"].lower()

    # Only one action file should exist
    assert len(list(pending.glob("teams-send-*.json"))) == 1


async def test_send_teams_message_dedup_case_insensitive(tmp_dir):
    """Dedup should be case-insensitive on target name."""
    pending = tmp_dir / ".pending-actions"
    with patch("sdk.tools.PENDING_ACTIONS_DIR", pending):
        await send_teams_message.handler({"arguments": {
            "recipient": "Alice", "message": "Hello there",
        }})
        r2 = await send_teams_message.handler({"arguments": {
            "recipient": "alice", "message": "Hello there",
        }})
        assert "already queued" in r2["textResultForLlm"].lower()


async def test_send_teams_message_dedup_allows_different_message(tmp_dir):
    """Different messages to the same person should both be allowed."""
    pending = tmp_dir / ".pending-actions"
    with patch("sdk.tools.PENDING_ACTIONS_DIR", pending):
        await send_teams_message.handler({"arguments": {
            "recipient": "Alice", "message": "Message one",
        }})
        r2 = await send_teams_message.handler({"arguments": {
            "recipient": "Alice", "message": "Message two",
        }})
        assert "queued" in r2["textResultForLlm"].lower()
        assert "already" not in r2["textResultForLlm"].lower()

    assert len(list(pending.glob("teams-send-*.json"))) == 2


async def test_send_teams_message_dedup_chat_name(tmp_dir):
    """Dedup should work with chat_name (reply flow)."""
    pending = tmp_dir / ".pending-actions"
    with patch("sdk.tools.PENDING_ACTIONS_DIR", pending):
        await send_teams_message.handler({"arguments": {
            "recipient": "", "message": "Reply", "chat_name": "Fatos Ismali",
        }})
        r2 = await send_teams_message.handler({"arguments": {
            "recipient": "", "message": "Reply", "chat_name": "Fatos Ismali",
        }})
        assert "already queued" in r2["textResultForLlm"].lower()


async def test_send_email_reply_dedup_rejects_identical(tmp_dir):
    """Second call with same search_query + message should be rejected."""
    pending = tmp_dir / ".pending-actions"
    with patch("sdk.tools.PENDING_ACTIONS_DIR", pending):
        r1 = await send_email_reply.handler({"arguments": {
            "search_query": "Bob budget review", "message": "Approved!",
        }})
        assert "queued" in r1["textResultForLlm"].lower()

        r2 = await send_email_reply.handler({"arguments": {
            "search_query": "Bob budget review", "message": "Approved!",
        }})
        assert "already queued" in r2["textResultForLlm"].lower()

    assert len(list(pending.glob("email-reply-*.json"))) == 1


# --- send_task_to_agent ---


async def test_send_task_to_agent_success(tmp_dir):
    """Convention-based paths — PULSE_TEAM_DIR/alias/ is auto-derived."""
    team_dir = tmp_dir / "Pulse-Team"
    (team_dir / "alice").mkdir(parents=True)
    team_config = {
        "team": [{"name": "Alice Test", "alias": "alice"}],
        "user": {"name": "Artur Zielinski", "alias": "artur"},
    }
    with patch("core.config.load_config", return_value=team_config), \
         patch("sdk.tools.PULSE_TEAM_DIR", team_dir):
        result = await send_task_to_agent.handler({"arguments": {
            "agent": "alice",
            "task": "What do you know about Vodafone?",
            "kind": "question",
        }})
    assert result["resultType"] == "success"
    assert "Alice Test" in result["textResultForLlm"]
    assert "Request ID" in result["textResultForLlm"]

    # Verify YAML was written to alice's convention path
    jobs_dir = team_dir / "alice" / "jobs" / "pending"
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
        "team": [{"name": "Alice Test", "alias": "alice"}],
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
    """Convention path doesn't exist — team folder not synced."""
    team_dir = tmp_dir / "Pulse-Team"
    # Don't create alice dir — simulates unsynced OneDrive
    team_config = {
        "team": [{"name": "Alice", "alias": "alice"}],
    }
    with patch("core.config.load_config", return_value=team_config), \
         patch("sdk.tools.PULSE_TEAM_DIR", team_dir):
        result = await send_task_to_agent.handler({"arguments": {
            "agent": "alice",
            "task": "Hello",
        }})
    assert "ERROR" in result["textResultForLlm"]
    assert "not accessible" in result["textResultForLlm"]


async def test_send_task_to_agent_case_insensitive(tmp_dir):
    team_dir = tmp_dir / "Pulse-Team"
    (team_dir / "alice").mkdir(parents=True)
    team_config = {
        "team": [{"name": "Alice Test", "alias": "alice"}],
        "user": {"name": "Artur", "alias": "artur"},
    }
    with patch("core.config.load_config", return_value=team_config), \
         patch("sdk.tools.PULSE_TEAM_DIR", team_dir):
        result = await send_task_to_agent.handler({"arguments": {
            "agent": "Alice",
            "task": "Test case insensitivity",
        }})
    assert result["resultType"] == "success"


async def test_send_task_to_agent_match_by_name(tmp_dir):
    team_dir = tmp_dir / "Pulse-Team"
    (team_dir / "alice").mkdir(parents=True)
    team_config = {
        "team": [{"name": "Alice Test", "alias": "alice"}],
        "user": {"name": "Artur", "alias": "artur"},
    }
    with patch("core.config.load_config", return_value=team_config), \
         patch("sdk.tools.PULSE_TEAM_DIR", team_dir):
        result = await send_task_to_agent.handler({"arguments": {
            "agent": "alice test",
            "task": "Test name matching",
        }})
    assert result["resultType"] == "success"


async def test_send_task_to_agent_explicit_agent_path(tmp_dir):
    """Backward compat — explicit agent_path in config still works."""
    (tmp_dir / "alice-custom").mkdir()
    team_config = {
        "team": [{"name": "Alice", "alias": "alice", "agent_path": str(tmp_dir / "alice-custom")}],
        "user": {"name": "Artur", "alias": "artur"},
    }
    with patch("core.config.load_config", return_value=team_config):
        result = await send_task_to_agent.handler({"arguments": {
            "agent": "alice",
            "task": "Test backward compat",
        }})
    assert result["resultType"] == "success"
    # Should write to explicit path's Jobs/ folder (not convention path)
    jobs_dir = tmp_dir / "alice-custom" / "Jobs"
    yaml_files = list(jobs_dir.glob("*.yaml"))
    assert len(yaml_files) == 1
