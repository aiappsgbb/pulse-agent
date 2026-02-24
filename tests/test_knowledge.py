"""Tests for knowledge mining mode — pre-process, trigger variables, search extension."""

import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, AsyncMock

import pytest
import yaml

from core.constants import TEAMS_MESSAGES_DIR
from sdk.runner import (
    _list_recent_artifacts,
    _build_trigger_variables,
    _load_projects,
    _build_projects_block,
    _extract_commitments_summary,
    _pre_process_knowledge,
    KNOWLEDGE_STATE_FILE,
)
from sdk.tools import search_local_files


# --- TEAMS_MESSAGES_DIR constant ---


def test_teams_messages_dir_exists():
    """TEAMS_MESSAGES_DIR should be defined and end with 'teams-messages'."""
    assert TEAMS_MESSAGES_DIR is not None
    assert TEAMS_MESSAGES_DIR.name == "teams-messages"


# --- _list_recent_artifacts ---


def test_list_recent_artifacts_empty(tmp_dir):
    """Returns 'no recent artifacts' when all dirs are empty or missing."""
    nonexistent = tmp_dir / "nonexistent"
    with patch("sdk.runner.TRANSCRIPTS_DIR", nonexistent), \
         patch("sdk.runner.EMAILS_DIR", nonexistent), \
         patch("sdk.runner.TEAMS_MESSAGES_DIR", nonexistent), \
         patch("sdk.runner.DOCUMENTS_DIR", nonexistent):
        result = _list_recent_artifacts(days=2)
    assert "No recent artifacts" in result


def test_list_recent_artifacts_finds_recent_files(tmp_dir):
    """Lists recently modified files from knowledge directories."""
    transcripts = tmp_dir / "transcripts"
    transcripts.mkdir()
    (transcripts / "2026-02-24_meeting.md").write_text("transcript content", encoding="utf-8")

    emails = tmp_dir / "emails"
    emails.mkdir()
    (emails / "2026-02-24_alice_budget.md").write_text("email content", encoding="utf-8")

    nonexistent = tmp_dir / "nonexistent"
    with patch("sdk.runner.TRANSCRIPTS_DIR", transcripts), \
         patch("sdk.runner.EMAILS_DIR", emails), \
         patch("sdk.runner.TEAMS_MESSAGES_DIR", nonexistent), \
         patch("sdk.runner.DOCUMENTS_DIR", nonexistent):
        result = _list_recent_artifacts(days=2)

    assert "Transcripts" in result
    assert "meeting.md" in result
    assert "Emails" in result
    assert "alice_budget.md" in result


def test_list_recent_artifacts_skips_old_files(tmp_dir):
    """Files older than the lookback window are not listed."""
    import os
    transcripts = tmp_dir / "transcripts"
    transcripts.mkdir()
    old_file = transcripts / "old_meeting.md"
    old_file.write_text("old content", encoding="utf-8")
    # Set mtime to 5 days ago
    old_time = (datetime.now() - timedelta(days=5)).timestamp()
    os.utime(old_file, (old_time, old_time))

    nonexistent = tmp_dir / "nonexistent"
    with patch("sdk.runner.TRANSCRIPTS_DIR", transcripts), \
         patch("sdk.runner.EMAILS_DIR", nonexistent), \
         patch("sdk.runner.TEAMS_MESSAGES_DIR", nonexistent), \
         patch("sdk.runner.DOCUMENTS_DIR", nonexistent):
        result = _list_recent_artifacts(days=2)

    assert "No recent artifacts" in result


def test_list_recent_artifacts_includes_teams_messages(tmp_dir):
    """teams-messages directory is included in artifact listing."""
    teams = tmp_dir / "teams-messages"
    teams.mkdir()
    (teams / "2026-02-24_chat_alice.md").write_text("teams message", encoding="utf-8")

    nonexistent = tmp_dir / "nonexistent"
    with patch("sdk.runner.TRANSCRIPTS_DIR", nonexistent), \
         patch("sdk.runner.EMAILS_DIR", nonexistent), \
         patch("sdk.runner.TEAMS_MESSAGES_DIR", teams), \
         patch("sdk.runner.DOCUMENTS_DIR", nonexistent):
        result = _list_recent_artifacts(days=2)

    assert "Teams-Messages" in result
    assert "chat_alice.md" in result


