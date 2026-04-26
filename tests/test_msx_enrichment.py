"""Tests for the CRM enrichment system (MSX-MCP as reference implementation).

Validates that:
1. Enrichments are additive-only — everything works identically without them
2. Enrichment files are loaded and injected only when the feature is available
3. Main prompts contain zero CRM-specific terminology
4. MCP servers are auto-injected alongside enrichments
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
# 2. load_enrichments()
# ---------------------------------------------------------------------------


class TestLoadEnrichments:
    """Test the enrichment loading system."""

    def test_loads_msx_enrichment_when_available(self):
        """Returns enrichment file content when MSX is available."""
        from sdk.prompts import load_enrichments

        with patch("sdk.agents.is_msx_available", return_value=True):
            text = load_enrichments("knowledge-miner")

        assert text != ""
        assert "CRM" in text

    def test_returns_empty_when_msx_not_available(self):
        """Returns empty string when MSX is not available."""
        from sdk.prompts import load_enrichments

        with patch("sdk.agents.is_msx_available", return_value=False):
            text = load_enrichments("knowledge-miner")

        assert text == ""

    def test_returns_empty_for_nonexistent_enrichment(self):
        """Returns empty string when no enrichment file exists for the name."""
        from sdk.prompts import load_enrichments

        with patch("sdk.agents.is_msx_available", return_value=True):
            text = load_enrichments("nonexistent-agent-that-has-no-file")

        assert text == ""

    def test_trigger_enrichment_files_exist(self):
        """All expected trigger enrichment files exist."""
        from sdk.prompts import ENRICHMENTS_DIR

        for mode in ("trigger-digest", "trigger-monitor", "trigger-knowledge-archive", "trigger-knowledge-project"):
            path = ENRICHMENTS_DIR / f"msx-{mode}.md"
            assert path.exists(), f"Missing enrichment file: {path}"

    def test_agent_enrichment_files_exist(self):
        """All expected agent enrichment files exist."""
        from sdk.prompts import ENRICHMENTS_DIR

        for agent in ("knowledge-miner", "project-researcher", "chat"):
            path = ENRICHMENTS_DIR / f"msx-{agent}.md"
            assert path.exists(), f"Missing enrichment file: {path}"


# ---------------------------------------------------------------------------
# 3. _build_projects_block with CRM data
# ---------------------------------------------------------------------------


class TestProjectsBlockCrm:
    """Test that _build_projects_block surfaces CRM data when present."""

    def test_project_with_msx_block(self):
        """CRM data appears in projects block output."""
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
        assert "CRM: Contoso Enterprise Renewal" in block
        assert "Stage: Proposal" in block
        assert "Revenue: $2.4M" in block
        assert "Close: 2026-04-15" in block

    def test_project_without_msx_block(self):
        """No CRM line when project has no msx: block."""
        from sdk.runner import _build_projects_block

        projects = [{
            "project": "Contoso Migration",
            "status": "active",
            "risk_level": "medium",
            "_file": "contoso-migration.yaml",
        }]

        block = _build_projects_block(projects)
        assert "CRM:" not in block
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
        assert "CRM: OP-12345" in block

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
        """Only projects with msx: block show CRM line."""
        from sdk.runner import _build_projects_block

        projects = [
            {
                "project": "With CRM",
                "status": "active",
                "_file": "with-crm.yaml",
                "msx": {"opportunity_name": "Deal A", "stage": "Propose", "revenue": "$2M"},
            },
            {
                "project": "Without CRM",
                "status": "active",
                "_file": "without-crm.yaml",
            },
        ]

        block = _build_projects_block(projects)
        assert block.count("CRM:") == 1
        assert "Deal A" in block

    def test_deal_team_rendering(self):
        """Deal team members are rendered when present."""
        from sdk.runner import _build_projects_block

        projects = [{
            "project": "Test",
            "status": "active",
            "_file": "test.yaml",
            "msx": {
                "opportunity_name": "Test",
                "stage": "Propose",
                "revenue": "$1M",
                "deal_team": [
                    {"name": "Jane Smith", "role": "AE"},
                    {"name": "Bob Jones", "role": "SA"},
                ],
            },
        }]

        block = _build_projects_block(projects)
        assert "Deal team:" in block
        assert "Jane Smith (AE)" in block
        assert "Bob Jones (SA)" in block

    def test_milestones_rendering(self):
        """Milestones are rendered when present."""
        from sdk.runner import _build_projects_block

        projects = [{
            "project": "Test",
            "status": "active",
            "_file": "test.yaml",
            "msx": {
                "opportunity_name": "Test",
                "stage": "Propose",
                "revenue": "$1M",
                "milestones": [
                    {"name": "Architecture Review", "status": "on-track", "date": "2026-03-15", "monthly_acr": "$50K"},
                    {"name": "PoC Execution", "status": "at-risk", "date": "2026-04-01"},
                ],
            },
        }]

        block = _build_projects_block(projects)
        assert "Milestones:" in block
        assert "[on-track] Architecture Review" in block
        assert "due: 2026-03-15" in block
        assert "ACR: $50K" in block
        assert "[at-risk] PoC Execution" in block

    def test_solution_area_and_deal_type_rendering(self):
        """Solution area and deal type show in CRM line."""
        from sdk.runner import _build_projects_block

        projects = [{
            "project": "Test",
            "status": "active",
            "_file": "test.yaml",
            "msx": {
                "opportunity_name": "Test",
                "stage": "Qualify",
                "revenue": "$500K",
                "solution_area": "Azure",
                "deal_type": "New",
            },
        }]

        block = _build_projects_block(projects)
        assert "Azure" in block
        assert "New" in block


# ---------------------------------------------------------------------------
# 4. _build_msx_gap_block
# ---------------------------------------------------------------------------


class TestCrmGapBlock:
    """Test CRM gap analysis block generation."""

    def test_no_projects(self):
        """Returns empty string when no projects."""
        from sdk.runner import _build_msx_gap_block
        assert _build_msx_gap_block([]) == ""

    def test_all_projects_linked(self):
        """Returns empty string when all active projects have CRM links."""
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
        assert "CRM Pipeline Gap Analysis" in block
        assert "1 of 2 active projects" in block
        assert "Fabrikam" in block
        assert "Contoso" not in block.split("The following")[1]

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
        assert "0 of 1 active projects" in block
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
# 5. Trigger variable wiring (enrichment-based)
# ---------------------------------------------------------------------------


class TestTriggerVariablesMsx:
    """Test trigger variables load from enrichment files."""

    def _make_context(self, msx_gap_block=""):
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
            "msx_gap_block": msx_gap_block,
        }

    def test_digest_msx_available(self, sample_config):
        """Digest variables include enrichment content when available."""
        from sdk.runner import _build_trigger_variables

        ctx = self._make_context(msx_gap_block="## Gap analysis here")
        with patch("sdk.agents.is_msx_available", return_value=True):
            variables = _build_trigger_variables("digest", sample_config, ctx)

        assert variables["msx_block"] == "## Gap analysis here"
        assert variables["msx_instructions"] != ""  # loaded from enrichment file

    def test_digest_msx_not_available(self, sample_config):
        """Digest variables have empty strings when not available."""
        from sdk.runner import _build_trigger_variables

        ctx = self._make_context()
        with patch("sdk.agents.is_msx_available", return_value=False):
            variables = _build_trigger_variables("digest", sample_config, ctx)

        assert variables["msx_block"] == ""
        assert variables["msx_instructions"] == ""

    def test_monitor_msx_available(self, sample_config):
        """Monitor variables include enrichment context when available."""
        from sdk.runner import _build_trigger_variables

        ctx = {
            "teams_inbox": "test",
            "outlook_inbox_block": "test",
            "calendar_block": "test",
        }
        with patch("sdk.agents.is_msx_available", return_value=True):
            variables = _build_trigger_variables("monitor", sample_config, ctx)

        assert variables["msx_context"] != ""

    def test_monitor_msx_not_available(self, sample_config):
        """Monitor variables have empty context when not available."""
        from sdk.runner import _build_trigger_variables

        ctx = {
            "teams_inbox": "test",
            "outlook_inbox_block": "test",
            "calendar_block": "test",
        }
        with patch("sdk.agents.is_msx_available", return_value=False):
            variables = _build_trigger_variables("monitor", sample_config, ctx)

        assert variables["msx_context"] == ""

    def test_knowledge_archive_msx_available(self, sample_config):
        """Knowledge-archive includes enrichment instructions when available."""
        from sdk.runner import _build_trigger_variables

        ctx = {
            "lookback_window": "48 hours",
            "lookback_note": "",
            "recent_artifacts": "",
            "teams_inbox_block": "",
            "outlook_inbox_block": "",
        }
        with patch("sdk.agents.is_msx_available", return_value=True):
            variables = _build_trigger_variables("knowledge-archive", sample_config, ctx)

        assert variables["msx_instructions"] != ""

    def test_knowledge_archive_msx_not_available(self, sample_config):
        """Knowledge-archive has empty instructions when not available."""
        from sdk.runner import _build_trigger_variables

        ctx = {
            "lookback_window": "48 hours",
            "lookback_note": "",
            "recent_artifacts": "",
            "teams_inbox_block": "",
            "outlook_inbox_block": "",
        }
        with patch("sdk.agents.is_msx_available", return_value=False):
            variables = _build_trigger_variables("knowledge-archive", sample_config, ctx)

        assert variables["msx_instructions"] == ""

    def test_knowledge_project_msx_available(self, sample_config):
        """Knowledge-project includes enrichment instructions when available."""
        from sdk.runner import _build_trigger_variables

        ctx = {
            "lookback_window": "48 hours",
            "project_id": "contoso",
            "project_name": "Contoso Migration",
            "project_yaml": "project: Contoso",
            "recent_artifacts": "",
        }
        with patch("sdk.agents.is_msx_available", return_value=True):
            variables = _build_trigger_variables("knowledge-project", sample_config, ctx)

        assert variables["msx_instructions"] != ""

    def test_knowledge_project_msx_not_available(self, sample_config):
        """Knowledge-project has empty instructions when not available."""
        from sdk.runner import _build_trigger_variables

        ctx = {
            "lookback_window": "48 hours",
            "project_id": "contoso",
            "project_name": "Contoso Migration",
            "project_yaml": "project: Contoso",
            "recent_artifacts": "",
        }
        with patch("sdk.agents.is_msx_available", return_value=False):
            variables = _build_trigger_variables("knowledge-project", sample_config, ctx)

        assert variables["msx_instructions"] == ""


# ---------------------------------------------------------------------------
# 6. Template contract tests
# ---------------------------------------------------------------------------


class TestTemplateContracts:
    """Verify trigger templates contain enrichment placeholders."""

    def _read_template(self, name):
        template_dir = Path(__file__).parent.parent / "config" / "prompts" / "triggers"
        return (template_dir / f"{name}.md").read_text(encoding="utf-8")

    def test_digest_template_has_enrichment_placeholders(self):
        text = self._read_template("digest")
        assert "{{msx_block}}" in text
        assert "{{msx_instructions}}" in text

    def test_monitor_template_has_enrichment_placeholder(self):
        text = self._read_template("monitor")
        assert "{{msx_context}}" in text

    def test_knowledge_project_template_has_enrichment_placeholder(self):
        text = self._read_template("knowledge-project")
        assert "{{msx_instructions}}" in text

    def test_knowledge_archive_template_has_enrichment_placeholder(self):
        text = self._read_template("knowledge-archive")
        assert "{{msx_instructions}}" in text


# ---------------------------------------------------------------------------
# 7. Agent enrichment injection
# ---------------------------------------------------------------------------


class TestAgentEnrichmentInjection:
    """Test that agents get enrichments and MCP servers auto-injected."""

    def test_knowledge_miner_no_msx_in_frontmatter(self):
        """knowledge-miner front-matter does NOT contain 'msx' — it's auto-injected."""
        from sdk.agents import parse_front_matter
        path = Path(__file__).parent.parent / "config" / "prompts" / "agents" / "knowledge-miner.md"
        meta, _ = parse_front_matter(path)
        assert "msx" not in meta.get("mcp_servers", [])

    def test_knowledge_miner_skips_msx_when_unavailable(self, sample_config, tmp_path):
        """load_agent does NOT inject msx when plugin not installed."""
        from sdk.agents import load_agent

        with patch("sdk.agents.Path.home", return_value=tmp_path):
            agent = load_agent("knowledge-miner", sample_config)

        mcp_servers = agent.get("mcp_servers", {})
        assert "msx" not in mcp_servers

    def test_knowledge_miner_gets_msx_when_available(self, sample_config, tmp_path):
        """load_agent auto-injects msx MCP server when plugin is installed."""
        from sdk.agents import load_agent

        # Create fake plugin dir
        plugin_dir = tmp_path / ".copilot" / "installed-plugins" / "_direct" / "MSX-MCP-main"
        plugin_dir.mkdir(parents=True)
        scripts = plugin_dir / "scripts"
        scripts.mkdir()
        (scripts / "bootstrap.mjs").write_text("// fake")

        with patch("sdk.agents.Path.home", return_value=tmp_path):
            agent = load_agent("knowledge-miner", sample_config)

        mcp_servers = agent.get("mcp_servers", {})
        assert "msx" in mcp_servers

    def test_knowledge_miner_prompt_includes_enrichment(self, sample_config, tmp_path):
        """Agent prompt includes enrichment content when MSX is available."""
        from sdk.agents import load_agent

        plugin_dir = tmp_path / ".copilot" / "installed-plugins" / "_direct" / "MSX-MCP-main"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "scripts").mkdir()
        (plugin_dir / "scripts" / "bootstrap.mjs").write_text("// fake")

        with patch("sdk.agents.Path.home", return_value=tmp_path):
            agent = load_agent("knowledge-miner", sample_config)

        assert "CRM" in agent["prompt"]

    def test_knowledge_miner_prompt_clean_without_msx(self, sample_config, tmp_path):
        """Agent prompt has no CRM/MSX content when plugin not installed."""
        from sdk.agents import load_agent

        with patch("sdk.agents.Path.home", return_value=tmp_path):
            agent = load_agent("knowledge-miner", sample_config)

        # Without MSX, no CRM tool names should appear
        assert "msx-get_" not in agent["prompt"].lower()
        assert "msx-search_" not in agent["prompt"].lower()


