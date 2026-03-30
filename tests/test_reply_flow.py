"""Integration tests for the reply flow: TUI → write_reply_job → YAML file → worker pickup.

These tests exercise the REAL logic paths — the actual action_type matching,
field extraction, YAML serialization, and worker routing. No mocking of the
code under test.
"""

import json
import yaml
from pathlib import Path
from unittest.mock import patch, AsyncMock

import pytest


# ---------------------------------------------------------------------------
# write_reply_job: action_type matching (THE BUG THAT WAS BROKEN)
# ---------------------------------------------------------------------------


class TestWriteReplyJobActionTypes:
    """Verify write_reply_job accepts ALL action_type values the LLM can produce."""

    def _make_item(self, action_type: str, **action_fields) -> dict:
        action = {"action_type": action_type, "draft": "Test reply", **action_fields}
        return {
            "id": "test-item-1",
            "title": "Test item",
            "source": "Teams: Alice",
            "suggested_actions": [action],
        }

    def test_draft_teams_reply_accepted(self, tmp_dir):
        """LLM outputs 'draft_teams_reply' — this is the most common case."""
        from tui.ipc import write_reply_job

        item = self._make_item("draft_teams_reply", target="Alice Smith")
        with patch("tui.ipc.JOBS_DIR", tmp_dir / "jobs"):
            result = write_reply_job(item, "Hi Alice, sounds good!")
        assert result is True

    def test_send_email_reply_accepted(self, tmp_dir):
        """LLM outputs 'send_email_reply' for email items."""
        from tui.ipc import write_reply_job

        item = self._make_item("send_email_reply", target="bob@contoso.com")
        with patch("tui.ipc.JOBS_DIR", tmp_dir / "jobs"):
            result = write_reply_job(item, "Thanks Bob, will review.")
        assert result is True

    def test_teams_reply_accepted(self, tmp_dir):
        """Internal 'teams_reply' value also works."""
        from tui.ipc import write_reply_job

        item = self._make_item("teams_reply", chat_name="Alice Smith")
        with patch("tui.ipc.JOBS_DIR", tmp_dir / "jobs"):
            result = write_reply_job(item, "Reply text")
        assert result is True

    def test_teams_send_accepted(self, tmp_dir):
        """Internal 'teams_send' value also works."""
        from tui.ipc import write_reply_job

        item = self._make_item("teams_send", recipient="Alice Smith")
        with patch("tui.ipc.JOBS_DIR", tmp_dir / "jobs"):
            result = write_reply_job(item, "Reply text")
        assert result is True

    def test_email_reply_accepted(self, tmp_dir):
        """Internal 'email_reply' value also works."""
        from tui.ipc import write_reply_job

        item = self._make_item("email_reply", search_query="Project update")
        with patch("tui.ipc.JOBS_DIR", tmp_dir / "jobs"):
            result = write_reply_job(item, "Reply text")
        assert result is True

    def test_unknown_action_type_rejected(self, tmp_dir):
        """Unknown action types like 'schedule_meeting' return False."""
        from tui.ipc import write_reply_job

        item = self._make_item("schedule_meeting")
        with patch("tui.ipc.JOBS_DIR", tmp_dir / "jobs"):
            result = write_reply_job(item, "Reply text")
        assert result is False

    def test_dismiss_action_type_rejected(self, tmp_dir):
        """'dismiss' action type is not a reply — returns False."""
        from tui.ipc import write_reply_job

        item = self._make_item("dismiss")
        with patch("tui.ipc.JOBS_DIR", tmp_dir / "jobs"):
            result = write_reply_job(item, "Reply text")
        assert result is False

    def test_empty_action_type_rejected(self, tmp_dir):
        """Empty/missing action_type returns False."""
        from tui.ipc import write_reply_job

        item = self._make_item("")
        with patch("tui.ipc.JOBS_DIR", tmp_dir / "jobs"):
            result = write_reply_job(item, "Reply text")
        assert result is False


# ---------------------------------------------------------------------------
# write_reply_job: target/recipient field resolution
# ---------------------------------------------------------------------------


