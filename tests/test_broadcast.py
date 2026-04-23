"""Tests for broadcast_to_team tool -- fan-out to all configured teammates."""
from unittest.mock import patch

import pytest
import yaml

from sdk.tools import broadcast_to_team


@pytest.fixture
def tmp_team(tmp_path):
    """Two teammates with PULSE_TEAM_DIR/{alias}/ folders ready."""
    team_dir = tmp_path / "Pulse-Team"
    (team_dir / "alpha").mkdir(parents=True)
    (team_dir / "beta").mkdir(parents=True)
    config = {
        "team": [
            {"name": "Alpha User", "alias": "alpha"},
            {"name": "Beta User", "alias": "beta"},
        ],
        "user": {"name": "Artur Zielinski", "alias": "artur"},
    }
    return team_dir, config


@pytest.mark.asyncio
async def test_broadcast_fans_out_to_all_teammates(tmp_team):
    team_dir, config = tmp_team
    with patch("core.config.load_config", return_value=config), \
         patch("sdk.tools.PULSE_TEAM_DIR", team_dir):
        result = await broadcast_to_team.handler({"arguments": {
            "question": "What do we know about Fabric-on-SAP objections?",
            "project_id": "fabric-sap-engagement",
        }})

    assert result["resultType"] == "success"
    assert "2 teammates" in result["textResultForLlm"]

    for alias in ("alpha", "beta"):
        jobs_dir = team_dir / alias / "jobs" / "pending"
        files = list(jobs_dir.glob("*.yaml"))
        assert len(files) == 1, f"expected 1 yaml for {alias}, got {len(files)}"
        data = yaml.safe_load(files[0].read_text())
        assert data["type"] == "agent_request"
        assert data["kind"] == "broadcast"
        assert data["project_id"] == "fabric-sap-engagement"
        assert data["from_alias"] == "artur"
        assert "Fabric-on-SAP" in data["task"]
        assert data["request_id"]  # UUID set


@pytest.mark.asyncio
async def test_broadcast_rejects_missing_project_id(tmp_team):
    _, config = tmp_team
    with patch("core.config.load_config", return_value=config):
        result = await broadcast_to_team.handler({"arguments": {
            "question": "anything",
            "project_id": "",
        }})
    assert "ERROR" in result["textResultForLlm"]
    assert "project_id" in result["textResultForLlm"]


@pytest.mark.asyncio
async def test_broadcast_empty_team_returns_clear_error(tmp_path):
    config = {"team": [], "user": {"name": "X", "alias": "x"}}
    with patch("core.config.load_config", return_value=config):
        result = await broadcast_to_team.handler({"arguments": {
            "question": "hello",
            "project_id": "any-project",
        }})
    assert "ERROR" in result["textResultForLlm"]
    assert "no teammates" in result["textResultForLlm"].lower()


@pytest.mark.asyncio
async def test_broadcast_skips_inaccessible_teammate_folders(tmp_path):
    """If one teammate's folder does not exist, skip and continue."""
    team_dir = tmp_path / "Pulse-Team"
    (team_dir / "alpha").mkdir(parents=True)  # alpha exists
    # beta does NOT exist - should be skipped, not crash
    config = {
        "team": [
            {"name": "Alpha", "alias": "alpha"},
            {"name": "Beta", "alias": "beta"},
        ],
        "user": {"name": "Artur", "alias": "artur"},
    }
    with patch("core.config.load_config", return_value=config), \
         patch("sdk.tools.PULSE_TEAM_DIR", team_dir):
        result = await broadcast_to_team.handler({"arguments": {
            "question": "hi",
            "project_id": "some-project",
        }})

    # Succeeded for alpha; beta was skipped
    assert result["resultType"] == "success"
    assert "1 teammate" in result["textResultForLlm"]
    assert "skipped" in result["textResultForLlm"].lower()
    assert "beta" in result["textResultForLlm"]
    # Verify only alpha got a YAML
    alpha_files = list((team_dir / "alpha" / "jobs" / "pending").glob("*.yaml"))
    assert len(alpha_files) == 1
