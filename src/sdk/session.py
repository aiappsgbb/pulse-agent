"""Session configuration builder — config-driven, no hardcoded if/elif chains."""

import asyncio
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path

import yaml

from copilot import (
    CopilotClient,
    SessionConfig,
    Tool,
    PermissionRequest,
    PermissionRequestResult,
)

from core.constants import PROJECT_ROOT, OUTPUT_DIR, CONFIG_DIR
from core.logging import log
from sdk.prompts import load_prompt
from sdk.agents import load_agents, _mcp_config

MAX_SESSION_RETRIES = 3


def _load_modes() -> dict:
    """Load mode definitions from config/modes.yaml."""
    modes_path = CONFIG_DIR / "modes.yaml"
    with open(modes_path, "r") as f:
        return yaml.safe_load(f)


def auto_approve_handler(request: PermissionRequest, context: dict) -> PermissionRequestResult:
    """Auto-approve all tool calls — agent runs autonomously."""
    return PermissionRequestResult(kind="approved", rules=[])


def make_user_input_handler(telegram_app, chat_id: int):
    """Create an async UserInputHandler that relays ask_user calls to Telegram.

    When the agent calls ask_user (e.g., to confirm a Teams message), this
    handler sends the question to the user's Telegram chat and waits for
    their reply. Timeout after 120s -> returns "no".
    """
    async def handler(request, context):
        from tg.bot import notify, wait_for_confirmation
        import asyncio

        question = request.get("question", "")
        await notify(telegram_app, chat_id, question)

        try:
            answer = await wait_for_confirmation(chat_id, timeout=120)
        except asyncio.TimeoutError:
            await notify(telegram_app, chat_id, "(Timed out — action cancelled)")
            answer = "no"

        return {"answer": answer, "wasFreeform": True}

    return handler


def _build_system_prompt(config: dict, mode: str, mode_cfg: dict) -> str:
    """Build system prompt by loading base + mode-specific prompt with variable interpolation."""
    # Chat mode replaces the entire system prompt
    if mode_cfg.get("system_prompt_mode") == "replace":
        prompt_path = mode_cfg["system_prompt"]
        variables = _build_prompt_variables(config, mode)
        return load_prompt(prompt_path, variables)

    # Other modes: base + mode-specific additions
    base = load_prompt("config/prompts/system/base.md")

    prompt_path = mode_cfg.get("system_prompt")
    if prompt_path:
        variables = _build_prompt_variables(config, mode)
        base += "\n" + load_prompt(prompt_path, variables)

    return base


def _build_prompt_variables(config: dict, mode: str) -> dict:
    """Build the variable dict for prompt interpolation based on mode.

    Only includes variables that have {{placeholders}} in the templates.
    Instruction content is merged directly into prompt templates (not via variables).
    """
    variables = {}

    if mode == "monitor" or mode == "triage":
        monitoring = config.get("monitoring", {})
        priorities = monitoring.get("priorities", [])
        vips = monitoring.get("vip_contacts", [])

        variables["priorities"] = "\n".join(f"- {p}" for p in priorities)
        variables["vips"] = ", ".join(vips) if vips else "None configured"

    return variables


def build_session_config(
    config: dict,
    mode: str,
    tools: list[Tool] | None = None,
    telegram_app=None,
    chat_id: int | None = None,
    cdp_endpoint: str | None = None,
) -> SessionConfig:
    """Build a SessionConfig from modes.yaml + standing instructions.

    Config-driven: reads mode definitions from modes.yaml instead of hardcoded if/elif.
    """
    modes = _load_modes()

    # Map legacy mode name
    mode_key = "monitor" if mode == "triage" else mode
    mode_cfg = modes.get(mode_key, {})

    if mode_cfg.get("standalone"):
        raise ValueError(f"Mode '{mode}' is standalone (no SDK session)")

    # Model
    model_key = mode_cfg.get("model_key", mode)
    models = config.get("models", {})
    model = models.get(model_key, models.get("default", "claude-sonnet"))

    # Working directory
    wd = mode_cfg.get("working_dir", "root")
    working_dir = str(OUTPUT_DIR) if wd == "output" else str(PROJECT_ROOT)

    # MCP servers
    mcp_servers = {}
    for name in mode_cfg.get("mcp_servers", []):
        mcp_servers[name] = _mcp_config(name, config, cdp_endpoint)

    # Custom agents
    agent_names = mode_cfg.get("agents", [])
    custom_agents = load_agents(agent_names, config) if agent_names else []

    # System prompt
    prompt_text = _build_system_prompt(config, mode_key, mode_cfg)
    sys_mode = mode_cfg.get("system_prompt_mode", "append")
    sys_msg = {"mode": sys_mode, "content": prompt_text}

    session_config: SessionConfig = {
        "model": model,
        "system_message": sys_msg,
        "mcp_servers": mcp_servers,
        "custom_agents": custom_agents,
        "skill_directories": [
            str(PROJECT_ROOT / "config" / "skills" / "pulse-signal-drafter"),
            str(PROJECT_ROOT / "config" / "skills" / "teams-sender"),
        ],
        "working_directory": working_dir,
        "streaming": True,
        "on_permission_request": auto_approve_handler,
    }

    # Excluded tools
    excluded = mode_cfg.get("excluded_tools", [])
    if excluded:
        session_config["excluded_tools"] = excluded

    # User input handler (for ask_user -> Telegram relay)
    if mode_cfg.get("user_input_handler") == "telegram" and telegram_app and chat_id:
        session_config["on_user_input_request"] = make_user_input_handler(
            telegram_app, chat_id
        )

    if tools:
        session_config["tools"] = tools

    return session_config


@asynccontextmanager
async def agent_session(
    client: CopilotClient,
    config: dict,
    mode: str,
    tools: list[Tool] | None = None,
    telegram_app=None,
    chat_id: int | None = None,
    on_delta: Callable[[str], None] | None = None,
):
    """Async context manager for GHCP SDK sessions.

    Yields (session, handler) tuple. Use session.send() + handler.done.wait()
    for non-blocking sends with proper timeout control:

        async with agent_session(client, config, "digest", tools=get_tools()) as (session, handler):
            await session.send({"prompt": prompt})
            await asyncio.wait_for(handler.done.wait(), timeout=1800)
            response_text = handler.final_text

    Handles session creation with retry, event streaming via dispatch table,
    and cleanup automatically.
    """
    from core.browser import get_browser_manager
    from sdk.event_handler import EventHandler

    mgr = get_browser_manager()
    cdp_endpoint = mgr.cdp_endpoint if mgr else None

    session_config = build_session_config(
        config, mode=mode, tools=tools,
        telegram_app=telegram_app, chat_id=chat_id,
        cdp_endpoint=cdp_endpoint,
    )

    # Retry session creation (handles transient CLI startup failures)
    session = None
    for attempt in range(1, MAX_SESSION_RETRIES + 1):
        try:
            session = await client.create_session(session_config)
            break
        except Exception as e:
            if attempt == MAX_SESSION_RETRIES:
                raise
            log.warning(f"Session creation failed (attempt {attempt}/{MAX_SESSION_RETRIES}): {e}")
            await asyncio.sleep(2 ** attempt)

    handler = EventHandler(on_delta=on_delta)
    unsub = session.on(handler)

    try:
        yield session, handler
    finally:
        if unsub:
            try:
                unsub()
            except Exception:
                pass
        try:
            await session.destroy()
        except Exception:
            log.debug("Error destroying session", exc_info=True)
