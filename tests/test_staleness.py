"""Tests for staleness prevention: resolved status, finality detection,
per-item verification queries, digest-actions cleanup, and inline notes.

Unit tests + integration tests covering the full flow from user action
through to agent prompt generation and housekeeping cleanup.
"""

import json
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Unit: Finality keyword detection
# ---------------------------------------------------------------------------


class TestFinalityDetection:
    """_is_finality_note correctly identifies 'done' signals."""

    def test_done(self):
        from tui.ipc import _is_finality_note
        assert _is_finality_note("done")

    def test_dealt_with(self):
        from tui.ipc import _is_finality_note
        assert _is_finality_note("dealt with already")

    def test_handled(self):
        from tui.ipc import _is_finality_note
        assert _is_finality_note("handled")

    def test_already_sent(self):
        from tui.ipc import _is_finality_note
        assert _is_finality_note("already sent that")

    def test_not_needed(self):
        from tui.ipc import _is_finality_note
        assert _is_finality_note("not needed anymore")

    def test_no_longer_relevant(self):
        from tui.ipc import _is_finality_note
        assert _is_finality_note("no longer relevant")

    def test_completed(self):
        from tui.ipc import _is_finality_note
        assert _is_finality_note("completed yesterday")

    def test_nothing_to_do(self):
        from tui.ipc import _is_finality_note
        assert _is_finality_note("nothing to do here")

    def test_never_bring_back(self):
        from tui.ipc import _is_finality_note
        assert _is_finality_note("never bring back")

    def test_doje_typo(self):
        from tui.ipc import _is_finality_note
        assert _is_finality_note("doje")

    def test_case_insensitive(self):
        from tui.ipc import _is_finality_note
        assert _is_finality_note("DONE")
        assert _is_finality_note("Already Handled")

    def test_non_finality_note(self):
        from tui.ipc import _is_finality_note
        assert not _is_finality_note("follow up Monday")

    def test_question_not_finality(self):
        from tui.ipc import _is_finality_note
        assert not _is_finality_note("what was this about?")

    def test_reminder_not_finality(self):
        from tui.ipc import _is_finality_note
        assert not _is_finality_note("remind me on Friday")

    def test_empty_not_finality(self):
        from tui.ipc import _is_finality_note
        assert not _is_finality_note("")

    def test_whitespace_not_finality(self):
        from tui.ipc import _is_finality_note
        assert not _is_finality_note("   ")


# ---------------------------------------------------------------------------
# Unit: Resolved status in add_note
# ---------------------------------------------------------------------------


class TestResolvedStatus:
    """add_note auto-upgrades to 'resolved' on finality keywords."""

    def test_add_note_done_upgrades_to_resolved(self, tmp_dir):
        from tui.ipc import dismiss_item, add_note, _load_digest_actions

        actions_file = tmp_dir / ".digest-actions.json"
        with patch("tui.ipc.DIGEST_ACTIONS_FILE", actions_file):
            dismiss_item("reply-raveen", title="Raveen pricing call")
            add_note("reply-raveen", "done")
            actions = _load_digest_actions()

        entry = actions["dismissed"][0]
        assert entry["status"] == "resolved"
        assert "resolved_at" in entry
        assert actions["notes"]["reply-raveen"]["note"] == "done"

    def test_add_note_already_handled_upgrades(self, tmp_dir):
        from tui.ipc import dismiss_item, add_note, _load_digest_actions

        actions_file = tmp_dir / ".digest-actions.json"
        with patch("tui.ipc.DIGEST_ACTIONS_FILE", actions_file):
            dismiss_item("action-send-doc", title="Send doc")
            add_note("action-send-doc", "already sent that yesterday")
            actions = _load_digest_actions()

        assert actions["dismissed"][0]["status"] == "resolved"

    def test_add_note_non_finality_keeps_status(self, tmp_dir):
        from tui.ipc import dismiss_item, add_note, _load_digest_actions

        actions_file = tmp_dir / ".digest-actions.json"
        with patch("tui.ipc.DIGEST_ACTIONS_FILE", actions_file):
            dismiss_item("reply-alice", title="Alice")
            add_note("reply-alice", "follow up on Monday")
            actions = _load_digest_actions()

        assert actions["dismissed"][0]["status"] == "archived"  # unchanged

    def test_add_finality_note_without_prior_dismiss_creates_resolved(self, tmp_dir):
        """If user adds a 'done' note for an item that wasn't dismissed yet,
        create a resolved entry."""
        from tui.ipc import add_note, _load_digest_actions

        actions_file = tmp_dir / ".digest-actions.json"
        with patch("tui.ipc.DIGEST_ACTIONS_FILE", actions_file):
            add_note("orphan-item", "done")
            actions = _load_digest_actions()

        assert len(actions["dismissed"]) == 1
        assert actions["dismissed"][0]["item"] == "orphan-item"
        assert actions["dismissed"][0]["status"] == "resolved"


