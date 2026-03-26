"""Tests for optional MSX-MCP enrichment integration.

Validates that MSX enrichment is additive-only — everything works identically
when MSX is not installed. No regressions for non-MSX users.
"""

from unittest.mock import patch
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# 1. is_msx_available() detection
# ---------------------------------------------------------------------------


class TestMsxAvailability:
    """Test MSX-MCP plugin detection."""

    def test_is_msx_available_when_direct_install_exists(self, tmp_path):
        """Returns True when _direct/MSX-MCP-main directory exists."""
        from sdk.agents import is_msx_available

        plugin_dir = tmp_path / ".copilot" / "installed-plugins" / "_direct" / "MSX-MCP-main"
        plugin_dir.mkdir(parents=True)

        with patch("sdk.agents.Path.home", return_value=tmp_path):
            assert is_msx_available() is True

    def test_is_msx_available_when_marketplace_install_exists(self, tmp_path):
        """Returns True when copilot-plugins/msx-mcp directory exists."""
        from sdk.agents import is_msx_available

        plugin_dir = tmp_path / ".copilot" / "installed-plugins" / "copilot-plugins" / "msx-mcp"
        plugin_dir.mkdir(parents=True)

        with patch("sdk.agents.Path.home", return_value=tmp_path):
            assert is_msx_available() is True

    def test_is_msx_available_when_not_installed(self, tmp_path):
        """Returns False when neither install path exists."""
        from sdk.agents import is_msx_available

        with patch("sdk.agents.Path.home", return_value=tmp_path):
            assert is_msx_available() is False

    def test_diagnostics_uses_is_msx_available(self):
        """Diagnostics delegates to the shared is_msx_available() function."""
        from core.diagnostics import _check_msx_mcp_plugin

        with patch("sdk.agents.is_msx_available", return_value=True):
            assert _check_msx_mcp_plugin() is True

        with patch("sdk.agents.is_msx_available", return_value=False):
            assert _check_msx_mcp_plugin() is False


# ---------------------------------------------------------------------------
# 2. _build_projects_block with MSX data
# ---------------------------------------------------------------------------


class TestProjectsBlockMsx:
    """Test that _build_projects_block surfaces MSX data when present."""

    def test_project_with_msx_block(self):
        """MSX data appears in projects block output."""
        from sdk.runner import _build_projects_block

        projects = [{
            "project": "Contoso Migration",
            "status": "active",
            "risk_level": "medium",
            "_file": "contoso-migration.yaml",
            "msx": {
                "opportunity_name": "Contoso Enterprise Renewal",
                "stage": "Proposal",
                "revenue": "$2.4M",
                "close_date": "2026-04-15",
            },
        }]

        block = _build_projects_block(projects)
        assert "MSX: Contoso Enterprise Renewal" in block
        assert "Stage: Proposal" in block
        assert "Revenue: $2.4M" in block
        assert "Close: 2026-04-15" in block

    def test_project_without_msx_block(self):
        """No MSX line when project has no msx: block."""
        from sdk.runner import _build_projects_block

        projects = [{
            "project": "Contoso Migration",
            "status": "active",
            "risk_level": "medium",
            "_file": "contoso-migration.yaml",
        }]

        block = _build_projects_block(projects)
        assert "MSX:" not in block
        assert "Contoso Migration" in block

    def test_project_not_in_deal_team(self):
        """Shows warning when not in deal team."""
        from sdk.runner import _build_projects_block

        projects = [{
            "project": "Fabrikam Deal",
            "status": "active",
            "risk_level": "low",
            "_file": "fabrikam-deal.yaml",
            "msx": {
                "opportunity_name": "Fabrikam Q2",
                "stage": "Qualify",
                "revenue": "$500K",
                "in_deal_team": False,
            },
        }]

        block = _build_projects_block(projects)
        assert "NOT in deal team" in block

    def test_project_in_deal_team_no_warning(self):
        """No warning when in_deal_team is True."""
        from sdk.runner import _build_projects_block

        projects = [{
            "project": "Fabrikam Deal",
            "status": "active",
            "risk_level": "low",
            "_file": "fabrikam-deal.yaml",
            "msx": {
                "opportunity_name": "Fabrikam Q2",
                "stage": "Qualify",
                "revenue": "$500K",
                "in_deal_team": True,
            },
        }]

        block = _build_projects_block(projects)
        assert "NOT in deal team" not in block

    def test_msx_with_opportunity_id_fallback(self):
        """Falls back to opportunity_id when opportunity_name is absent."""
        from sdk.runner import _build_projects_block

        projects = [{
            "project": "Test",
            "status": "active",
            "_file": "test.yaml",
            "msx": {
                "opportunity_id": "OP-12345",
                "stage": "Close",
                "revenue": "$1M",
            },
        }]

        block = _build_projects_block(projects)
        assert "MSX: OP-12345" in block

    def test_msx_without_close_date(self):
        """No 'Close:' segment when close_date is absent."""
        from sdk.runner import _build_projects_block

        projects = [{
            "project": "Test",
            "status": "active",
            "_file": "test.yaml",
            "msx": {
                "opportunity_name": "Test Opp",
                "stage": "Discover",
                "revenue": "$100K",
            },
        }]

        block = _build_projects_block(projects)
        assert "Close:" not in block

    def test_mixed_projects_msx_and_no_msx(self):
        """Only projects with msx: block show MSX line."""
        from sdk.runner import _build_projects_block

        projects = [
            {
                "project": "With MSX",
                "status": "active",
                "_file": "with-msx.yaml",
                "msx": {"opportunity_name": "Deal A", "stage": "Propose", "revenue": "$2M"},
            },
            {
                "project": "Without MSX",
                "status": "active",
                "_file": "without-msx.yaml",
            },
        ]

        block = _build_projects_block(projects)
        # MSX line only appears once (for the first project)
        assert block.count("MSX:") == 1
        assert "Deal A" in block


