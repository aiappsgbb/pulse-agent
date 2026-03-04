"""Tests for sdk/ modules — prompts, agents, session config building."""

import tempfile
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sdk.prompts import load_prompt, load_instruction
from sdk.agents import parse_front_matter, dataverse_mcp_config, _mcp_config


# --- load_prompt ---

def test_load_prompt_basic():
    """Load a real prompt file."""
    text = load_prompt("config/prompts/system/base.md")
    assert "Pulse Agent" in text
    assert len(text) > 10


def test_load_prompt_with_variables():
    """Variable interpolation replaces {{placeholders}}."""
    text = load_prompt("config/prompts/system/monitor.md", {
        "priorities": "- Test priority",
        "vips": "TestVIP",
        "auto_send": "False",
        "auto_send_low_risk": "True",
        "max_nudges": "2",
    })
    assert "Test priority" in text
    assert "TestVIP" in text
    assert "{{priorities}}" not in text


def test_load_prompt_unreplaced_variables():
    """Variables not in the dict stay as-is."""
    text = load_prompt("config/prompts/system/monitor.md")
    # Without providing variables, placeholders remain
    assert "{{priorities}}" in text


# --- load_instruction ---

def test_load_instruction_local():
    """Load an instruction from config/instructions/."""
    from core.config import load_config
    config = load_config()
    text = load_instruction("triage", config)
    assert len(text) > 0


def test_load_instruction_missing():
    """Missing instruction returns empty string."""
    text = load_instruction("nonexistent_instruction", {})
    assert text == ""


# --- parse_front_matter ---

def test_parse_front_matter_basic():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "test.md"
        p.write_text("---\nname: test-agent\ndisplay_name: Test\n---\nPrompt body here.", encoding="utf-8")
        meta, body = parse_front_matter(p)
        assert meta["name"] == "test-agent"
        assert meta["display_name"] == "Test"
        assert body == "Prompt body here."


def test_parse_front_matter_no_front_matter():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "plain.md"
        p.write_text("Just plain markdown.", encoding="utf-8")
        meta, body = parse_front_matter(p)
        assert meta == {}
        assert body == "Just plain markdown."


def test_parse_front_matter_real_agent():
    """Parse a real agent definition file."""
    meta, body = parse_front_matter(
        Path(__file__).parent.parent / "config" / "prompts" / "agents" / "pulse-reader.md"
    )
    assert meta["name"] == "pulse-reader"
    assert meta["display_name"] == "Pulse Reader"
    assert "infer" in meta
    assert len(body) > 0


# --- session config building ---

def test_build_session_config_monitor():
    """Build session config for monitor mode."""
    from core.config import load_config
    from sdk.session import build_session_config
    config = load_config()
    sc = build_session_config(config, "monitor")
    assert "model" in sc
    assert "system_message" in sc
    assert sc["system_message"]["mode"] == "append"
    assert "workiq" in sc.get("mcp_servers", {})
    # Hooks are attached to all sessions
    assert "hooks" in sc
    hooks = sc["hooks"]
    assert callable(hooks["on_pre_tool_use"])
    assert callable(hooks["on_post_tool_use"])
    assert callable(hooks["on_error_occurred"])
    assert callable(hooks["on_session_end"])


def test_build_session_config_chat():
    """Chat mode uses 'replace' for system message."""
    from core.config import load_config
    from sdk.session import build_session_config
    config = load_config()
    sc = build_session_config(config, "chat")
    assert sc["system_message"]["mode"] == "replace"
    # Chat mode has pulse-reader and m365-query agents
    agent_names = [a["name"] for a in sc.get("custom_agents", [])]
    assert "pulse-reader" in agent_names
    assert "m365-query" in agent_names


def test_build_session_config_standalone_rejected():
    """Standalone modes should raise ValueError."""
    from core.config import load_config
    from sdk.session import build_session_config
    config = load_config()
    with pytest.raises(ValueError, match="standalone"):
        build_session_config(config, "transcripts")