# --- search_local_files with teams-messages ---


async def test_search_teams_messages_dir(tmp_dir):
    """search_local_files should search teams-messages directory."""
    teams_dir = tmp_dir / "teams-messages"
    teams_dir.mkdir()
    (teams_dir / "2026-02-24_chat_alice.md").write_text(
        "# Teams: Project Chat\nAlice said the HSBC deal is at risk.",
        encoding="utf-8",
    )
    nonexistent = tmp_dir / "nonexistent"
    with patch("sdk.tools.TRANSCRIPTS_DIR", nonexistent), \
         patch("sdk.tools.DOCUMENTS_DIR", nonexistent), \
         patch("sdk.tools.EMAILS_DIR", nonexistent), \
         patch("sdk.tools.TEAMS_MESSAGES_DIR", teams_dir), \
         patch("sdk.tools.DIGESTS_DIR", nonexistent), \
         patch("sdk.tools.INTEL_DIR", nonexistent), \
         patch("sdk.tools.PROJECTS_DIR", nonexistent):
        result = await search_local_files.handler({"arguments": {"query": "HSBC"}})

    assert result["resultType"] == "success"
    assert "HSBC" in result["textResultForLlm"]
    assert "teams-messages/" in result["textResultForLlm"]


async def test_search_across_emails_and_teams(tmp_dir):
    """search_local_files finds matches across both emails and teams-messages."""
    emails_dir = tmp_dir / "emails"
    emails_dir.mkdir()
    (emails_dir / "email.md").write_text("Contoso migration update from Bob", encoding="utf-8")

    teams_dir = tmp_dir / "teams-messages"
    teams_dir.mkdir()
    (teams_dir / "chat.md").write_text("Contoso team standup notes", encoding="utf-8")

    nonexistent = tmp_dir / "nonexistent"
    with patch("sdk.tools.TRANSCRIPTS_DIR", nonexistent), \
         patch("sdk.tools.DOCUMENTS_DIR", nonexistent), \
         patch("sdk.tools.EMAILS_DIR", emails_dir), \
         patch("sdk.tools.TEAMS_MESSAGES_DIR", teams_dir), \
         patch("sdk.tools.DIGESTS_DIR", nonexistent), \
         patch("sdk.tools.INTEL_DIR", nonexistent), \
         patch("sdk.tools.PROJECTS_DIR", nonexistent):
        result = await search_local_files.handler({"arguments": {"query": "Contoso", "max_results": 10}})

    text = result["textResultForLlm"]
    assert "Found 2 file(s)" in text
    assert "emails/" in text
    assert "teams-messages/" in text


# --- Knowledge trigger variables ---


def test_trigger_variables_knowledge_mode():
    """Knowledge mode builds correct trigger variables."""
    config = {"user": {"name": "Test"}}
    context = {
        "projects_block": "## Projects\nHSBC active",
        "commitments_summary": "1 overdue",
        "recent_artifacts": "### Transcripts\n- meeting.md",
        "lookback_window": "48 hours",
        "lookback_note": "First run",
        "teams_inbox_block": "No unread",
        "outlook_inbox_block": "No unread",
    }
    variables = _build_trigger_variables("knowledge", config, context)
    assert variables["date"] == datetime.now().strftime("%Y-%m-%d")
    assert variables["lookback_window"] == "48 hours"
    assert "HSBC" in variables["projects_block"]
    assert "meeting.md" in variables["recent_artifacts"]


def test_trigger_variables_knowledge_defaults():
    """Knowledge mode defaults when context is empty."""
    config = {"user": {"name": "Test"}}
    variables = _build_trigger_variables("knowledge", config, {})
    assert variables["date"]
    assert variables["lookback_window"] == "48 hours"
    assert "No project" in variables["projects_block"]
    assert "No recent" in variables["recent_artifacts"]


# --- _pre_process_knowledge ---