class TestWriteReplyJobTargetResolution:
    """Verify the target → chat_name/recipient/search_query field mapping."""

    def test_teams_target_becomes_chat_name(self, tmp_dir):
        """LLM 'target' field maps to 'chat_name' in the job YAML."""
        from tui.ipc import write_reply_job

        item = {
            "id": "t1",
            "title": "Budget review",
            "source": "Teams: Fatos Ismali",
            "suggested_actions": [{
                "action_type": "draft_teams_reply",
                "target": "Fatos Ismali",
                "draft": "Will review today",
            }],
        }
        with patch("tui.ipc.JOBS_DIR", tmp_dir / "jobs"):
            write_reply_job(item, "Hi Fatos, will review today.")

        yaml_files = list((tmp_dir / "jobs" / "pending").glob("*.yaml"))
        assert len(yaml_files) == 1
        job = yaml.safe_load(yaml_files[0].read_text(encoding="utf-8"))
        assert job["type"] == "teams_send"
        assert job["chat_name"] == "Fatos Ismali"
        assert job["message"] == "Hi Fatos, will review today."

    def test_teams_chat_name_takes_priority_over_target(self, tmp_dir):
        """If both chat_name and target exist, chat_name wins."""
        from tui.ipc import write_reply_job

        item = {
            "id": "t2",
            "title": "Test",
            "suggested_actions": [{
                "action_type": "draft_teams_reply",
                "chat_name": "Specific Chat",
                "target": "Person Name",
            }],
        }
        with patch("tui.ipc.JOBS_DIR", tmp_dir / "jobs"):
            write_reply_job(item, "Reply")

        job = yaml.safe_load(list((tmp_dir / "jobs" / "pending").glob("*.yaml"))[0].read_text())
        assert job["chat_name"] == "Specific Chat"
        assert "recipient" not in job

    def test_teams_recipient_takes_priority_over_target(self, tmp_dir):
        """If both recipient and target exist, recipient wins."""
        from tui.ipc import write_reply_job

        item = {
            "id": "t3",
            "title": "Test",
            "suggested_actions": [{
                "action_type": "teams_send",
                "recipient": "alice@contoso.com",
                "target": "Alice",
            }],
        }
        with patch("tui.ipc.JOBS_DIR", tmp_dir / "jobs"):
            write_reply_job(item, "Reply")

        job = yaml.safe_load(list((tmp_dir / "jobs" / "pending").glob("*.yaml"))[0].read_text())
        assert job["recipient"] == "alice@contoso.com"
        assert "chat_name" not in job

    def test_teams_fallback_to_title_when_source_is_teams(self, tmp_dir):
        """When no target/chat_name/recipient, falls back to item title if source=Teams."""
        from tui.ipc import write_reply_job

        item = {
            "id": "t4",
            "title": "Alice Smith conversation about budget",
            "source": "Teams: Alice Smith",
            "suggested_actions": [{"action_type": "draft_teams_reply"}],
        }
        with patch("tui.ipc.JOBS_DIR", tmp_dir / "jobs"):
            write_reply_job(item, "Reply")

        job = yaml.safe_load(list((tmp_dir / "jobs" / "pending").glob("*.yaml"))[0].read_text())
        assert job["chat_name"] == "Alice Smith conversation about budget"[:50]

    def test_email_target_becomes_search_query(self, tmp_dir):
        """LLM 'target' field maps to 'search_query' for email replies."""
        from tui.ipc import write_reply_job

        item = {
            "id": "e1",
            "title": "Q1 pricing update",
            "suggested_actions": [{
                "action_type": "send_email_reply",
                "target": "Bob Wilson",
            }],
        }
        with patch("tui.ipc.JOBS_DIR", tmp_dir / "jobs"):
            write_reply_job(item, "Thanks Bob")

        job = yaml.safe_load(list((tmp_dir / "jobs" / "pending").glob("*.yaml"))[0].read_text())
        assert job["type"] == "email_reply"
        assert job["search_query"] == "Bob Wilson"

    def test_email_search_query_takes_priority_over_target(self, tmp_dir):
        """Explicit search_query in action takes priority over target."""
        from tui.ipc import write_reply_job

        item = {
            "id": "e2",
            "title": "Pricing update",
            "suggested_actions": [{
                "action_type": "send_email_reply",
                "search_query": "RE: Q1 pricing",
                "target": "Bob Wilson",
            }],
        }
        with patch("tui.ipc.JOBS_DIR", tmp_dir / "jobs"):
            write_reply_job(item, "Reply")

        job = yaml.safe_load(list((tmp_dir / "jobs" / "pending").glob("*.yaml"))[0].read_text())
        assert job["search_query"] == "RE: Q1 pricing"

    def test_email_fallback_to_title(self, tmp_dir):
        """When no search_query or target, falls back to item title."""
        from tui.ipc import write_reply_job

        item = {
            "id": "e3",
            "title": "Important contract review",
            "suggested_actions": [{"action_type": "send_email_reply"}],
        }
        with patch("tui.ipc.JOBS_DIR", tmp_dir / "jobs"):
            write_reply_job(item, "Reply")

        job = yaml.safe_load(list((tmp_dir / "jobs" / "pending").glob("*.yaml"))[0].read_text())
        assert job["search_query"] == "Important contract review"


