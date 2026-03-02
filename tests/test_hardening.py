"""Tests for hardening plan fixes — production readiness.

Covers all Day 1–3 fixes from docs/HARDENING-PLAN.md.
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure src/ is on path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ============================================================================
# Fix #1: Scanner exception returns None (not [])
# ============================================================================


@pytest.mark.asyncio
async def test_teams_inbox_exception_returns_none():
    """Teams inbox: browser crash → None (unavailable), not [] (empty)."""
    from collectors.teams_inbox import scan_teams_inbox

    mock_mgr = MagicMock()
    mock_mgr.context = MagicMock()
    mock_mgr.is_alive = True
    mock_mgr.new_page = AsyncMock(side_effect=Exception("crash"))
    with patch("core.browser.get_browser_manager", return_value=mock_mgr):
        result = await scan_teams_inbox({})
    assert result is None


@pytest.mark.asyncio
async def test_outlook_inbox_exception_returns_none():
    """Outlook inbox: browser crash → None (unavailable), not [] (empty)."""
    from collectors.outlook_inbox import scan_outlook_inbox

    mock_mgr = MagicMock()
    mock_mgr.context = MagicMock()
    mock_mgr.is_alive = True
    mock_mgr.new_page = AsyncMock(side_effect=Exception("crash"))
    with patch("core.browser.get_browser_manager", return_value=mock_mgr):
        result = await scan_outlook_inbox({})
    assert result is None


@pytest.mark.asyncio
async def test_calendar_exception_returns_none():
    """Calendar: browser crash → None (unavailable), not [] (empty)."""
    from collectors.calendar import scan_calendar

    mock_mgr = MagicMock()
    mock_mgr.context = MagicMock()
    mock_mgr.is_alive = True
    mock_mgr.new_page = AsyncMock(side_effect=Exception("crash"))
    with patch("core.browser.get_browser_manager", return_value=mock_mgr):
        result = await scan_calendar({})
    assert result is None


# ============================================================================
# Fix #2: Atomic state persistence
# ============================================================================


def test_save_json_state_atomic(tmp_path):
    """save_json_state should write to .tmp then rename (atomic)."""
    from core.state import save_json_state

    target = tmp_path / "state.json"
    data = {"key": "value", "count": 42}
    save_json_state(target, data)

    # File should exist with correct content
    assert target.exists()
    loaded = json.loads(target.read_text(encoding="utf-8"))
    assert loaded == data

    # No stale .tmp file left behind
    tmp_file = target.with_suffix(".json.tmp")
    assert not tmp_file.exists()


def test_save_json_state_tmp_doesnt_corrupt_main(tmp_path):
    """If main file exists, a crash during .tmp write shouldn't corrupt it."""
    from core.state import save_json_state, load_json_state

    target = tmp_path / "state.json"
    # Write initial valid state
    save_json_state(target, {"version": 1})

    # Simulate: main file intact, stale .tmp from interrupted write
    tmp_file = target.with_suffix(".json.tmp")
    tmp_file.write_text("corrupt!", encoding="utf-8")

    # Main file should still be loadable
    loaded = load_json_state(target, {"default": True})
    assert loaded == {"version": 1}


def test_save_json_state_creates_parents(tmp_path):
    """Should create parent directories."""
    from core.state import save_json_state

    target = tmp_path / "deep" / "nested" / "state.json"
    save_json_state(target, {"ok": True})
    assert target.exists()


# ============================================================================
# Fix #3: Chat name exact match for reply_to_chat
# ============================================================================


def test_find_chat_sidebar_exact_match():
    """FIND_CHAT_IN_SIDEBAR_JS should use word-boundary matching, not substring."""
    from collectors.teams_sender import FIND_CHAT_IN_SIDEBAR_JS

    js = FIND_CHAT_IN_SIDEBAR_JS
    # Should NOT contain text.includes(lower) — that's the old substring match
    assert "text.includes(lower)" not in js
    # Should use first-line matching
    assert "firstLine" in js or "first_line" in js or "split" in js


# ============================================================================
# Fix #4: Action file UUID to prevent collisions
# ============================================================================


@pytest.mark.asyncio
async def test_queue_task_uuid_suffix(tmp_path):
    """queue_task tool should produce unique filenames with UUID suffix."""
    from sdk.tools import queue_task

    with patch("sdk.tools.JOBS_DIR", tmp_path):
        await queue_task.handler({"arguments": {
            "type": "research", "task": "test task", "description": "A test"
        }})
        await queue_task.handler({"arguments": {
            "type": "research", "task": "test task", "description": "Another test"
        }})

    pending = list((tmp_path / "pending").glob("*.yaml"))
    assert len(pending) == 2
    # Filenames should be different (UUID suffix)
    assert pending[0].name != pending[1].name


