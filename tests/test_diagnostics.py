"""Tests for core/diagnostics.py — startup preflight checks."""

from pathlib import Path
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from core.diagnostics import run_diagnostics


def test_empty_config():
    """Empty config triggers multiple warnings."""
    warnings = run_diagnostics({})
    assert any("models" in w for w in warnings)
    assert any("monitoring" in w for w in warnings)


def test_copilot_cli_missing():
    config = {"models": {"default": "gpt-4.1"}, "monitoring": {}}
    with patch("core.diagnostics.shutil.which", return_value=None):
        warnings = run_diagnostics(config)
    assert any("Copilot CLI" in w for w in warnings)


def test_copilot_cli_present():
    config = {"models": {"default": "gpt-4.1"}, "monitoring": {}}
    with patch("core.diagnostics.shutil.which", side_effect=lambda cmd: "/usr/bin/copilot" if cmd == "copilot" else None):
        warnings = run_diagnostics(config)
    assert not any("Copilot CLI" in w for w in warnings)


def test_workiq_missing_is_optional():
    """WorkIQ missing is a warning but mentions 'optional'."""
    config = {"models": {"default": "gpt-4.1"}, "monitoring": {}}
    with patch("core.diagnostics.shutil.which", side_effect=lambda cmd: "/bin/copilot" if cmd == "copilot" else None):
        warnings = run_diagnostics(config)
    workiq_warnings = [w for w in warnings if "WorkIQ" in w]
    assert len(workiq_warnings) == 1
    assert "optional" in workiq_warnings[0].lower()


def test_browser_profile_missing(tmp_path):
    config = {
        "models": {"default": "gpt-4.1"}, "monitoring": {},
        "transcripts": {"playwright": {"user_data_dir": str(tmp_path / "nonexistent")}},
    }
    with patch("core.diagnostics.shutil.which", return_value="/bin/copilot"):
        warnings = run_diagnostics(config)
    assert any("Browser profile" in w for w in warnings)


def test_browser_profile_exists(tmp_path):
    """No warning when browser profile directory exists."""
    profile_dir = tmp_path / "edge-profile"
    profile_dir.mkdir()
    config = {
        "models": {"default": "gpt-4.1"}, "monitoring": {},
        "transcripts": {"playwright": {"user_data_dir": str(profile_dir)}},
    }
    with patch("core.diagnostics.shutil.which", return_value="/bin/copilot"):
        warnings = run_diagnostics(config)
    assert not any("Browser profile" in w for w in warnings)


def test_complete_config_minimal_warnings():
    """A complete config with all deps present produces only optional warnings."""
    config = {
        "models": {"default": "gpt-4.1"},
        "monitoring": {},
    }
    with patch("core.diagnostics.shutil.which", return_value="/bin/exists"):
        warnings = run_diagnostics(config)
    # Should have zero non-optional warnings
    non_optional = [w for w in warnings if "optional" not in w.lower()]
    assert len(non_optional) == 0
