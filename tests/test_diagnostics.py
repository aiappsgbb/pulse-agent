"""Tests for core/diagnostics.py — preflight checks + health check."""

from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from core.diagnostics import (
    run_diagnostics, run_health_check, HealthCheck,
    print_health_report, verify_browser_auth, _is_login_page,
)


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


# ---------------------------------------------------------------------------
# run_diagnostics (existing tests, unchanged)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# HealthCheck
# ---------------------------------------------------------------------------

class TestHealthCheck:
    def test_repr_pass(self):
        c = HealthCheck("Test", True, "all good")
        assert "PASS" in repr(c)

    def test_repr_fail(self):
        c = HealthCheck("Test", False, "broken", "fix it")
        assert "FAIL" in repr(c)

    def test_is_namedtuple(self):
        c = HealthCheck("X", True, "y", "z")
        assert c.name == "X"
        assert c.ok is True
        assert c.detail == "y"
        assert c.fix == "z"

    def test_defaults(self):
        c = HealthCheck("X", True)
        assert c.detail == ""
        assert c.fix == ""


# ---------------------------------------------------------------------------
# _is_login_page
# ---------------------------------------------------------------------------

class TestIsLoginPage:
    def test_microsoftonline(self):
        assert _is_login_page("https://login.microsoftonline.com/common/oauth2/authorize")

    def test_live(self):
        assert _is_login_page("https://login.live.com/oauth20_authorize.srf")

    def test_microsoft(self):
        assert _is_login_page("https://login.microsoft.com/common/login")

    def test_oauth2_path(self):
        assert _is_login_page("https://example.com/oauth2/callback")

    def test_teams_is_not_login(self):
        assert not _is_login_page("https://teams.microsoft.com/v2/")

    def test_case_insensitive(self):
        assert _is_login_page("https://LOGIN.MICROSOFTONLINE.COM/foo")


# ---------------------------------------------------------------------------
# run_health_check
# ---------------------------------------------------------------------------

def _no_playwright():
    """Stub for _check_playwright_edge — avoids launching a real browser."""
    return HealthCheck("Playwright Edge", True, "mocked")


class TestRunHealthCheck:
    def test_returns_list_of_health_checks(self):
        with patch("core.diagnostics._check_playwright_edge", _no_playwright):
            checks = run_health_check({})
        assert isinstance(checks, list)
        assert all(isinstance(c, HealthCheck) for c in checks)

    def test_python_version_check(self):
        with patch("core.diagnostics._check_playwright_edge", _no_playwright):
            checks = run_health_check({})
        py = next(c for c in checks if "Python" in c.name)
        assert py.ok

    def test_import_checks_present(self):
        with patch("core.diagnostics._check_playwright_edge", _no_playwright):
            checks = run_health_check({})
        names = [c.name for c in checks]
        assert any("yaml" in n for n in names)
        assert any("Playwright" in n for n in names)

    def test_config_none_reports_missing(self):
        with patch("core.diagnostics._check_playwright_edge", _no_playwright):
            checks = run_health_check(None)
        cfg = next(c for c in checks if c.name == "Config")
        assert not cfg.ok

    def test_config_with_valid_user(self):
        config = {"user": {"name": "Alice", "email": "a@b.com"}, "models": {"default": "gpt-4.1"}}
        with patch("core.diagnostics._check_playwright_edge", _no_playwright):
            checks = run_health_check(config)
        identity = next(c for c in checks if "identity" in c.name)
        assert identity.ok

    def test_config_with_todo_user(self):
        config = {"user": {"name": "TODO: your name", "email": "a@b.com"}, "models": {}}
        with patch("core.diagnostics._check_playwright_edge", _no_playwright):
            checks = run_health_check(config)
        identity = next(c for c in checks if "identity" in c.name)
        assert not identity.ok

    def test_config_missing_models(self):
        config = {"user": {"name": "Alice", "email": "a@b.com"}}
        with patch("core.diagnostics._check_playwright_edge", _no_playwright):
            checks = run_health_check(config)
        models = next(c for c in checks if "models" in c.name)
        assert not models.ok

    def test_venv_check_present(self):
        with patch("core.diagnostics._check_playwright_edge", _no_playwright):
            checks = run_health_check({})
        venv = next(c for c in checks if "Virtual" in c.name)
        assert isinstance(venv.ok, bool)

    def test_pulse_home_with_env(self, tmp_path):
        pulse_dir = tmp_path / "Pulse"
        pulse_dir.mkdir()
        with patch.dict("os.environ", {"PULSE_HOME": str(pulse_dir)}), \
             patch("core.diagnostics._check_playwright_edge", _no_playwright):
            checks = run_health_check({})
        home = next(c for c in checks if "PULSE_HOME" in c.name)
        assert home.ok

    def test_pulse_home_missing_env(self):
        with patch.dict("os.environ", {}, clear=False) as env, \
             patch("core.diagnostics._check_playwright_edge", _no_playwright):
            env.pop("PULSE_HOME", None)
            env.pop("OneDriveCommercial", None)
            checks = run_health_check({})
        home = next(c for c in checks if "PULSE_HOME" in c.name)
        assert not home.ok

    def test_gh_auth_check_when_gh_missing(self):
        with patch("core.diagnostics.shutil.which", return_value=None), \
             patch("core.diagnostics._check_playwright_edge", _no_playwright):
            checks = run_health_check({})
        gh = next(c for c in checks if "GitHub CLI auth" in c.name)
        assert not gh.ok
        assert "not installed" in gh.detail


