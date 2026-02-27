"""Tests for dismiss/archive/restore logic (TUI IPC + runner TTL)."""

import json
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# TUI IPC: dismiss, archive, restore
# ---------------------------------------------------------------------------


def test_dismiss_item_stores_snoozed_status(tmp_dir):
    """TUI dismiss stores status='dismissed' (snooze)."""
    from tui.ipc import dismiss_item, _load_digest_actions, DIGEST_ACTIONS_FILE

    actions_file = tmp_dir / ".digest-actions.json"
    with patch("tui.ipc.DIGEST_ACTIONS_FILE", actions_file):
        dismiss_item("reply-alice", reason="not now", title="Alice budget review", source="teams")
        actions = _load_digest_actions()
    assert len(actions["dismissed"]) == 1
    entry = actions["dismissed"][0]
    assert entry["item"] == "reply-alice"
    assert entry["status"] == "dismissed"
    assert entry["title"] == "Alice budget review"
    assert entry["source"] == "teams"
    assert entry["reason"] == "not now"
    assert "dismissed_at" in entry


def test_dismiss_item_deduplicates(tmp_dir):
    """Dismissing the same item twice doesn't create duplicates."""
    from tui.ipc import dismiss_item, _load_digest_actions

    actions_file = tmp_dir / ".digest-actions.json"
    with patch("tui.ipc.DIGEST_ACTIONS_FILE", actions_file):
        dismiss_item("reply-alice", title="Alice")
        dismiss_item("reply-alice", title="Alice again")
        actions = _load_digest_actions()
    assert len(actions["dismissed"]) == 1


def test_archive_item_changes_status(tmp_dir):
    """Archive changes status from 'dismissed' to 'archived'."""
    from tui.ipc import dismiss_item, archive_item, _load_digest_actions

    actions_file = tmp_dir / ".digest-actions.json"
    with patch("tui.ipc.DIGEST_ACTIONS_FILE", actions_file):
        dismiss_item("reply-alice", title="Alice")
        archive_item("reply-alice")
        actions = _load_digest_actions()
    assert actions["dismissed"][0]["status"] == "archived"
    assert "archived_at" in actions["dismissed"][0]


def test_restore_item_removes_entry(tmp_dir):
    """Restore removes the item from the dismissed list entirely."""
    from tui.ipc import dismiss_item, restore_item, _load_digest_actions

    actions_file = tmp_dir / ".digest-actions.json"
    with patch("tui.ipc.DIGEST_ACTIONS_FILE", actions_file):
        dismiss_item("reply-alice", title="Alice")
        dismiss_item("reply-bob", title="Bob")
        restore_item("reply-alice")
        actions = _load_digest_actions()
    assert len(actions["dismissed"]) == 1
    assert actions["dismissed"][0]["item"] == "reply-bob"


def test_restore_nonexistent_item_is_safe(tmp_dir):
    """Restoring an item that doesn't exist is a no-op."""
    from tui.ipc import restore_item, _load_digest_actions

    actions_file = tmp_dir / ".digest-actions.json"
    with patch("tui.ipc.DIGEST_ACTIONS_FILE", actions_file):
        restore_item("nonexistent")
        actions = _load_digest_actions()
    assert len(actions["dismissed"]) == 0


def test_load_dismissed_items_returns_all(tmp_dir):
    """load_dismissed_items returns both snoozed and archived items."""
    from tui.ipc import dismiss_item, archive_item, load_dismissed_items

    actions_file = tmp_dir / ".digest-actions.json"
    with patch("tui.ipc.DIGEST_ACTIONS_FILE", actions_file):
        dismiss_item("item-a", title="A")
        dismiss_item("item-b", title="B")
        archive_item("item-b")
        items = load_dismissed_items()
    assert len(items) == 2
    statuses = {i["item"]: i["status"] for i in items}
    assert statuses["item-a"] == "dismissed"
    assert statuses["item-b"] == "archived"


def test_backwards_compat_legacy_entries(tmp_dir):
    """Legacy entries without 'status' or 'title' work correctly."""
    from tui.ipc import load_dismissed_items

    actions_file = tmp_dir / ".digest-actions.json"
    actions_file.write_text(json.dumps({
        "dismissed": [
            {"item": "old-item", "dismissed_at": datetime.now().isoformat()},
        ],
        "notes": {},
    }))
    with patch("tui.ipc.DIGEST_ACTIONS_FILE", actions_file):
        items = load_dismissed_items()
    assert len(items) == 1
    # No status field — callers should default to "archived"
    assert items[0].get("status") is None
    assert items[0].get("title") is None
