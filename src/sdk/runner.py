"""Unified config-driven job runner — replaces per-mode orchestration functions."""

import json
from datetime import datetime
from pathlib import Path

import yaml

from copilot import CopilotClient

from core.constants import PROJECT_ROOT, OUTPUT_DIR, CONFIG_DIR
from core.logging import log
from sdk.prompts import load_prompt
from sdk.session import agent_session
from sdk.tools import get_tools, load_actions


def _load_modes() -> dict:
    """Load mode definitions from config/modes.yaml."""
    modes_path = CONFIG_DIR / "modes.yaml"
    with open(modes_path, "r") as f:
        return yaml.safe_load(f)


async def run_job(
    client: CopilotClient,
    config: dict,
    mode: str,
    context: dict | None = None,
    telegram_app=None,
    chat_id: int | None = None,
) -> str | None:
    """Unified entry point for running any SDK-based job.

    Args:
        client: GHCP SDK client
        config: Parsed standing-instructions.yaml
        mode: Job mode (monitor, digest, intel, research, chat)
        context: Extra context for the job (e.g. research task details, chat prompt)
        telegram_app: Telegram Application (for ask_user relay)
        chat_id: Telegram chat ID
    Returns:
        Response text from the agent, or None
    """
    context = context or {}

    modes = _load_modes()
    mode_key = "monitor" if mode == "triage" else mode
    mode_cfg = modes.get(mode_key, {})

    if mode_cfg.get("standalone"):
        raise ValueError(f"Mode '{mode}' is standalone — use its handler directly")

    log.info(f"=== {mode_key} cycle start ===")

    # Pre-process: collect data before agent call
    pre_process = mode_cfg.get("pre_process")
    if pre_process == "collect_content_and_feeds":
        context.update(_pre_process_digest(config))
    elif pre_process == "collect_feeds":
        context.update(_pre_process_intel(config))
    elif pre_process == "scan_teams_inbox":
        context.update(await _pre_process_monitor(config))

    # Build trigger prompt
    prompt = _build_trigger_prompt(mode_key, mode_cfg, config, context)

    # Determine timeout
    timeout = 3600 if mode_key == "research" else 600

    # Run the session
    async with agent_session(
        client, config, mode_key,
        tools=get_tools(),
        telegram_app=telegram_app,
        chat_id=chat_id,
    ) as session:
        log.info("  Agent working...")
        response = await session.send_and_wait({"prompt": prompt}, timeout=timeout)

        if not response:
            log.warning("  No response from agent (timed out).")
            return None

        log.info(f"=== {mode_key} cycle end ===")

        if response.data and response.data.content:
            return response.data.content
        return None


def _build_trigger_prompt(mode: str, mode_cfg: dict, config: dict, context: dict) -> str:
    """Build the trigger prompt from template + variables."""
    # Chat mode: use the user's message directly
    if mode == "chat":
        return context.get("prompt", "")

    trigger_path = mode_cfg.get("trigger_prompt")
    if not trigger_path:
        return context.get("prompt", "")

    # Build variables for template interpolation
    variables = _build_trigger_variables(mode, config, context)
    return load_prompt(trigger_path, variables)


def _build_trigger_variables(mode: str, config: dict, context: dict) -> dict:
    """Build the variable dict for trigger prompt interpolation."""
    variables = {}
    date_str = datetime.now().strftime("%Y-%m-%d")

    if mode == "digest":
        variables["date"] = date_str

        # Dynamic WorkIQ window based on previous digest
        prev = _load_previous_digest()
        if prev and prev.get("date"):
            variables["workiq_window"] = f"since {prev['date']}"
        else:
            variables["workiq_window"] = "in the last 7 days"

        # Priorities
        digest_cfg = config.get("digest", {})
        priorities = digest_cfg.get("priorities", [])
        variables["priorities"] = "\n".join(f"- {p}" for p in priorities)

        # Dismissed items
        actions = load_actions()
        dismissed = actions.get("dismissed", [])
        notes = actions.get("notes", {})

        if dismissed:
            dismissed_items = "\n".join(f"- {d['item']}" for d in dismissed)
            variables["dismissed_block"] = f"\n## Previously Dismissed Items (DO NOT include these)\n{dismissed_items}\n"
        else:
            variables["dismissed_block"] = ""

        if notes:
            note_items = "\n".join(f"- **{k}**: {v['note']}" for k, v in notes.items())
            variables["notes_block"] = f"\n## User Notes (context for your analysis)\n{note_items}\n"
        else:
            variables["notes_block"] = ""

        # Carry-forward from previous digest
        variables["carry_forward"] = _build_carry_forward(prev)

        # Content sections from pre-processing
        variables["content_sections"] = context.get("content_block", "No new local content.")

        # Articles block from pre-processing
        variables["articles_block"] = context.get("articles_block", "")

    elif mode == "intel":
        variables["date"] = date_str
        articles = context.get("articles", [])
        variables["article_count"] = str(len(articles))

        intel_cfg = config.get("intelligence", {})
        variables["topics"] = ", ".join(intel_cfg.get("topics", []))

        competitors = intel_cfg.get("competitors", [])
        variables["competitors"] = "\n".join(
            f"- **{c['company']}**: watching {', '.join(c['watch'])}"
            for c in competitors
        )

        article_lines = []
        for a in articles:
            article_lines.append(
                f"- [{a['source']}] **{a['title']}**\n"
                f"  Link: {a['link']}\n"
                f"  Published: {a['published']}\n"
                f"  Summary: {a['summary']}"
            )
        variables["articles"] = "\n\n".join(article_lines)

    elif mode == "monitor":
        variables["teams_inbox"] = context.get("teams_inbox", "No Teams inbox data available.")

    elif mode == "research":
        task = context.get("task", {})
        variables["task"] = task.get("task", "unnamed")
        variables["description"] = task.get("description", variables["task"])
        output_cfg = task.get("output", {})
        variables["output_path"] = output_cfg.get("local", "./output/")

    return variables


