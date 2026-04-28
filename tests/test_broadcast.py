"""Tests for broadcast_to_team tool -- fan-out to all configured teammates."""
from pathlib import Path
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
        # reply_to must point to the sender's own inbox so responses route back
        assert Path(data["reply_to"]) == team_dir / "artur" / "jobs" / "pending"


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


@pytest.mark.asyncio
async def test_broadcast_all_folders_inaccessible_returns_error(tmp_path):
    """When every teammate's folder is missing, return ERROR so LLM sees the failure."""
    team_dir = tmp_path / "Pulse-Team"
    # Neither alpha nor beta folder exists
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
            "question": "What do you know about the Contoso deal?",
            "project_id": "contoso-deal",
        }})

    assert "ERROR" in result["textResultForLlm"]
    assert "broadcast failed" in result["textResultForLlm"]


# --- Landing-zone enforcement ---
# broadcast_to_team must guarantee that projects/{project_id}.yaml exists before
# sending. Otherwise the asker's worker will silently drop every reply. See
# _ingest_agent_response in src/daemon/worker.py.


@pytest.mark.asyncio
async def test_broadcast_creates_project_stub_when_missing(tmp_team, tmp_path):
    """If the target project YAML doesn't exist, broadcast seeds a minimal stub.

    The stub lands replies via team_context and carries origin='broadcast' so
    downstream tooling can distinguish it from fully-formed projects.
    """
    team_dir, config = tmp_team
    projects_dir = tmp_path / "projects"
    with patch("core.config.load_config", return_value=config), \
         patch("sdk.tools.PULSE_TEAM_DIR", team_dir), \
         patch("sdk.tools.PROJECTS_DIR", projects_dir):
        result = await broadcast_to_team.handler({"arguments": {
            "question": "Any context on Fabric on SAP?",
            "project_id": "fabric-sap",
        }})

    assert result["resultType"] == "success"
    assert "2 teammates" in result["textResultForLlm"]

    stub_path = projects_dir / "fabric-sap.yaml"
    assert stub_path.exists(), "broadcast must create a landing zone for replies"
    data = yaml.safe_load(stub_path.read_text())
    assert data["origin"] == "broadcast"
    assert data["status"] == "active"
    assert "Fabric on SAP" in data["summary"]
    # Must be ready to accept ingested replies
    assert data["team_context"] == []


@pytest.mark.asyncio
async def test_broadcast_preserves_existing_project(tmp_team, tmp_path):
    """If the project YAML already exists, broadcast leaves it untouched."""
    team_dir, config = tmp_team
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    existing = {
        "project": "Fabric on SAP",
        "status": "active",
        "stakeholders": [{"name": "Jane", "role": "PM"}],
        "team_context": [{"from": "Old", "request_id": "r1", "answer": "prior"}],
    }
    (projects_dir / "fabric-sap.yaml").write_text(yaml.dump(existing))

    with patch("core.config.load_config", return_value=config), \
         patch("sdk.tools.PULSE_TEAM_DIR", team_dir), \
         patch("sdk.tools.PROJECTS_DIR", projects_dir):
        result = await broadcast_to_team.handler({"arguments": {
            "question": "New question",
            "project_id": "fabric-sap",
        }})

    assert result["resultType"] == "success"
    data = yaml.safe_load((projects_dir / "fabric-sap.yaml").read_text())
    # Pre-existing fields untouched — broadcast doesn't overwrite real projects
    assert data["stakeholders"] == [{"name": "Jane", "role": "PM"}]
    assert data["team_context"] == [{"from": "Old", "request_id": "r1", "answer": "prior"}]
    assert "origin" not in data  # no stub markers written on top of real projects


@pytest.mark.asyncio
async def test_broadcast_blocks_on_similar_project_slug(tmp_team, tmp_path):
    """Duplicate-slug guard: refuse to stub a new slug that overlaps an existing project.

    Mirrors update_project's BLOCKED response so the agent retries with the
    existing project_id and replies accrete into the real project instead of a
    sibling stub.
    """
    team_dir, config = tmp_team
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    (projects_dir / "fabric-sap-contoso.yaml").write_text(
        yaml.dump({"project": "Fabric on SAP Contoso", "status": "active"})
    )

    with patch("core.config.load_config", return_value=config), \
         patch("sdk.tools.PULSE_TEAM_DIR", team_dir), \
         patch("sdk.tools.PROJECTS_DIR", projects_dir):
        result = await broadcast_to_team.handler({"arguments": {
            "question": "More on Fabric on SAP",
            "project_id": "fabric-sap-pricing",
        }})

    text = result["textResultForLlm"]
    assert "BLOCKED" in text
    assert "fabric-sap-contoso" in text
    # Nothing was sent and no stub was written
    assert not (projects_dir / "fabric-sap-pricing.yaml").exists()
    alpha_files = list((team_dir / "alpha" / "jobs" / "pending").glob("*.yaml"))
    beta_files = list((team_dir / "beta" / "jobs" / "pending").glob("*.yaml"))
    assert alpha_files == [] and beta_files == []