# ---------------------------------------------------------------------------
# 3. _build_msx_gap_block
# ---------------------------------------------------------------------------


class TestMsxGapBlock:
    """Test MSX gap analysis block generation."""

    def test_no_projects(self):
        """Returns empty string when no projects."""
        from sdk.runner import _build_msx_gap_block
        assert _build_msx_gap_block([]) == ""

    def test_all_projects_linked(self):
        """Returns empty string when all active projects have MSX links."""
        from sdk.runner import _build_msx_gap_block

        projects = [{
            "project": "Contoso",
            "status": "active",
            "_file": "contoso.yaml",
            "msx": {"opportunity_id": "OP-123"},
        }]

        assert _build_msx_gap_block(projects) == ""

    def test_some_unlinked(self):
        """Surfaces unlinked projects."""
        from sdk.runner import _build_msx_gap_block

        projects = [
            {
                "project": "Contoso",
                "status": "active",
                "_file": "contoso.yaml",
                "msx": {"opportunity_id": "OP-123"},
            },
            {
                "project": "Fabrikam",
                "status": "active",
                "_file": "fabrikam.yaml",
            },
        ]

        block = _build_msx_gap_block(projects)
        assert "MSX Pipeline Gap Analysis" in block
        assert "1 of 2 active projects" in block
        assert "Fabrikam" in block
        assert "Contoso" not in block.split("The following")[1]  # Contoso not in unlinked list

    def test_all_unlinked(self):
        """All active projects shown as unlinked."""
        from sdk.runner import _build_msx_gap_block

        projects = [
            {"project": "Alpha", "status": "active", "_file": "alpha.yaml"},
            {"project": "Beta", "status": "blocked", "_file": "beta.yaml"},
        ]

        block = _build_msx_gap_block(projects)
        assert "0 of 2 active projects" in block
        assert "Alpha" in block
        assert "Beta" in block

    def test_completed_projects_excluded(self):
        """Only active/blocked projects are considered."""
        from sdk.runner import _build_msx_gap_block

        projects = [
            {"project": "Done", "status": "completed", "_file": "done.yaml"},
            {"project": "Active", "status": "active", "_file": "active.yaml"},
        ]

        block = _build_msx_gap_block(projects)
        assert "0 of 1 active projects" in block  # only "Active" counted, and it's unlinked
        assert "Done" not in block

    def test_empty_msx_block_treated_as_unlinked(self):
        """Project with msx: {} (no opportunity_id) is treated as unlinked."""
        from sdk.runner import _build_msx_gap_block

        projects = [
            {"project": "Partial", "status": "active", "_file": "partial.yaml", "msx": {}},
        ]

        block = _build_msx_gap_block(projects)
        assert "Partial" in block

    def test_project_ids_in_gap_block(self):
        """Gap block includes project file IDs for easy reference."""
        from sdk.runner import _build_msx_gap_block

        projects = [
            {"project": "Contoso Cloud", "status": "active", "_file": "contoso-cloud.yaml"},
        ]

        block = _build_msx_gap_block(projects)
        assert "contoso-cloud" in block