def _load_previous_digest() -> dict | None:
    """Load the most recent digest JSON for carry-forward."""
    digests_dir = OUTPUT_DIR / "digests"
    if not digests_dir.exists():
        return None
    json_files = sorted(digests_dir.glob("*.json"), reverse=True)
    if not json_files:
        return None
    try:
        return json.loads(json_files[0].read_text(encoding="utf-8"))
    except Exception as e:
        log.warning(f"Could not load previous digest: {e}")
        return None


def _build_carry_forward(prev: dict | None) -> str:
    """Build carry-forward block from previous digest items."""
    if not prev or not prev.get("items"):
        return ""

    lines = [
        "## Known Outstanding Items (from previous digest)\n",
        "These items were flagged previously. For each one:",
        "- **KEEP** if still unresolved (no reply sent, no action taken)",
        "- **DROP** if WorkIQ confirms it's been handled (reply sent, meeting attended, etc.)",
        "- **UPDATE** if there's new activity on the same thread\n",
    ]
    for item in prev["items"]:
        priority = item.get("priority", "?")
        title = item.get("title", "?")
        item_id = item.get("id", "?")
        source = item.get("source", "?")
        date = item.get("date", "?")
        lines.append(
            f"- [{priority.upper()}] **{title}** "
            f"(id: {item_id}, source: {source}, date: {date})"
        )
    return "\n".join(lines)


async def _pre_process_monitor(config: dict) -> dict:
    """Scan Teams inbox for unread messages before monitor agent call."""
    from collectors.teams_inbox import scan_teams_inbox, format_inbox_for_prompt

    log.info("Phase 0: Scanning Teams inbox for unread messages...")
    items = await scan_teams_inbox(config)

    if items:
        log.info(f"  Found {len(items)} unread Teams messages")
    else:
        log.info("  No unread Teams messages detected.")

    return {"teams_inbox": format_inbox_for_prompt(items)}


def _pre_process_digest(config: dict) -> dict:
    """Collect local content and RSS feeds before digest agent call."""
    from collectors.content import collect_content
    from collectors.feeds import collect_feeds

    log.info("Phase 1: Collecting content from input folders...")
    items = collect_content(config)

    if items:
        log.info(f"  Collected {len(items)} local items:")
        for item in items:
            log.info(f"    - [{item['type']}] {item['name']} ({item['size']} chars)")
    else:
        log.info("  No new local content.")

    log.info("\nPhase 1b: Fetching RSS feeds...")
    articles = collect_feeds(config)
    if articles:
        log.info(f"  Collected {len(articles)} new articles")
    else:
        log.info("  No new articles.")

    # Build content block
    by_type: dict[str, list[dict]] = {}
    for item in items:
        by_type.setdefault(item["type"], []).append(item)

    content_sections = []
    for content_type, type_items in by_type.items():
        section = f"### {content_type.title()} ({len(type_items)} files)\n"
        for item in type_items:
            section += f"\n---\n#### File: {item['name']}\n```\n{item['content']}\n```\n"
        content_sections.append(section)

    content_block = "\n".join(content_sections)

    # Build articles block
    articles_block = ""
    if articles:
        article_lines = [f"- [{a['source']}] **{a['title']}** ({a['published']})" for a in articles]
        articles_block = f"\n## Part C — External Intel ({len(articles)} articles from RSS feeds)\n" + "\n".join(article_lines) + "\n"

    return {
        "content_block": content_block,
        "articles_block": articles_block,
        "articles": articles,
    }


def _pre_process_intel(config: dict) -> dict:
    """Collect RSS feeds before intel agent call."""
    from collectors.feeds import collect_feeds

    log.info("Phase 1: Fetching RSS feeds...")
    articles = collect_feeds(config)

    if not articles:
        log.info("  No new articles.")
    else:
        log.info(f"  Collected {len(articles)} new articles")

    return {"articles": articles}