# ---------------------------------------------------------------------------
# write_reply_job: edge cases and failure modes
# ---------------------------------------------------------------------------


class TestWriteReplyJobEdgeCases:
    """Boundary conditions and failure modes."""

    def test_no_suggested_actions_returns_false(self, tmp_dir):
        """Item with no suggested_actions returns False."""
        from tui.ipc import write_reply_job

        item = {"id": "x", "title": "No actions"}
        with patch("tui.ipc.JOBS_DIR", tmp_dir / "jobs"):
            assert write_reply_job(item, "Reply") is False

    def test_empty_suggested_actions_returns_false(self, tmp_dir):
        """Item with empty suggested_actions list returns False."""
        from tui.ipc import write_reply_job

        item = {"id": "x", "title": "Empty actions", "suggested_actions": []}
        with patch("tui.ipc.JOBS_DIR", tmp_dir / "jobs"):
            assert write_reply_job(item, "Reply") is False

    def test_creates_pending_dir_if_missing(self, tmp_dir):
        """jobs/pending/ directory is auto-created."""
        from tui.ipc import write_reply_job

        jobs_dir = tmp_dir / "fresh-jobs"
        assert not jobs_dir.exists()

        item = {
            "id": "x",
            "suggested_actions": [{"action_type": "draft_teams_reply", "target": "Alice"}],
        }
        with patch("tui.ipc.JOBS_DIR", jobs_dir):
            result = write_reply_job(item, "Reply")
        assert result is True
        assert (jobs_dir / "pending").exists()
        assert len(list((jobs_dir / "pending").glob("*.yaml"))) == 1

    def test_yaml_file_content_is_valid(self, tmp_dir):
        """The written YAML file is valid YAML and has expected structure."""
        from tui.ipc import write_reply_job

        item = {
            "id": "x",
            "suggested_actions": [{"action_type": "draft_teams_reply", "target": "Alice"}],
        }
        with patch("tui.ipc.JOBS_DIR", tmp_dir / "jobs"):
            write_reply_job(item, "Hello Alice, reviewing now.")

        yaml_file = list((tmp_dir / "jobs" / "pending").glob("*.yaml"))[0]
        job = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))

        assert isinstance(job, dict)
        assert job["type"] == "teams_send"
        assert job["message"] == "Hello Alice, reviewing now."
        assert job["_source"] == "tui"
        assert "chat_name" in job or "recipient" in job

    def test_source_field_case_insensitive(self, tmp_dir):
        """Source matching is case-insensitive (Teams, teams, TEAMS all work)."""
        from tui.ipc import write_reply_job

        for source in ["Teams: Alice", "teams: alice", "TEAMS: ALICE"]:
            item = {
                "id": "x",
                "title": "Alice chat",
                "source": source,
                "suggested_actions": [{"action_type": "draft_teams_reply"}],
            }
            with patch("tui.ipc.JOBS_DIR", tmp_dir / "jobs"):
                result = write_reply_job(item, "Reply")
            assert result is True


# ---------------------------------------------------------------------------
# End-to-end: write_reply_job output → worker pickup compatibility
# ---------------------------------------------------------------------------


