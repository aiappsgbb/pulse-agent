"""Unified config-driven job runner — replaces per-mode orchestration functions."""


class ProxyError(RuntimeError):
    """Raised when GHCP SDK fails with a proxy/firewall 502 (ProxyResponseError)."""

import asyncio
import json
from datetime import datetime
from pathlib import Path

import yaml

from copilot import CopilotClient

from core.constants import (
    PROJECT_ROOT, OUTPUT_DIR, CONFIG_DIR, PROJECTS_DIR, DIGESTS_DIR,
    TRANSCRIPTS_DIR, EMAILS_DIR, TEAMS_MESSAGES_DIR, DOCUMENTS_DIR, PULSE_HOME,
)
from core.logging import log
from core.state import load_json_state, save_json_state
from sdk.prompts import load_prompt
from sdk.session import agent_session, load_modes
from sdk.tools import get_tools, load_actions


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

    modes = load_modes()
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
        context.update(await _pre_process_intel(config, client))
    elif pre_process == "scan_teams_inbox":
        context.update(await _pre_process_monitor(config))
    elif pre_process == "collect_knowledge_context":
        context.update(await _pre_process_knowledge(config))

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
            if "ProxyResponseError" in str(handler.error):
                raise ProxyError(f"HTTP 502 proxy error: {handler.error}")
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

        # Dismissed items (auto-expire after 30 days)
        actions = load_actions()
        dismissed_raw = actions.get("dismissed", [])
        dismissed = []
        for d in dismissed_raw:
            try:
                dismissed_at = datetime.fromisoformat(d.get("dismissed_at", ""))
                if (datetime.now() - dismissed_at).days > 30:
                    continue
            except (ValueError, TypeError):
                pass
            dismissed.append(d)
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

        # Project memory & commitments
        variables["projects_block"] = context.get("projects_block", "")
        variables["commitments_summary"] = context.get("commitments_summary", "")

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
            why = a.get("why", "")
            line = (
                f"- [{a['source']}] **{a['title']}**\n"
                f"  Link: {a['link']}\n"
                f"  Published: {a['published']}"
            )
            if why:
                line += f"\n  Why it matters: {why}"
            else:
                line += f"\n  Summary: {a['summary']}"
            article_lines.append(line)
        variables["articles"] = "\n\n".join(article_lines)

    elif mode == "monitor":
        variables["teams_inbox"] = context.get("teams_inbox", "No Teams inbox data available.")
        variables["outlook_inbox_block"] = context.get("outlook_inbox_block", "Outlook inbox scan unavailable.")
        variables["calendar_block"] = context.get("calendar_block", "Calendar scan unavailable.")

    elif mode == "knowledge-archive":
        variables["date"] = date_str
        variables["lookback_window"] = context.get("lookback_window", "48 hours")
        variables["lookback_note"] = context.get("lookback_note", "")
        variables["recent_artifacts"] = context.get("recent_artifacts", "No recent artifacts found.")
        variables["teams_inbox_block"] = context.get("teams_inbox_block", "Teams inbox scan unavailable.")
        variables["outlook_inbox_block"] = context.get("outlook_inbox_block", "Outlook inbox scan unavailable.")

    elif mode == "knowledge-project":
        variables["date"] = date_str
        variables["lookback_window"] = context.get("lookback_window", "48 hours")
        variables["project_id"] = context.get("project_id", "unknown")
        variables["project_name"] = context.get("project_name", "Unknown Project")
        variables["project_yaml"] = context.get("project_yaml", "# No project data")
        variables["recent_artifacts"] = context.get("recent_artifacts", "No recent artifacts found.")

    elif mode == "research":
        task = context.get("task", {})
        variables["task"] = task.get("task", "unnamed")
        variables["description"] = task.get("description", variables["task"])
        output_cfg = task.get("output", {})
        variables["output_path"] = output_cfg.get("local", str(PULSE_HOME))

    return variables


def _load_previous_digest() -> dict | None:
    """Load the most recent digest JSON for carry-forward."""
    digests_dir = DIGESTS_DIR
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