# ---------------------------------------------------------------------------
# 8. Main prompts are clean (no CRM terminology)
# ---------------------------------------------------------------------------


class TestMainPromptsClean:
    """Main agent prompts must not contain CRM-specific tool names."""

    def test_knowledge_miner_no_msx_tools(self):
        """knowledge-miner.md (base) does not mention msx-mcp tools."""
        path = Path(__file__).parent.parent / "config" / "prompts" / "agents" / "knowledge-miner.md"
        text = path.read_text(encoding="utf-8")
        assert "msx-mcp" not in text.lower()

    def test_project_researcher_no_msx_tools(self):
        """project-researcher.md (base) does not mention msx-mcp tools."""
        path = Path(__file__).parent.parent / "config" / "prompts" / "agents" / "project-researcher.md"
        text = path.read_text(encoding="utf-8")
        assert "msx-mcp" not in text.lower()

    def test_chat_system_prompt_no_msx_tools(self):
        """chat.md (base) does not mention msx-mcp tools."""
        path = Path(__file__).parent.parent / "config" / "prompts" / "system" / "chat.md"
        text = path.read_text(encoding="utf-8")
        assert "msx-mcp" not in text.lower()

    def test_modes_yaml_no_msx(self):
        """modes.yaml default_mcp_servers does not contain 'msx'."""
        import yaml
        path = Path(__file__).parent.parent / "config" / "modes.yaml"
        modes = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert "msx" not in modes.get("default_mcp_servers", [])


