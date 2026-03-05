"""Tests for sdk/event_handler.py — dispatch table, delta callback, completion tracking."""

from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sdk.event_handler import EventHandler


class _MockData:
    """Minimal mock for event data objects."""
    def __init__(self, **kwargs):
        self._attrs = kwargs

    def __getattr__(self, name):
        return self._attrs.get(name)


class _MockEvent:
    """Minimal mock for SDK events."""
    def __init__(self, **data_kwargs):
        self.data = _MockData(**data_kwargs) if data_kwargs else None


# --- delta handling ---

def test_delta_callback():
    """on_delta callback receives text chunks."""
    chunks = []
    handler = EventHandler(on_delta=lambda c: chunks.append(c))
    event = _MockEvent(delta_content="Hello ")
    handler._handle_delta(event)
    assert chunks == ["Hello "]


def test_delta_multiple_chunks():
    """Multiple delta events accumulate."""
    chunks = []
    handler = EventHandler(on_delta=lambda c: chunks.append(c))
    handler._handle_delta(_MockEvent(delta_content="Hello "))
    handler._handle_delta(_MockEvent(delta_content="world"))
    assert chunks == ["Hello ", "world"]


def test_delta_no_callback(capsys):
    """Delta without callback still prints to terminal."""
    handler = EventHandler()
    event = _MockEvent(delta_content="Hello")
    handler._handle_delta(event)
    captured = capsys.readouterr()
    assert "Hello" in captured.out


def test_delta_empty_content():
    """Empty delta content is ignored."""
    chunks = []
    handler = EventHandler(on_delta=lambda c: chunks.append(c))
    handler._handle_delta(_MockEvent(delta_content=""))
    handler._handle_delta(_MockEvent(delta_content=None))
    assert chunks == []


# --- tool events ---

def test_tool_start(capsys):
    handler = EventHandler()
    event = _MockEvent(tool_name="write_output", mcp_server_name=None, arguments='{"path": "test.md"}')
    handler._handle_tool_start(event)
    captured = capsys.readouterr()
    assert "[TOOL] write_output" in captured.out


def test_tool_start_mcp(capsys):
    handler = EventHandler()
    event = _MockEvent(tool_name="ask_work_iq", mcp_server_name="workiq", arguments=None)
    handler._handle_tool_start(event)
    captured = capsys.readouterr()
    assert "[TOOL] ask_work_iq (workiq)" in captured.out


def test_tool_complete(capsys):
    handler = EventHandler()
    event = _MockEvent(result="File written successfully")
    handler._handle_tool_complete(event)
    captured = capsys.readouterr()
    assert "[RESULT] File written successfully" in captured.out


def test_tool_complete_no_result(capsys):
    handler = EventHandler()
    event = _MockEvent(result=None)
    handler._handle_tool_complete(event)
    captured = capsys.readouterr()
    assert captured.out == ""


# --- message handler ---

def test_message_prints_newline(capsys):
    handler = EventHandler()
    handler._handle_message(_MockEvent())
    captured = capsys.readouterr()
    assert captured.out == "\n"


def test_message_captures_final_text():
    """ASSISTANT_MESSAGE captures content into final_text."""
    handler = EventHandler()
    event = _MockEvent(content="Here is the digest summary.")
    handler._handle_message(event)
    assert handler.final_text == "Here is the digest summary."


def test_message_no_content_leaves_final_text_none():
    """ASSISTANT_MESSAGE with no content doesn't overwrite final_text."""
    handler = EventHandler()
    handler._handle_message(_MockEvent())
    assert handler.final_text is None


# --- completion tracking (done, final_text, error) ---

def test_initial_state():
    """Handler starts with done=unset, final_text=None, error=None."""
    handler = EventHandler()
    assert not handler.done.is_set()
    assert handler.final_text is None
    assert handler.error is None


def test_idle_sets_done():
    """SESSION_IDLE sets the done event."""
    handler = EventHandler()
    handler._handle_idle(_MockEvent())
    assert handler.done.is_set()
    assert handler.error is None


def test_error_sets_done_and_error():
    """SESSION_ERROR sets both error and done."""
    handler = EventHandler()

    class _ErrorEvent:
        data = "Something went wrong"

    handler._handle_error(_ErrorEvent())
    assert handler.done.is_set()
    assert handler.error is not None
    assert "Something went wrong" in handler.error


def test_error_without_data():
    """SESSION_ERROR with no data uses fallback message."""
    handler = EventHandler()

    class _BareEvent:
        data = None
    handler._handle_error(_BareEvent())
    assert handler.done.is_set()
    assert handler.error == "Unknown session error"


def test_full_flow():
    """Simulates delta -> message -> idle flow."""
    chunks = []
    handler = EventHandler(on_delta=lambda c: chunks.append(c))

    # Delta events
    handler._handle_delta(_MockEvent(delta_content="Hello "))
    handler._handle_delta(_MockEvent(delta_content="world"))

    # Final message
    handler._handle_message(_MockEvent(content="Hello world"))

    # Session idle
    handler._handle_idle(_MockEvent())

    assert chunks == ["Hello ", "world"]
    assert handler.final_text == "Hello world"
    assert handler.done.is_set()
    assert handler.error is None