# ---------------------------------------------------------------------------
# Unit: Snooze function
# ---------------------------------------------------------------------------


class TestSnoozeItem:
    """snooze_item creates 1-day TTL entries."""

    def test_snooze_creates_dismissed_status(self, tmp_dir):
        from tui.ipc import snooze_item, _load_digest_actions

        actions_file = tmp_dir / ".digest-actions.json"
        with patch("tui.ipc.DIGEST_ACTIONS_FILE", actions_file):
            snooze_item("reply-bob", title="Bob", source="Teams")
            actions = _load_digest_actions()

        assert len(actions["dismissed"]) == 1
        assert actions["dismissed"][0]["status"] == "dismissed"
        assert actions["dismissed"][0]["item"] == "reply-bob"

    def test_snooze_deduplicates(self, tmp_dir):
        from tui.ipc import snooze_item, _load_digest_actions

        actions_file = tmp_dir / ".digest-actions.json"
        with patch("tui.ipc.DIGEST_ACTIONS_FILE", actions_file):
            snooze_item("reply-bob", title="Bob")
            snooze_item("reply-bob", title="Bob again")
            actions = _load_digest_actions()

        assert len(actions["dismissed"]) == 1


# ---------------------------------------------------------------------------
# Unit: Dismissed block with resolved + inline notes
# ---------------------------------------------------------------------------


class TestDismissedBlockResolved:
    """_build_dismissed_block handles resolved items and inline notes."""

    def test_resolved_items_always_included(self):
        """Resolved items have no TTL — they appear regardless of age."""
        from sdk.runner import _build_dismissed_block

        actions = {
            "dismissed": [{
                "item": "reply-raveen-old",
                "status": "resolved",
                "title": "Raveen pricing",
                "dismissed_at": (datetime.now() - timedelta(days=365)).isoformat(),
                "resolved_at": (datetime.now() - timedelta(days=365)).isoformat(),
            }],
            "notes": {"reply-raveen-old": {"note": "done"}},
        }
        with patch("sdk.runner.load_actions", return_value=actions):
            result = _build_dismissed_block()

        assert "reply-raveen-old" in result
        assert "PERMANENTLY done" in result
        assert "NEVER re-create" in result

    def test_notes_included_inline(self):
        """Notes appear inline with their dismissed item."""
        from sdk.runner import _build_dismissed_block

        actions = {
            "dismissed": [{
                "item": "action-gsk",
                "status": "archived",
                "title": "GSK diagram",
                "dismissed_at": datetime.now().isoformat(),
            }],
            "notes": {"action-gsk": {"note": "this has been dealt with"}},
        }
        with patch("sdk.runner.load_actions", return_value=actions):
            result = _build_dismissed_block()

        assert "action-gsk" in result
        assert "this has been dealt with" in result
        assert 'User note: "this has been dealt with"' in result

    def test_mixed_statuses(self):
        """Snoozed, archived, and resolved items grouped correctly."""
        from sdk.runner import _build_dismissed_block

        now = datetime.now()
        actions = {
            "dismissed": [
                {"item": "snz", "status": "dismissed",
                 "dismissed_at": now.isoformat()},
                {"item": "arc", "status": "archived",
                 "dismissed_at": (now - timedelta(days=5)).isoformat()},
                {"item": "res", "status": "resolved",
                 "dismissed_at": (now - timedelta(days=100)).isoformat()},
            ],
            "notes": {},
        }
        with patch("sdk.runner.load_actions", return_value=actions):
            result = _build_dismissed_block()

        assert "snz" in result
        assert "arc" in result
        assert "res" in result
        assert "Snoozed today" in result
        assert "Archived" in result
        assert "Resolved" in result


