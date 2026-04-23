"""End-to-end contract test for the cross-agent flow.

Two fake PULSE_HOMEs in temp dirs. The SDK is mocked; this test validates
the file-plumbing and data contracts, not LLM behavior.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from sdk.tools import broadcast_to_team
from daemon.worker import _handle_agent_request, _ingest_agent_response


@pytest.mark.asyncio
async def test_full_loop_broadcast_guardian_ingest(tmp_path, monkeypatch):
    """
    Sender (artur) broadcasts, teammate (beta) YAML dropped,
    Guardian session (mocked) drafts answer, response YAML lands in artur's inbox,
    ingestion appends to artur's project YAML.
    """
    # --- Set up sender's world (artur) ---
    artur_home = tmp_path / "artur-home"
    artur_home.mkdir()
    (artur_home / "projects").mkdir()
    artur_projects = artur_home / "projects"
    (artur_projects / "fabric-sap.yaml").write_text(
        yaml.dump({"project": "Fabric SAP", "status": "active"}, default_flow_style=False),
        encoding="utf-8",
    )

    # --- Set up teammate's world (beta) ---
    team_dir = tmp_path / "Pulse-Team"
    (team_dir / "beta").mkdir(parents=True)
    (team_dir / "artur").mkdir(parents=True)  # artur's own inbox for responses

    # --- Config seen by sender ---
    sender_config = {
        "team": [{"name": "Beta User", "alias": "beta"}],
        "user": {"name": "Artur Zielinski", "alias": "artur"},
    }

    # --- Step 1: Broadcast from artur ---
    with patch("core.config.load_config", return_value=sender_config), \
         patch("sdk.tools.PULSE_TEAM_DIR", team_dir):
        result = await broadcast_to_team.handler({"arguments": {
            "question": "Prior Fabric-on-SAP objections?",
            "project_id": "fabric-sap",
        }})
    assert result["resultType"] == "success"

    # Verify teammate got the YAML
    beta_pending = team_dir / "beta" / "jobs" / "pending"
    beta_files = list(beta_pending.glob("*.yaml"))
    assert len(beta_files) == 1
    beta_job = yaml.safe_load(beta_files[0].read_text())
    assert beta_job["kind"] == "broadcast"
    assert beta_job["project_id"] == "fabric-sap"
    assert "artur" in beta_job["reply_to"]

    # --- Step 2: Beta's worker runs Guardian session (mocked LLM response) ---
    fake_guardian_output = (
        '```json\n'
        '{"status": "answered", "result": "3 POCs; licensing main objection.", "sources": ["transcripts/demo.md"]}\n'
        '```'
    )

    beta_config = {"user": {"name": "Beta User", "alias": "beta"}}
    beta_job["_file"] = str(beta_files[0])

    fake_run = AsyncMock(return_value=fake_guardian_output)
    monkeypatch.setattr("daemon.worker._run_guardian_session", fake_run)

    await _handle_agent_request(MagicMock(), beta_config, beta_job)

    # Response YAML should have landed in artur's inbox
    artur_pending = team_dir / "artur" / "jobs" / "pending"
    response_files = list(artur_pending.glob("*-response-*.yaml"))
    assert len(response_files) == 1
    response = yaml.safe_load(response_files[0].read_text())
    assert response["type"] == "agent_response"
    assert response["status"] == "answered"
    assert response["project_id"] == "fabric-sap"
    assert "POCs" in response["result"]

    # --- Step 3: Artur ingests the response ---
    response["_file"] = str(response_files[0])
    monkeypatch.setattr("daemon.worker.PROJECTS_DIR", artur_projects)
    _ingest_agent_response(response)

    # Project YAML should now have team_context entry
    final = yaml.safe_load((artur_projects / "fabric-sap.yaml").read_text())
    assert "team_context" in final
    assert len(final["team_context"]) == 1
    entry = final["team_context"][0]
    assert entry["from"] == "Beta User"
    assert entry["answer"].startswith("3 POCs")
    assert entry["sources"] == ["transcripts/demo.md"]
