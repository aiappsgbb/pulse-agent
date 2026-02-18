"""Tests for tg/ modules — bot utilities, confirmations."""

import asyncio
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tg.bot import md_to_telegram_html, _match_quick_action
from tg.confirmations import has_pending_confirmation, resolve_confirmation, wait_for_confirmation


# --- md_to_telegram_html ---

def test_bold():
    assert "<b>bold</b>" in md_to_telegram_html("**bold**")


def test_italic():
    assert "<i>italic</i>" in md_to_telegram_html("*italic*")


def test_inline_code():
    assert "<code>code</code>" in md_to_telegram_html("`code`")


def test_code_block():
    result = md_to_telegram_html("```python\nprint('hi')\n```")
    assert "<pre>" in result


def test_header():
    result = md_to_telegram_html("## My Header")
    assert "<b>My Header</b>" in result


def test_html_escaping():
    result = md_to_telegram_html("<script>alert('xss')</script>")
    assert "<script>" not in result
    assert "&lt;script&gt;" in result


def test_mixed():
    result = md_to_telegram_html("**bold** and *italic* and `code`")
    assert "<b>bold</b>" in result
    assert "<i>italic</i>" in result
    assert "<code>code</code>" in result


# --- _match_quick_action ---

def test_quick_action_digest():
    assert _match_quick_action("run digest") == "digest"
    assert _match_quick_action("digest") == "digest"
    assert _match_quick_action("morning digest") == "digest"


def test_quick_action_triage():
    assert _match_quick_action("triage") == "monitor"
    assert _match_quick_action("run triage") == "monitor"


def test_quick_action_intel():
    assert _match_quick_action("intel") == "intel"
    assert _match_quick_action("intel brief") == "intel"


def test_quick_action_transcripts():
    assert _match_quick_action("transcripts") == "transcripts"
    assert _match_quick_action("grab transcripts") == "transcripts"


def test_quick_action_no_match():
    assert _match_quick_action("hello world") is None
    assert _match_quick_action("analyze something") is None


def test_quick_action_case_insensitive():
    assert _match_quick_action("Run Digest") == "digest"
    assert _match_quick_action("TRIAGE") == "monitor"


# --- confirmations ---

def test_has_pending_empty():
    assert has_pending_confirmation({}, 12345) is False


def test_has_pending_with_entry():
    pending = {12345: asyncio.Future()}
    assert has_pending_confirmation(pending, 12345) is True
    assert has_pending_confirmation(pending, 99999) is False


def test_resolve_confirmation():
    loop = asyncio.new_event_loop()
    fut = loop.create_future()
    pending = {12345: fut}
    resolve_confirmation(pending, 12345, "yes")
    assert 12345 not in pending


@pytest.mark.asyncio
async def test_wait_for_confirmation_resolved():
    pending = {}

    async def resolve_after_delay():
        await asyncio.sleep(0.05)
        from tg.confirmations import resolve_confirmation as _resolve
        _resolve(pending, 12345, "approved")

    asyncio.create_task(resolve_after_delay())
    result = await wait_for_confirmation(pending, 12345, timeout=2)
    assert result == "approved"


@pytest.mark.asyncio
async def test_wait_for_confirmation_timeout():
    pending = {}
    with pytest.raises(asyncio.TimeoutError):
        await wait_for_confirmation(pending, 12345, timeout=0.05)
