"""Unified config-driven job runner — replaces per-mode orchestration functions."""

import asyncio
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
    on_delta=None,
) -> str | None:
    """Unified entry point for running any SDK-based job.

    Args:
        client: GHCP SDK client
        config: Parsed standing-instructions.yaml
        mode: Job mode (monitor, digest, intel, research, chat)
        context: Extra context for the job (e.g. research task details, chat prompt)
        telegram_app: Telegram Application (for ask_user relay)
        chat_id: Telegram chat ID
        on_delta: Optional callback for streaming text deltas
    Returns:
        Response text from the agent, or None
    """
    context = context or {}

    modes = _load_modes()
    mode_key = "monitor" if mode == "triage" else mode
    mode_cfg = modes.get(mode_key, {})

    if mode_cfg.get("standalone"):
        raise ValueError(f"Mode '{mode}' is standalone — use its handler directly")

    date_str = datetime.now().strftime("%Y-%m-%d")
    log.info(f"=== {mode_key} cycle start ===")

    # Pre-process: collect data before agent call
    pre_process = mode_cfg.get("pre_process")
    if pre_process == "collect_content_and_feeds":
        context.update(await _pre_process_digest(config, client))
    elif pre_process == "collect_feeds":
        context.update(_pre_process_intel(config))
    elif pre_process == "scan_teams_inbox":
        context.update(await _pre_process_monitor(config))

    # Build trigger prompt
    prompt = _build_trigger_prompt(mode_key, mode_cfg, config, context)

    # Determine timeout — generous limits since agent may use many tools
    timeout = 3600 if mode_key == "research" else 1800

    # Run the session
    async with agent_session(
        client, config, mode_key,
        tools=get_tools(),
        telegram_app=telegram_app,
        chat_id=chat_id,
        on_delta=on_delta,
    ) as (session, handler):
        log.info("  Agent working...")
        await session.send({"prompt": prompt})

        try:
            await asyncio.wait_for(handler.done.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            log.warning(f"  Agent timed out after {timeout}s (partial text: {bool(handler.final_text)})")
            return handler.final_text  # return partial if available

        if handler.error:
            log.error(f"  Session error: {handler.error}")
            return None

        # Post-process: validate digest JSON if written
        if mode_key == "digest":
            _validate_digest_json(date_str)

        log.info(f"=== {mode_key} cycle end ===")
        return handler.final_text


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
            dismissed_lines = []
            for d in dismissed:
                reason = d.get("reason", "")
                if reason:
                    dismissed_lines.append(f"- {d['item']} — *Reason: {reason}*")
                else:
                    dismissed_lines.append(f"- {d['item']}")
            dismissed_items = "\n".join(dismissed_lines)
            variables["dismissed_block"] = (
                f"\n## Previously Dismissed Items (DO NOT include these)\n{dismissed_items}\n"
                "\nLearn from these: if items were dismissed because \"already replied\" or "
                "\"not my responsibility\", apply the same logic to similar items today.\n"
            )
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
        content_block = context.get("content_block", "")
        if not content_block or not content_block.strip():
            content_block = (
                "No new local content since last digest. "
                "Focus on WorkIQ inbox check and carry-forward verification only."
            )
        variables["content_sections"] = content_block

        # Articles block from pre-processing
        variables["articles_block"] = context.get("articles_block", "")

        # Teams inbox scan (ground truth for unread messages)
        variables["teams_inbox_block"] = context.get("teams_inbox_block", "Teams inbox scan unavailable.")

        # Outlook inbox scan
        variables["outlook_inbox_block"] = context.get("outlook_inbox_block", "Outlook inbox scan unavailable.")

        # Calendar scan
        variables["calendar_block"] = context.get("calendar_block", "Calendar scan unavailable.")

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
        variables["outlook_inbox_block"] = context.get("outlook_inbox_block", "Outlook inbox scan unavailable.")
        variables["calendar_block"] = context.get("calendar_block", "Calendar scan unavailable.")

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


MAX_CARRY_FORWARD_DAYS = 5


def _build_carry_forward(prev: dict | None) -> str:
    """Build carry-forward block from previous digest items.

    Items older than MAX_CARRY_FORWARD_DAYS are auto-dropped — if they
    haven't been verified or dismissed in that time, they're stale.
    """
    if not prev or not prev.get("items"):
        return ""

    today = datetime.now().date()
    fresh_items = []
    stale_count = 0

    for item in prev["items"]:
        item_date_str = item.get("date", "")
        try:
            item_date = datetime.strptime(item_date_str, "%Y-%m-%d").date()
            age_days = (today - item_date).days
        except (ValueError, TypeError):
            age_days = 0

        if age_days > MAX_CARRY_FORWARD_DAYS:
            stale_count += 1
            log.info(f"  Dropping stale item ({age_days}d old): {item.get('title', '?')}")
            continue

        item["_age_days"] = age_days
        fresh_items.append(item)

    if not fresh_items:
        if stale_count:
            return f"(Auto-dropped {stale_count} items older than {MAX_CARRY_FORWARD_DAYS} days.)\n"
        return ""

    lines = [
        "## Known Outstanding Items (from previous digest)\n",
        "These items were flagged previously. For each one:",
        "- **KEEP** if still unresolved (no reply sent, no action taken)",
        "- **DROP** if WorkIQ or Teams inbox scan confirms it's been handled",
        "- **UPDATE** if there's new activity on the same thread\n",
    ]

    if stale_count:
        lines.append(f"(Auto-dropped {stale_count} items older than {MAX_CARRY_FORWARD_DAYS} days.)\n")

    for item in fresh_items:
        priority = item.get("priority", "?")
        title = item.get("title", "?")
        item_id = item.get("id", "?")
        source = item.get("source", "?")
        date = item.get("date", "?")
        age = item.get("_age_days", 0)
        age_label = f", {age}d outstanding" if age > 0 else ", new today"
        lines.append(
            f"- [{priority.upper()}] **{title}** "
            f"(id: {item_id}, source: {source}, date: {date}{age_label})"
        )
    return "\n".join(lines)


REQUIRED_ITEM_FIELDS = {"id", "priority", "date", "title", "source"}


def _validate_digest_json(date_str: str):
    """Validate the digest JSON after the agent writes it.

    Checks that the file exists, is valid JSON, and each item has the
    required fields for carry-forward to work correctly.
    """
    digest_file = OUTPUT_DIR / "digests" / f"{date_str}.json"
    if not digest_file.exists():
        log.warning(f"  Digest validation: {date_str}.json was not written by agent")
        return

    try:
        data = json.loads(digest_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        log.error(f"  Digest validation: {date_str}.json is invalid JSON — {e}")
        return

    if "items" not in data:
        log.warning("  Digest validation: missing 'items' key")
        return

    items = data["items"]
    issues = []
    for i, item in enumerate(items):
        missing = REQUIRED_ITEM_FIELDS - set(item.keys())
        if missing:
            issues.append(f"  item[{i}] ({item.get('id', '?')}): missing {missing}")

        # Check date format for carry-forward
        item_date = item.get("date", "")
        if item_date:
            try:
                datetime.strptime(item_date, "%Y-%m-%d")
            except ValueError:
                issues.append(f"  item[{i}] ({item.get('id', '?')}): bad date format '{item_date}'")

    if issues:
        log.warning(f"  Digest validation: {len(issues)} issue(s) in {date_str}.json:")
        for issue in issues:
            log.warning(issue)
    else:
        log.info(f"  Digest validation: {date_str}.json OK ({len(items)} items)")


async def _pre_process_monitor(config: dict) -> dict:
    """Scan Teams inbox, Outlook inbox, and calendar before monitor agent call."""
    from collectors.teams_inbox import scan_teams_inbox, format_inbox_for_prompt
    from collectors.outlook_inbox import scan_outlook_inbox, format_outlook_for_prompt
    from collectors.calendar import scan_calendar, format_calendar_for_prompt

    scan_time = datetime.now().strftime("%H:%M:%S")

    log.info("Phase 0: Scanning Teams inbox for unread messages...")
    items = await scan_teams_inbox(config)
    if items:
        log.info(f"  Found {len(items)} unread Teams messages (scanned at {scan_time})")
    else:
        log.info(f"  No unread Teams messages detected (scanned at {scan_time}).")

    formatted = format_inbox_for_prompt(items)
    formatted = f"*(Scanned at {scan_time} — data may be 5-10 min stale by the time you read this)*\n\n{formatted}"

    log.info("Phase 0b: Scanning Outlook inbox for unread emails...")
    outlook_items = await scan_outlook_inbox(config)
    outlook_block = format_outlook_for_prompt(outlook_items)
    outlook_block = f"*(Scanned at {scan_time})*\n\n{outlook_block}"

    log.info("Phase 0c: Scanning calendar for today's events...")
    cal_events = await scan_calendar(config)
    calendar_block = format_calendar_for_prompt(cal_events)

    return {
        "teams_inbox": formatted,
        "outlook_inbox_block": outlook_block,
        "calendar_block": calendar_block,
    }


async def _pre_process_digest(config: dict, client=None) -> dict:
    """Collect local content, RSS feeds, and Teams inbox scan before digest.

    The Teams inbox scan provides ground truth about what's actually unread
    right now — critical for verifying carry-forward items when WorkIQ is down.
    """
    from collectors.content import collect_content
    from collectors.feeds import collect_feeds
    from collectors.teams_inbox import scan_teams_inbox, format_inbox_for_prompt
    from collectors.outlook_inbox import scan_outlook_inbox, format_outlook_for_prompt
    from collectors.calendar import scan_calendar, format_calendar_for_prompt
    from collectors.transcripts.compressor import compress_existing_transcripts

    # Phase 0: Compress any raw .txt transcripts before content collection
    if client:
        transcripts_dir = PROJECT_ROOT / "input" / "transcripts"
        if transcripts_dir.exists() and list(transcripts_dir.glob("*.txt")):
            tc_models = config.get("models", {})
            compress_model = tc_models.get("transcripts", tc_models.get("default", "claude-sonnet"))
            log.info("Phase 0: Compressing raw transcripts via GHCP SDK...")
            compressed_count = await compress_existing_transcripts(client, transcripts_dir, model=compress_model)
            log.info(f"  Compressed {compressed_count} transcripts")

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

    log.info("\nPhase 1c: Scanning Teams inbox for unread messages...")
    teams_items = await scan_teams_inbox(config)
    scan_time = datetime.now().strftime("%H:%M:%S")
    teams_inbox_block = format_inbox_for_prompt(teams_items)
    # Prepend timestamp so the agent knows when this snapshot was taken
    teams_inbox_block = f"*(Scanned at {scan_time} — data may be 5-10 min stale by the time you read this)*\n\n{teams_inbox_block}"
    if teams_items:
        log.info(f"  Teams inbox: {len(teams_items)} unread (scanned at {scan_time})")
    else:
        log.info(f"  Teams inbox: no unread messages (scanned at {scan_time})")

    log.info("\nPhase 1d: Scanning Outlook inbox for unread emails...")
    outlook_items = await scan_outlook_inbox(config)
    outlook_block = format_outlook_for_prompt(outlook_items)
    outlook_block = f"*(Scanned at {scan_time})*\n\n{outlook_block}"
    if outlook_items:
        log.info(f"  Outlook inbox: {len(outlook_items)} unread (scanned at {scan_time})")
    else:
        log.info(f"  Outlook inbox: no unread emails (scanned at {scan_time})")

    log.info("\nPhase 1e: Scanning calendar for today's events...")
    cal_events = await scan_calendar(config)
    calendar_block = format_calendar_for_prompt(cal_events)
    active_count = len([e for e in cal_events if not e.get("is_declined")])
    log.info(f"  Calendar: {active_count} active events today")

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

    # Build articles block — with filter instruction
    articles_block = ""
    if articles:
        article_lines = [f"- [{a['source']}] **{a['title']}** ({a['published']})" for a in articles]
        articles_block = (
            f"\n## Part C — External Intel ({len(articles)} articles from RSS feeds)\n"
            f"**FILTER**: Only include articles that directly name an active customer "
            f"(Vodafone, Colt, QBE, Havas) or a competitor in a live deal. "
            f"Generic AI/LLM news belongs in the separate intel mode — skip it here.\n\n"
            + "\n".join(article_lines) + "\n"
        )

    return {
        "content_block": content_block,
        "articles_block": articles_block,
        "articles": articles,
        "teams_inbox_block": teams_inbox_block,
        "outlook_inbox_block": outlook_block,
        "calendar_block": calendar_block,
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
