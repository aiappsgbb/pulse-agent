"""Load agent definitions from config/prompts/agents/*.md files."""

import logging
from pathlib import Path

import yaml

from copilot import CustomAgentConfig, MCPLocalServerConfig, MCPRemoteServerConfig, MCPServerConfig

from core.constants import CONFIG_DIR, PROJECT_ROOT

log = logging.getLogger(__name__)


def parse_front_matter(path: Path) -> tuple[dict, str]:
    """Split a markdown file into YAML front matter and body.

    Expects files starting with '---' delimiter.
    Returns (metadata_dict, body_text).
    """
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}, text

    # Find the closing ---
    try:
        end = text.index("---", 3)
    except ValueError:
        # Malformed front matter — opening --- but no closing ---
        return {}, text

    front = text[3:end].strip()
    body = text[end + 3:].strip()
    metadata = yaml.safe_load(front) or {}
    return metadata, body


def workiq_mcp_config(config: dict = None, cdp_endpoint: str | None = None) -> MCPLocalServerConfig:
    """Standard WorkIQ MCP config — reused across agents."""
    return MCPLocalServerConfig(
        type="local",
        command="workiq",
        args=["mcp"],
        tools=["*"],
        timeout=60000,
    )


def is_msx_available() -> bool:
    """Check if MSX-MCP plugin is installed (without building full config).

    Reusable lightweight check — used by pre-processing to decide whether
    to inject MSX prompt blocks. Avoids constructing a full MCPLocalServerConfig.
    """
    for subdir in ("_direct/MSX-MCP-main", "copilot-plugins/msx-mcp"):
        if (Path.home() / ".copilot" / "installed-plugins" / subdir).exists():
            return True
    return False


def msx_mcp_config(config: dict = None, cdp_endpoint: str | None = None) -> MCPLocalServerConfig | None:
    """MSX-MCP config — Dataverse/CRM tools for MSX pipeline data.

    Matches the plugin's own .mcp.json: stdio transport, node scripts/bootstrap.mjs.
    Only loaded if the msx-mcp plugin is installed in the Copilot CLI.
    Returns None if not installed (graceful skip).
    """
    # Check both install paths (direct install vs marketplace)
    for subdir in ("_direct/MSX-MCP-main", "copilot-plugins/msx-mcp"):
        plugin_dir = Path.home() / ".copilot" / "installed-plugins" / subdir
        if plugin_dir.exists():
            break
    else:
        return None

    # Use bootstrap.mjs (their .mcp.json entry point), fall back to dist/index.js
    bootstrap = plugin_dir / "scripts" / "bootstrap.mjs"
    entry = str(bootstrap) if bootstrap.exists() else str(plugin_dir / "dist" / "index.js")

    # Pass system PATH so the node process can find Azure CLI (az) for auth.
    # Without this, AzureCliCredential fails with "Azure CLI: Not installed".
    import os
    env = {"PATH": os.environ.get("PATH", "")}

    return MCPLocalServerConfig(
        type="local",
        command="node",
        args=[entry],
        tools=["*"],
        timeout=120000,  # 2 min — first call includes auth token acquisition
        cwd=str(plugin_dir),
        env=env,
    )


def playwright_mcp_config(config: dict, cdp_endpoint: str | None = None) -> MCPLocalServerConfig:
    """Playwright MCP config — reused across agents that need browser automation.

    When cdp_endpoint is provided, connects to an existing shared browser
    instead of launching a new one (avoids user-data-dir profile locking).
    """
    if cdp_endpoint:
        return MCPLocalServerConfig(
            type="local",
            command="npx",
            args=[
                "@playwright/mcp@latest",
                "--cdp-endpoint", cdp_endpoint,
            ],
            tools=["*"],
            timeout=120000,
        )

    # Fallback: launch own browser (CLI --once mode, no shared browser)
    from core.browser import _default_profile_dir
    user_data_dir = _default_profile_dir()
    return MCPLocalServerConfig(
        type="local",
        command="npx",
        args=[
            "@playwright/mcp@latest",
            "--browser", "msedge",
            "--headless",
            "--user-data-dir", user_data_dir,
        ],
        tools=["*"],
        timeout=120000,
    )


