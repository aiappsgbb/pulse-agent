"""Tests for core/diagnostics.py — startup preflight checks."""

from pathlib import Path
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from core.diagnostics import run_diagnostics


def _full_config(**overrides):
    """Minimal config that passes all checks."""
    cfg = {
        "models": {"default": "gpt-4.1"},
        "monitoring": {},
        "user": {"name": "Test User"},
        "team": [],
    }
    cfg.update(overrides)
    return cfg


def test_empty_config():
    """Empty config triggers multiple warnings."""
    warnings = run_diagnostics({})
    assert any("models" in w for w in warnings)
    assert any("monitoring" in w for w in warnings)


def test_copilot_cli_missing():
    config = _full_config()
    with patch("core.diagnostics.shutil.which", return_value=None):
        warnings = run_diagnostics(config)
    assert any("Copilot CLI" in w for w in warnings)


def test_copilot_cli_present():
    config = _full_config()
    with patch("core.diagnostics.shutil.which", side_effect=lambda cmd: "/usr/bin/copilot" if cmd == "copilot" else None):
        warnings = run_diagnostics(config)
    assert not any("Copilot CLI" in w for w in warnings)


def test_workiq_missing_is_optional():
    """WorkIQ missing is a warning but mentions 'optional'."""
    config = _full_config()
    with patch("core.diagnostics.shutil.which", side_effect=lambda cmd: "/bin/copilot" if cmd == "copilot" else None):
        warnings = run_diagnostics(config)
    workiq_warnings = [w for w in warnings if "WorkIQ" in w]
    assert len(workiq_warnings) == 1
    assert "optional" in workiq_warnings[0].lower()


def test_browser_profile_missing(tmp_path):
    config = _full_config(
        transcripts={"playwright": {"user_data_dir": str(tmp_path / "nonexistent")}},
    )
    with patch("core.diagnostics.shutil.which", return_value="/bin/copilot"):
        warnings = run_diagnostics(config)
    assert any("Browser profile" in w for w in warnings)


def test_browser_profile_exists(tmp_path):
    """No warning when browser profile directory exists."""
    profile_dir = tmp_path / "edge-profile"
    profile_dir.mkdir()
    config = _full_config(
        transcripts={"playwright": {"user_data_dir": str(profile_dir)}},
    )
    with patch("core.diagnostics.shutil.which", return_value="/bin/copilot"):
        warnings = run_diagnostics(config)
    assert not any("Browser profile" in w for w in warnings)


def test_complete_config_minimal_warnings(tmp_path):
    """A complete config with all deps present produces only optional warnings."""
    config = _full_config()
    with patch("core.diagnostics.shutil.which", return_value="/bin/exists"), \
         patch.dict("os.environ", {"PULSE_HOME": str(tmp_path)}):
        warnings = run_diagnostics(config)
    # Should have zero non-optional warnings
    non_optional = [w for w in warnings if "optional" not in w.lower()]
    assert len(non_optional) == 0


def test_missing_user_name():
    """Missing user.name triggers a warning."""
    config = _full_config(user={})
    with patch("core.diagnostics.shutil.which", return_value="/bin/copilot"):
        warnings = run_diagnostics(config)
    assert any("user.name" in w for w in warnings)


def test_pulse_home_not_set():
    """Warning when PULSE_HOME env var is not set."""
    config = _full_config()
    with patch("core.diagnostics.shutil.which", return_value="/bin/copilot"), \
         patch.dict("os.environ", {}, clear=False) as env:
        env.pop("PULSE_HOME", None)
        env.pop("OneDriveCommercial", None)
        warnings = run_diagnostics(config)
    assert any("PULSE_HOME" in w or "OneDrive" in w for w in warnings)


def test_team_member_missing_alias():
    """Warning for team members without an alias."""
    config = _full_config(team=[{"name": "Alice"}])
    with patch("core.diagnostics.shutil.which", return_value="/bin/copilot"):
        warnings = run_diagnostics(config)
    assert any("no alias" in w for w in warnings)


def test_pulse_team_dir_missing(tmp_path):
    """Warning when team is configured but Pulse-Team dir doesn't exist."""
    config = _full_config(team=[{"name": "Alice", "alias": "alice"}])
    with patch("core.diagnostics.shutil.which", return_value="/bin/copilot"), \
         patch("core.constants.PULSE_TEAM_DIR", tmp_path / "nonexistent"):
        warnings = run_diagnostics(config)
    assert any("Pulse-Team" in w for w in warnings)


def test_pulse_team_dir_exists(tmp_path):
    """No Pulse-Team warning when directory exists."""
    team_dir = tmp_path / "Pulse-Team"
    team_dir.mkdir()
    config = _full_config(team=[{"name": "Alice", "alias": "alice"}])
    with patch("core.diagnostics.shutil.which", return_value="/bin/copilot"), \
         patch("core.constants.PULSE_TEAM_DIR", team_dir):
        warnings = run_diagnostics(config)
    assert not any("Pulse-Team" in w for w in warnings)


def test_no_team_section_is_optional():
    """Empty team config mentions 'optional'."""
    config = _full_config(team=[])
    with patch("core.diagnostics.shutil.which", return_value="/bin/copilot"):
        warnings = run_diagnostics(config)
    team_warnings = [w for w in warnings if "team" in w.lower() and "inter-agent" in w.lower()]
    assert all("optional" in w.lower() for w in team_warnings)