# ---------------------------------------------------------------------------
# 4. Trigger variable wiring
# ---------------------------------------------------------------------------


class TestTriggerVariablesMsx:
    """Test MSX variables in _build_trigger_variables."""

    def _make_context(self, msx_available=False, msx_gap_block=""):
        """Helper to create a minimal context dict."""
        return {
            "content_block": "test content",
            "collection_warnings": "",
            "articles_block": "",
            "teams_inbox_block": "no unread",
            "outlook_inbox_block": "no unread",
            "calendar_block": "no events",
            "projects_block": "",
            "commitments_summary": "",
            "msx_available": msx_available,
            "msx_gap_block": msx_gap_block,
        }

    def test_digest_msx_available(self, sample_config):
        """Digest variables include MSX block and instructions when available."""
        from sdk.runner import _build_trigger_variables

        ctx = self._make_context(msx_available=True, msx_gap_block="## Gap analysis here")
        variables = _build_trigger_variables("digest", sample_config, ctx)

        assert variables["msx_block"] == "## Gap analysis here"
        assert "MSX Pipeline Enrichment" in variables["msx_instructions"]
        assert "msx-mcp-search_opportunities" in variables["msx_instructions"]

    def test_digest_msx_not_available(self, sample_config):
        """Digest variables have empty MSX strings when not available."""
        from sdk.runner import _build_trigger_variables

        ctx = self._make_context(msx_available=False)
        variables = _build_trigger_variables("digest", sample_config, ctx)

        assert variables["msx_block"] == ""
        assert variables["msx_instructions"] == ""

    def test_monitor_msx_available(self, sample_config):
        """Monitor variables include MSX context when available."""
        from sdk.runner import _build_trigger_variables

        ctx = {
            "teams_inbox": "test",
            "outlook_inbox_block": "test",
            "calendar_block": "test",
            "msx_available": True,
        }
        variables = _build_trigger_variables("monitor", sample_config, ctx)

        assert "MSX Pipeline Context" in variables["msx_context"]

    def test_monitor_msx_not_available(self, sample_config):
        """Monitor variables have empty MSX context when not available."""
        from sdk.runner import _build_trigger_variables

        ctx = {
            "teams_inbox": "test",
            "outlook_inbox_block": "test",
            "calendar_block": "test",
            "msx_available": False,
        }
        variables = _build_trigger_variables("monitor", sample_config, ctx)

        assert variables["msx_context"] == ""

    def test_knowledge_archive_msx_available(self, sample_config):
        """Knowledge-archive includes MSX instructions when available."""
        from sdk.runner import _build_trigger_variables

        ctx = {
            "lookback_window": "48 hours",
            "lookback_note": "",
            "recent_artifacts": "",
            "teams_inbox_block": "",
            "outlook_inbox_block": "",
            "msx_available": True,
        }
        variables = _build_trigger_variables("knowledge-archive", sample_config, ctx)

        assert "MSX Linking" in variables["msx_instructions"]

    def test_knowledge_archive_msx_not_available(self, sample_config):
        """Knowledge-archive has empty MSX instructions when not available."""
        from sdk.runner import _build_trigger_variables

        ctx = {
            "lookback_window": "48 hours",
            "lookback_note": "",
            "recent_artifacts": "",
            "teams_inbox_block": "",
            "outlook_inbox_block": "",
            "msx_available": False,
        }
        variables = _build_trigger_variables("knowledge-archive", sample_config, ctx)

        assert variables["msx_instructions"] == ""

    def test_knowledge_project_msx_available(self, sample_config):
        """Knowledge-project includes MSX sync instructions when available."""
        from sdk.runner import _build_trigger_variables

        ctx = {
            "lookback_window": "48 hours",
            "project_id": "contoso",
            "project_name": "Contoso Migration",
            "project_yaml": "project: Contoso",
            "recent_artifacts": "",
            "msx_available": True,
        }
        variables = _build_trigger_variables("knowledge-project", sample_config, ctx)

        assert "MSX Pipeline Sync" in variables["msx_instructions"]

    def test_knowledge_project_msx_not_available(self, sample_config):
        """Knowledge-project has empty MSX instructions when not available."""
        from sdk.runner import _build_trigger_variables

        ctx = {
            "lookback_window": "48 hours",
            "project_id": "contoso",
            "project_name": "Contoso Migration",
            "project_yaml": "project: Contoso",
            "recent_artifacts": "",
            "msx_available": False,
        }
        variables = _build_trigger_variables("knowledge-project", sample_config, ctx)

        assert variables["msx_instructions"] == ""


