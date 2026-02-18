"""Tests for core/ modules — constants, state, logging, config."""

import json
import os
import tempfile
from pathlib import Path

import pytest
import yaml

# Add src/ to path so imports work
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from core.constants import PROJECT_ROOT, OUTPUT_DIR, TASKS_DIR, CONFIG_DIR
from core.state import load_json_state, save_json_state
from core.logging import safe_encode


# --- core/constants ---

def test_project_root_exists():
    assert PROJECT_ROOT.exists()
    assert (PROJECT_ROOT / "CLAUDE.md").exists()


def test_path_relationships():
    assert OUTPUT_DIR == PROJECT_ROOT / "output"
    assert TASKS_DIR == PROJECT_ROOT / "tasks"
    assert CONFIG_DIR == PROJECT_ROOT / "config"


# --- core/state ---

def test_load_missing_file_returns_default():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "missing.json"
        result = load_json_state(p, {"key": "default"})
        assert result == {"key": "default"}


def test_load_corrupt_file_returns_default():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "corrupt.json"
        p.write_text("not valid json {{{", encoding="utf-8")
        result = load_json_state(p, {"key": "default"})
        assert result == {"key": "default"}


def test_save_and_load_roundtrip():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "state.json"
        data = {"items": [1, 2, 3], "count": 42}
        save_json_state(p, data)
        assert p.exists()
        result = load_json_state(p, {})
        assert result == data


def test_save_creates_parent_dirs():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "nested" / "deep" / "state.json"
        save_json_state(p, {"x": 1})
        assert p.exists()


def test_load_default_not_mutated():
    """Ensure the default dict is copied, not returned directly."""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "missing.json"
        default = {"items": []}
        result = load_json_state(p, default)
        result["items"].append("modified")
        # Original default should be unchanged
        assert default == {"items": []}


# --- core/logging ---

def test_safe_encode_ascii():
    assert safe_encode("hello") == "hello"


def test_safe_encode_unicode():
    result = safe_encode("caf\u00e9 \u2603")
    assert "?" in result  # non-ASCII replaced
    assert "caf" in result


def test_safe_encode_empty():
    assert safe_encode("") == ""


# --- core/config ---

def test_load_config():
    from core.config import load_config
    config = load_config()
    assert "monitoring" in config
    assert "models" in config
    assert "digest" in config


def test_validate_config_warnings():
    from core.config import validate_config
    # Config with no models section
    warnings = validate_config({})
    assert any("models" in w for w in warnings)


def test_validate_config_empty_allowed_users():
    from core.config import validate_config
    config = {"models": {"default": "x"}, "telegram": {"allowed_users": []}}
    warnings = validate_config(config)
    assert any("allowed_users" in w for w in warnings)


def test_expand_env_vars():
    from core.config import _expand_env_vars
    os.environ["_TEST_VAR_"] = "expanded"
    result = _expand_env_vars("$_TEST_VAR_")
    assert result == "expanded"
    del os.environ["_TEST_VAR_"]


def test_expand_env_vars_nested():
    from core.config import _expand_env_vars
    os.environ["_TEST_NESTED_"] = "val"
    result = _expand_env_vars({"key": "$_TEST_NESTED_", "list": ["$_TEST_NESTED_"]})
    assert result == {"key": "val", "list": ["val"]}
    del os.environ["_TEST_NESTED_"]
