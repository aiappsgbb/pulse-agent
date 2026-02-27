"""Tests for onboarding detection, config merging, and save_config tool."""

import yaml
from pathlib import Path

import pytest

from core.onboarding import (
    is_first_run,
    build_config_from_answers,
    write_config,
    load_template_config,
)


# ---------------------------------------------------------------------------
# is_first_run
# ---------------------------------------------------------------------------


class TestIsFirstRun:
    def test_none_config(self):
        assert is_first_run(None) is True

    def test_empty_config(self):
        assert is_first_run({}) is True

    def test_missing_user_section(self):
        assert is_first_run({"models": {"default": "claude-sonnet"}}) is True

    def test_todo_in_name(self):
        config = {"user": {"name": "TODO: Your Full Name", "email": "a@b.com"}}
        assert is_first_run(config) is True

    def test_todo_in_email(self):
        config = {"user": {"name": "Alice", "email": "TODO: you@example.com"}}
        assert is_first_run(config) is True

    def test_todo_lowercase(self):
        config = {"user": {"name": "todo: fill in", "email": "a@b.com"}}
        assert is_first_run(config) is True

    def test_clean_config(self):
        config = {"user": {"name": "Alice Smith", "email": "alice@example.com"}}
        assert is_first_run(config) is False

    def test_empty_name(self):
        config = {"user": {"name": "", "email": "alice@example.com"}}
        assert is_first_run(config) is True

    def test_no_email(self):
        config = {"user": {"name": "Alice Smith"}}
        assert is_first_run(config) is True

    def test_non_required_fields_with_todo_ok(self):
        """TODO in optional fields (role, org) doesn't trigger first run."""
        config = {
            "user": {
                "name": "Alice Smith",
                "email": "alice@example.com",
                "role": "TODO: Your job title",
                "org": "TODO: Your org",
            }
        }
        assert is_first_run(config) is False


# ---------------------------------------------------------------------------
# build_config_from_answers
# ---------------------------------------------------------------------------


class TestBuildConfig:
    def test_merges_user_section(self):
        template = {
            "user": {"name": "TODO: Your Name", "email": "TODO: you@example.com"},
            "models": {"default": "claude-sonnet"},
        }
        answers = {
            "user": {"name": "Alice", "email": "alice@example.com"},
        }
        result = build_config_from_answers(answers, template)
        assert result["user"]["name"] == "Alice"
        assert result["user"]["email"] == "alice@example.com"
        assert result["models"]["default"] == "claude-sonnet"  # preserved

    def test_strips_todo_from_user(self):
        template = {
            "user": {
                "name": "TODO: fill",
                "role": "TODO: role",
                "what_matters": ["TODO: add items", "Customer deals"],
            }
        }
        answers = {"user": {"name": "Bob"}}
        result = build_config_from_answers(answers, template)
        assert result["user"]["name"] == "Bob"
        assert result["user"]["role"] == ""  # cleared
        assert "TODO: add items" not in result["user"]["what_matters"]
        assert "Customer deals" in result["user"]["what_matters"]

    def test_preserves_untouched_sections(self):
        template = {
            "user": {"name": "TODO"},
            "digest": {
                "input_paths": [{"path": "transcripts", "type": "transcripts"}],
                "supported_extensions": [".md", ".txt"],
            },
            "transcripts": {"lookback_weeks": 2},
        }
        answers = {"user": {"name": "Alice", "email": "a@b.com"}}
        result = build_config_from_answers(answers, template)
        assert result["digest"]["input_paths"][0]["path"] == "transcripts"
        assert result["transcripts"]["lookback_weeks"] == 2

    def test_replaces_schedule(self):
        template = {
            "schedule": [{"id": "old", "type": "digest", "pattern": "daily 07:00"}]
        }
        new_schedule = [
            {"id": "morning-digest", "type": "digest", "pattern": "daily 08:00"}
        ]
        answers = {"schedule": new_schedule}
        result = build_config_from_answers(answers, template)
        assert result["schedule"][0]["pattern"] == "daily 08:00"

    def test_replaces_team(self):
        template = {"team": []}
        answers = {"team": [{"name": "Jane Doe", "alias": "jane"}]}
        result = build_config_from_answers(answers, template)
        assert len(result["team"]) == 1
        assert result["team"][0]["alias"] == "jane"

    def test_strips_todo_from_intelligence(self):
        template = {
            "intelligence": {
                "topics": ["TODO: Add topics", "AI Agents"],
                "competitors": [
                    {"company": "TODO: Competitor 1", "watch": ["pricing"]},
                    {"company": "Acme Corp", "watch": ["pricing"]},
                ],
            }
        }
        answers = {}
        result = build_config_from_answers(answers, template)
        assert "TODO: Add topics" not in result["intelligence"]["topics"]
        assert "AI Agents" in result["intelligence"]["topics"]
        companies = [c["company"] for c in result["intelligence"]["competitors"]]
        assert "TODO: Competitor 1" not in companies
        assert "Acme Corp" in companies

    def test_no_template(self):
        """Works without a template — just returns answers."""
        answers = {"user": {"name": "Alice", "email": "a@b.com"}}
        result = build_config_from_answers(answers, template={})
        assert result["user"]["name"] == "Alice"