# ---------------------------------------------------------------------------
# 9. Tool name correctness — NO msx-mcp- prefix anywhere
# ---------------------------------------------------------------------------


class TestToolNamePrefix:
    """Enrichment files must use the correct tool prefix `msx-` not `msx-mcp-`.

    The MCP server is registered as 'msx' in _MCP_BUILDERS. Copilot CLI
    prefixes tool names with {server_name}-. So tools are msx-search_accounts,
    NOT msx-mcp-search_accounts. The LLM was hallucinating the wrong prefix
    because enrichment instructions listed the wrong names.
    """

    ENRICHMENTS_DIR = Path(__file__).parent.parent / "config" / "prompts" / "enrichments"

    def _all_enrichment_files(self):
        return list(self.ENRICHMENTS_DIR.glob("msx-*.md"))

    def test_no_enrichment_uses_msx_mcp_prefix(self):
        """No enrichment file should contain 'msx-mcp-' tool prefix."""
        for path in self._all_enrichment_files():
            text = path.read_text(encoding="utf-8")
            assert "msx-mcp-" not in text, (
                f"{path.name} contains 'msx-mcp-' — should be 'msx-' "
                f"(the MCP server is registered as 'msx', not 'msx-mcp')"
            )

    def test_all_enrichments_use_correct_prefix(self):
        """Every enrichment that references tool calls uses `msx-` prefix."""
        for path in self._all_enrichment_files():
            text = path.read_text(encoding="utf-8")
            # Files that mention tool calls should use msx- prefix
            if "search_accounts" in text or "get_my_deals" in text:
                assert "msx-search_accounts" in text or "msx-get_my_deals" in text, (
                    f"{path.name} mentions tools but doesn't use the correct msx- prefix"
                )

    def test_chat_enrichment_tool_names(self):
        """Chat enrichment lists exact correct tool names."""
        text = (self.ENRICHMENTS_DIR / "msx-chat.md").read_text(encoding="utf-8")
        expected_tools = [
            "msx-get_my_deals",
            "msx-search_opportunities",
            "msx-search_accounts",
            "msx-get_opportunity_details",
            "msx-get_account_overview",
            "msx-get_pipeline_summary",
            "msx-get_my_milestones",
            "msx-get_account_team",
            "msx-get_account_deal_teams",
            "msx-get_milestones_for_opportunity",
            "msx-get_opportunity_solutions",
            "msx-msx_auth_status",
        ]
        for tool in expected_tools:
            assert f"`{tool}`" in text, f"Chat enrichment missing tool: {tool}"

    def test_knowledge_miner_enrichment_tool_names(self):
        """Knowledge-miner enrichment uses correct tool names."""
        text = (self.ENRICHMENTS_DIR / "msx-knowledge-miner.md").read_text(encoding="utf-8")
        # Must contain these exact tool names
        for tool in ["msx-search_opportunities", "msx-get_opportunity_details",
                      "msx-get_milestones_for_opportunity", "msx-get_account_deal_teams",
                      "msx-get_opportunity_solutions", "msx-get_my_deals",
                      "msx-get_my_milestones"]:
            assert f"`{tool}`" in text, f"Knowledge-miner enrichment missing: {tool}"

    def test_enrichment_files_warn_about_prefix(self):
        """Enrichment files that list tools include a prefix warning."""
        for path in self._all_enrichment_files():
            text = path.read_text(encoding="utf-8")
            if "`msx-" in text:
                # Files listing tool names should mention the correct prefix
                assert "msx-" in text.lower() and "prefix" in text.lower(), (
                    f"{path.name} lists tools but doesn't warn about the correct prefix"
                )

    def test_no_msx_mcp_in_any_prompt_file(self):
        """No prompt file (system, trigger, agent) should contain msx-mcp- prefix."""
        prompts_dir = Path(__file__).parent.parent / "config" / "prompts"
        for md_file in prompts_dir.rglob("*.md"):
            # Skip enrichment files (they're the ones we're testing specifically)
            if "enrichments" in str(md_file):
                continue
            text = md_file.read_text(encoding="utf-8")
            assert "msx-mcp-" not in text, (
                f"{md_file.relative_to(prompts_dir)} contains 'msx-mcp-' — "
                f"main prompts must not reference CRM tool names"
            )


