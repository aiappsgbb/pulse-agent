"""Contract tests: verify that prompt schemas, code parsers, and consumers all agree.

These tests catch the exact class of bug that broke reply: the LLM outputs
one thing, the code expects another, and nobody notices because everything
is tested in isolation with mocks.

Contract = "producer and consumer agree on the shape of data between them."
"""

import json
import re
import yaml
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest


PROMPTS_DIR = Path(__file__).parent.parent / "config" / "prompts"


# ---------------------------------------------------------------------------
# Contract: Prompt action_type values ↔ write_reply_job accepted values
# ---------------------------------------------------------------------------


class TestActionTypeContract:
    """The action_type values in prompt templates MUST be handled by write_reply_job."""

    def _extract_action_types_from_prompt(self, prompt_path: Path) -> set[str]:
        """Extract all action_type values from a prompt template."""
        content = prompt_path.read_text(encoding="utf-8")
        # Match: "action_type": "value1|value2|value3"
        matches = re.findall(r'"action_type":\s*"([^"]+)"', content)
        types = set()
        for match in matches:
            for t in match.split("|"):
                t = t.strip()
                if t:
                    types.add(t)
        return types

    def _get_accepted_types(self) -> tuple[set[str], set[str]]:
        """Extract accepted types from write_reply_job source code."""
        from tui.ipc import write_reply_job
        import inspect
        source = inspect.getsource(write_reply_job)

        # The function defines teams_types and email_types tuples
        teams_match = re.search(r'teams_types\s*=\s*\(([^)]+)\)', source)
        email_match = re.search(r'email_types\s*=\s*\(([^)]+)\)', source)

        teams = set()
        if teams_match:
            teams = {s.strip().strip('"').strip("'") for s in teams_match.group(1).split(",")}

        email = set()
        if email_match:
            email = {s.strip().strip('"').strip("'") for s in email_match.group(1).split(",")}

        return teams, email

    def test_monitor_prompt_types_are_handled(self):
        """Every action_type in monitor.md is handled by write_reply_job."""
        prompt = PROMPTS_DIR / "triggers" / "monitor.md"
        if not prompt.exists():
            pytest.skip("monitor.md not found")

        prompt_types = self._extract_action_types_from_prompt(prompt)
        teams, email = self._get_accepted_types()
        replyable = teams | email

        # These types are intentionally NOT reply actions
        non_reply_types = {"dismiss", "schedule_meeting", "schedule_followup"}

        for t in prompt_types:
            if t in non_reply_types:
                continue
            assert t in replyable, \
                f"Prompt action_type '{t}' in monitor.md is NOT handled by write_reply_job. " \
                f"Accepted: {replyable}"

    def test_digest_prompt_types_are_handled(self):
        """Every action_type in digest.md is handled by write_reply_job."""
        prompt = PROMPTS_DIR / "triggers" / "digest.md"
        if not prompt.exists():
            pytest.skip("digest.md not found")

        prompt_types = self._extract_action_types_from_prompt(prompt)
        teams, email = self._get_accepted_types()
        replyable = teams | email

        non_reply_types = {"dismiss", "schedule_meeting", "schedule_followup"}

        for t in prompt_types:
            if t in non_reply_types:
                continue
            assert t in replyable, \
                f"Prompt action_type '{t}' in digest.md is NOT handled by write_reply_job. " \
                f"Accepted: {replyable}"


# ---------------------------------------------------------------------------
# Contract: Digest JSON schema ↔ TUI data loaders
# ---------------------------------------------------------------------------