# ---------------------------------------------------------------------------
# write_config
# ---------------------------------------------------------------------------


class TestWriteConfig:
    def test_writes_yaml(self, tmp_path):
        config = {"user": {"name": "Alice"}, "models": {"default": "claude-sonnet"}}
        dest = tmp_path / "standing-instructions.yaml"
        result = write_config(config, dest)
        assert result == dest
        assert dest.exists()
        loaded = yaml.safe_load(dest.read_text(encoding="utf-8"))
        assert loaded["user"]["name"] == "Alice"
        assert loaded["models"]["default"] == "claude-sonnet"

    def test_creates_parent_dirs(self, tmp_path):
        dest = tmp_path / "deep" / "nested" / "config.yaml"
        write_config({"user": {"name": "Bob"}}, dest)
        assert dest.exists()

    def test_no_todo_in_output(self, tmp_path):
        config = build_config_from_answers(
            {"user": {"name": "Alice", "email": "a@b.com"}},
            template={
                "user": {"name": "TODO: name", "role": "TODO: role"},
                "intelligence": {"topics": ["TODO: add", "LLM"]},
            },
        )
        dest = tmp_path / "config.yaml"
        write_config(config, dest)
        text = dest.read_text(encoding="utf-8")
        assert "TODO" not in text


# ---------------------------------------------------------------------------
# load_template_config
# ---------------------------------------------------------------------------


class TestLoadTemplate:
    def test_loads_template(self):
        """Template file exists in the repo — should load without error."""
        template = load_template_config()
        assert isinstance(template, dict)
        assert "user" in template
        assert "schedule" in template


# ---------------------------------------------------------------------------
# save_config tool handler
# ---------------------------------------------------------------------------


class TestSaveConfigTool:
    @pytest.fixture
    def mock_pulse_home(self, tmp_path, monkeypatch):
        monkeypatch.setattr("sdk.tools.PULSE_HOME", tmp_path)
        monkeypatch.setattr("core.onboarding.PULSE_HOME", tmp_path)
        return tmp_path

    async def test_save_config_writes_file(self, mock_pulse_home):
        from sdk.tools import save_config_tool
        result = await save_config_tool.handler(
            {"arguments": {
                "config": {
                    "user": {"name": "Alice", "email": "alice@example.com"},
                    "schedule": [],
                    "team": [],
                }
            }},
        )
        text = result.get("textResultForLlm", "")
        assert "Configuration saved" in text or "saved" in text.lower()
        config_path = mock_pulse_home / "standing-instructions.yaml"
        assert config_path.exists()
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert loaded["user"]["name"] == "Alice"

    async def test_save_config_rejects_empty(self):
        from sdk.tools import save_config_tool
        result = await save_config_tool.handler({"arguments": {"config": {}}})
        assert "ERROR" in result.get("textResultForLlm", "")

    async def test_save_config_rejects_todo_name(self):
        from sdk.tools import save_config_tool
        result = await save_config_tool.handler(
            {"arguments": {
                "config": {
                    "user": {"name": "TODO: Your Name", "email": "a@b.com"}
                }
            }},
        )
        assert "ERROR" in result.get("textResultForLlm", "")

    async def test_save_config_rejects_todo_email(self):
        from sdk.tools import save_config_tool
        result = await save_config_tool.handler(
            {"arguments": {
                "config": {
                    "user": {"name": "Alice", "email": "TODO: you@example.com"}
                }
            }},
        )
        assert "ERROR" in result.get("textResultForLlm", "")