# ---------------------------------------------------------------------------
# 10. msx_install_info() diagnostics
# ---------------------------------------------------------------------------


class TestMsxInstallInfo:
    """Test the diagnostic info function for MSX-MCP installation."""

    def test_returns_not_installed_when_missing(self, tmp_path):
        """Returns installed=False when no plugin directory exists."""
        from sdk.agents import msx_install_info

        with patch("sdk.agents.Path.home", return_value=tmp_path):
            info = msx_install_info()

        assert info["installed"] is False
        assert info["path"] is None
        assert info["entry_point"] is None
        assert isinstance(info["has_node"], bool)
        assert isinstance(info["has_az_cli"], bool)

    def test_returns_installed_with_bootstrap(self, tmp_path):
        """Returns full info when plugin is installed with bootstrap.mjs."""
        from sdk.agents import msx_install_info

        plugin_dir = tmp_path / ".copilot" / "installed-plugins" / "_direct" / "MSX-MCP-main"
        scripts = plugin_dir / "scripts"
        scripts.mkdir(parents=True)
        (scripts / "bootstrap.mjs").write_text("// fake")

        with patch("sdk.agents.Path.home", return_value=tmp_path):
            info = msx_install_info()

        assert info["installed"] is True
        assert info["path"] == str(plugin_dir)
        assert "bootstrap.mjs" in info["entry_point"]

    def test_returns_installed_with_dist_fallback(self, tmp_path):
        """Falls back to dist/index.js when bootstrap.mjs is missing."""
        from sdk.agents import msx_install_info

        plugin_dir = tmp_path / ".copilot" / "installed-plugins" / "_direct" / "MSX-MCP-main"
        plugin_dir.mkdir(parents=True)
        dist = plugin_dir / "dist"
        dist.mkdir()
        (dist / "index.js").write_text("// fake")

        with patch("sdk.agents.Path.home", return_value=tmp_path):
            info = msx_install_info()

        assert info["installed"] is True
        assert "index.js" in info["entry_point"]

    def test_returns_missing_entry_point(self, tmp_path):
        """Returns MISSING entry_point when neither bootstrap nor dist exists."""
        from sdk.agents import msx_install_info

        plugin_dir = tmp_path / ".copilot" / "installed-plugins" / "_direct" / "MSX-MCP-main"
        plugin_dir.mkdir(parents=True)

        with patch("sdk.agents.Path.home", return_value=tmp_path):
            info = msx_install_info()

        assert info["installed"] is True
        assert info["entry_point"] == "MISSING"

    def test_checks_node_and_az_cli(self, tmp_path):
        """Checks for node and az CLI in PATH."""
        from sdk.agents import msx_install_info

        with patch("sdk.agents.Path.home", return_value=tmp_path):
            with patch("shutil.which", side_effect=lambda x: "/usr/bin/node" if x == "node" else None):
                info = msx_install_info()

        assert info["has_node"] is True
        assert info["has_az_cli"] is False