# ---------------------------------------------------------------------------
# print_health_report
# ---------------------------------------------------------------------------

class TestPrintHealthReport:
    def test_prints_summary(self, capsys):
        checks = [
            HealthCheck("Good", True, "ok"),
            HealthCheck("Bad", False, "broken", "fix it"),
        ]
        print_health_report(checks)
        out = capsys.readouterr().out
        assert "1/2 checks passed" in out
        assert "Health Check" in out
        assert "Fix:" in out

    def test_all_pass_message(self, capsys):
        checks = [HealthCheck("A", True, "ok")]
        print_health_report(checks)
        out = capsys.readouterr().out
        assert "All good" in out

    def test_optional_only_failure(self, capsys):
        checks = [
            HealthCheck("OK Check", True, "fine"),
            HealthCheck("WorkIQ MCP server", False, "missing", "npm install"),
        ]
        print_health_report(checks)
        out = capsys.readouterr().out
        assert "optional" in out.lower() or "reduced functionality" in out.lower()


# ---------------------------------------------------------------------------
# verify_browser_auth (mock _launch_edge to avoid real browser)
# ---------------------------------------------------------------------------

def _mock_page(url, title=""):
    """Build a mock page at a given URL."""
    page = AsyncMock()
    page.url = url
    page.title = AsyncMock(return_value=title)
    page.goto = AsyncMock()
    page.wait_for_load_state = AsyncMock()
    return page


class TestVerifyBrowserAuth:
    async def test_launch_failure_returns_error(self):
        with patch("core.diagnostics._launch_edge", AsyncMock(side_effect=Exception("browser not found"))), \
             patch("core.browser._default_profile_dir", return_value="/fake"):
            result = await verify_browser_auth(headless=True)
        assert not result["ok"]
        assert "browser not found" in result["error"]
        assert "profile_dir" in result

    async def test_login_page_detection(self):
        page = _mock_page("https://login.microsoftonline.com/common/oauth2/authorize", "Sign in")
        ctx = AsyncMock()
        ctx.pages = [page]

        with patch("core.diagnostics._launch_edge", AsyncMock(return_value=(AsyncMock(), ctx))), \
             patch("core.browser._default_profile_dir", return_value="/fake"):
            result = await verify_browser_auth(headless=True)
        assert result["needs_login"] is True
        assert result["ok"] is False

    async def test_authenticated_page(self):
        page = _mock_page("https://teams.microsoft.com/v2/", "Microsoft Teams")
        ctx = AsyncMock()
        ctx.pages = [page]

        with patch("core.diagnostics._launch_edge", AsyncMock(return_value=(AsyncMock(), ctx))), \
             patch("core.browser._default_profile_dir", return_value="/fake"):
            result = await verify_browser_auth(headless=True)
        assert result["ok"] is True
        assert result["needs_login"] is False

    @pytest.mark.parametrize("url", [
        "https://login.microsoftonline.com/common/login",
        "https://login.live.com/oauth20_authorize.srf",
        "https://login.microsoft.com/common/oauth2/v2.0/authorize",
        "https://example.com/oauth2/callback",
    ])
    async def test_multiple_login_url_patterns(self, url):
        page = _mock_page(url, "Sign in")
        ctx = AsyncMock()
        ctx.pages = [page]

        with patch("core.diagnostics._launch_edge", AsyncMock(return_value=(AsyncMock(), ctx))), \
             patch("core.browser._default_profile_dir", return_value="/fake"):
            result = await verify_browser_auth(headless=True)
        assert result["needs_login"] is True, f"Failed for URL: {url}"
