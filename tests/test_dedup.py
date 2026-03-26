"""Tests for duplicate message/email prevention — 3-layer dedup defense.

Layer 1: Tool return message ("Do NOT call this tool again...")
Layer 2: Tool-level dedup (tools.py — scan pending files before creating new ones)
Layer 3: Batch-level dedup (worker.py — track seen tuples in process_pending_actions)
Plus: CKEditor draft contamination fix (teams_sender.py — clear + verify)
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ============================================================================
# Layer 2: Tool-level dedup (sdk/tools.py)
# ============================================================================


class TestToolLevelDedupTeams:
    """send_teams_message rejects duplicate sends when identical action is pending."""

    @pytest.mark.asyncio
    async def test_rejects_identical_message(self, tmp_path):
        from sdk.tools import send_teams_message

        with patch("sdk.tools.PENDING_ACTIONS_DIR", tmp_path):
            # First call succeeds
            result1 = await send_teams_message.handler({"arguments": {
                "recipient": "Alice", "message": "Hello there",
            }})
            assert "queued" in result1["textResultForLlm"].lower()

            # Second identical call is rejected
            result2 = await send_teams_message.handler({"arguments": {
                "recipient": "Alice", "message": "Hello there",
            }})
            assert "already queued" in result2["textResultForLlm"]
            assert len(list(tmp_path.glob("teams-send-*.json"))) == 1

    @pytest.mark.asyncio
    async def test_dedup_case_insensitive_target(self, tmp_path):
        from sdk.tools import send_teams_message

        with patch("sdk.tools.PENDING_ACTIONS_DIR", tmp_path):
            await send_teams_message.handler({"arguments": {
                "recipient": "Alice Smith", "message": "Hi",
            }})
            result = await send_teams_message.handler({"arguments": {
                "recipient": "alice smith", "message": "Hi",
            }})
            assert "already queued" in result["textResultForLlm"]
            assert len(list(tmp_path.glob("teams-send-*.json"))) == 1

    @pytest.mark.asyncio
    async def test_allows_different_message(self, tmp_path):
        from sdk.tools import send_teams_message

        with patch("sdk.tools.PENDING_ACTIONS_DIR", tmp_path):
            await send_teams_message.handler({"arguments": {
                "recipient": "Alice", "message": "Hello",
            }})
            result = await send_teams_message.handler({"arguments": {
                "recipient": "Alice", "message": "Goodbye",
            }})
            assert "queued" in result["textResultForLlm"].lower()
            assert "already" not in result["textResultForLlm"]
            assert len(list(tmp_path.glob("teams-send-*.json"))) == 2

    @pytest.mark.asyncio
    async def test_allows_different_target(self, tmp_path):
        from sdk.tools import send_teams_message

        with patch("sdk.tools.PENDING_ACTIONS_DIR", tmp_path):
            await send_teams_message.handler({"arguments": {
                "recipient": "Alice", "message": "Hi",
            }})
            result = await send_teams_message.handler({"arguments": {
                "recipient": "Bob", "message": "Hi",
            }})
            assert "queued" in result["textResultForLlm"].lower()
            assert "already" not in result["textResultForLlm"]
            assert len(list(tmp_path.glob("teams-send-*.json"))) == 2

    @pytest.mark.asyncio
    async def test_dedup_uses_chat_name_field(self, tmp_path):
        """chat_name is the primary target field for Teams messages."""
        from sdk.tools import send_teams_message

        with patch("sdk.tools.PENDING_ACTIONS_DIR", tmp_path):
            await send_teams_message.handler({"arguments": {
                "recipient": "x", "chat_name": "Team Chat", "message": "Update",
            }})
            result = await send_teams_message.handler({"arguments": {
                "recipient": "x", "chat_name": "Team Chat", "message": "Update",
            }})
            assert "already queued" in result["textResultForLlm"]

    @pytest.mark.asyncio
    async def test_dedup_strips_whitespace(self, tmp_path):
        """Trailing whitespace in message should not bypass dedup."""
        from sdk.tools import send_teams_message

        with patch("sdk.tools.PENDING_ACTIONS_DIR", tmp_path):
            await send_teams_message.handler({"arguments": {
                "recipient": "Alice", "message": "Hello",
            }})
            result = await send_teams_message.handler({"arguments": {
                "recipient": "Alice", "message": "Hello  ",
            }})
            assert "already queued" in result["textResultForLlm"]

    @pytest.mark.asyncio
    async def test_dedup_survives_corrupt_file(self, tmp_path):
        """Corrupt JSON in pending dir should not crash — dedup is best-effort."""
        from sdk.tools import send_teams_message

        # Write a corrupt file
        (tmp_path / "teams-send-000000-corrupt.json").write_text("not json{{{")

        with patch("sdk.tools.PENDING_ACTIONS_DIR", tmp_path):
            result = await send_teams_message.handler({"arguments": {
                "recipient": "Alice", "message": "Hi",
            }})
            # Should still succeed (dedup fails gracefully)
            assert "queued" in result["textResultForLlm"].lower()

    @pytest.mark.asyncio
    async def test_return_message_warns_against_retry(self, tmp_path):
        """Return message should explicitly tell LLM not to call again."""
        from sdk.tools import send_teams_message

        with patch("sdk.tools.PENDING_ACTIONS_DIR", tmp_path):
            result = await send_teams_message.handler({"arguments": {
                "recipient": "Alice", "message": "Hi",
            }})
            assert "Do NOT call this tool again" in result["textResultForLlm"]


class TestToolLevelDedupEmail:
    """send_email_reply rejects duplicate sends when identical action is pending."""

    @pytest.mark.asyncio
    async def test_rejects_identical_reply(self, tmp_path):
        from sdk.tools import send_email_reply

        with patch("sdk.tools.PENDING_ACTIONS_DIR", tmp_path):
            result1 = await send_email_reply.handler({"arguments": {
                "search_query": "RE: Budget", "message": "Approved",
            }})
            assert "queued" in result1["textResultForLlm"].lower()

            result2 = await send_email_reply.handler({"arguments": {
                "search_query": "RE: Budget", "message": "Approved",
            }})
            assert "already queued" in result2["textResultForLlm"]
            assert len(list(tmp_path.glob("email-reply-*.json"))) == 1

    @pytest.mark.asyncio
    async def test_dedup_case_insensitive_query(self, tmp_path):
        from sdk.tools import send_email_reply

        with patch("sdk.tools.PENDING_ACTIONS_DIR", tmp_path):
            await send_email_reply.handler({"arguments": {
                "search_query": "RE: Budget Proposal", "message": "Yes",
            }})
            result = await send_email_reply.handler({"arguments": {
                "search_query": "re: budget proposal", "message": "Yes",
            }})
            assert "already queued" in result["textResultForLlm"]

    @pytest.mark.asyncio
    async def test_allows_different_message(self, tmp_path):
        from sdk.tools import send_email_reply

        with patch("sdk.tools.PENDING_ACTIONS_DIR", tmp_path):
            await send_email_reply.handler({"arguments": {
                "search_query": "RE: Budget", "message": "Approved",
            }})
            result = await send_email_reply.handler({"arguments": {
                "search_query": "RE: Budget", "message": "Rejected",
            }})
            assert "already" not in result["textResultForLlm"]
            assert len(list(tmp_path.glob("email-reply-*.json"))) == 2

    @pytest.mark.asyncio
    async def test_allows_different_query(self, tmp_path):
        from sdk.tools import send_email_reply

        with patch("sdk.tools.PENDING_ACTIONS_DIR", tmp_path):
            await send_email_reply.handler({"arguments": {
                "search_query": "RE: Budget", "message": "OK",
            }})
            result = await send_email_reply.handler({"arguments": {
                "search_query": "RE: Schedule", "message": "OK",
            }})
            assert "already" not in result["textResultForLlm"]

    @pytest.mark.asyncio
    async def test_return_message_warns_against_retry(self, tmp_path):
        from sdk.tools import send_email_reply

        with patch("sdk.tools.PENDING_ACTIONS_DIR", tmp_path):
            result = await send_email_reply.handler({"arguments": {
                "search_query": "RE: Test", "message": "OK",
            }})
            assert "Do NOT call this tool again" in result["textResultForLlm"]


# ============================================================================
# Layer 3: Batch-level dedup (daemon/worker.py — process_pending_actions)
# ============================================================================


class TestBatchLevelDedup:
    """process_pending_actions skips duplicate sends within a single batch."""

    @pytest.mark.asyncio
    async def test_dedup_teams_sends_in_batch(self, tmp_path):
        from daemon.worker import process_pending_actions

        actions_dir = tmp_path / ".pending-actions"
        actions_dir.mkdir()

        # Create two identical teams_send actions (using recipient field)
        for i in range(2):
            (actions_dir / f"teams-send-{i:06d}-abc{i}.json").write_text(json.dumps({
                "type": "teams_send",
                "recipient": "Alice Smith",
                "message": "Hello there",
            }))

        with patch("sdk.tools.PENDING_ACTIONS_DIR", actions_dir), \
             patch("collectors.teams_sender.send_teams_message", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = {"success": True, "detail": "Sent"}
            await process_pending_actions()

        # Only called once despite two files
        mock_send.assert_called_once_with("Alice Smith", "Hello there")

    @pytest.mark.asyncio
    async def test_dedup_email_replies_in_batch(self, tmp_path):
        from daemon.worker import process_pending_actions

        actions_dir = tmp_path / ".pending-actions"
        actions_dir.mkdir()

        for i in range(2):
            (actions_dir / f"email-reply-{i:06d}-abc{i}.json").write_text(json.dumps({
                "type": "email_reply",
                "search_query": "RE: Budget proposal",
                "message": "Approved, thanks.",
            }))

        with patch("sdk.tools.PENDING_ACTIONS_DIR", actions_dir), \
             patch("collectors.outlook_sender.reply_to_email", new_callable=AsyncMock) as mock_reply:
            mock_reply.return_value = {"success": True, "detail": "Replied"}
            await process_pending_actions()

        mock_reply.assert_called_once_with("RE: Budget proposal", "Approved, thanks.")

    @pytest.mark.asyncio
    async def test_dedup_case_insensitive_target(self, tmp_path):
        from daemon.worker import process_pending_actions

        actions_dir = tmp_path / ".pending-actions"
        actions_dir.mkdir()

        (actions_dir / "teams-send-000001-aaa.json").write_text(json.dumps({
            "type": "teams_send",
            "recipient": "Alice Smith",
            "message": "Hello",
        }))
        (actions_dir / "teams-send-000002-bbb.json").write_text(json.dumps({
            "type": "teams_send",
            "recipient": "alice smith",
            "message": "Hello",
        }))

        with patch("sdk.tools.PENDING_ACTIONS_DIR", actions_dir), \
             patch("collectors.teams_sender.send_teams_message", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = {"success": True, "detail": "Sent"}
            await process_pending_actions()

        mock_send.assert_called_once()

    @pytest.mark.asyncio
    async def test_allows_different_messages_in_batch(self, tmp_path):
        from daemon.worker import process_pending_actions

        actions_dir = tmp_path / ".pending-actions"
        actions_dir.mkdir()

        (actions_dir / "teams-send-000001-aaa.json").write_text(json.dumps({
            "type": "teams_send",
            "recipient": "Alice",
            "message": "Hello",
        }))
        (actions_dir / "teams-send-000002-bbb.json").write_text(json.dumps({
            "type": "teams_send",
            "recipient": "Alice",
            "message": "Follow up",
        }))

        with patch("sdk.tools.PENDING_ACTIONS_DIR", actions_dir), \
             patch("collectors.teams_sender.send_teams_message", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = {"success": True, "detail": "Sent"}
            await process_pending_actions()

        assert mock_send.call_count == 2

    @pytest.mark.asyncio
    async def test_dedup_uses_recipient_fallback(self, tmp_path):
        """When chat_name is missing, dedup should use recipient field."""
        from daemon.worker import process_pending_actions

        actions_dir = tmp_path / ".pending-actions"
        actions_dir.mkdir()

        for i in range(2):
            (actions_dir / f"teams-send-{i:06d}-x{i}.json").write_text(json.dumps({
                "type": "teams_send",
                "recipient": "Bob Jones",
                "message": "Meeting at 3?",
            }))

        with patch("sdk.tools.PENDING_ACTIONS_DIR", actions_dir), \
             patch("collectors.teams_sender.send_teams_message", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = {"success": True, "detail": "Sent"}
            await process_pending_actions()

        mock_send.assert_called_once()

    @pytest.mark.asyncio
    async def test_mixed_types_not_deduped(self, tmp_path):
        """Different action types with same target/message should both execute."""
        from daemon.worker import process_pending_actions

        actions_dir = tmp_path / ".pending-actions"
        actions_dir.mkdir()

        (actions_dir / "teams-send-000001-aaa.json").write_text(json.dumps({
            "type": "teams_send",
            "recipient": "Alice",
            "message": "Hello",
        }))
        (actions_dir / "email-reply-000001-bbb.json").write_text(json.dumps({
            "type": "email_reply",
            "search_query": "Alice",
            "message": "Hello",
        }))

        with patch("sdk.tools.PENDING_ACTIONS_DIR", actions_dir), \
             patch("collectors.teams_sender.send_teams_message", new_callable=AsyncMock) as mock_teams, \
             patch("collectors.outlook_sender.reply_to_email", new_callable=AsyncMock) as mock_email:
            mock_teams.return_value = {"success": True, "detail": "Sent"}
            mock_email.return_value = {"success": True, "detail": "Replied"}
            await process_pending_actions()

        mock_teams.assert_called_once()
        mock_email.assert_called_once()


# ============================================================================
# CKEditor draft contamination fix (collectors/teams_sender.py)
# ============================================================================


def _make_page():
    """Create a mock page for _type_and_send tests."""
    page = MagicMock()
    page.url = "https://teams.cloud.microsoft/"
    page.evaluate = AsyncMock()
    page.keyboard = MagicMock()
    page.keyboard.press = AsyncMock()
    page.keyboard.insert_text = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    return page


class TestCKEditorClearAndVerify:
    """_type_and_send clears CKEditor drafts and verifies content before send."""

    @pytest.mark.asyncio
    async def test_js_clear_success_no_keyboard_fallback(self):
        """When JS clear succeeds, keyboard Ctrl+A/Backspace should NOT be called."""
        from collectors.teams_sender import _type_and_send

        page = _make_page()
        page.evaluate = AsyncMock(side_effect=[
            "ckeditor",      # FIND_COMPOSE_BOX_JS
            True,            # FOCUS_COMPOSE_BOX_JS
            True,            # JS clear compose box (success)
            "Hello",         # Content verification (matches)
            True,            # Send verification (compose empty)
        ])

        result = await _type_and_send(page, "Hello", "Alice")
        assert result["success"] is True

        # Keyboard clearing should NOT have been used
        press_calls = [c.args[0] for c in page.keyboard.press.call_args_list]
        assert "Control+a" not in press_calls
        assert "Backspace" not in press_calls

    @pytest.mark.asyncio
    async def test_js_clear_fails_keyboard_fallback(self):
        """When JS clear fails, should fall back to Ctrl+A/Backspace."""
        from collectors.teams_sender import _type_and_send

        page = _make_page()
        page.evaluate = AsyncMock(side_effect=[
            "ckeditor",      # FIND_COMPOSE_BOX_JS
            True,            # FOCUS_COMPOSE_BOX_JS
            False,           # JS clear compose box (FAILS)
            "Hello",         # Content verification (matches)
            True,            # Send verification (compose empty)
        ])

        result = await _type_and_send(page, "Hello", "Alice")
        assert result["success"] is True

        # Keyboard clearing SHOULD have been used as fallback
        press_calls = [c.args[0] for c in page.keyboard.press.call_args_list]
        assert "Control+a" in press_calls
        assert "Backspace" in press_calls

    @pytest.mark.asyncio
    async def test_content_mismatch_triggers_retry(self):
        """If compose box content doesn't match message, clear and retry once."""
        from collectors.teams_sender import _type_and_send

        page = _make_page()
        page.evaluate = AsyncMock(side_effect=[
            "ckeditor",             # FIND_COMPOSE_BOX_JS
            True,                   # FOCUS_COMPOSE_BOX_JS
            True,                   # JS clear compose box
            "OLD DRAFT Hello",      # Content verification (MISMATCH!)
            None,                   # Retry clear (void return)
            True,                   # Send verification (compose empty)
        ])

        result = await _type_and_send(page, "Hello", "Alice")
        assert result["success"] is True
        # insert_text called twice: initial + retry
        assert page.keyboard.insert_text.call_count == 2

    @pytest.mark.asyncio
    async def test_content_matches_no_retry(self):
        """If compose box matches message exactly, no retry needed."""
        from collectors.teams_sender import _type_and_send

        page = _make_page()
        page.evaluate = AsyncMock(side_effect=[
            "ckeditor",      # FIND_COMPOSE_BOX_JS
            True,            # FOCUS_COMPOSE_BOX_JS
            True,            # JS clear compose box
            "Hello",         # Content verification (MATCHES)
            True,            # Send verification (compose empty)
        ])

        result = await _type_and_send(page, "Hello", "Alice")
        assert result["success"] is True
        # insert_text called only once — no retry
        page.keyboard.insert_text.assert_called_once_with("Hello")

    @pytest.mark.asyncio
    async def test_content_verification_null_skips_check(self):
        """If content verification returns null (no compose box), proceed without retry."""
        from collectors.teams_sender import _type_and_send

        page = _make_page()
        page.evaluate = AsyncMock(side_effect=[
            "ckeditor",      # FIND_COMPOSE_BOX_JS
            True,            # FOCUS_COMPOSE_BOX_JS
            True,            # JS clear compose box
            None,            # Content verification returns null
            True,            # Send verification (compose empty)
        ])

        result = await _type_and_send(page, "Hello", "Alice")
        assert result["success"] is True
        page.keyboard.insert_text.assert_called_once_with("Hello")

    @pytest.mark.asyncio
    async def test_content_verification_empty_string_skips_check(self):
        """Empty string from verification means compose box cleared (falsy) — no retry."""
        from collectors.teams_sender import _type_and_send

        page = _make_page()
        page.evaluate = AsyncMock(side_effect=[
            "ckeditor",      # FIND_COMPOSE_BOX_JS
            True,            # FOCUS_COMPOSE_BOX_JS
            True,            # JS clear compose box
            "",              # Content verification returns empty (falsy)
            True,            # Send verification
        ])

        result = await _type_and_send(page, "Hello", "Alice")
        assert result["success"] is True
        page.keyboard.insert_text.assert_called_once_with("Hello")