# ---------------------------------------------------------------------------
# 11. Logging integration — pre-process logs CRM status with details
# ---------------------------------------------------------------------------


class TestCrmLogging:
    """Test that pre-process functions log CRM availability with details."""

    def test_msx_install_info_used_in_logging(self):
        """msx_install_info returns enough data for meaningful log messages."""
        from sdk.agents import msx_install_info

        with patch("sdk.agents.Path.home", return_value=Path("/nonexistent")):
            info = msx_install_info()

        # When not installed, logging should know it
        assert info["installed"] is False
        # The dict has all keys needed for a useful log line
        assert "path" in info
        assert "has_node" in info
        assert "has_az_cli" in info

    def test_msx_install_info_provides_debug_context(self, tmp_path):
        """When installed, info gives enough for debugging connection failures."""
        from sdk.agents import msx_install_info

        plugin_dir = tmp_path / ".copilot" / "installed-plugins" / "_direct" / "MSX-MCP-main"
        scripts = plugin_dir / "scripts"
        scripts.mkdir(parents=True)
        (scripts / "bootstrap.mjs").write_text("// fake")

        with patch("sdk.agents.Path.home", return_value=tmp_path):
            info = msx_install_info()

        # When installed, we get actionable debug info
        assert info["installed"] is True
        assert str(plugin_dir) in info["path"]
        assert "bootstrap.mjs" in info["entry_point"]
        # Can construct a useful log line
        log_line = f"CRM plugin: {info['path']}, node: {info['has_node']}, az: {info['has_az_cli']}"
        assert info["path"] in log_line