def _load_projects() -> list[dict]:
    """Load all project memory files from output/projects/*.yaml.

    Returns list of parsed project dicts. Skips files with YAML errors.
    """
    if not PROJECTS_DIR.exists():
        return []

    projects = []
    for path in sorted(PROJECTS_DIR.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data["_file"] = path.name
                projects.append(data)
        except Exception as e:
            log.warning(f"  Skipping corrupt project file {path.name}: {e}")
    return projects


def _build_projects_block(projects: list[dict]) -> str:
    """Format project data for injection into the digest trigger prompt."""
    if not projects:
        return ""

    lines = [
        "## Part D -- Active Projects & Engagements\n",
        "These are your known active projects with tracked commitments.",
        "For each project: verify status, update commitments, add new findings from today's content.",
        "Use the `update_project` tool to save changes to any project file.\n",
    ]

    active = [p for p in projects if p.get("status") in ("active", "blocked", None)]
    other = [p for p in projects if p.get("status") not in ("active", "blocked", None)]

    for project in active:
        name = project.get("project", project.get("_file", "unnamed"))
        status = project.get("status", "active")
        risk = project.get("risk_level", "unknown")
        pid = project.get("_file", "").replace(".yaml", "")
        lines.append(f"### {name} (status: {status}, risk: {risk}, file: {pid})")

        stakeholders = project.get("stakeholders", [])
        if stakeholders:
            contacts = ", ".join(
                f"{s.get('name', '?')} ({s.get('role', '?')})" if s.get("role") else s.get("name", "?")
                for s in stakeholders
            )
            lines.append(f"  Stakeholders: {contacts}")

        summary = project.get("summary", "")
        if summary:
            lines.append(f"  Context: {summary}")

        commitments = project.get("commitments", [])
        if commitments:
            lines.append("  Commitments:")
            for c in commitments:
                c_status = c.get("status", "open")
                what = c.get("what", "?")
                to = c.get("to", "?")
                due = c.get("due", "no deadline")
                lines.append(f"    - [{c_status.upper()}] {what} (to: {to}, due: {due})")

        next_mtg = project.get("next_meeting", "")
        if next_mtg:
            lines.append(f"  Next meeting: {next_mtg}")

        lines.append("")

    if other:
        statuses = ", ".join(sorted(set(p.get("status", "?") for p in other)))
        lines.append(f"({len(other)} other project(s) with status: {statuses})\n")

    return "\n".join(lines)


def _extract_commitments_summary(projects: list[dict]) -> str:
    """Build a global commitment summary across all projects.

    Highlights overdue and approaching-deadline commitments.
    """
    if not projects:
        return ""

    today = datetime.now().date()
    overdue = []
    upcoming = []
    open_count = 0

    for project in projects:
        project_name = project.get("project", project.get("_file", "?"))
        for c in project.get("commitments", []):
            if c.get("status") == "done":
                continue
            open_count += 1
            what = c.get("what", "?")
            to = c.get("to", "?")
            due_str = c.get("due", "")

            try:
                due_date = datetime.strptime(str(due_str), "%Y-%m-%d").date()
                days_until = (due_date - today).days
            except (ValueError, TypeError):
                days_until = None

            entry = f"- **{what}** (to: {to}, project: {project_name}, due: {due_str})"

            if days_until is not None and days_until < 0:
                entry += f" -- **{abs(days_until)} days OVERDUE**"
                overdue.append(entry)
            elif days_until is not None and days_until <= 3:
                entry += f" -- due in {days_until} day(s)"
                upcoming.append(entry)

    if not overdue and not upcoming:
        if open_count:
            return f"({open_count} open commitment(s), none overdue or due soon.)\n"
        return ""

    lines = ["## Commitment Status\n"]
    if overdue:
        lines.append(f"**OVERDUE ({len(overdue)}):**")
        lines.extend(overdue)
        lines.append("")
    if upcoming:
        lines.append(f"**Due soon ({len(upcoming)}):**")
        lines.extend(upcoming)
        lines.append("")
    if open_count > len(overdue) + len(upcoming):
        remaining = open_count - len(overdue) - len(upcoming)
        lines.append(f"({remaining} other open commitment(s) with no imminent deadline.)\n")

    return "\n".join(lines)


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
    digest_file = DIGESTS_DIR / f"{date_str}.json"
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
    if items is None:
        formatted = "**Teams inbox scan UNAVAILABLE** — browser not running."
        log.warning("  Teams inbox: UNAVAILABLE — browser not running")
    elif items:
        formatted = f"*(Scanned at {scan_time} — data may be 5-10 min stale by the time you read this)*\n\n{format_inbox_for_prompt(items)}"
        log.info(f"  Found {len(items)} unread Teams messages (scanned at {scan_time})")
    else:
        formatted = f"*(Scanned at {scan_time} — data may be 5-10 min stale by the time you read this)*\n\n{format_inbox_for_prompt(items)}"
        log.info(f"  No unread Teams messages detected (scanned at {scan_time}).")

    log.info("Phase 0b: Scanning Outlook inbox for unread emails...")
    outlook_items = await scan_outlook_inbox(config)
    if outlook_items is None:
        outlook_block = "**Outlook inbox scan UNAVAILABLE** — browser not running."
    else:
        outlook_block = f"*(Scanned at {scan_time})*\n\n{format_outlook_for_prompt(outlook_items)}"

    log.info("Phase 0c: Scanning calendar for upcoming events...")
    cal_events = await scan_calendar(config)
    if cal_events is None:
        calendar_block = "**Calendar scan UNAVAILABLE** — browser not running."
    else:
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

    # Transcript collection + compression runs in the overnight knowledge pipeline
    # (daily 02:00). The digest just reads whatever was already collected/compressed.
    # Knowledge mining also runs overnight — projects are already enriched by morning.

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
    articles_filtered = True  # assume filtered unless proven otherwise
    if articles:
        log.info(f"  Collected {len(articles)} new articles")
        # Pre-filter via SDK — only keep articles worth reading
        if client:
            from collectors.article_filter import filter_articles
            intel_cfg = config.get("intelligence", {})
            articles, articles_filtered = await filter_articles(
                client, articles,
                topics=intel_cfg.get("topics"),
                competitors=intel_cfg.get("competitors"),
                model=config.get("models", {}).get("intel", "gpt-4.1"),
            )
    else:
        log.info("  No new articles.")

    log.info("\nPhase 1c: Scanning Teams inbox for unread messages...")
    teams_items = await scan_teams_inbox(config)
    scan_time = datetime.now().strftime("%H:%M:%S")
    if teams_items is None:
        teams_inbox_block = "**Teams inbox scan UNAVAILABLE** — browser not running. Cannot verify unread messages."
        log.warning("  Teams inbox: UNAVAILABLE — browser not running")
    elif teams_items:
        teams_inbox_block = f"*(Scanned at {scan_time} — data may be 5-10 min stale by the time you read this)*\n\n{format_inbox_for_prompt(teams_items)}"
        log.info(f"  Teams inbox: {len(teams_items)} unread (scanned at {scan_time})")
    else:
        teams_inbox_block = f"*(Scanned at {scan_time} — data may be 5-10 min stale by the time you read this)*\n\n{format_inbox_for_prompt(teams_items)}"
        log.info(f"  Teams inbox: no unread messages (scanned at {scan_time})")

    log.info("\nPhase 1d: Scanning Outlook inbox for unread emails...")
    outlook_items = await scan_outlook_inbox(config)
    if outlook_items is None:
        outlook_block = "**Outlook inbox scan UNAVAILABLE** — browser not running. Cannot verify unread emails."
        log.warning("  Outlook inbox: UNAVAILABLE — browser not running")
    elif outlook_items:
        outlook_block = f"*(Scanned at {scan_time})*\n\n{format_outlook_for_prompt(outlook_items)}"
        log.info(f"  Outlook inbox: {len(outlook_items)} unread (scanned at {scan_time})")
    else:
        outlook_block = f"*(Scanned at {scan_time})*\n\n{format_outlook_for_prompt(outlook_items)}"
        log.info(f"  Outlook inbox: no unread emails (scanned at {scan_time})")

    log.info("\nPhase 1e: Scanning calendar for upcoming events...")
    cal_events = await scan_calendar(config)
    if cal_events is None:
        calendar_block = "**Calendar scan UNAVAILABLE** — browser not running."
        log.warning("  Calendar: UNAVAILABLE — browser not running")
    else:
        calendar_block = format_calendar_for_prompt(cal_events)
        active_count = len([e for e in cal_events if not e.get("is_declined")])
        log.info(f"  Calendar: {active_count} active events")

    log.info("\nPhase 1f: Loading active project files...")
    projects = _load_projects()
    projects_block = _build_projects_block(projects)
    commitments_summary = _extract_commitments_summary(projects)
    if projects:
        log.info(f"  Loaded {len(projects)} project(s)")
    else:
        log.info("  No project files found")

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

    # Build articles block — pre-filtered by SDK, each article has a "why it matters"
    articles_block = ""
    if articles:
        article_lines = []
        for a in articles:
            why = a.get("why", "")
            line = f"- [{a['source']}] **{a['title']}**"
            if why:
                line += f" — {why}"
            article_lines.append(line)
        if articles_filtered:
            articles_block = (
                f"\n## Part C — External Intel ({len(articles)} pre-filtered articles)\n"
                f"These articles have already been filtered for relevance. Include any that "
                f"affect active customers, competitive positioning, or upcoming conversations.\n\n"
                + "\n".join(article_lines) + "\n"
            )
        else:
            articles_block = (
                f"\n## Part C — External Intel ({len(articles)} UNFILTERED articles)\n"
                f"**WARNING: Article filter failed.** These articles are raw and unfiltered. "
                f"Most are noise. Only include articles that DIRECTLY affect active customers, "
                f"competitive positioning, or upcoming conversations. Be very selective.\n\n"
                + "\n".join(article_lines) + "\n"
            )

    return {
        "content_block": content_block,
        "articles_block": articles_block,
        "articles": articles,
        "teams_inbox_block": teams_inbox_block,
        "outlook_inbox_block": outlook_block,
        "calendar_block": calendar_block,
        "projects_block": projects_block,
        "commitments_summary": commitments_summary,
    }


async def _pre_process_intel(config: dict, client: CopilotClient | None = None) -> dict:
    """Collect RSS feeds and pre-filter via SDK before intel agent call."""
    from collectors.feeds import collect_feeds

    log.info("Phase 1: Fetching RSS feeds...")
    articles = collect_feeds(config)

    if not articles:
        log.info("  No new articles.")
    else:
        log.info(f"  Collected {len(articles)} new articles")
        # Pre-filter via SDK
        if client:
            from collectors.article_filter import filter_articles
            intel_cfg = config.get("intelligence", {})
            articles, _ = await filter_articles(
                client, articles,
                topics=intel_cfg.get("topics"),
                competitors=intel_cfg.get("competitors"),
                model=config.get("models", {}).get("intel", "gpt-4.1"),
            )

    return {"articles": articles}


def _list_recent_artifacts(days: int = 2) -> str:
    """List recently modified files across knowledge directories.

    Returns a formatted string of recent artifacts for the knowledge-miner
    agent to process. Pure file listing — no LLM, no content extraction.
    """
    from datetime import timedelta
    cutoff = datetime.now().timestamp() - (days * 86400)

    dirs = [
        ("transcripts", TRANSCRIPTS_DIR),
        ("emails", EMAILS_DIR),
        ("teams-messages", TEAMS_MESSAGES_DIR),
        ("documents", DOCUMENTS_DIR),
    ]

    lines = []
    for label, directory in dirs:
        if not directory.exists():
            continue
        files = []
        for f in sorted(directory.rglob("*.*"), key=lambda p: p.stat().st_mtime, reverse=True):
            if not f.is_file():
                continue
            try:
                if f.stat().st_mtime >= cutoff:
                    rel = f.relative_to(directory)
                    size = f.stat().st_size
                    files.append(f"  - {label}/{rel} ({size:,} bytes)")
            except Exception:
                continue

        if files:
            lines.append(f"### {label.title()} ({len(files)} recent files)")
            lines.extend(files[:20])  # cap at 20 per dir to keep prompt manageable
            if len(files) > 20:
                lines.append(f"  ... and {len(files) - 20} more")
            lines.append("")

    if not lines:
        return "No recent artifacts found in the last 48 hours."

    return "\n".join(lines)


KNOWLEDGE_STATE_FILE = PULSE_HOME / ".knowledge-state.json"


async def _pre_process_knowledge(config: dict) -> dict:
    """Lightweight pre-process for knowledge mode.

    Loads project state and lists recent artifacts — everything else is
    agent-driven via WorkIQ, write_output, and update_project tools.
    """
    from collectors.teams_inbox import scan_teams_inbox, format_inbox_for_prompt
    from collectors.outlook_inbox import scan_outlook_inbox, format_outlook_for_prompt

    log.info("Knowledge pre-process: Loading projects and listing recent artifacts...")

    # Load projects (reuse existing function)
    projects = _load_projects()
    projects_block = _build_projects_block(projects)
    commitments_summary = _extract_commitments_summary(projects)
    log.info(f"  Loaded {len(projects)} project(s)")

    # List recent artifacts for agent context
    recent_artifacts = _list_recent_artifacts(days=2)
    log.info(f"  Listed recent artifacts")

    # Determine lookback window from last knowledge run
    state = load_json_state(KNOWLEDGE_STATE_FILE, {})
    last_run = state.get("last_run")
    if last_run:
        lookback_note = f"Last knowledge run: {last_run}. Focus on content since then."
        lookback_window = f"since {last_run}"
    else:
        lookback_note = "First knowledge run — archive last 48 hours of communications."
        lookback_window = "48 hours"

    # Update last_run timestamp
    save_json_state(KNOWLEDGE_STATE_FILE, {"last_run": datetime.now().isoformat()})

    # Quick inbox snapshots for cross-reference (same as monitor)
    scan_time = datetime.now().strftime("%H:%M:%S")

    teams_items = await scan_teams_inbox(config)
    if teams_items is None:
        teams_inbox_block = "**Teams inbox scan UNAVAILABLE** — browser not running."
    else:
        teams_inbox_block = f"*(Scanned at {scan_time})*\n\n{format_inbox_for_prompt(teams_items)}"

    outlook_items = await scan_outlook_inbox(config)
    if outlook_items is None:
        outlook_block = "**Outlook inbox scan UNAVAILABLE** — browser not running."
    else:
        outlook_block = f"*(Scanned at {scan_time})*\n\n{format_outlook_for_prompt(outlook_items)}"

    return {
        "projects_block": projects_block,
        "commitments_summary": commitments_summary,
        "recent_artifacts": recent_artifacts,
        "lookback_window": lookback_window,
        "lookback_note": lookback_note,
        "teams_inbox_block": teams_inbox_block,
        "outlook_inbox_block": outlook_block,
    }


async def run_knowledge_pipeline(client, config: dict):
    """Run the full knowledge mining pipeline: archive once, then enrich per-project.

    This replaces the old monolithic knowledge session. Architecture:
    1. One archive session: fetch emails/Teams via WorkIQ, discover new projects
    2. N enrichment sessions: one per active/blocked project
    Each project gets a focused session with just its context — the agent uses
    WorkIQ and search_local_files to determine what's worth updating.
    """
    log.info("=== Knowledge pipeline start ===")

    # Phase 0: Collect fresh transcripts + compress
    # Knowledge needs fresh data to mine — transcripts, emails, Teams messages.
    # Transcript collection runs here (not in digest) so overnight runs have fresh content.
    from collectors.transcripts.compressor import compress_existing_transcripts
    from collectors.transcripts import run_transcript_collection
    from core.browser import get_browser_manager

    browser_mgr = get_browser_manager()
    if browser_mgr and browser_mgr.context:
        log.info("  Phase 0a: Collecting fresh transcripts from Teams...")
        try:
            await asyncio.wait_for(
                run_transcript_collection(client, config),
                timeout=1800,  # 30 min cap
            )
        except asyncio.TimeoutError:
            log.warning("    Transcript collection timed out (non-fatal)")
        except Exception as e:
            log.warning(f"    Transcript collection failed (non-fatal): {e}")
    else:
        log.info("  Phase 0a: Skipping transcript collection (no browser)")

    if client:
        transcripts_dir = TRANSCRIPTS_DIR
        if transcripts_dir.exists() and list(transcripts_dir.glob("*.txt")):
            tc_models = config.get("models", {})
            compress_model = tc_models.get("transcripts", tc_models.get("default", "claude-sonnet"))
            log.info("  Phase 0b: Compressing raw transcripts via GHCP SDK...")
            compressed_count = await compress_existing_transcripts(client, transcripts_dir, model=compress_model)
            log.info(f"    Compressed {compressed_count} transcripts")

    # Phase 1: Run archive session (global — emails, Teams messages, new project discovery)
    log.info("  Phase 1: Archiving emails/Teams messages + discovering new projects...")
    try:
        await asyncio.wait_for(
            run_job(client, config, "knowledge-archive"),
            timeout=600,  # 10 min cap for archival
        )
        log.info("  Phase 1 complete.")
    except asyncio.TimeoutError:
        log.warning("  Archive phase timed out after 5 minutes (continuing to enrichment)")
    except Exception as e:
        log.warning(f"  Archive phase failed: {e} (continuing to enrichment)")

    # Phase 2: Per-project enrichment — reload projects (archive may have created new ones)
    projects = _load_projects()
    if not projects:
        log.info("  No projects to enrich. Pipeline done.")
        return

    # Only process active/blocked projects — completed/on-hold don't need enrichment
    active = [p for p in projects if p.get("status") in ("active", "blocked", None)]
    if not active:
        log.info(f"  {len(projects)} projects loaded but none are active/blocked. Skipping enrichment.")
        return

    recent_artifacts = _list_recent_artifacts(days=2)

    # Determine lookback window for per-project context
    state = load_json_state(KNOWLEDGE_STATE_FILE, {})
    last_run = state.get("last_run")
    lookback_window = f"since {last_run}" if last_run else "48 hours"

    log.info(f"  Phase 2: Enriching {len(active)} active projects:")
    for p in active:
        pid = p.get("_file", "").replace(".yaml", "")
        log.info(f"    - {p.get('project', pid)}")

    enriched = 0
    for project in active:
        pid = project.get("_file", "").replace(".yaml", "")
        pname = project.get("project", pid)
        log.info(f"  Enriching: {pname} ({pid})...")

        # Build per-project context
        project_copy = {k: v for k, v in project.items() if k != "_file"}
        project_context = {
            "project_id": pid,
            "project_name": pname,
            "project_yaml": yaml.dump(project_copy, default_flow_style=False, allow_unicode=True),
            "recent_artifacts": recent_artifacts,
            "lookback_window": lookback_window,
        }

        try:
            await asyncio.wait_for(
                run_job(client, config, "knowledge-project", context=project_context),
                timeout=600,  # 10 min per project
            )
            enriched += 1
            log.info(f"    Done: {pname}")
        except asyncio.TimeoutError:
            log.warning(f"    Timeout enriching {pname} (10 min cap)")
        except Exception as e:
            log.warning(f"    Failed enriching {pname}: {e}")

    log.info(f"=== Knowledge pipeline done — enriched {enriched}/{len(active)} projects ===")
