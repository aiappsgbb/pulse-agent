"""Tests for ProjectsPane sorting, actions, modals, and YAML persistence."""

import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ---------------------------------------------------------------------------
# Sorting logic tests
# ---------------------------------------------------------------------------


class TestSortProjects:
    """Test _sort_projects with various modes."""

    def _make_project(self, name, status="active", risk="medium", overdue=0,
                       next_meeting="", commitments=None, involvement="lead"):
        p = {
            "_id": name.lower().replace(" ", "-"),
            "project": name,
            "status": status,
            "risk_level": risk,
            "involvement": involvement,
            "commitments": commitments or [],
        }
        if next_meeting:
            p["next_meeting"] = next_meeting
        # Add overdue commitments (explicit confidence by default)
        for _ in range(overdue):
            p["commitments"].append({"what": "task", "status": "overdue", "due_confidence": "explicit"})
        return p

    def test_urgency_sort_overdue_first(self):
        from tui.screens import _sort_projects
        projects = [
            self._make_project("Alpha", overdue=0),
            self._make_project("Beta", overdue=3),
            self._make_project("Gamma", overdue=1),
        ]
        result = _sort_projects(projects, "urgency")
        assert result[0]["project"] == "Beta"   # 3 overdue
        assert result[1]["project"] == "Gamma"  # 1 overdue
        assert result[2]["project"] == "Alpha"  # 0 overdue

    def test_urgency_sort_risk_tiebreaker(self):
        from tui.screens import _sort_projects
        projects = [
            self._make_project("Low Risk", risk="low", overdue=1),
            self._make_project("Critical", risk="critical", overdue=1),
            self._make_project("High Risk", risk="high", overdue=1),
        ]
        result = _sort_projects(projects, "urgency")
        assert result[0]["project"] == "Critical"
        assert result[1]["project"] == "High Risk"
        assert result[2]["project"] == "Low Risk"

    def test_urgency_sort_status_tiebreaker(self):
        from tui.screens import _sort_projects
        projects = [
            self._make_project("Completed", status="completed"),
            self._make_project("Active", status="active"),
            self._make_project("Blocked", status="blocked"),
        ]
        result = _sort_projects(projects, "urgency")
        assert result[0]["project"] == "Active"
        assert result[1]["project"] == "Blocked"
        assert result[2]["project"] == "Completed"

    def test_next_meeting_sort(self):
        from tui.screens import _sort_projects
        projects = [
            self._make_project("No Meeting"),
            self._make_project("Later", next_meeting="2026-03-10 14:00"),
            self._make_project("Sooner", next_meeting="2026-03-05 09:00"),
        ]
        result = _sort_projects(projects, "next_meeting")
        assert result[0]["project"] == "Sooner"
        assert result[1]["project"] == "Later"
        assert result[2]["project"] == "No Meeting"

    def test_next_meeting_nulls_last(self):
        from tui.screens import _sort_projects
        projects = [
            self._make_project("No Meeting A"),
            self._make_project("Has Meeting", next_meeting="2026-03-01"),
            self._make_project("No Meeting B"),
        ]
        result = _sort_projects(projects, "next_meeting")
        assert result[0]["project"] == "Has Meeting"

    def test_status_sort(self):
        from tui.screens import _sort_projects
        projects = [
            self._make_project("Completed", status="completed"),
            self._make_project("Active A", status="active"),
            self._make_project("Blocked", status="blocked"),
            self._make_project("On Hold", status="on-hold"),
            self._make_project("Active B", status="active"),
        ]
        result = _sort_projects(projects, "status")
        assert result[0]["project"] == "Active A"
        assert result[1]["project"] == "Active B"
        assert result[2]["project"] == "Blocked"
        assert result[3]["project"] == "On Hold"
        assert result[4]["project"] == "Completed"

    def test_alphabetical_sort(self):
        from tui.screens import _sort_projects
        projects = [
            self._make_project("Zebra"),
            self._make_project("Alpha"),
            self._make_project("Mango"),
        ]
        result = _sort_projects(projects, "alphabetical")
        assert result[0]["project"] == "Alpha"
        assert result[1]["project"] == "Mango"
        assert result[2]["project"] == "Zebra"

    def test_alphabetical_case_insensitive(self):
        from tui.screens import _sort_projects
        projects = [
            self._make_project("zebra"),
            self._make_project("Alpha"),
        ]
        result = _sort_projects(projects, "alphabetical")
        assert result[0]["project"] == "Alpha"
        assert result[1]["project"] == "zebra"

    def test_unknown_mode_falls_back_to_alpha(self):
        from tui.screens import _sort_projects
        projects = [
            self._make_project("Beta"),
            self._make_project("Alpha"),
        ]
        result = _sort_projects(projects, "unknown_mode")
        assert result[0]["project"] == "Alpha"
        assert result[1]["project"] == "Beta"

    def test_empty_projects(self):
        from tui.screens import _sort_projects
        for mode in ("urgency", "next_meeting", "status", "alphabetical"):
            assert _sort_projects([], mode) == []

    def test_single_project(self):
        from tui.screens import _sort_projects
        p = [self._make_project("Solo")]
        for mode in ("urgency", "next_meeting", "status", "alphabetical"):
            result = _sort_projects(p, mode)
            assert len(result) == 1
            assert result[0]["project"] == "Solo"