class TestReplyJobWorkerCompat:
    """Verify that YAML files written by write_reply_job are compatible with
    the worker's _execute_teams_send and _execute_email_reply functions."""

    def test_teams_job_has_fields_worker_expects(self, tmp_dir):
        """Worker needs: type=teams_send, message, and (chat_name or recipient)."""
        from tui.ipc import write_reply_job

        item = {
            "id": "compat-1",
            "source": "Teams: Fatos",
            "suggested_actions": [{
                "action_type": "draft_teams_reply",
                "target": "Fatos Ismali",
            }],
        }
        with patch("tui.ipc.JOBS_DIR", tmp_dir / "jobs"):
            write_reply_job(item, "Will review today.")

        job = yaml.safe_load(list((tmp_dir / "jobs" / "pending").glob("*.yaml"))[0].read_text())

        # Worker checks these exact fields:
        assert job["type"] == "teams_send"
        assert job.get("message") == "Will review today."
        assert job.get("chat_name") or job.get("recipient"), \
            "Worker needs chat_name or recipient — got neither"

    def test_email_job_has_fields_worker_expects(self, tmp_dir):
        """Worker needs: type=email_reply, message, and search_query."""
        from tui.ipc import write_reply_job

        item = {
            "id": "compat-2",
            "title": "Q1 budget review",
            "suggested_actions": [{
                "action_type": "send_email_reply",
                "target": "bob@contoso.com",
            }],
        }
        with patch("tui.ipc.JOBS_DIR", tmp_dir / "jobs"):
            write_reply_job(item, "Thanks Bob.")

        job = yaml.safe_load(list((tmp_dir / "jobs" / "pending").glob("*.yaml"))[0].read_text())

        # Worker checks these exact fields:
        assert job["type"] == "email_reply"
        assert job.get("message") == "Thanks Bob."
        assert job.get("search_query"), \
            "Worker needs search_query — got empty/missing"

    @pytest.mark.asyncio
    async def test_teams_send_worker_routing(self, tmp_dir):
        """Worker routes teams_send job to reply_to_chat or send_teams_message."""
        from daemon.worker import _execute_teams_send

        job = {
            "type": "teams_send",
            "message": "Hello Alice",
            "chat_name": "Alice Smith",
        }
        with patch("collectors.teams_sender.reply_to_chat", new_callable=AsyncMock) as mock_reply:
            mock_reply.return_value = {"success": True, "detail": "Sent"}
            result = await _execute_teams_send(job)

        assert result["success"] is True
        mock_reply.assert_called_once_with("Alice Smith", "Hello Alice")

    @pytest.mark.asyncio
    async def test_teams_send_by_recipient(self, tmp_dir):
        """Worker uses send_teams_message when recipient (not chat_name) is set."""
        from daemon.worker import _execute_teams_send

        job = {
            "type": "teams_send",
            "message": "Hello Bob",
            "recipient": "Bob Wilson",
        }
        with patch("collectors.teams_sender.send_teams_message", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = {"success": True, "detail": "Sent"}
            result = await _execute_teams_send(job)

        assert result["success"] is True
        mock_send.assert_called_once_with("Bob Wilson", "Hello Bob")

    @pytest.mark.asyncio
    async def test_teams_send_sidebar_fallback_to_new_chat(self, tmp_dir):
        """When reply_to_chat can't find chat in sidebar, falls back to send_teams_message."""
        from daemon.worker import _execute_teams_send

        job = {
            "type": "teams_send",
            "message": "Hello Esther",
            "chat_name": "Esther Dediashvili",
        }
        with (
            patch("collectors.teams_sender.reply_to_chat", new_callable=AsyncMock) as mock_reply,
            patch("collectors.teams_sender.send_teams_message", new_callable=AsyncMock) as mock_send,
        ):
            # Sidebar lookup fails
            mock_reply.return_value = {"success": False, "detail": "Chat 'Esther Dediashvili' not found in sidebar"}
            # Fallback new-chat search succeeds
            mock_send.return_value = {"success": True, "detail": "Message sent to Esther Dediashvili"}
            result = await _execute_teams_send(job)

        assert result["success"] is True
        mock_reply.assert_called_once_with("Esther Dediashvili", "Hello Esther")
        mock_send.assert_called_once_with("Esther Dediashvili", "Hello Esther")

    @pytest.mark.asyncio
    async def test_teams_send_sidebar_fallback_not_triggered_on_other_errors(self):
        """Fallback only triggers for 'not found in sidebar', not other failures."""
        from daemon.worker import _execute_teams_send

        job = {
            "type": "teams_send",
            "message": "Hello",
            "chat_name": "Alice",
        }
        with patch("collectors.teams_sender.reply_to_chat", new_callable=AsyncMock) as mock_reply:
            mock_reply.return_value = {"success": False, "detail": "Could not find compose box"}
            result = await _execute_teams_send(job)

        assert result["success"] is False
        assert "compose box" in result["detail"]

    @pytest.mark.asyncio
    async def test_teams_send_no_target_fails_gracefully(self):
        """Worker returns failure when neither chat_name nor recipient is set."""
        from daemon.worker import _execute_teams_send

        job = {"type": "teams_send", "message": "Orphaned message"}
        result = await _execute_teams_send(job)
        assert result["success"] is False
        assert "No recipient" in result["detail"]

    @pytest.mark.asyncio
    async def test_teams_send_no_message_fails(self):
        """Worker returns failure when message is empty."""
        from daemon.worker import _execute_teams_send

        job = {"type": "teams_send", "message": "", "chat_name": "Alice"}
        result = await _execute_teams_send(job)
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_email_reply_worker_routing(self, tmp_dir):
        """Worker routes email_reply job to reply_to_email."""
        from daemon.worker import _execute_email_reply

        job = {
            "type": "email_reply",
            "message": "Thanks for the update.",
            "search_query": "RE: Q1 budget",
        }
        with patch("collectors.outlook_sender.reply_to_email", new_callable=AsyncMock) as mock_reply:
            mock_reply.return_value = {"success": True, "detail": "Replied"}
            result = await _execute_email_reply(job)

        assert result["success"] is True
        mock_reply.assert_called_once_with("RE: Q1 budget", "Thanks for the update.")

    @pytest.mark.asyncio
    async def test_email_reply_no_search_query_fails(self):
        """Worker returns failure when search_query is empty."""
        from daemon.worker import _execute_email_reply

        job = {"type": "email_reply", "message": "Reply", "search_query": ""}
        result = await _execute_email_reply(job)
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_email_reply_no_message_fails(self):
        """Worker returns failure when message is empty."""
        from daemon.worker import _execute_email_reply

        job = {"type": "email_reply", "message": "", "search_query": "RE: test"}
        result = await _execute_email_reply(job)
        assert result["success"] is False


# ---------------------------------------------------------------------------
# Full round-trip: simulated LLM output → write_reply_job → worker execution
# ---------------------------------------------------------------------------


class TestFullRoundTrip:
    """Simulate real digest/triage JSON items (as the LLM would produce them)
    through the full write → read → execute pipeline."""

    SAMPLE_TRIAGE_ITEM = {
        "id": "reply-fatos-vodafone-arch",
        "title": "Vodafone architecture whitepaper review - due TOMORROW",
        "type": "reply_needed",
        "priority": "high",
        "source": "Teams: Fatos Ismali",
        "summary": "Fatos requested review of Vodafone_ARCHITECTURE_RATIONALE.md",
        "date": "2026-02-27",
        "suggested_actions": [
            {
                "label": "Reply to Fatos",
                "action_type": "draft_teams_reply",
                "draft": "Hi Fatos - I'll review the Vodafone architecture whitepaper today.",
                "target": "Fatos Ismali",
            }
        ],
    }

    SAMPLE_DIGEST_EMAIL_ITEM = {
        "id": "email-bob-pricing",
        "title": "Q1 pricing proposal review",
        "type": "action_needed",
        "priority": "medium",
        "source": "Email: Bob Wilson",
        "summary": "Bob sent the updated pricing proposal for review",
        "date": "2026-03-01",
        "suggested_actions": [
            {
                "label": "Reply to Bob",
                "action_type": "send_email_reply",
                "draft": "Thanks Bob, I'll review the pricing proposal today.",
                "target": "Bob Wilson",
            }
        ],
    }

    def test_triage_teams_reply_round_trip(self, tmp_dir):
        """Triage item with draft_teams_reply → YAML → valid worker input."""
        from tui.ipc import write_reply_job

        with patch("tui.ipc.JOBS_DIR", tmp_dir / "jobs"):
            result = write_reply_job(
                self.SAMPLE_TRIAGE_ITEM,
                self.SAMPLE_TRIAGE_ITEM["suggested_actions"][0]["draft"],
            )
        assert result is True

        # Read back and verify worker compatibility
        job = yaml.safe_load(
            list((tmp_dir / "jobs" / "pending").glob("*.yaml"))[0].read_text()
        )
        assert job["type"] == "teams_send"
        assert job["chat_name"] == "Fatos Ismali"
        assert "whitepaper" in job["message"]

    def test_digest_email_reply_round_trip(self, tmp_dir):
        """Digest email item with send_email_reply → YAML → valid worker input."""
        from tui.ipc import write_reply_job

        with patch("tui.ipc.JOBS_DIR", tmp_dir / "jobs"):
            result = write_reply_job(
                self.SAMPLE_DIGEST_EMAIL_ITEM,
                self.SAMPLE_DIGEST_EMAIL_ITEM["suggested_actions"][0]["draft"],
            )
        assert result is True

        job = yaml.safe_load(
            list((tmp_dir / "jobs" / "pending").glob("*.yaml"))[0].read_text()
        )
        assert job["type"] == "email_reply"
        assert job["search_query"] == "Bob Wilson"
        assert "pricing" in job["message"]

    @pytest.mark.asyncio
    async def test_full_pipeline_teams(self, tmp_dir):
        """Full: triage item → write_reply_job → read YAML → _execute_teams_send."""
        from tui.ipc import write_reply_job
        from daemon.worker import _execute_teams_send

        # Step 1: TUI writes the job
        with patch("tui.ipc.JOBS_DIR", tmp_dir / "jobs"):
            write_reply_job(self.SAMPLE_TRIAGE_ITEM, "Hi Fatos, on it!")

        # Step 2: Worker reads the YAML (simulating what job_worker does)
        yaml_file = list((tmp_dir / "jobs" / "pending").glob("*.yaml"))[0]
        job = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))

        # Step 3: Worker executes
        with patch("collectors.teams_sender.reply_to_chat", new_callable=AsyncMock) as mock_reply:
            mock_reply.return_value = {"success": True, "detail": "Sent"}
            result = await _execute_teams_send(job)

        assert result["success"] is True
        mock_reply.assert_called_once_with("Fatos Ismali", "Hi Fatos, on it!")

    @pytest.mark.asyncio
    async def test_full_pipeline_email(self, tmp_dir):
        """Full: digest email → write_reply_job → read YAML → _execute_email_reply."""
        from tui.ipc import write_reply_job
        from daemon.worker import _execute_email_reply

        # Step 1: TUI writes the job
        with patch("tui.ipc.JOBS_DIR", tmp_dir / "jobs"):
            write_reply_job(self.SAMPLE_DIGEST_EMAIL_ITEM, "Thanks Bob!")

        # Step 2: Worker reads the YAML
        yaml_file = list((tmp_dir / "jobs" / "pending").glob("*.yaml"))[0]
        job = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))

        # Step 3: Worker executes
        with patch("collectors.outlook_sender.reply_to_email", new_callable=AsyncMock) as mock_reply:
            mock_reply.return_value = {"success": True, "detail": "Replied"}
            result = await _execute_email_reply(job)

        assert result["success"] is True
        mock_reply.assert_called_once_with("Bob Wilson", "Thanks Bob!")