# ---------------------------------------------------------------------------
# Unit: Per-item verification queries
# ---------------------------------------------------------------------------


class TestVerificationQueries:
    """_build_verification_query generates appropriate queries per item type."""

    def test_reply_needed_generates_source_query(self):
        from sdk.runner import _build_verification_query
        item = {
            "type": "reply_needed",
            "title": "Raveen pricing discussion",
            "source": "Teams: Raveen Bains (1:1 chat)",
        }
        result = _build_verification_query(item)
        assert "WorkIQ" in result
        assert "Teams: Raveen Bains" in result
        assert "reply" in result.lower() or "interact" in result.lower()

    def test_action_item_generates_title_query(self):
        from sdk.runner import _build_verification_query
        item = {
            "type": "action_item",
            "title": "Send pricing proposal to Contoso",
            "source": "Meeting: Weekly standup",
        }
        result = _build_verification_query(item)
        assert "WorkIQ" in result
        assert "Send pricing proposal" in result

    def test_input_needed_uses_source(self):
        from sdk.runner import _build_verification_query
        item = {
            "type": "input_needed",
            "title": "HSF scoping decision",
            "source": "Email from Sarah Jones (HSF)",
        }
        result = _build_verification_query(item)
        assert "Email from Sarah Jones" in result

    def test_empty_source_returns_empty(self):
        from sdk.runner import _build_verification_query
        item = {"type": "fyi", "title": "Nothing important", "source": ""}
        result = _build_verification_query(item)
        assert result == ""

    def test_carry_forward_includes_verify_lines(self):
        """_build_carry_forward includes Verify lines per item."""
        from sdk.runner import _build_carry_forward
        today = datetime.now().strftime("%Y-%m-%d")
        prev = {
            "items": [
                {"priority": "high", "title": "Reply to Raveen",
                 "id": "reply-raveen", "source": "Teams: Raveen Bains (1:1)",
                 "type": "reply_needed", "date": today},
                {"priority": "medium", "title": "Send deck to client",
                 "id": "action-deck", "source": "Meeting: Strategy call",
                 "type": "action_item", "date": today},
            ]
        }
        result = _build_carry_forward(prev)
        assert "**Verify**:" in result
        assert "MANDATORY" in result
        # Each item should have a verify line
        assert result.count("**Verify**:") == 2


# ---------------------------------------------------------------------------
# Unit: Housekeeping digest-actions cleanup
# ---------------------------------------------------------------------------