def dataverse_mcp_config(config: dict, cdp_endpoint: str | None = None) -> MCPRemoteServerConfig | None:
    """Dataverse MCP config — remote HTTP server for Dynamics 365 / CRM data.

    URL must be configured in standing-instructions.yaml under mcp_servers.dataverse.url.
    No hardcoded default — each org has its own Dataverse instance.
    Returns None if not configured (graceful skip).
    """
    dv_cfg = config.get("mcp_servers", {}).get("dataverse", {})
    url = dv_cfg.get("url", "")
    if not url or url.startswith("TODO"):
        return None
    return MCPRemoteServerConfig(
        type="http",
        url=url,
        tools=["*"],
        timeout=60000,
    )


_MCP_BUILDERS: dict[str, callable] = {
    "workiq": workiq_mcp_config,
    "msx": msx_mcp_config,
    "playwright": playwright_mcp_config,
    "dataverse": dataverse_mcp_config,
}


def _mcp_config(name: str, config: dict, cdp_endpoint: str | None = None) -> MCPServerConfig | None:
    """Build MCP config by name. Returns None if server is unknown or unconfigured."""
    builder = _MCP_BUILDERS.get(name)
    if not builder:
        log.warning("Unknown MCP server '%s' — skipping", name)
        return None
    return builder(config, cdp_endpoint)


def load_agent(name: str, config: dict) -> CustomAgentConfig:
    """Load an agent definition from config/prompts/agents/{name}.md."""
    from sdk.prompts import load_enrichments, ENRICHMENTS_DIR

    path = CONFIG_DIR / "prompts" / "agents" / f"{name}.md"
    front_matter, prompt = parse_front_matter(path)

    # Append feature-specific enrichments (e.g., CRM missions for knowledge-miner)
    enrichment = load_enrichments(name)
    if enrichment:
        prompt += "\n\n" + enrichment

    agent_cfg: CustomAgentConfig = {
        "name": front_matter["name"],
        "display_name": front_matter["display_name"],
        "description": front_matter["description"],
        "prompt": prompt,
        "infer": front_matter.get("infer", True),
    }

    # Add MCP servers if specified (filter unconfigured ones)
    mcp_names = list(front_matter.get("mcp_servers", []))

    # Auto-inject MCP servers from enrichments — if an enrichment file
    # was loaded, the agent also needs the corresponding MCP server.
    # e.g., msx-knowledge-miner.md enrichment → add 'msx' MCP server.
    for prefix, checker_path in _ENRICHMENT_MCP_MAP:
        enrichment_file = ENRICHMENTS_DIR / f"{prefix}-{name}.md"
        if enrichment_file.exists() and prefix not in mcp_names:
            module_path, func_name = checker_path.rsplit(".", 1)
            import importlib
            mod = importlib.import_module(module_path)
            if getattr(mod, func_name)():
                mcp_names.append(prefix)

    if mcp_names:
        mcp_cfgs = {}
        for s in mcp_names:
            cfg = _mcp_config(s, config)
            if cfg is not None:
                mcp_cfgs[s] = cfg
        if mcp_cfgs:
            agent_cfg["mcp_servers"] = mcp_cfgs

    return agent_cfg


# Maps enrichment prefix → availability checker for auto-injecting MCP servers
_ENRICHMENT_MCP_MAP: list[tuple[str, str]] = [
    ("msx", "sdk.agents.is_msx_available"),
]


def load_agents(names: list[str], config: dict) -> list[CustomAgentConfig]:
    """Load multiple agent definitions by name."""
    return [load_agent(name, config) for name in names]