# ---------------------------------------------------------------------------
# Dismiss/archive flow integration
# ---------------------------------------------------------------------------


class TestDismissArchiveFlow:
    """Verify the dismiss/archive/restore cycle works end-to-end with real items."""

    def test_dismiss_then_restore(self, tmp_dir):
        """Dismiss an item, verify it's gone, restore it, verify it's back."""
        from tui.ipc import dismiss_item, restore_item, _load_digest_actions

        actions_file = tmp_dir / ".digest-actions.json"
        with patch("tui.ipc.DIGEST_ACTIONS_FILE", actions_file):
            dismiss_item("reply-alice", title="Alice review", source="Teams: Alice")
            actions = _load_digest_actions()
            assert any(d["item"] == "reply-alice" for d in actions["dismissed"])

            restore_item("reply-alice")
            actions = _load_digest_actions()
            assert not any(d["item"] == "reply-alice" for d in actions["dismissed"])

    def test_archive_preserves_30_day_ttl(self, tmp_dir):
        """Archived items have status=archived (30-day TTL vs 1-day snooze)."""
        from tui.ipc import dismiss_item, archive_item, _load_digest_actions

        actions_file = tmp_dir / ".digest-actions.json"
        with patch("tui.ipc.DIGEST_ACTIONS_FILE", actions_file):
            dismiss_item("item-x", title="X")
            archive_item("item-x")
            actions = _load_digest_actions()

        entry = actions["dismissed"][0]
        assert entry["status"] == "archived"
        assert "archived_at" in entry

    def test_add_note_persists(self, tmp_dir):
        """Notes are persisted and retrievable."""
        from tui.ipc import add_note, _load_digest_actions

        actions_file = tmp_dir / ".digest-actions.json"
        with patch("tui.ipc.DIGEST_ACTIONS_FILE", actions_file):
            add_note("item-y", "Need to follow up next week")
            actions = _load_digest_actions()

        note_entry = actions["notes"]["item-y"]
        assert note_entry["note"] == "Need to follow up next week"
        assert "added_at" in note_entry