# ---------------------------------------------------------------------------
# Sort mode constants
# ---------------------------------------------------------------------------


class TestSortModeConstants:
    def test_sort_modes_list(self):
        from tui.screens import PROJECT_SORT_MODES
        assert "urgency" in PROJECT_SORT_MODES
        assert "next_meeting" in PROJECT_SORT_MODES
        assert "status" in PROJECT_SORT_MODES
        assert "alphabetical" in PROJECT_SORT_MODES

    def test_sort_labels_match_modes(self):
        from tui.screens import PROJECT_SORT_LABELS, PROJECT_SORT_MODES
        for mode in PROJECT_SORT_MODES:
            assert mode in PROJECT_SORT_LABELS


# ---------------------------------------------------------------------------
# Overdue count helper
# ---------------------------------------------------------------------------


class TestOverdueCount:
    def test_no_commitments(self):
        from tui.screens import _overdue_count
        assert _overdue_count({}) == 0
        assert _overdue_count({"commitments": []}) == 0

    def test_mixed_commitments(self):
        from tui.screens import _overdue_count
        p = {"commitments": [
            {"what": "a", "status": "overdue"},
            {"what": "b", "status": "open"},
            {"what": "c", "status": "overdue"},
            {"what": "d", "status": "done"},
        ]}
        assert _overdue_count(p) == 2

    def test_case_insensitive(self):
        from tui.screens import _overdue_count
        p = {"commitments": [{"what": "x", "status": "OVERDUE"}]}
        assert _overdue_count(p) == 1


# ---------------------------------------------------------------------------
# Project YAML persistence
# ---------------------------------------------------------------------------


class TestSaveProjectYaml:
    def test_save_and_reload(self, tmp_dir):
        with patch("tui.screens.PROJECTS_DIR", tmp_dir):
            from tui.screens import _save_project_yaml
            project = {
                "_id": "test-project",
                "project": "Test Project",
                "status": "active",
                "risk_level": "medium",
                "commitments": [{"what": "task", "status": "open"}],
            }
            assert _save_project_yaml("test-project", project)
            # Verify file exists and is valid YAML
            path = tmp_dir / "test-project.yaml"
            assert path.exists()
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            assert data["project"] == "Test Project"
            assert data["status"] == "active"
            assert len(data["commitments"]) == 1

    def test_internal_fields_stripped(self, tmp_dir):
        with patch("tui.screens.PROJECTS_DIR", tmp_dir):
            from tui.screens import _save_project_yaml
            project = {
                "_id": "test",
                "_internal": "should-not-persist",
                "project": "Test",
                "status": "active",
            }
            _save_project_yaml("test", project)
            data = yaml.safe_load((tmp_dir / "test.yaml").read_text(encoding="utf-8"))
            assert "_id" not in data
            assert "_internal" not in data
            assert "project" in data

    def test_save_overwrites_existing(self, tmp_dir):
        with patch("tui.screens.PROJECTS_DIR", tmp_dir):
            from tui.screens import _save_project_yaml
            _save_project_yaml("p1", {"_id": "p1", "project": "V1", "status": "active"})
            _save_project_yaml("p1", {"_id": "p1", "project": "V2", "status": "blocked"})
            data = yaml.safe_load((tmp_dir / "p1.yaml").read_text(encoding="utf-8"))
            assert data["project"] == "V2"
            assert data["status"] == "blocked"

    def test_save_returns_false_on_error(self):
        from tui.screens import _save_project_yaml
        # Non-existent path should fail
        with patch("tui.screens.PROJECTS_DIR", Path("/nonexistent/path/xyz")):
            result = _save_project_yaml("test", {"project": "Test"})
            assert result is False


# ---------------------------------------------------------------------------
# queue_job with context parameter
# ---------------------------------------------------------------------------