class TestDigestActionsCleanup:
    """_prune_digest_actions removes expired entries, keeps resolved."""

    def test_expired_snooze_removed(self, tmp_path):
        from core.housekeeping import _prune_digest_actions

        actions = {
            "dismissed": [{
                "item": "old-snooze",
                "status": "dismissed",
                "dismissed_at": (datetime.now() - timedelta(days=3)).isoformat(),
            }],
            "notes": {},
        }
        path = tmp_path / ".digest-actions.json"
        path.write_text(json.dumps(actions), encoding="utf-8")

        removed = _prune_digest_actions(path)
        assert removed == 1

        data = json.loads(path.read_text(encoding="utf-8"))
        assert len(data["dismissed"]) == 0

    def test_expired_archive_removed(self, tmp_path):
        from core.housekeeping import _prune_digest_actions

        actions = {
            "dismissed": [{
                "item": "old-archive",
                "status": "archived",
                "dismissed_at": (datetime.now() - timedelta(days=45)).isoformat(),
            }],
            "notes": {},
        }
        path = tmp_path / ".digest-actions.json"
        path.write_text(json.dumps(actions), encoding="utf-8")

        removed = _prune_digest_actions(path)
        assert removed == 1

    def test_resolved_never_removed(self, tmp_path):
        from core.housekeeping import _prune_digest_actions

        actions = {
            "dismissed": [{
                "item": "resolved-item",
                "status": "resolved",
                "dismissed_at": (datetime.now() - timedelta(days=365)).isoformat(),
                "resolved_at": (datetime.now() - timedelta(days=365)).isoformat(),
            }],
            "notes": {"resolved-item": {"note": "done"}},
        }
        path = tmp_path / ".digest-actions.json"
        path.write_text(json.dumps(actions), encoding="utf-8")

        removed = _prune_digest_actions(path)
        assert removed == 0

        data = json.loads(path.read_text(encoding="utf-8"))
        assert len(data["dismissed"]) == 1
        assert data["dismissed"][0]["status"] == "resolved"

    def test_fresh_entries_kept(self, tmp_path):
        from core.housekeeping import _prune_digest_actions

        now = datetime.now()
        actions = {
            "dismissed": [
                {"item": "fresh-snooze", "status": "dismissed",
                 "dismissed_at": now.isoformat()},
                {"item": "fresh-archive", "status": "archived",
                 "dismissed_at": (now - timedelta(days=10)).isoformat()},
            ],
            "notes": {},
        }
        path = tmp_path / ".digest-actions.json"
        path.write_text(json.dumps(actions), encoding="utf-8")

        removed = _prune_digest_actions(path)
        assert removed == 0

    def test_orphaned_notes_cleaned(self, tmp_path):
        """Notes for items that were pruned should also be removed."""
        from core.housekeeping import _prune_digest_actions

        actions = {
            "dismissed": [{
                "item": "expired-snooze",
                "status": "dismissed",
                "dismissed_at": (datetime.now() - timedelta(days=5)).isoformat(),
            }],
            "notes": {
                "expired-snooze": {"note": "some note"},
                "nonexistent-item": {"note": "orphaned"},
            },
        }
        path = tmp_path / ".digest-actions.json"
        path.write_text(json.dumps(actions), encoding="utf-8")

        removed = _prune_digest_actions(path)
        assert removed >= 1  # at least the expired snooze + orphaned notes

        data = json.loads(path.read_text(encoding="utf-8"))
        assert len(data["notes"]) == 0

    def test_missing_file_returns_zero(self, tmp_path):
        from core.housekeeping import _prune_digest_actions
        assert _prune_digest_actions(tmp_path / "nonexistent.json") == 0

    def test_legacy_entries_treated_as_archived(self, tmp_path):
        """Entries without status field use archived TTL (30 days)."""
        from core.housekeeping import _prune_digest_actions

        actions = {
            "dismissed": [
                {"item": "legacy-fresh", "dismissed_at": datetime.now().isoformat()},
                {"item": "legacy-old",
                 "dismissed_at": (datetime.now() - timedelta(days=45)).isoformat()},
            ],
            "notes": {},
        }
        path = tmp_path / ".digest-actions.json"
        path.write_text(json.dumps(actions), encoding="utf-8")

        removed = _prune_digest_actions(path)
        assert removed == 1  # only legacy-old

        data = json.loads(path.read_text(encoding="utf-8"))
        assert len(data["dismissed"]) == 1
        assert data["dismissed"][0]["item"] == "legacy-fresh"

    def test_housekeeping_includes_digest_actions(self, tmp_path, monkeypatch):
        """run_housekeeping calls _prune_digest_actions."""
        from core.housekeeping import run_housekeeping

        monkeypatch.setattr("core.housekeeping.PULSE_HOME", tmp_path)
        monkeypatch.setattr("core.housekeeping.LOGS_DIR", tmp_path / "logs")
        monkeypatch.setattr("core.housekeeping.DIGESTS_DIR", tmp_path / "digests")
        monkeypatch.setattr("core.housekeeping.INTEL_DIR", tmp_path / "intel")
        monkeypatch.setattr("core.housekeeping.JOBS_DIR", tmp_path / "jobs")

        # Create expired digest action
        actions = {
            "dismissed": [{
                "item": "old-item",
                "status": "dismissed",
                "dismissed_at": (datetime.now() - timedelta(days=5)).isoformat(),
            }],
            "notes": {},
        }
        (tmp_path / ".digest-actions.json").write_text(
            json.dumps(actions), encoding="utf-8"
        )

        result = run_housekeeping()
        assert result["digest_actions"] == 1


# ---------------------------------------------------------------------------
# Integration: Full flow — dismiss + note + resolved → dismissed block + cleanup
# ---------------------------------------------------------------------------