# ---------------------------------------------------------------------------
# Data loader → reply flow (simulates real monitoring/digest JSON on disk)
# ---------------------------------------------------------------------------


class TestDataLoaderReplyFlow:
    """Test that items loaded from real JSON files on disk can be replied to."""

    MONITORING_JSON = {
        "items": [
            {
                "id": "reply-fatos-arch-review",
                "title": "Vodafone architecture whitepaper review",
                "type": "reply_needed",
                "priority": "high",
                "source": "Teams: Fatos Ismali",
                "summary": "Fatos requested review",
                "suggested_actions": [
                    {
                        "label": "Reply to Fatos",
                        "action_type": "draft_teams_reply",
                        "draft": "Hi Fatos, will review today.",
                        "target": "Fatos Ismali",
                    }
                ],
            },
            {
                "id": "cal-apex-workshop",
                "title": "Apex AI Deep Dive workshop",
                "type": "meeting_prep",
                "priority": "medium",
                "source": "Calendar: Tomorrow 12:00",
                "summary": "Prepare for workshop",
                "suggested_actions": [
                    {
                        "label": "Dismiss",
                        "action_type": "dismiss",
                    }
                ],
            },
        ],
        "stats": {"teams_unread": 3, "emails_actioned": 1},
    }

    DIGEST_JSON = {
        "items": [
            {
                "id": "email-gsk-data",
                "title": "GSK mock investigation data - OVERDUE",
                "type": "action_needed",
                "priority": "high",
                "source": "Email: compliance team",
                "summary": "Investigation data due Feb 28",
                "project": "gsk-investigations",
                "suggested_actions": [
                    {
                        "label": "Reply to team",
                        "action_type": "send_email_reply",
                        "draft": "Will send the data today.",
                        "target": "compliance team",
                    }
                ],
            },
        ],
    }

    def test_triage_items_from_json_can_be_replied(self, tmp_dir):
        """Load monitoring JSON → pick an item → write_reply_job succeeds."""
        from tui.ipc import write_reply_job

        # Write monitoring JSON as the TUI data loader would find it
        monitoring_file = tmp_dir / "monitoring-2026-03-02T09-00.json"
        monitoring_file.write_text(json.dumps(self.MONITORING_JSON), encoding="utf-8")

        # Simulate _load_triage_items
        data = json.loads(monitoring_file.read_text(encoding="utf-8"))
        items = data.get("items", [])

        # First item has draft_teams_reply — should succeed
        with patch("tui.ipc.JOBS_DIR", tmp_dir / "jobs"):
            result = write_reply_job(items[0], items[0]["suggested_actions"][0]["draft"])
        assert result is True

        # Verify the YAML
        job = yaml.safe_load(list((tmp_dir / "jobs" / "pending").glob("*.yaml"))[0].read_text())
        assert job["type"] == "teams_send"
        assert job["chat_name"] == "Fatos Ismali"

    def test_dismiss_action_not_replyable(self, tmp_dir):
        """Items with only 'dismiss' action can't be replied to."""
        from tui.ipc import write_reply_job

        monitoring_file = tmp_dir / "monitoring-2026-03-02T09-00.json"
        monitoring_file.write_text(json.dumps(self.MONITORING_JSON), encoding="utf-8")

        data = json.loads(monitoring_file.read_text(encoding="utf-8"))
        items = data.get("items", [])

        # Second item has dismiss action — should fail (not a reply)
        with patch("tui.ipc.JOBS_DIR", tmp_dir / "jobs"):
            result = write_reply_job(items[1], "Some text")
        assert result is False

    def test_digest_email_items_from_json(self, tmp_dir):
        """Load digest JSON → pick an email item → write_reply_job succeeds."""
        from tui.ipc import write_reply_job

        digest_file = tmp_dir / "digests" / "2026-03-02.json"
        digest_file.parent.mkdir(parents=True)
        digest_file.write_text(json.dumps(self.DIGEST_JSON), encoding="utf-8")

        data = json.loads(digest_file.read_text(encoding="utf-8"))
        items = data.get("items", [])

        with patch("tui.ipc.JOBS_DIR", tmp_dir / "jobs"):
            result = write_reply_job(items[0], items[0]["suggested_actions"][0]["draft"])
        assert result is True

        job = yaml.safe_load(list((tmp_dir / "jobs" / "pending").glob("*.yaml"))[0].read_text())
        assert job["type"] == "email_reply"
        assert job["search_query"] == "compliance team"