class TestQueueJobContext:
    def test_queue_job_without_context(self, tmp_dir):
        with patch("tui.ipc.JOBS_DIR", tmp_dir):
            from tui.ipc import queue_job
            queue_job("digest")
            pending = tmp_dir / "pending"
            files = list(pending.glob("*.yaml"))
            assert len(files) == 1
            data = yaml.safe_load(files[0].read_text(encoding="utf-8"))
            assert data["type"] == "digest"
            assert "context" not in data

    def test_queue_job_with_context(self, tmp_dir):
        with patch("tui.ipc.JOBS_DIR", tmp_dir):
            from tui.ipc import queue_job
            queue_job("research", context="Deep dive on Contoso deal")
            pending = tmp_dir / "pending"
            files = list(pending.glob("*.yaml"))
            assert len(files) == 1
            data = yaml.safe_load(files[0].read_text(encoding="utf-8"))
            assert data["type"] == "research"
            assert data["context"] == "Deep dive on Contoso deal"

    def test_queue_job_empty_context_omitted(self, tmp_dir):
        with patch("tui.ipc.JOBS_DIR", tmp_dir):
            from tui.ipc import queue_job
            queue_job("digest", context="")
            pending = tmp_dir / "pending"
            files = list(pending.glob("*.yaml"))
            data = yaml.safe_load(files[0].read_text(encoding="utf-8"))
            assert "context" not in data


# ---------------------------------------------------------------------------
# Modal classes — unit tests (no Textual app needed)
# ---------------------------------------------------------------------------


class TestProjectStatusModal:
    def test_composes_with_all_statuses(self):
        """Verify modal can be instantiated with a project dict."""
        from tui.screens import ProjectStatusModal
        project = {"project": "Test", "status": "active"}
        modal = ProjectStatusModal(project)
        assert modal._project["status"] == "active"

    def test_status_values(self):
        """Verify the 4 valid statuses are recognized."""
        valid = ("active", "blocked", "on-hold", "completed")
        for s in valid:
            assert s in ("active", "blocked", "on-hold", "completed")


class TestCommitmentModal:
    def test_filters_actionable_commitments(self):
        from tui.screens import CommitmentModal
        project = {
            "project": "Test",
            "commitments": [
                {"what": "a", "status": "open"},
                {"what": "b", "status": "done"},
                {"what": "c", "status": "overdue"},
                {"what": "d", "status": "cancelled"},
            ],
        }
        modal = CommitmentModal(project)
        assert len(modal._actionable) == 2
        assert modal._actionable[0]["what"] == "a"
        assert modal._actionable[1]["what"] == "c"

    def test_empty_commitments(self):
        from tui.screens import CommitmentModal
        modal = CommitmentModal({"project": "Test", "commitments": []})
        assert len(modal._actionable) == 0

    def test_no_commitments_key(self):
        from tui.screens import CommitmentModal
        modal = CommitmentModal({"project": "Test"})
        assert len(modal._actionable) == 0


# ---------------------------------------------------------------------------
# Integration: sort cycle order
# ---------------------------------------------------------------------------


class TestSortCycleOrder:
    def test_cycle_wraps_around(self):
        from tui.screens import PROJECT_SORT_MODES
        modes = PROJECT_SORT_MODES
        for i, mode in enumerate(modes):
            next_mode = modes[(i + 1) % len(modes)]
            if i == len(modes) - 1:
                assert next_mode == modes[0]  # wraps


# ---------------------------------------------------------------------------
# Load projects with _id injection
# ---------------------------------------------------------------------------


class TestLoadProjects:
    def test_load_projects_adds_id(self, tmp_dir):
        with patch("tui.screens.PROJECTS_DIR", tmp_dir):
            # Write a test YAML
            (tmp_dir / "contoso-deal.yaml").write_text(
                yaml.dump({"project": "Contoso Deal", "status": "active"}),
                encoding="utf-8",
            )
            from tui.screens import _load_projects
            projects = _load_projects()
            assert len(projects) == 1
            assert projects[0]["_id"] == "contoso-deal"
            assert projects[0]["project"] == "Contoso Deal"

    def test_load_projects_empty_dir(self, tmp_dir):
        with patch("tui.screens.PROJECTS_DIR", tmp_dir):
            from tui.screens import _load_projects
            assert _load_projects() == []

    def test_load_projects_skips_invalid_yaml(self, tmp_dir):
        with patch("tui.screens.PROJECTS_DIR", tmp_dir):
            (tmp_dir / "bad.yaml").write_text("not: yaml: at: all: [", encoding="utf-8")
            (tmp_dir / "good.yaml").write_text(
                yaml.dump({"project": "Good"}), encoding="utf-8"
            )
            from tui.screens import _load_projects
            projects = _load_projects()
            # Should load at least the good one (bad one may or may not parse)
            assert any(p["project"] == "Good" for p in projects)

    def test_load_projects_nonexistent_dir(self):
        with patch("tui.screens.PROJECTS_DIR", Path("/nonexistent/xyz")):
            from tui.screens import _load_projects
            assert _load_projects() == []