@pytest.mark.asyncio
async def test_send_teams_message_uuid_suffix(tmp_path):
    """send_teams_message tool filenames should have UUID suffix."""
    from sdk.tools import send_teams_message

    with patch("sdk.tools.PENDING_ACTIONS_DIR", tmp_path):
        await send_teams_message.handler({"arguments": {
            "recipient": "Alice", "message": "Hi",
        }})
        await send_teams_message.handler({"arguments": {
            "recipient": "Alice", "message": "Hi 2",
        }})

    files = list(tmp_path.glob("teams-send-*.json"))
    assert len(files) == 2
    assert files[0].name != files[1].name


@pytest.mark.asyncio
async def test_send_email_reply_uuid_suffix(tmp_path):
    """send_email_reply tool filenames should have UUID suffix."""
    from sdk.tools import send_email_reply

    with patch("sdk.tools.PENDING_ACTIONS_DIR", tmp_path):
        await send_email_reply.handler({"arguments": {
            "search_query": "Bob", "message": "Reply 1",
        }})
        await send_email_reply.handler({"arguments": {
            "search_query": "Bob", "message": "Reply 2",
        }})

    files = list(tmp_path.glob("email-reply-*.json"))
    assert len(files) == 2
    assert files[0].name != files[1].name


def test_ipc_queue_job_uuid_suffix(tmp_path):
    """TUI queue_job filenames should have UUID suffix."""
    with patch("tui.ipc.JOBS_DIR", tmp_path):
        from tui.ipc import queue_job

        queue_job("digest")
        queue_job("digest")

    pending = list((tmp_path / "pending").glob("*.yaml"))
    assert len(pending) == 2
    assert pending[0].name != pending[1].name


# ============================================================================
# Fix #5: parse_front_matter crash protection
# ============================================================================


def test_parse_front_matter_malformed(tmp_path):
    """Malformed front matter (no closing ---) should not crash."""
    from sdk.agents import parse_front_matter

    # Opening --- but no closing ---
    bad_file = tmp_path / "bad.md"
    bad_file.write_text("---\nname: test\nThis is body text\n", encoding="utf-8")

    meta, body = parse_front_matter(bad_file)
    # Should return empty metadata and full text as body
    assert meta == {}
    assert "name: test" in body


def test_parse_front_matter_valid(tmp_path):
    """Well-formed front matter should parse correctly."""
    from sdk.agents import parse_front_matter

    good_file = tmp_path / "good.md"
    good_file.write_text("---\nname: test\n---\nBody text\n", encoding="utf-8")

    meta, body = parse_front_matter(good_file)
    assert meta["name"] == "test"
    assert body == "Body text"


def test_parse_front_matter_no_delimiter(tmp_path):
    """File without --- should return empty metadata."""
    from sdk.agents import parse_front_matter

    plain = tmp_path / "plain.md"
    plain.write_text("Just plain markdown\n", encoding="utf-8")

    meta, body = parse_front_matter(plain)
    assert meta == {}
    assert "plain markdown" in body


# ============================================================================
# Fix #6: Send confirmation in Teams sender
# ============================================================================


@pytest.mark.asyncio
async def test_teams_send_verification_compose_not_empty():
    """If compose box still has content after send, report failure."""
    from collectors.teams_sender import _type_and_send

    page = MagicMock()
    page.evaluate = AsyncMock(side_effect=[
        "ckeditor",  # FIND_COMPOSE_BOX_JS
        True,        # FOCUS_COMPOSE_BOX_JS
        False,       # Send verification — compose NOT empty
    ])
    page.keyboard.press = AsyncMock()
    page.keyboard.insert_text = AsyncMock()
    page.wait_for_timeout = AsyncMock()

    result = await _type_and_send(page, "Hello", "Alice")
    assert result["success"] is False
    assert "compose box still has content" in result["detail"]


# ============================================================================
# Fix #7: Outlook reply preserves quoted content
# ============================================================================


def test_outlook_reply_no_ctrl_a_backspace():
    """Outlook reply should NOT use Ctrl+A + Backspace (destroys quoted thread)."""
    from collectors.outlook_sender import _do_reply

    import inspect
    source = inspect.getsource(_do_reply)
    # The old code had Ctrl+A + Backspace to clear — should be replaced
    # with Ctrl+Home to position cursor at start
    assert "Control+Home" in source


# ============================================================================
# Fix #8: QuestionModal — delete pending file after showing
# ============================================================================


def test_check_pending_question_deletes_file():
    """_check_pending_question should delete .pending-question.json before pushing modal."""
    import inspect
    from tui.app import PulseApp

    source = inspect.getsource(PulseApp._check_pending_question)
    # Should unlink the file before pushing the modal
    assert "unlink" in source
    assert "PENDING_QUESTION_FILE" in source


# ============================================================================
# Fix #9: browser.close() must be awaited
# ============================================================================


def test_browser_close_is_awaited():
    """BrowserManager.stop() should await browser.close(), not call it synchronously."""
    import inspect
    from core.browser import BrowserManager

    source = inspect.getsource(BrowserManager.stop)
    # The line should be "await self._browser.close()" not "self._browser.close()"
    assert "await self._browser.close()" in source


# ============================================================================
# Fix #10: Auth-redirect detection in all scanners
# ============================================================================