class TestDigestSchemaContract:
    """Digest JSON output schema matches what the TUI expects to render."""

    REQUIRED_ITEM_FIELDS = {"id", "title", "priority", "source", "summary"}
    OPTIONAL_ITEM_FIELDS = {"type", "project", "date", "age", "verified",
                            "status", "suggested_actions", "context"}

    def _make_digest_json(self, items: list[dict]) -> dict:
        return {"items": items}

    def test_minimal_item_renders(self, tmp_dir):
        """TUI can render an item with only required fields."""
        from tui.screens import _load_digest_items

        digest = self._make_digest_json([{
            "id": "test-1",
            "title": "Test item",
            "priority": "medium",
            "source": "Teams: Alice",
            "summary": "A test item",
        }])

        digests_dir = tmp_dir / "digests"
        digests_dir.mkdir()
        (digests_dir / "2026-03-02.json").write_text(json.dumps(digest), encoding="utf-8")

        with patch("tui.screens.DIGESTS_DIR", digests_dir):
            items = _load_digest_items()

        assert len(items) == 1
        assert items[0]["title"] == "Test item"

    def test_item_with_suggested_actions_is_replyable(self, tmp_dir):
        """Item with suggested_actions can be passed to write_reply_job."""
        from tui.ipc import write_reply_job

        item = {
            "id": "test-2",
            "title": "Reply test",
            "priority": "high",
            "source": "Teams: Fatos",
            "summary": "Needs reply",
            "suggested_actions": [{
                "label": "Reply",
                "action_type": "draft_teams_reply",
                "draft": "Will review",
                "target": "Fatos",
            }],
        }

        with patch("tui.ipc.JOBS_DIR", tmp_dir / "jobs"):
            result = write_reply_job(item, item["suggested_actions"][0]["draft"])
        assert result is True

    def test_item_without_actions_not_replyable(self, tmp_dir):
        """Item without suggested_actions cannot be replied to."""
        from tui.ipc import write_reply_job

        item = {
            "id": "test-3",
            "title": "FYI item",
            "priority": "low",
            "source": "Email: newsletter",
            "summary": "For your information",
        }

        with patch("tui.ipc.JOBS_DIR", tmp_dir / "jobs"):
            result = write_reply_job(item, "Some text")
        assert result is False

    def test_corrupted_json_handled_gracefully(self, tmp_dir):
        """TUI doesn't crash on malformed digest JSON."""
        from tui.screens import _load_digest_items

        digests_dir = tmp_dir / "digests"
        digests_dir.mkdir()
        (digests_dir / "2026-03-02.json").write_text("{bad json", encoding="utf-8")

        with patch("tui.screens.DIGESTS_DIR", digests_dir):
            items = _load_digest_items()

        assert items == []

    def test_empty_items_array(self, tmp_dir):
        """TUI handles digest with empty items array."""
        from tui.screens import _load_digest_items

        digests_dir = tmp_dir / "digests"
        digests_dir.mkdir()
        (digests_dir / "2026-03-02.json").write_text('{"items": []}', encoding="utf-8")

        with patch("tui.screens.DIGESTS_DIR", digests_dir):
            items = _load_digest_items()

        assert items == []


# ---------------------------------------------------------------------------
# Contract: Monitoring JSON schema ↔ TUI data loaders
# ---------------------------------------------------------------------------


class TestMonitoringSchemaContract:
    """Monitoring JSON output schema matches what TUI + reply flow expects."""

    def test_triage_items_loaded(self, tmp_dir):
        """TUI loads items from monitoring-*.json correctly."""
        from tui.screens import _load_triage_items

        monitoring = {
            "items": [
                {
                    "id": "triage-1",
                    "title": "Unread from Alice",
                    "type": "reply_needed",
                    "priority": "urgent",
                    "source": "Teams: Alice",
                    "summary": "Alice needs a reply",
                    "suggested_actions": [{
                        "label": "Reply",
                        "action_type": "draft_teams_reply",
                        "draft": "On it",
                        "target": "Alice",
                    }],
                },
            ],
            "stats": {"teams_unread": 1},
        }

        monitoring_file = tmp_dir / "monitoring-2026-03-02T09-00.json"
        monitoring_file.write_text(json.dumps(monitoring), encoding="utf-8")

        with patch("tui.screens.PULSE_HOME", tmp_dir):
            items = _load_triage_items()

        assert len(items) == 1
        assert items[0]["id"] == "triage-1"
        assert items[0]["suggested_actions"][0]["action_type"] == "draft_teams_reply"

    def test_latest_monitoring_file_used(self, tmp_dir):
        """TUI picks the most recent monitoring file."""
        from tui.screens import _load_triage_items

        # Older file
        old = {"items": [{"id": "old", "title": "Old"}]}
        (tmp_dir / "monitoring-2026-03-01T09-00.json").write_text(
            json.dumps(old), encoding="utf-8"
        )
        # Newer file
        new = {"items": [{"id": "new", "title": "New"}]}
        (tmp_dir / "monitoring-2026-03-02T09-00.json").write_text(
            json.dumps(new), encoding="utf-8"
        )

        with patch("tui.screens.PULSE_HOME", tmp_dir):
            items = _load_triage_items()

        assert len(items) == 1
        assert items[0]["id"] == "new"


# ---------------------------------------------------------------------------
# Contract: Job YAML schema ↔ worker routing
# ---------------------------------------------------------------------------