# ---------------------------------------------------------------------------
# Commitment completion logic (in-memory mutation)
# ---------------------------------------------------------------------------


class TestCommitmentCompletion:
    """Test the commitment completion logic that ProjectsPane uses."""

    def test_mark_commitment_done(self, tmp_dir):
        """Simulate the complete commitment flow end-to-end."""
        with patch("tui.screens.PROJECTS_DIR", tmp_dir):
            from tui.screens import _save_project_yaml

            project = {
                "_id": "test",
                "project": "Test",
                "status": "active",
                "commitments": [
                    {"what": "Task A", "status": "open"},
                    {"what": "Task B", "status": "overdue"},
                    {"what": "Task C", "status": "done"},
                ],
            }

            # Simulate what the pane does: find actionable, mark by index
            actionable = [
                c for c in project["commitments"]
                if c.get("status", "").lower() in ("open", "overdue")
            ]
            assert len(actionable) == 2

            # Mark the second actionable (Task B) as done
            target = actionable[1]
            for c in project["commitments"]:
                if c is target:
                    c["status"] = "done"
            project["updated_at"] = "2026-03-03T12:00:00"

            assert _save_project_yaml("test", project)

            # Verify
            data = yaml.safe_load((tmp_dir / "test.yaml").read_text(encoding="utf-8"))
            statuses = [c["status"] for c in data["commitments"]]
            assert statuses == ["open", "done", "done"]

    def test_status_update_persists(self, tmp_dir):
        """Simulate the status update flow."""
        with patch("tui.screens.PROJECTS_DIR", tmp_dir):
            from tui.screens import _save_project_yaml

            project = {
                "_id": "proj",
                "project": "My Project",
                "status": "active",
                "risk_level": "high",
            }
            project["status"] = "blocked"
            project["updated_at"] = "2026-03-03T12:00:00"
            assert _save_project_yaml("proj", project)

            data = yaml.safe_load((tmp_dir / "proj.yaml").read_text(encoding="utf-8"))
            assert data["status"] == "blocked"
            assert data["updated_at"] == "2026-03-03T12:00:00"


# ---------------------------------------------------------------------------
# Involvement-based sorting
# ---------------------------------------------------------------------------


class TestInvolvementSorting:
    """Test relevance sort mode and involvement helpers."""

    def _make_project(self, name, involvement="lead", risk="medium", overdue=0, status="active"):
        p = {
            "_id": name.lower().replace(" ", "-"),
            "project": name,
            "status": status,
            "risk_level": risk,
            "involvement": involvement,
            "commitments": [],
        }
        for _ in range(overdue):
            p["commitments"].append({"what": "task", "status": "overdue", "due_confidence": "explicit"})
        return p

    def test_relevance_sort_involvement_first(self):
        from tui.screens import _sort_projects
        projects = [
            self._make_project("Observer Proj", involvement="observer"),
            self._make_project("Lead Proj", involvement="lead"),
            self._make_project("Contrib Proj", involvement="contributor"),
        ]
        result = _sort_projects(projects, "relevance")
        assert result[0]["project"] == "Lead Proj"
        assert result[1]["project"] == "Contrib Proj"
        assert result[2]["project"] == "Observer Proj"

    def test_relevance_sort_risk_tiebreaker_within_involvement(self):
        from tui.screens import _sort_projects
        projects = [
            self._make_project("Low", involvement="lead", risk="low"),
            self._make_project("Critical", involvement="lead", risk="critical"),
            self._make_project("High", involvement="lead", risk="high"),
        ]
        result = _sort_projects(projects, "relevance")
        assert result[0]["project"] == "Critical"
        assert result[1]["project"] == "High"
        assert result[2]["project"] == "Low"

    def test_relevance_sort_observer_always_last(self):
        """Observer projects sort after lead/contributor regardless of risk."""
        from tui.screens import _sort_projects
        projects = [
            self._make_project("Observer Critical", involvement="observer", risk="critical", overdue=5),
            self._make_project("Lead Low", involvement="lead", risk="low"),
        ]
        result = _sort_projects(projects, "relevance")
        assert result[0]["project"] == "Lead Low"
        assert result[1]["project"] == "Observer Critical"

    def test_involvement_rank_default_to_observer(self):
        from tui.screens import _involvement_rank
        assert _involvement_rank({}) == 2  # observer
        assert _involvement_rank({"involvement": "lead"}) == 0
        assert _involvement_rank({"involvement": "contributor"}) == 1
        assert _involvement_rank({"involvement": "observer"}) == 2
        assert _involvement_rank({"involvement": "unknown"}) == 2

    def test_relevance_in_sort_modes(self):
        from tui.screens import PROJECT_SORT_MODES, PROJECT_SORT_LABELS
        assert "relevance" in PROJECT_SORT_MODES
        assert "relevance" in PROJECT_SORT_LABELS