async def test_pre_process_knowledge_loads_projects(tmp_dir):
    """Pre-process loads project files and lists recent artifacts."""
    projects_dir = tmp_dir / "projects"
    projects_dir.mkdir()
    (projects_dir / "hsbc-migration.yaml").write_text(
        "project: HSBC Migration\nstatus: active\nrisk_level: high\n",
        encoding="utf-8",
    )

    transcripts_dir = tmp_dir / "transcripts"
    transcripts_dir.mkdir()
    (transcripts_dir / "2026-02-24_hsbc.md").write_text("transcript", encoding="utf-8")

    state_file = tmp_dir / ".knowledge-state.json"
    nonexistent = tmp_dir / "nonexistent"

    config = {"user": {"name": "Test"}}

    with patch("sdk.runner.PROJECTS_DIR", projects_dir), \
         patch("sdk.runner.TRANSCRIPTS_DIR", transcripts_dir), \
         patch("sdk.runner.EMAILS_DIR", nonexistent), \
         patch("sdk.runner.TEAMS_MESSAGES_DIR", nonexistent), \
         patch("sdk.runner.DOCUMENTS_DIR", nonexistent), \
         patch("sdk.runner.KNOWLEDGE_STATE_FILE", state_file), \
         patch("collectors.teams_inbox.scan_teams_inbox", new_callable=AsyncMock, return_value=None), \
         patch("collectors.outlook_inbox.scan_outlook_inbox", new_callable=AsyncMock, return_value=None):
        result = await _pre_process_knowledge(config)

    assert "HSBC" in result["projects_block"]
    assert "hsbc.md" in result["recent_artifacts"]
    assert result["lookback_note"]  # should have some note
    # State file should be written with last_run
    assert state_file.exists()
    state = json.loads(state_file.read_text())
    assert "last_run" in state


async def test_pre_process_knowledge_uses_last_run(tmp_dir):
    """Pre-process uses last_run from state file for lookback window."""
    projects_dir = tmp_dir / "projects"
    projects_dir.mkdir()

    state_file = tmp_dir / ".knowledge-state.json"
    last_run = (datetime.now() - timedelta(hours=6)).isoformat()
    state_file.write_text(json.dumps({"last_run": last_run}), encoding="utf-8")

    nonexistent = tmp_dir / "nonexistent"
    config = {"user": {"name": "Test"}}

    with patch("sdk.runner.PROJECTS_DIR", projects_dir), \
         patch("sdk.runner.TRANSCRIPTS_DIR", nonexistent), \
         patch("sdk.runner.EMAILS_DIR", nonexistent), \
         patch("sdk.runner.TEAMS_MESSAGES_DIR", nonexistent), \
         patch("sdk.runner.DOCUMENTS_DIR", nonexistent), \
         patch("sdk.runner.KNOWLEDGE_STATE_FILE", state_file), \
         patch("collectors.teams_inbox.scan_teams_inbox", new_callable=AsyncMock, return_value=None), \
         patch("collectors.outlook_inbox.scan_outlook_inbox", new_callable=AsyncMock, return_value=None):
        result = await _pre_process_knowledge(config)

    assert f"since {last_run}" in result["lookback_window"]
    assert "Last knowledge run" in result["lookback_note"]


# --- modes.yaml includes knowledge ---


def test_modes_yaml_has_knowledge_mode():
    """modes.yaml should define the knowledge mode."""
    from sdk.session import load_modes
    modes = load_modes()
    assert "knowledge" in modes
    km = modes["knowledge"]
    assert km["pre_process"] == "collect_knowledge_context"
    assert "knowledge-miner" in km["agents"]
    assert "workiq" in km["mcp_servers"]


# --- Knowledge-miner agent definition loads ---


def test_knowledge_miner_agent_loads():
    """knowledge-miner agent definition should load without errors."""
    from sdk.agents import parse_front_matter
    from core.constants import CONFIG_DIR
    path = CONFIG_DIR / "prompts" / "agents" / "knowledge-miner.md"
    assert path.exists(), f"Agent definition not found: {path}"
    meta, body = parse_front_matter(path)
    assert meta["name"] == "knowledge-miner"
    assert "mcp_servers" in meta
    assert "workiq" in meta["mcp_servers"]
    assert "Archive" in body  # should mention archiving
    assert "watch_queries" in body  # should mention watch queries
    assert "timeline" in body  # should mention timeline enrichment