class TestJobSchemaContract:
    """Job YAML files must have fields the worker expects."""

    # Job types routed through job_worker. "chat" is handled separately
    # via poll_tui_chat_requests (file-based IPC, not job queue).
    VALID_JOB_TYPES = {
        "digest", "monitor", "intel", "research", "transcripts",
        "knowledge", "teams_send", "email_reply",
        "agent_request", "agent_response",
    }

    def test_all_job_types_routed(self):
        """Every valid job type has a handler in the worker."""
        import inspect
        from daemon.worker import job_worker
        source = inspect.getsource(job_worker)

        for jtype in self.VALID_JOB_TYPES:
            # Check that the worker has routing for this type
            assert jtype in source, \
                f"Job type '{jtype}' has no routing in job_worker"

    def test_teams_send_job_schema(self, tmp_dir):
        """teams_send YAML has the fields _execute_teams_send expects."""
        from daemon.worker import _execute_teams_send
        from unittest.mock import AsyncMock

        # Minimum viable job
        job = {"type": "teams_send", "message": "Hello", "chat_name": "Alice"}

        import asyncio
        with patch("collectors.teams_sender.reply_to_chat", new_callable=AsyncMock) as mock:
            mock.return_value = {"success": True}
            result = asyncio.get_event_loop().run_until_complete(_execute_teams_send(job))
        assert result["success"] is True

    def test_email_reply_job_schema(self, tmp_dir):
        """email_reply YAML has the fields _execute_email_reply expects."""
        from daemon.worker import _execute_email_reply
        from unittest.mock import AsyncMock

        job = {"type": "email_reply", "message": "Thanks", "search_query": "RE: test"}

        import asyncio
        with patch("collectors.outlook_sender.reply_to_email", new_callable=AsyncMock) as mock:
            mock.return_value = {"success": True}
            result = asyncio.get_event_loop().run_until_complete(_execute_email_reply(job))
        assert result["success"] is True


# ---------------------------------------------------------------------------
# Contract: Project YAML schema ↔ digest loading
# ---------------------------------------------------------------------------


class TestProjectSchemaContract:
    """Project YAML files must be loadable by the digest pipeline."""

    SAMPLE_PROJECT = {
        "project": "Vodafone Agentic Platform",
        "status": "active",
        "risk_level": "medium",
        "summary": "Architecture review for Vodafone's agentic platform.",
        "stakeholders": [
            {"name": "Fatos Ismali", "role": "Lead"},
            {"name": "Artur Zielinski", "role": "Reviewer"},
        ],
        "commitments": [
            {
                "what": "Review architecture whitepaper",
                "who": "Artur",
                "to": "Fatos",
                "due": "2026-03-03",
                "status": "open",
                "source": "Mar 2 standup",
            },
        ],
        "next_meeting": "2026-03-05 14:00",
        "key_dates": [
            {"date": "2026-03-10", "event": "Customer presentation"},
        ],
        "updated_at": "2026-03-02T09:00:00",
    }

    def test_project_yaml_roundtrip(self, tmp_dir):
        """Project YAML can be written and read back correctly."""
        project_file = tmp_dir / "vodafone-agentic-platform.yaml"
        project_file.write_text(
            yaml.dump(self.SAMPLE_PROJECT, default_flow_style=False),
            encoding="utf-8",
        )

        loaded = yaml.safe_load(project_file.read_text(encoding="utf-8"))
        assert loaded["project"] == "Vodafone Agentic Platform"
        assert loaded["status"] == "active"
        assert len(loaded["commitments"]) == 1
        assert loaded["commitments"][0]["status"] == "open"

    def test_overdue_commitment_detected(self):
        """Commitments past their due date are detectable."""
        commitment = {
            "what": "Send pricing",
            "due": "2026-02-28",
            "status": "open",
        }
        due_date = datetime.strptime(commitment["due"], "%Y-%m-%d").date()
        today = datetime(2026, 3, 2).date()
        assert due_date < today  # It's overdue

    def test_approaching_commitment_detected(self):
        """Commitments due soon are detectable."""
        commitment = {
            "what": "Review whitepaper",
            "due": "2026-03-03",
            "status": "open",
        }
        due_date = datetime.strptime(commitment["due"], "%Y-%m-%d").date()
        today = datetime(2026, 3, 2).date()
        days_until = (due_date - today).days
        assert 0 <= days_until <= 3  # Due within 3 days
