"""Integration tests for diagnostics — exercises real code paths, no mocking
of the functions under test.

These catch bugs where unit tests pass (mocks correct) but real code fails
(sync playwright inside asyncio, wrong env, subprocess errors, etc.).

Run: pytest tests/test_diagnostics_integration.py -v
"""

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from core.diagnostics import (
    run_health_check,
    _check_playwright_edge,
    _check_gh_auth,
    _check_pulse_home,
    _check_config,
    HealthCheck,
    _is_login_page,
)


# ---------------------------------------------------------------------------
# Async tests FIRST — before any sync playwright launches (which poison
# the event loop on Windows due to Playwright subprocess management)
# ---------------------------------------------------------------------------

class TestCheckPlaywrightEdgeFromAsync:
    """The exact bug that was missed: _check_playwright_edge called from
    inside an asyncio event loop (which is what --health-check does).

    sync_playwright crashes with "Playwright Sync API inside asyncio loop".
    The fix detects the running loop and defers to the async browser auth check.
    """

    async def test_detects_running_loop_and_defers(self):
        """When called from async context, must not crash — must defer."""
        result = _check_playwright_edge()
        assert isinstance(result, HealthCheck)
        assert result.name == "Playwright Edge"
        # Must NOT crash. Should detect loop and defer.
        assert result.ok
        assert "deferred" in result.detail.lower()

    async def test_run_health_check_from_async(self):
        """run_health_check (sync) called from async must not crash.

        This is the exact call chain: --health-check → asyncio.run →
        run_health_check_async → run_health_check → _check_playwright_edge.
        """
        checks = run_health_check(config=None)
        assert isinstance(checks, list)
        assert len(checks) > 0
        # Playwright check should be present and deferred
        pw = next((c for c in checks if "Playwright" in c.name), None)
        assert pw is not None
        assert pw.ok
        assert "deferred" in pw.detail.lower()

    async def test_profile_dir_resolves(self):
        """Browser profile path is a real string pointing to the daemon profile."""
        from core.browser import _default_profile_dir
        profile = _default_profile_dir()
        assert isinstance(profile, str)
        assert "pulse-daemon-profile" in profile


# ---------------------------------------------------------------------------
# Sync tests — real system calls, no mocks
# ---------------------------------------------------------------------------

def _pw_skip():
    """Stub _check_playwright_edge to avoid sync_playwright which poisons
    the event loop for ALL subsequent async tests in the pytest session."""
    return HealthCheck("Playwright Edge", True, "skipped in integration test")


class TestRunHealthCheckReal:
    """Call run_health_check with real system state.

    Only _check_playwright_edge is stubbed because sync_playwright
    corrupts the Windows event loop for all subsequent async tests.
    Everything else (gh auth, PULSE_HOME, imports, config) is real.
    """

    def test_returns_checks_without_crashing(self):
        with patch("core.diagnostics._check_playwright_edge", _pw_skip):
            checks = run_health_check(config=None)
        assert isinstance(checks, list)
        assert len(checks) > 0
        assert all(isinstance(c, HealthCheck) for c in checks)

    def test_with_config(self):
        config = {
            "user": {"name": "Test", "email": "test@test.com"},
            "models": {"default": "gpt-4.1"},
        }
        with patch("core.diagnostics._check_playwright_edge", _pw_skip):
            checks = run_health_check(config)
        identity = next(c for c in checks if "identity" in c.name)
        assert identity.ok

    def test_python_version_is_real(self):
        """Python version check reflects actual runtime."""
        with patch("core.diagnostics._check_playwright_edge", _pw_skip):
            checks = run_health_check(config=None)
        py = next(c for c in checks if "Python" in c.name)
        expected = sys.version_info.major >= 3 and sys.version_info.minor >= 12
        assert py.ok == expected

    def test_no_exception_on_missing_tools(self):
        """Doesn't crash if gh/node/npm are missing — reports FAIL."""
        with patch.dict(os.environ, {"PATH": ""}), \
             patch("core.diagnostics._check_playwright_edge", _pw_skip):
            checks = run_health_check(config=None)
        assert len(checks) > 0
        gh = next((c for c in checks if c.name == "CLI: gh"), None)
        if gh:
            assert not gh.ok


class TestIndividualChecksReal:
    """Each check function called for real."""

    def test_check_gh_auth(self):
        result = _check_gh_auth()
        assert isinstance(result, HealthCheck)
        assert "GitHub" in result.name

    def test_check_pulse_home(self):
        result = _check_pulse_home()
        assert isinstance(result, HealthCheck)
        assert "PULSE_HOME" in result.name

    def test_check_config_none(self):
        results = _check_config(None)
        assert len(results) == 1
        assert not results[0].ok

    def test_check_config_complete(self):
        config = {
            "user": {"name": "Alice", "email": "alice@example.com"},
            "models": {"default": "gpt-4.1"},
        }
        results = _check_config(config)
        assert len(results) == 2
        assert all(c.ok for c in results)


class TestLoginPageDetectionReal:
    """_is_login_page with real URLs — no mocking needed."""

    def test_real_microsoft_urls(self):
        assert _is_login_page("https://login.microsoftonline.com/common/oauth2/v2.0/authorize?client_id=abc")
        assert _is_login_page("https://login.live.com/oauth20_authorize.srf?client_id=abc&scope=openid")
        assert _is_login_page("https://login.microsoft.com/common/login?prompt=select_account")

    def test_real_teams_url(self):
        assert not _is_login_page("https://teams.microsoft.com/v2/")
        assert not _is_login_page("https://teams.cloud.microsoft/")

    def test_edge_cases(self):
        assert not _is_login_page("")
        assert not _is_login_page("about:blank")
        assert _is_login_page("https://example.com/oauth2/authorize")


# ---------------------------------------------------------------------------
# Real sync playwright launch — EXCLUDED from normal pytest runs.
# sync_playwright poisons the event loop for ALL subsequent async tests
# in the entire pytest session (not just this file). Run manually:
#   python -c "from core.diagnostics import _check_playwright_edge; print(_check_playwright_edge())"
# ---------------------------------------------------------------------------