@pytest.mark.asyncio
async def test_teams_inbox_auth_redirect_returns_none():
    """Teams inbox: login redirect during scan → return None."""
    from collectors.teams_inbox import _do_scan

    page = MagicMock()
    page.url = "https://login.microsoftonline.com/oauth2/authorize"
    page.goto = AsyncMock()
    page.wait_for_load_state = AsyncMock()

    result = await _do_scan(page)
    assert result is None


@pytest.mark.asyncio
async def test_outlook_inbox_auth_redirect_returns_none():
    """Outlook inbox: login redirect during scan → return None."""
    from collectors.outlook_inbox import _do_scan

    page = MagicMock()
    page.url = "https://login.microsoftonline.com/oauth2/authorize"
    page.goto = AsyncMock()
    page.wait_for_load_state = AsyncMock()

    result = await _do_scan(page)
    assert result is None


@pytest.mark.asyncio
async def test_calendar_auth_redirect_returns_none():
    """Calendar: login redirect during scan → return None."""
    from collectors.calendar import _do_scan

    page = MagicMock()
    page.url = "https://login.microsoftonline.com/oauth2/authorize"
    page.goto = AsyncMock()
    page.wait_for_load_state = AsyncMock()

    result = await _do_scan(page)
    assert result is None


# ============================================================================
# Fix #11: Replace WMIC with PowerShell
# ============================================================================


def test_kill_orphan_edge_uses_powershell():
    """_kill_orphan_edge should use PowerShell, not WMIC (deprecated on Win 11)."""
    import inspect
    from core.browser import _kill_orphan_edge

    source = inspect.getsource(_kill_orphan_edge)
    assert '"powershell"' in source
    # Should NOT have ["wmic" as a command — only in docstring is ok
    assert '["wmic"' not in source


def test_find_cdp_port_uses_powershell():
    """_find_cdp_port should use PowerShell, not WMIC."""
    import inspect
    from collectors.transcripts.collector import _find_cdp_port

    source = inspect.getsource(_find_cdp_port)
    assert '"powershell"' in source
    assert '["wmic"' not in source


# ============================================================================
# Fix #12: Browser crash detection
# ============================================================================


def test_browser_manager_is_alive_no_context():
    """is_alive should return False when context is None."""
    from core.browser import BrowserManager

    mgr = BrowserManager()
    assert mgr.is_alive is False


def test_browser_manager_is_alive_healthy():
    """is_alive should return True when context.pages works."""
    from core.browser import BrowserManager

    mgr = BrowserManager()
    mgr._context = MagicMock()
    mgr._context.pages = [MagicMock()]  # healthy
    assert mgr.is_alive is True


def test_browser_manager_is_alive_crashed():
    """is_alive should return False when context.pages raises."""
    from core.browser import BrowserManager

    mgr = BrowserManager()
    ctx = MagicMock()
    type(ctx).pages = property(lambda self: (_ for _ in ()).throw(Exception("dead")))
    mgr._context = ctx
    assert mgr.is_alive is False


@pytest.mark.asyncio
async def test_scanner_skips_dead_browser():
    """Scanners should skip when browser is_alive is False."""
    from collectors.teams_inbox import scan_teams_inbox

    mock_mgr = MagicMock()
    mock_mgr.context = MagicMock()
    mock_mgr.is_alive = False  # browser crashed
    with patch("core.browser.get_browser_manager", return_value=mock_mgr):
        result = await scan_teams_inbox({})
    assert result is None


# ============================================================================
# Fix #13: Logging in except blocks
# ============================================================================


def test_ipc_has_logging():
    """ipc.py should have a logger set up."""
    from tui import ipc
    assert hasattr(ipc, "log")


def test_app_has_logging():
    """app.py should have a logger set up."""
    import tui.app
    assert hasattr(tui.app, "log")


# ============================================================================
# Fix #14: Atomic write for digest actions
# ============================================================================


def test_save_digest_actions_atomic(tmp_path):
    """_save_digest_actions should use atomic write (tmp + rename)."""
    import inspect
    from tui.ipc import _save_digest_actions

    source = inspect.getsource(_save_digest_actions)
    assert "os.replace" in source or "rename" in source


# ============================================================================
# Fix #15: Agent prompt template variables
# ============================================================================


def test_knowledge_miner_no_template_variables():
    """knowledge-miner.md should have no uninterpolated {{variables}}."""
    from core.constants import CONFIG_DIR

    path = CONFIG_DIR / "prompts" / "agents" / "knowledge-miner.md"
    text = path.read_text(encoding="utf-8")
    assert "{{" not in text, f"Found uninterpolated template variable in knowledge-miner.md"


def test_all_agent_prompts_no_template_variables():
    """All agent prompts should have no uninterpolated {{variables}}."""
    from core.constants import CONFIG_DIR

    agents_dir = CONFIG_DIR / "prompts" / "agents"
    for path in agents_dir.glob("*.md"):
        text = path.read_text(encoding="utf-8")
        assert "{{" not in text, f"Found uninterpolated template variable in {path.name}"