def test_build_session_config_digest():
    """Digest mode has WorkIQ MCP and agents."""
    from core.config import load_config
    from sdk.session import build_session_config
    config = load_config()
    sc = build_session_config(config, "digest")
    assert "model" in sc
    assert sc["system_message"]["mode"] == "append"
    assert "workiq" in sc.get("mcp_servers", {})
    agent_names = [a["name"] for a in sc.get("custom_agents", [])]
    assert "digest-writer" in agent_names


def test_build_session_config_intel():
    """Intel mode builds correctly."""
    from core.config import load_config
    from sdk.session import build_session_config
    config = load_config()
    sc = build_session_config(config, "intel")
    assert sc["system_message"]["mode"] == "append"
    assert "workiq" in sc.get("mcp_servers", {})


def test_build_session_config_research():
    """Research mode builds correctly."""
    from core.config import load_config
    from sdk.session import build_session_config
    config = load_config()
    sc = build_session_config(config, "research")
    assert sc["system_message"]["mode"] == "append"


def test_build_session_config_has_all_skills():
    """All 4 skills are registered in every session config."""
    from core.config import load_config
    from sdk.session import build_session_config
    config = load_config()
    sc = build_session_config(config, "monitor")
    skill_dirs = sc.get("skill_directories", [])
    skill_names = [d.split("\\")[-1].split("/")[-1] for d in skill_dirs]
    assert "pulse-signal-drafter" in skill_names
    assert "teams-sender" in skill_names
    assert "meeting-scheduler" in skill_names
    assert "email-reply" in skill_names


# --- DRY MCP server config ---

def test_dataverse_returns_none_when_not_configured():
    """Dataverse MCP is skipped when URL not in config."""
    assert dataverse_mcp_config({}) is None
    assert dataverse_mcp_config({"mcp_servers": {}}) is None
    assert dataverse_mcp_config({"mcp_servers": {"dataverse": {}}}) is None


def test_dataverse_returns_none_for_todo_placeholder():
    """Dataverse MCP is skipped when URL is a TODO placeholder."""
    cfg = {"mcp_servers": {"dataverse": {"url": "TODO: https://example.crm.dynamics.com/api/mcp"}}}
    assert dataverse_mcp_config(cfg) is None


def test_dataverse_returns_config_when_url_set():
    """Dataverse MCP returns valid config when URL is configured."""
    cfg = {"mcp_servers": {"dataverse": {"url": "https://myorg.crm.dynamics.com/api/mcp"}}}
    result = dataverse_mcp_config(cfg)
    assert result is not None
    assert result["url"] == "https://myorg.crm.dynamics.com/api/mcp"
    assert result["type"] == "http"


def test_mcp_config_unknown_server_returns_none():
    """Unknown MCP server names return None instead of raising."""
    result = _mcp_config("nonexistent", {})
    assert result is None


def test_default_mcp_servers_inherited():
    """Modes without mcp_servers inherit default_mcp_servers."""
    from core.config import load_config
    from sdk.session import build_session_config
    config = load_config()
    sc = build_session_config(config, "monitor")
    # workiq comes from default_mcp_servers, not from mode entry
    assert "workiq" in sc.get("mcp_servers", {})


def test_dataverse_skipped_when_unconfigured():
    """Dataverse is in default_mcp_servers but skipped when URL not in config."""
    from core.config import load_config
    from sdk.session import build_session_config
    config = load_config()
    # Template config has no dataverse URL → should be skipped
    sc = build_session_config(config, "monitor")
    assert "dataverse" not in sc.get("mcp_servers", {})


def test_dataverse_included_when_configured():
    """Dataverse is included in session when URL is configured."""
    from core.config import load_config
    from sdk.session import build_session_config
    config = load_config()
    config.setdefault("mcp_servers", {})["dataverse"] = {
        "url": "https://test.crm.dynamics.com/api/mcp"
    }
    sc = build_session_config(config, "monitor")
    assert "dataverse" in sc.get("mcp_servers", {})
    assert sc["mcp_servers"]["dataverse"]["url"] == "https://test.crm.dynamics.com/api/mcp"
