"""Tests for agent_response -> project YAML ingestion."""

import pytest
import yaml

from daemon.worker import _ingest_agent_response


def _write_project(projects_dir, project_id, data=None):
    data = data or {"project": project_id, "status": "active"}
    (projects_dir / f"{project_id}.yaml").write_text(
        yaml.dump(data, default_flow_style=False), encoding="utf-8"
    )


@pytest.fixture
def project_dir(tmp_path, monkeypatch):
    d = tmp_path / "projects"
    d.mkdir()
    monkeypatch.setattr("daemon.worker.PROJECTS_DIR", d)
    return d


def test_ingest_answered_response_appends_team_context(project_dir):
    _write_project(project_dir, "fabric-sap", {
        "project": "Fabric on SAP", "status": "active",
    })
    job = {
        "type": "agent_response",
        "status": "answered",
        "project_id": "fabric-sap",
        "request_id": "req-abc",
        "from": "Beta User",
        "from_alias": "beta",
        "original_task": "any prior objections data?",
        "result": "3 POCs; licensing was the main objection.",
        "sources": ["transcripts/2026-01-15.md"],
        "created_at": "2026-04-23T10:00:00",
    }

    _ingest_agent_response(job)

    data = yaml.safe_load((project_dir / "fabric-sap.yaml").read_text())
    assert "team_context" in data
    assert len(data["team_context"]) == 1
    entry = data["team_context"][0]
    assert entry["from"] == "Beta User"
    assert entry["request_id"] == "req-abc"
    assert entry["answer"] == "3 POCs; licensing was the main objection."
    assert entry["sources"] == ["transcripts/2026-01-15.md"]


def test_ingest_no_context_response_is_dropped(project_dir):
    _write_project(project_dir, "fabric-sap")
    job = {
        "status": "no_context",
        "project_id": "fabric-sap",
        "request_id": "req-skip",
    }
    _ingest_agent_response(job)
    data = yaml.safe_load((project_dir / "fabric-sap.yaml").read_text())
    assert data.get("team_context", []) == []


def test_ingest_duplicate_request_id_is_deduped(project_dir):
    _write_project(project_dir, "fabric-sap", {
        "project": "Fabric on SAP",
        "status": "active",
        "team_context": [
            {"from": "Beta", "request_id": "req-abc", "answer": "existing", "sources": []}
        ],
    })
    job = {
        "status": "answered",
        "project_id": "fabric-sap",
        "request_id": "req-abc",
        "from": "Beta",
        "from_alias": "beta",
        "result": "new answer (should be ignored)",
        "sources": [],
        "created_at": "2026-04-23T10:00:00",
    }
    _ingest_agent_response(job)
    data = yaml.safe_load((project_dir / "fabric-sap.yaml").read_text())
    assert len(data["team_context"]) == 1
    assert data["team_context"][0]["answer"] == "existing"


def test_ingest_missing_project_is_logged_not_crashed(project_dir, caplog):
    job = {
        "status": "answered",
        "project_id": "nonexistent-project",
        "request_id": "req-orphan",
        "from": "Beta",
        "from_alias": "beta",
        "result": "answer",
        "sources": [],
        "created_at": "2026-04-23T10:00:00",
    }
    # Should not raise
    _ingest_agent_response(job)
    # No project file should be created
    assert not (project_dir / "nonexistent-project.yaml").exists()
    # Verify the warning log was emitted
    assert any(
        "not found" in rec.message.lower() or "dropping" in rec.message.lower()
        for rec in caplog.records
    )


def test_ingest_declined_response_is_dropped(project_dir):
    _write_project(project_dir, "fabric-sap")
    job = {
        "status": "declined",
        "project_id": "fabric-sap",
        "request_id": "req-declined",
        "reason": "too sensitive",
    }
    _ingest_agent_response(job)
    data = yaml.safe_load((project_dir / "fabric-sap.yaml").read_text())
    assert data.get("team_context", []) == []


def test_ingest_preserves_other_project_fields(project_dir):
    _write_project(project_dir, "fabric-sap", {
        "project": "Fabric on SAP",
        "status": "active",
        "stakeholders": [{"name": "Jane Doe", "role": "PM"}],
        "commitments": [{"what": "send proposal", "due": "2026-05-01"}],
    })
    job = {
        "status": "answered",
        "project_id": "fabric-sap",
        "request_id": "req-new",
        "from": "Beta",
        "from_alias": "beta",
        "result": "something new",
        "sources": ["a.md"],
        "created_at": "2026-04-23T10:00:00",
    }
    _ingest_agent_response(job)
    data = yaml.safe_load((project_dir / "fabric-sap.yaml").read_text())
    assert data["project"] == "Fabric on SAP"
    assert data["status"] == "active"
    assert data["stakeholders"] == [{"name": "Jane Doe", "role": "PM"}]
    assert data["commitments"] == [{"what": "send proposal", "due": "2026-05-01"}]
    assert len(data["team_context"]) == 1