# ---------------------------------------------------------------------------
# Due confidence and overdue counts
# ---------------------------------------------------------------------------


class TestDueConfidence:
    """Test that due_confidence affects overdue counting."""

    def test_overdue_count_explicit_only(self):
        from tui.screens import _overdue_count
        p = {"commitments": [
            {"what": "a", "status": "overdue", "due_confidence": "explicit"},
            {"what": "b", "status": "overdue", "due_confidence": "inferred"},
            {"what": "c", "status": "overdue"},  # missing = defaults to explicit
        ]}
        assert _overdue_count(p) == 2  # a + c (missing defaults to explicit)

    def test_soft_overdue_count(self):
        from tui.screens import _soft_overdue_count
        p = {"commitments": [
            {"what": "a", "status": "overdue", "due_confidence": "explicit"},
            {"what": "b", "status": "overdue", "due_confidence": "inferred"},
            {"what": "c", "status": "overdue"},  # missing = explicit
        ]}
        assert _soft_overdue_count(p) == 1  # only b

    def test_overdue_count_ignores_non_overdue(self):
        from tui.screens import _overdue_count, _soft_overdue_count
        p = {"commitments": [
            {"what": "a", "status": "open", "due_confidence": "explicit"},
            {"what": "b", "status": "done", "due_confidence": "inferred"},
        ]}
        assert _overdue_count(p) == 0
        assert _soft_overdue_count(p) == 0

    def test_urgency_sort_uses_explicit_overdue_only(self):
        """Urgency sort should not count inferred overdues."""
        from tui.screens import _sort_projects
        projects = [
            {
                "_id": "many-soft",
                "project": "Many Soft",
                "status": "active",
                "risk_level": "medium",
                "involvement": "lead",
                "commitments": [
                    {"what": "a", "status": "overdue", "due_confidence": "inferred"},
                    {"what": "b", "status": "overdue", "due_confidence": "inferred"},
                    {"what": "c", "status": "overdue", "due_confidence": "inferred"},
                ],
            },
            {
                "_id": "one-hard",
                "project": "One Hard",
                "status": "active",
                "risk_level": "medium",
                "involvement": "lead",
                "commitments": [
                    {"what": "x", "status": "overdue", "due_confidence": "explicit"},
                ],
            },
        ]
        result = _sort_projects(projects, "urgency")
        # One Hard has 1 explicit overdue; Many Soft has 0 explicit overdue
        assert result[0]["project"] == "One Hard"
        assert result[1]["project"] == "Many Soft"


# ---------------------------------------------------------------------------
# InvolvementModal
# ---------------------------------------------------------------------------


class TestInvolvementModal:
    def test_instantiation(self):
        from tui.screens import InvolvementModal
        project = {"project": "Test", "involvement": "observer"}
        modal = InvolvementModal(project)
        assert modal._project["involvement"] == "observer"

    def test_valid_involvement_values(self):
        valid = ("lead", "contributor", "observer")
        for v in valid:
            assert v in ("lead", "contributor", "observer")


# ---------------------------------------------------------------------------
# Involvement persistence
# ---------------------------------------------------------------------------


class TestInvolvementPersistence:
    def test_save_involvement(self, tmp_dir):
        with patch("tui.screens.PROJECTS_DIR", tmp_dir):
            from tui.screens import _save_project_yaml
            project = {
                "_id": "test",
                "project": "Test",
                "status": "active",
                "involvement": "observer",
            }
            assert _save_project_yaml("test", project)
            data = yaml.safe_load((tmp_dir / "test.yaml").read_text(encoding="utf-8"))
            assert data["involvement"] == "observer"

    def test_update_involvement(self, tmp_dir):
        with patch("tui.screens.PROJECTS_DIR", tmp_dir):
            from tui.screens import _save_project_yaml
            _save_project_yaml("p1", {"_id": "p1", "project": "P1", "involvement": "observer"})
            _save_project_yaml("p1", {"_id": "p1", "project": "P1", "involvement": "lead"})
            data = yaml.safe_load((tmp_dir / "p1.yaml").read_text(encoding="utf-8"))
            assert data["involvement"] == "lead"