# ---------------------------------------------------------------------------
# 5. Template contract tests
# ---------------------------------------------------------------------------


class TestTemplateContracts:
    """Verify trigger templates contain MSX placeholders."""

    def _read_template(self, name):
        template_dir = Path(__file__).parent.parent / "config" / "prompts" / "triggers"
        return (template_dir / f"{name}.md").read_text(encoding="utf-8")

    def test_digest_template_has_msx_placeholders(self):
        text = self._read_template("digest")
        assert "{{msx_block}}" in text
        assert "{{msx_instructions}}" in text

    def test_monitor_template_has_msx_placeholder(self):
        text = self._read_template("monitor")
        assert "{{msx_context}}" in text

    def test_knowledge_project_template_has_msx_placeholder(self):
        text = self._read_template("knowledge-project")
        assert "{{msx_instructions}}" in text

    def test_knowledge_archive_template_has_msx_placeholder(self):
        text = self._read_template("knowledge-archive")
        assert "{{msx_instructions}}" in text


# ---------------------------------------------------------------------------
# 6. Agent MCP server loading
# ---------------------------------------------------------------------------


class TestAgentMcpLoading:
    """Test knowledge-miner agent loads MSX when available."""

    def test_knowledge_miner_has_msx_in_mcp_servers(self):
        """Knowledge-miner front-matter declares msx in mcp_servers."""
        from sdk.agents import parse_front_matter
        path = Path(__file__).parent.parent / "config" / "prompts" / "agents" / "knowledge-miner.md"
        meta, _ = parse_front_matter(path)
        assert "msx" in meta.get("mcp_servers", [])

    def test_knowledge_miner_skips_msx_when_unavailable(self, sample_config, tmp_path):
        """load_agent filters out msx when plugin not installed."""
        from sdk.agents import load_agent

        with patch("sdk.agents.Path.home", return_value=tmp_path):
            agent = load_agent("knowledge-miner", sample_config)

        mcp_servers = agent.get("mcp_servers", {})
        assert "msx" not in mcp_servers

    def test_knowledge_miner_includes_msx_when_available(self, sample_config, tmp_path):
        """load_agent includes msx when plugin is installed."""
        from sdk.agents import load_agent

        # Create fake plugin dir
        plugin_dir = tmp_path / ".copilot" / "installed-plugins" / "_direct" / "MSX-MCP-main"
        plugin_dir.mkdir(parents=True)
        # Create bootstrap.mjs so config finds an entry point
        scripts = plugin_dir / "scripts"
        scripts.mkdir()
        (scripts / "bootstrap.mjs").write_text("// fake")

        with patch("sdk.agents.Path.home", return_value=tmp_path):
            agent = load_agent("knowledge-miner", sample_config)

        mcp_servers = agent.get("mcp_servers", {})
        assert "msx" in mcp_servers


# ---------------------------------------------------------------------------
# 7. Agent prompt content
# ---------------------------------------------------------------------------


class TestAgentPromptContent:
    """Test agent prompts contain MSX guidance."""

    def test_project_researcher_mentions_msx(self):
        """project-researcher has MSX guidance section."""
        path = Path(__file__).parent.parent / "config" / "prompts" / "agents" / "project-researcher.md"
        text = path.read_text(encoding="utf-8")
        assert "MSX Pipeline Data" in text

    def test_knowledge_miner_has_msx_mission(self):
        """knowledge-miner has MSX Pipeline Sync mission."""
        path = Path(__file__).parent.parent / "config" / "prompts" / "agents" / "knowledge-miner.md"
        text = path.read_text(encoding="utf-8")
        assert "MSX Pipeline Sync" in text
        assert "msx-mcp-get_opportunity_details" in text