class TestEndToEndStalenessFlow:
    """Integration tests: user actions through to agent prompt and cleanup."""

    def test_dismiss_with_done_note_becomes_resolved_in_prompt(self, tmp_dir):
        """D + note 'done' → resolved → shows in dismissed block forever."""
        from tui.ipc import dismiss_item, add_note
        from sdk.runner import _build_dismissed_block

        actions_file = tmp_dir / ".digest-actions.json"
        with patch("tui.ipc.DIGEST_ACTIONS_FILE", actions_file):
            dismiss_item("reply-raveen", title="Raveen pricing")
            add_note("reply-raveen", "done")

        # Read the file and feed it to the dismissed block builder
        actions = json.loads(actions_file.read_text(encoding="utf-8"))
        with patch("sdk.runner.load_actions", return_value=actions):
            block = _build_dismissed_block()

        assert "reply-raveen" in block
        assert "PERMANENTLY done" in block
        assert '"done"' in block  # note included inline

    def test_snooze_expires_but_resolved_survives(self, tmp_dir):
        """Snoozed items expire after 1 day. Resolved items never expire."""
        from tui.ipc import snooze_item, dismiss_item, add_note

        actions_file = tmp_dir / ".digest-actions.json"
        with patch("tui.ipc.DIGEST_ACTIONS_FILE", actions_file):
            snooze_item("snoozed-item", title="Snooze me")
            dismiss_item("resolved-item", title="Done item")
            add_note("resolved-item", "done")

        # Simulate time passing — manually backdate the snooze
        actions = json.loads(actions_file.read_text(encoding="utf-8"))
        for d in actions["dismissed"]:
            if d["item"] == "snoozed-item":
                d["dismissed_at"] = (datetime.now() - timedelta(days=2)).isoformat()
        actions_file.write_text(json.dumps(actions), encoding="utf-8")

        # Check dismissed block
        from sdk.runner import _build_dismissed_block
        with patch("sdk.runner.load_actions", return_value=actions):
            block = _build_dismissed_block()

        assert "snoozed-item" not in block  # expired
        assert "resolved-item" in block      # permanent

    def test_resolved_survives_housekeeping(self, tmp_path):
        """Housekeeping prunes expired entries but keeps resolved ones."""
        from core.housekeeping import _prune_digest_actions

        now = datetime.now()
        actions = {
            "dismissed": [
                {"item": "expired-snooze", "status": "dismissed",
                 "dismissed_at": (now - timedelta(days=3)).isoformat()},
                {"item": "expired-archive", "status": "archived",
                 "dismissed_at": (now - timedelta(days=45)).isoformat()},
                {"item": "permanent-done", "status": "resolved",
                 "dismissed_at": (now - timedelta(days=200)).isoformat(),
                 "resolved_at": (now - timedelta(days=200)).isoformat()},
            ],
            "notes": {
                "expired-snooze": {"note": "will check later"},
                "permanent-done": {"note": "done"},
            },
        }
        path = tmp_path / ".digest-actions.json"
        path.write_text(json.dumps(actions), encoding="utf-8")

        removed = _prune_digest_actions(path)
        assert removed >= 2  # expired snooze + archive + orphan note

        data = json.loads(path.read_text(encoding="utf-8"))
        assert len(data["dismissed"]) == 1
        assert data["dismissed"][0]["item"] == "permanent-done"
        assert data["dismissed"][0]["status"] == "resolved"
        # Resolved note kept, orphaned note cleaned
        assert "permanent-done" in data["notes"]
        assert "expired-snooze" not in data["notes"]

    def test_carry_forward_with_verification_queries(self):
        """Carry-forward block includes per-item verification queries."""
        from sdk.runner import _build_carry_forward

        today = datetime.now().strftime("%Y-%m-%d")
        prev = {
            "items": [
                {
                    "priority": "high",
                    "title": "Raveen pricing discussion",
                    "id": "reply-raveen-pricing",
                    "source": "Teams: Raveen Bains (1:1 chat)",
                    "type": "reply_needed",
                    "date": today,
                },
                {
                    "priority": "medium",
                    "title": "Send architecture diagram",
                    "id": "action-arch-diagram",
                    "source": "Meeting: Technical review",
                    "type": "action_item",
                    "date": today,
                },
            ]
        }
        result = _build_carry_forward(prev)

        # Both items present
        assert "reply-raveen-pricing" in result
        assert "action-arch-diagram" in result

        # Verification queries present
        assert "**Verify**:" in result
        assert "MANDATORY" in result
        assert "Teams: Raveen Bains" in result
        assert "Send architecture diagram" in result