# ---------------------------------------------------------------------------
# Pending actions flow (SDK tool → .pending-actions → worker)
# ---------------------------------------------------------------------------


class TestPendingActionsFlow:
    """Test the SDK tool → pending actions → worker execution path."""

    @pytest.mark.asyncio
    async def test_process_pending_teams_action(self, tmp_dir):
        """SDK send_teams_message tool output → process_pending_actions → execution."""
        from daemon.worker import process_pending_actions

        actions_dir = tmp_dir / ".pending-actions"
        actions_dir.mkdir()

        # Simulate what the SDK send_teams_message tool writes
        action_data = {
            "type": "teams_send",
            "recipient": "Alice Smith",
            "message": "Hello from chat mode",
            "chat_name": "",
            "queued_at": "2026-03-02T09:00:00",
        }
        (actions_dir / "teams-send-090000.json").write_text(
            json.dumps(action_data), encoding="utf-8"
        )

        with patch("sdk.tools.PENDING_ACTIONS_DIR", actions_dir), \
             patch("collectors.teams_sender.send_teams_message", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = {"success": True, "detail": "Sent"}
            await process_pending_actions()

        mock_send.assert_called_once_with("Alice Smith", "Hello from chat mode")
        # Action file should be cleaned up
        assert len(list(actions_dir.glob("*.json"))) == 0

    @pytest.mark.asyncio
    async def test_process_pending_email_action(self, tmp_dir):
        """SDK send_email_reply tool output → process_pending_actions → execution."""
        from daemon.worker import process_pending_actions

        actions_dir = tmp_dir / ".pending-actions"
        actions_dir.mkdir()

        action_data = {
            "type": "email_reply",
            "search_query": "RE: Budget proposal",
            "message": "Approved, thanks.",
            "queued_at": "2026-03-02T09:00:00",
        }
        (actions_dir / "email-reply-090000.json").write_text(
            json.dumps(action_data), encoding="utf-8"
        )

        with patch("sdk.tools.PENDING_ACTIONS_DIR", actions_dir), \
             patch("collectors.outlook_sender.reply_to_email", new_callable=AsyncMock) as mock_reply:
            mock_reply.return_value = {"success": True, "detail": "Replied"}
            await process_pending_actions()

        mock_reply.assert_called_once_with("RE: Budget proposal", "Approved, thanks.")
        assert len(list(actions_dir.glob("*.json"))) == 0

    @pytest.mark.asyncio
    async def test_process_pending_actions_empty_dir(self, tmp_dir):
        """No crash when pending actions directory exists but is empty."""
        from daemon.worker import process_pending_actions

        actions_dir = tmp_dir / ".pending-actions"
        actions_dir.mkdir()

        with patch("sdk.tools.PENDING_ACTIONS_DIR", actions_dir):
            await process_pending_actions()  # Should not raise

    @pytest.mark.asyncio
    async def test_process_pending_actions_no_dir(self, tmp_dir):
        """No crash when pending actions directory doesn't exist."""
        from daemon.worker import process_pending_actions

        with patch("sdk.tools.PENDING_ACTIONS_DIR", tmp_dir / "nonexistent"):
            await process_pending_actions()  # Should not raise
