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

# Session timeout constants (seconds)
_TIMEOUT_DEFAULT = 1800   # 30 min — triage, digest, intel, chat
_TIMEOUT_RESEARCH = 3600  # 60 min — deep research missions


async def run_job(
    client: CopilotClient,
    config: dict,
    mode: str,
    context: dict | None = None,
    on_delta=None,
    job_log_file: str | None = None,
) -> str | None:
    """Unified entry point for running any SDK-based job.

    Args:
        client: GHCP SDK client
        config: Parsed standing-instructions.yaml
        mode: Job mode (monitor, digest, intel, research, chat)
        context: Extra context for the job (e.g. research task details, chat prompt)
        on_delta: Optional callback for streaming text deltas
        job_log_file: Optional per-job activity log file path
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
    timeout = _TIMEOUT_RESEARCH if mode_key == "research" else _TIMEOUT_DEFAULT

    # Run the session
    async with agent_session(
        client, config, mode_key,
        tools=get_tools(),
        on_delta=on_delta,
        log_file=job_log_file,
    ) as (session, handler):
        log.info("  Agent working...")
        await session.send({"prompt": prompt})

        try:
            await asyncio.wait_for(handler.done.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            log.warning(f"  Agent timed out after {timeout}s (partial text: {bool(handler.final_text)})")
            return handler.final_text  # return partial if available

        if handler.error:
            error_str = str(handler.error)
            log.error(f"  Session error: {error_str}")
            if "ProxyResponseError" in error_str:
                raise ProxyError(f"HTTP 502 proxy error: {handler.error}")
            if "fetch failed" in error_str or "Request timed out" in error_str:
                raise ProxyError(f"Transient MCP/network error: {handler.error}")
            return None

        # Post-process: validate digest JSON if written
        if mode_key == "digest":
            _validate_digest_json(date_str)

        log.info(f"=== {mode_key} cycle end ===")
        return handler.final_text


def _build_dismissed_block() -> str:
    """Build dismissed items text block with tri-state TTL.

    Statuses:
      - snoozed (1-day TTL): temporarily hidden, re-surfaces tomorrow
      - archived (30-day TTL): suppressed for a month
      - resolved (no expiry): permanently done, never re-surface

    Notes are included inline so the agent knows WHY something was dismissed.
    """
    actions = load_actions()
    dismissed_raw = actions.get("dismissed", [])
    notes = actions.get("notes", {})
    snoozed = []
    archived = []
    resolved = []
    for d in dismissed_raw:
        try:
            dismissed_at = datetime.fromisoformat(d.get("dismissed_at", ""))
            age_days = (datetime.now() - dismissed_at).days
        except (ValueError, TypeError):
            age_days = 0
        status = d.get("status", "archived")  # legacy entries = archived
        if status == "resolved":
            resolved.append(d)  # no expiry
        elif status == "dismissed":
            if age_days > 1:
                continue  # expired snooze — agent can re-surface
            snoozed.append(d)
        else:
            if age_days > 30:
                continue  # expired archive
            archived.append(d)

    def _format_item(d: dict) -> str:
        item_id = d.get("item", "?")
        title = d.get("title", "")
        reason = d.get("reason", "")
        note_entry = notes.get(item_id, {})
        note = note_entry.get("note", "") if isinstance(note_entry, dict) else ""
        line = f"- {item_id}"
        if title:
            line += f" ({title})"
        if note:
            line += f" — *User note: \"{note}\"*"
        elif reason:
            line += f" — *Reason: {reason}*"
        return line

    dismissed_lines = []
    if snoozed:
        dismissed_lines.append("### Snoozed today (do NOT include — will re-surface tomorrow)")
        for d in snoozed:
            dismissed_lines.append(_format_item(d))
    if archived:
        dismissed_lines.append("### Archived (do NOT include — user dealt with these)")
        for d in archived:
            dismissed_lines.append(_format_item(d))
    if resolved:
        dismissed_lines.append("### Resolved (PERMANENTLY done — NEVER re-create these items or similar items about the same topic)")
        for d in resolved:
            dismissed_lines.append(_format_item(d))
    if dismissed_lines:
        return (
            "\n## Previously Dismissed Items\n"
            + "\n".join(dismissed_lines)
            + "\n"
        )
    return ""


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
    from sdk.prompts import load_enrichments

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

        # Dismissed items (notes are included inline in the dismissed block)
        variables["dismissed_block"] = _build_dismissed_block()
        variables["notes_block"] = ""  # notes now inline in dismissed_block

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

        # Collection warnings (transcript failures, stale data)
        variables["collection_warnings"] = context.get("collection_warnings", "")

        # CRM pipeline enrichment (optional — loaded from enrichment files when available)
        variables["msx_block"] = context.get("msx_gap_block", "")
        variables["msx_instructions"] = load_enrichments("trigger-digest")

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
        variables["dismissed_block"] = _build_dismissed_block()

        # CRM enrichment context (optional — loaded from enrichment files)
        variables["msx_context"] = load_enrichments("trigger-monitor")

    elif mode == "knowledge-archive":
        variables["date"] = date_str
        variables["lookback_window"] = context.get("lookback_window", "48 hours")
        variables["lookback_note"] = context.get("lookback_note", "")
        variables["recent_artifacts"] = context.get("recent_artifacts", "No recent artifacts found.")
        variables["teams_inbox_block"] = context.get("teams_inbox_block", "Teams inbox scan unavailable.")
        variables["outlook_inbox_block"] = context.get("outlook_inbox_block", "Outlook inbox scan unavailable.")

        # CRM enrichment (optional — loaded from enrichment files)
        variables["msx_instructions"] = load_enrichments("trigger-knowledge-archive")

    elif mode == "knowledge-project":
        variables["date"] = date_str
        variables["lookback_window"] = context.get("lookback_window", "48 hours")
        variables["project_id"] = context.get("project_id", "unknown")
        variables["project_name"] = context.get("project_name", "Unknown Project")
        variables["project_yaml"] = context.get("project_yaml", "# No project data")
        variables["recent_artifacts"] = context.get("recent_artifacts", "No recent artifacts found.")

        # CRM enrichment (optional — loaded from enrichment files)
        variables["msx_instructions"] = load_enrichments("trigger-knowledge-project")

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


_STALE_OVERDUE_DAYS = 5  # Auto-cancel commitments overdue by more than this


def _auto_cancel_stale_commitments(max_overdue_days: int = _STALE_OVERDUE_DAYS) -> tuple[int, list[dict]]:
    """Auto-cancel commitments overdue by more than max_overdue_days.

    Scans all project YAML files, finds open/overdue commitments with due dates
    far enough in the past, marks them cancelled, and saves back.
    Returns (number_cancelled, loaded_projects) — reuse projects to avoid re-reading.
    """
    if not PROJECTS_DIR.exists():
        return 0, []

    today = datetime.now().date()
    total_cancelled = 0
    projects = []

    for path in sorted(PROJECTS_DIR.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                continue
        except Exception:
            continue

        commitments = data.get("commitments")
        changed = False
        if commitments and isinstance(commitments, list):
            for c in commitments:
                status = (c.get("status") or "").lower()
                if status in ("done", "cancelled"):
                    continue
                due_raw = c.get("due")
                if not due_raw:
                    continue
                try:
                    due_date = datetime.strptime(str(due_raw), "%Y-%m-%d").date()
                except (ValueError, TypeError):
                    continue
                days_overdue = (today - due_date).days
                confidence = (c.get("due_confidence") or "explicit").lower()
                if days_overdue > max_overdue_days:
                    if confidence == "inferred":
                        c["status"] = "cancelled"
                        c["cancelled_reason"] = f"Auto-cancelled: inferred deadline {days_overdue}d past"
                    else:
                        c["status"] = "cancelled"
                        c["cancelled_reason"] = f"Auto-cancelled: {days_overdue}d overdue (>{max_overdue_days}d limit)"
                    changed = True
                    total_cancelled += 1
                    log.info(f"  Auto-cancelled: '{c.get('what', '?')}' in {path.stem} ({days_overdue}d overdue, confidence={confidence})")

            if changed:
                data["updated_at"] = datetime.now().isoformat()
                with open(path, "w", encoding="utf-8") as f:
                    yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

        data["_file"] = path.name
        projects.append(data)

    return total_cancelled, projects


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
        involvement = project.get("involvement", "observer")
        pid = project.get("_file", "").replace(".yaml", "")
        lines.append(f"### {name} (involvement: {involvement}, status: {status}, risk: {risk}, file: {pid})")

        stakeholders = project.get("stakeholders", [])
        if stakeholders:
            contacts = ", ".join(
                (f"{s.get('name', '?')} ({s.get('role', '?')})" if s.get("role") else s.get("name", "?"))
                if isinstance(s, dict) else str(s)
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

        # Surface CRM pipeline data when present (optional — absent for non-CRM users)
        msx = project.get("msx", {})
        if msx and isinstance(msx, dict):
            opp = msx.get("opportunity_name", msx.get("opportunity_id", ""))
            stage = msx.get("stage", "?")
            revenue = msx.get("revenue", "?")
            close = msx.get("close_date", "")
            in_team = msx.get("in_deal_team")
            solution_area = msx.get("solution_area", "")
            deal_type = msx.get("deal_type", "")

            msx_line = f"  CRM: {opp} | Stage: {stage} | Revenue: {revenue}"
            if solution_area:
                msx_line += f" | {solution_area}"
            if deal_type:
                msx_line += f" | {deal_type}"
            if close:
                msx_line += f" | Close: {close}"
            if in_team is False:
                msx_line += " | !! NOT in deal team"
            lines.append(msx_line)

            # Deal team members
            deal_team = msx.get("deal_team", [])
            if deal_team:
                team_str = ", ".join(
                    f"{m.get('name', '?')} ({m.get('role', '?')})"
                    if isinstance(m, dict) else str(m)
                    for m in deal_team[:6]
                )
                lines.append(f"  Deal team: {team_str}")

            # Active milestones (show up to 4 most relevant)
            milestones = msx.get("milestones", [])
            if milestones and isinstance(milestones, list):
                lines.append("  Milestones:")
                for ms in milestones[:4]:
                    if not isinstance(ms, dict):
                        continue
                    ms_name = ms.get("name", "?")
                    ms_status = ms.get("status", "?")
                    ms_date = ms.get("date", "")
                    ms_acr = ms.get("monthly_acr", "")
                    ms_line = f"    - [{ms_status}] {ms_name}"
                    if ms_date:
                        ms_line += f" (due: {ms_date})"
                    if ms_acr:
                        ms_line += f" — ACR: {ms_acr}"
                    lines.append(ms_line)

        lines.append("")

    if other:
        statuses = ", ".join(sorted(set(p.get("status", "?") for p in other)))
        lines.append(f"({len(other)} other project(s) with status: {statuses})\n")

    return "\n".join(lines)


def _build_msx_gap_block(projects: list[dict]) -> str:
    """Identify active projects without CRM opportunity links for gap detection.

    Returns a prompt block highlighting projects that may be missing from the
    CRM pipeline, or an empty string if all projects are linked (or no projects
    exist). Only called when CRM tools are available.
    """
    active = [p for p in projects if p.get("status") in ("active", "blocked", None)]
    if not active:
        return ""

    linked = [p for p in active if isinstance(p.get("msx"), dict) and p["msx"].get("opportunity_id")]
    unlinked = [p for p in active if not (isinstance(p.get("msx"), dict) and p["msx"].get("opportunity_id"))]

    if not unlinked:
        return ""  # All projects have MSX links — no gaps

    lines = [
        "\n## Part E — CRM Pipeline Gap Analysis\n",
        f"{len(linked)} of {len(active)} active projects are linked to CRM opportunities.",
        "The following projects have NO CRM opportunity link — search for matches:\n",
    ]
    for p in unlinked:
        name = p.get("project", p.get("_file", "?"))
        pid = p.get("_file", "").replace(".yaml", "")
        lines.append(f"- **{name}** (file: {pid}) — search CRM for this customer")
    lines.append("")
    return "\n".join(lines)


def _extract_commitments_summary(projects: list[dict]) -> str:
    """Build a global commitment summary across all projects.

    Highlights overdue and approaching-deadline commitments.
    Only treats commitments with explicit due dates as truly overdue.
    """
    if not projects:
        return ""

    today = datetime.now().date()
    hard_overdue = []
    soft_overdue = []
    upcoming = []
    open_count = 0

    for project in projects:
        project_name = project.get("project", project.get("_file", "?"))
        for c in project.get("commitments", []):
            if not isinstance(c, dict):
                continue
            if c.get("status") == "done":
                continue
            open_count += 1
            what = c.get("what", "?")
            to = c.get("to", "?")
            due_str = c.get("due", "")
            confidence = (c.get("due_confidence") or "explicit").lower()

            try:
                due_date = datetime.strptime(str(due_str), "%Y-%m-%d").date()
                days_until = (due_date - today).days
            except (ValueError, TypeError):
                days_until = None

            entry = f"- **{what}** (to: {to}, project: {project_name}, due: {due_str})"

            if days_until is not None and days_until < 0:
                if confidence == "explicit":
                    entry += f" -- **{abs(days_until)} days OVERDUE**"
                    hard_overdue.append(entry)
                else:
                    entry += f" -- inferred deadline {abs(days_until)}d past (soft due)"
                    soft_overdue.append(entry)
            elif days_until is not None and days_until <= 3:
                entry += f" -- due in {days_until} day(s)"
                upcoming.append(entry)

    if not hard_overdue and not soft_overdue and not upcoming:
        if open_count:
            return f"({open_count} open commitment(s), none overdue or due soon.)\n"
        return ""

    lines = ["## Commitment Status\n"]
    if hard_overdue:
        lines.append(f"**OVERDUE ({len(hard_overdue)}):**")
        lines.extend(hard_overdue)
        lines.append("")
    if soft_overdue:
        lines.append(f"**Soft due — inferred deadlines ({len(soft_overdue)}):**")
        lines.extend(soft_overdue)
        lines.append("")
    if upcoming:
        lines.append(f"**Due soon ({len(upcoming)}):**")
        lines.extend(upcoming)
        lines.append("")
    total_flagged = len(hard_overdue) + len(soft_overdue) + len(upcoming)
    if open_count > total_flagged:
        remaining = open_count - total_flagged
        lines.append(f"({remaining} other open commitment(s) with no imminent deadline.)\n")

    return "\n".join(lines)


MAX_CARRY_FORWARD_DAYS = 5


def _build_verification_query(item: dict) -> str:
    """Generate a specific WorkIQ verification query for a carry-forward item.

    Uses the item's own structured fields (source, title, type) directly —
    no regex parsing needed since the digest JSON already contains this data.
    """
    item_type = item.get("type", "")
    title = item.get("title", "")
    source = item.get("source", "")

    if item_type in ("reply_needed", "input_needed"):
        return f'Ask WorkIQ: "Did I reply to or interact with {source} recently?"'
    elif item_type in ("action_item", "action_needed", "review_needed"):
        return f'Ask WorkIQ: "Have I completed or acted on: {title[:80]}?"'
    elif source:
        return f'Ask WorkIQ: "Any recent activity related to {source}?"'
    return ""


def _build_carry_forward(prev: dict | None) -> str:
    """Build carry-forward block from previous digest items.

    Items older than MAX_CARRY_FORWARD_DAYS are auto-dropped — if they
    haven't been verified or dismissed in that time, they're stale.

    Each item includes a specific WorkIQ verification query so the agent
    checks whether the item has actually been dealt with.
    """
    if not prev or not prev.get("items"):
        return ""

    today = datetime.now().date()
    fresh_items = []
    stale_count = 0

    for item in prev["items"]:
        if not isinstance(item, dict):
            continue
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
        "**MANDATORY**: For EACH item below, you MUST run the verification query BEFORE deciding to keep it.",
        "Do NOT blindly carry forward — verify EACH one individually via WorkIQ or inbox scans.\n",
        "Decision rules:",
        "- **DROP** if WorkIQ confirms you replied/acted, OR person is NOT unread in inbox scans",
        "- **KEEP** only if verification confirms it's genuinely still outstanding",
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
        # Add per-item verification query
        vq = _build_verification_query(item)
        if vq:
            lines.append(f"  - **Verify**: {vq}")
        else:
            lines.append("  - **Verify**: Check inbox scans — is this person/topic still unread or unresolved?")
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
        if not isinstance(item, dict):
            issues.append(f"  item[{i}]: expected dict, got {type(item).__name__}")
            continue
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


def _build_collection_warnings() -> str:
    """Check transcript collection status and build warning text for the digest.

    Reads .transcript-collection-status.json — written by the collector on success
    and by the runner on failure. If the last collection failed or is stale (>26h),
    the digest agent gets a visible warning about incomplete data.
    """
    from core.constants import TRANSCRIPT_STATUS_FILE
    import json

    if not TRANSCRIPT_STATUS_FILE.exists():
        return ""

    try:
        status = json.loads(TRANSCRIPT_STATUS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return ""

    ts = status.get("timestamp", "")
    success = status.get("success", True)
    error_msg = status.get("error_message", "")
    collected = status.get("collected", 0)

    # Check staleness — if last collection was >26 hours ago, warn
    # (normal schedule: knowledge at 02:00 + digest at 08:00 = ~6h gap max)
    stale = False
    if ts:
        try:
            from datetime import datetime as _dt
            last_run = _dt.fromisoformat(ts)
            hours_ago = (datetime.now() - last_run).total_seconds() / 3600
            stale = hours_ago > 26
        except Exception:
            pass

    warnings = []

    if not success:
        warnings.append(
            f"**WARNING: Transcript collection FAILED** at {ts[:16]}. "
            f"Error: {error_msg}. "
            f"Yesterday's meeting transcripts may NOT be included in the content below. "
            f"Flag this to the user in the digest."
        )
    elif stale:
        warnings.append(
            f"**WARNING: Transcript collection is STALE** — last successful run was {ts[:16]} "
            f"({hours_ago:.0f}h ago). Recent meeting transcripts may be missing."
        )

    if not warnings:
        return ""

    block = "\n## Data Collection Warnings\n\n" + "\n".join(warnings) + "\n"
    log.warning(f"Collection warnings: {'; '.join(warnings)}")
    return block


def _persist_calendar_scan(cal_events: list[dict] | None) -> None:
    """Persist calendar scan results to .calendar-scan.json for TUI consumption."""
    scan_file = PULSE_HOME / ".calendar-scan.json"
    try:
        data = {
            "scanned_at": datetime.now().isoformat(),
            "events": cal_events if cal_events is not None else [],
            "available": cal_events is not None,
        }
        scan_file.write_text(json.dumps(data, default=str), encoding="utf-8")
    except Exception:
        log.debug("Failed to persist calendar scan", exc_info=True)


async def _pre_process_monitor(config: dict) -> dict:
    """Scan Teams inbox, Outlook inbox, and calendar before monitor agent call."""
    from collectors.teams_inbox import scan_teams_inbox, format_inbox_for_prompt
    from collectors.outlook_inbox import scan_outlook_inbox, format_outlook_for_prompt
    from collectors.calendar import scan_calendar, format_calendar_for_prompt

    scan_time = datetime.now().strftime("%H:%M:%S")

    log.info("Phase 0: Scanning inboxes and calendar concurrently...")
    items, outlook_items, cal_events = await asyncio.gather(
        scan_teams_inbox(config),
        scan_outlook_inbox(config),
        scan_calendar(config),
    )

    if items is None:
        formatted = "**Teams inbox scan UNAVAILABLE** — browser not running."
        log.warning("  Teams inbox: UNAVAILABLE — browser not running")
    elif items:
        formatted = f"*(Scanned at {scan_time} — data may be 5-10 min stale by the time you read this)*\n\n{format_inbox_for_prompt(items)}"
        log.info(f"  Found {len(items)} unread Teams messages (scanned at {scan_time})")
    else:
        formatted = f"*(Scanned at {scan_time} — data may be 5-10 min stale by the time you read this)*\n\n{format_inbox_for_prompt(items)}"
        log.info(f"  No unread Teams messages detected (scanned at {scan_time}).")

    if outlook_items is None:
        outlook_block = "**Outlook inbox scan UNAVAILABLE** — browser not running."
    else:
        outlook_block = f"*(Scanned at {scan_time})*\n\n{format_outlook_for_prompt(outlook_items)}"

    _persist_calendar_scan(cal_events)
    if cal_events is None:
        calendar_block = "**Calendar scan UNAVAILABLE** — browser not running."
    else:
        calendar_block = format_calendar_for_prompt(cal_events)

    # CRM enrichment availability check (optional)
    from sdk.agents import is_msx_available
    msx_available = is_msx_available()
    log.info(f"  CRM tools: {'available' if msx_available else 'not installed (skipping)'}")

    return {
        "teams_inbox": formatted,
        "outlook_inbox_block": outlook_block,
        "calendar_block": calendar_block,
        "msx_available": msx_available,
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

    # Check transcript collection status — surface failures to the digest agent
    collection_warnings = _build_collection_warnings()

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

    log.info("\nPhase 1c-e: Scanning inboxes and calendar concurrently...")
    teams_items, outlook_items, cal_events = await asyncio.gather(
        scan_teams_inbox(config),
        scan_outlook_inbox(config),
        scan_calendar(config),
    )
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

    if outlook_items is None:
        outlook_block = "**Outlook inbox scan UNAVAILABLE** — browser not running. Cannot verify unread emails."
        log.warning("  Outlook inbox: UNAVAILABLE — browser not running")
    elif outlook_items:
        outlook_block = f"*(Scanned at {scan_time})*\n\n{format_outlook_for_prompt(outlook_items)}"
        log.info(f"  Outlook inbox: {len(outlook_items)} unread (scanned at {scan_time})")
    else:
        outlook_block = f"*(Scanned at {scan_time})*\n\n{format_outlook_for_prompt(outlook_items)}"
        log.info(f"  Outlook inbox: no unread emails (scanned at {scan_time})")

    _persist_calendar_scan(cal_events)
    if cal_events is None:
        calendar_block = "**Calendar scan UNAVAILABLE** — browser not running."
        log.warning("  Calendar: UNAVAILABLE — browser not running")
    else:
        calendar_block = format_calendar_for_prompt(cal_events)
        active_count = sum(1 for e in cal_events if not e.get("is_declined"))
        log.info(f"  Calendar: {active_count} active events")

    log.info("\nPhase 1f: Loading active project files...")
    cancelled, projects = _auto_cancel_stale_commitments()
    if cancelled:
        log.info(f"  Auto-cancelled {cancelled} stale overdue commitment(s)")
    projects_block = _build_projects_block(projects)
    commitments_summary = _extract_commitments_summary(projects)
    if projects:
        log.info(f"  Loaded {len(projects)} project(s)")
    else:
        log.info("  No project files found")

    # Phase 1g: CRM enrichment availability check (optional)
    from sdk.agents import is_msx_available, msx_install_info
    msx_available = is_msx_available()
    msx_gap_block = ""
    if msx_available:
        info = msx_install_info()
        log.info(f"\nPhase 1g: CRM tools available (plugin: {info['path']}, node: {info['has_node']}, az: {info['has_az_cli']})")
        msx_gap_block = _build_msx_gap_block(projects)
        if msx_gap_block:
            log.info("  Found projects without CRM links")
        else:
            log.info("  All active projects linked to CRM (or no projects)")
    else:
        log.info("\nPhase 1g: CRM plugin not installed — skipping CRM enrichment")

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
        "collection_warnings": collection_warnings,
        "articles_block": articles_block,
        "articles": articles,
        "teams_inbox_block": teams_inbox_block,
        "outlook_inbox_block": outlook_block,
        "calendar_block": calendar_block,
        "projects_block": projects_block,
        "commitments_summary": commitments_summary,
        "msx_available": msx_available,
        "msx_gap_block": msx_gap_block,
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
                st = f.stat()
                if st.st_mtime >= cutoff:
                    rel = f.relative_to(directory)
                    files.append(f"  - {label}/{rel} ({st.st_size:,} bytes)")
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
    teams_items, outlook_items = await asyncio.gather(
        scan_teams_inbox(config),
        scan_outlook_inbox(config),
    )
    scan_time = datetime.now().strftime("%H:%M:%S")

    if teams_items is None:
        teams_inbox_block = "**Teams inbox scan UNAVAILABLE** — browser not running."
    else:
        teams_inbox_block = f"*(Scanned at {scan_time})*\n\n{format_inbox_for_prompt(teams_items)}"

    if outlook_items is None:
        outlook_block = "**Outlook inbox scan UNAVAILABLE** — browser not running."
    else:
        outlook_block = f"*(Scanned at {scan_time})*\n\n{format_outlook_for_prompt(outlook_items)}"

    # CRM enrichment availability check (optional)
    from sdk.agents import is_msx_available, msx_install_info
    msx_available = is_msx_available()
    if msx_available:
        info = msx_install_info()
        log.info(f"  CRM tools available (plugin: {info['path']}, node: {info['has_node']}, az: {info['has_az_cli']})")
    else:
        log.info("  CRM plugin not installed — CRM enrichment will be skipped")

    return {
        "projects_block": projects_block,
        "commitments_summary": commitments_summary,
        "recent_artifacts": recent_artifacts,
        "lookback_window": lookback_window,
        "lookback_note": lookback_note,
        "teams_inbox_block": teams_inbox_block,
        "outlook_inbox_block": outlook_block,
        "msx_available": msx_available,
    }


async def run_knowledge_init_phases(client, config: dict, job_log_file: str | None = None):
    """Run knowledge Phase 0 (transcripts + compression) and Phase 1 (archive).

    Called by the worker's ``_run_knowledge_init``.  Phase 2 (per-project
    enrichment) is handled separately — the worker queues individual
    ``knowledge-project`` jobs via ``prepare_knowledge_projects()``.
    """
    log.info("=== Knowledge init: Phase 0 + 1 ===")

    def _log_pipeline(entry_type: str, **kwargs):
        if not job_log_file:
            return
        try:
            entry = {"ts": datetime.now().isoformat(), "type": entry_type, **kwargs}
            with open(job_log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:
            pass

    # Phase 0: Collect fresh transcripts + compress
    from collectors.transcripts.compressor import compress_existing_transcripts
    from collectors.transcripts import run_transcript_collection
    from core.browser import ensure_browser

    browser_mgr = await ensure_browser()
    if browser_mgr:
        log.info("  Phase 0a: Collecting fresh transcripts from Teams...")
        _log_pipeline("message", preview="Phase 0a: Collecting fresh transcripts from Teams...")
        try:
            await asyncio.wait_for(
                run_transcript_collection(client, config),
                timeout=_TIMEOUT_DEFAULT,  # 30 min cap
            )
            _log_pipeline("message", preview="Transcript collection complete")
        except asyncio.TimeoutError:
            log.warning("    Transcript collection timed out (non-fatal)")
            _log_pipeline("error", preview="Transcript collection timed out after 30 minutes")
            from collectors.transcripts.collector import write_collection_failure
            write_collection_failure("Transcript collection timed out after 30 minutes")
        except Exception as e:
            log.warning(f"    Transcript collection failed (non-fatal): {e}")
            _log_pipeline("error", preview=f"Transcript collection failed: {str(e)[:200]}")
            from collectors.transcripts.collector import write_collection_failure
            write_collection_failure(str(e))
    else:
        log.info("  Phase 0a: Skipping transcript collection (no browser)")
        _log_pipeline("message", preview="Phase 0a: Skipped — no browser available")
        from collectors.transcripts.collector import write_collection_failure
        write_collection_failure("No browser available for transcript collection")

    if client:
        transcripts_dir = TRANSCRIPTS_DIR
        if transcripts_dir.exists() and list(transcripts_dir.glob("*.txt")):
            tc_models = config.get("models", {})
            compress_model = tc_models.get("transcripts", tc_models.get("default", "claude-sonnet"))
            log.info("  Phase 0b: Compressing raw transcripts via GHCP SDK...")
            _log_pipeline("message", preview="Phase 0b: Compressing raw transcripts...")
            compressed_count = await compress_existing_transcripts(client, transcripts_dir, model=compress_model)
            log.info(f"    Compressed {compressed_count} transcripts")
            _log_pipeline("message", preview=f"Compressed {compressed_count} transcripts")

    # Phase 1: Run archive session (global — emails, Teams messages, new project discovery)
    log.info("  Phase 1: Archiving emails/Teams messages + discovering new projects...")
    _log_pipeline("message", preview="Phase 1: Archiving emails/Teams messages + discovering new projects...")
    try:
        await asyncio.wait_for(
            run_job(client, config, "knowledge-archive", job_log_file=job_log_file),
            timeout=600,  # 10 min cap for archival
        )
        log.info("  Phase 1 complete.")
        _log_pipeline("message", preview="Phase 1 complete — archive session done")
    except asyncio.TimeoutError:
        log.warning("  Archive phase timed out after 5 minutes (continuing to enrichment)")
        _log_pipeline("error", preview="Archive phase timed out after 5 minutes")
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        log.warning(f"  Archive phase failed: {e}\n{tb}")
        _log_pipeline("error", preview=f"Archive phase failed: {str(e)[:200]}\n{tb[-500:]}")

    log.info("  Knowledge init (Phase 0+1) complete.")


def prepare_knowledge_projects(config: dict) -> list[dict]:
    """Build a list of ``knowledge-project`` job dicts for Phase 2.

    Returns one job per active/blocked project.  Each job carries the
    context the worker needs to run a single enrichment session.
    Called synchronously — no SDK session needed.
    """
    projects = _load_projects()
    if not projects:
        return []

    active = [p for p in projects if p.get("status") in ("active", "blocked", None)]
    if not active:
        log.info(f"  {len(projects)} projects loaded but none are active/blocked.")
        return []

    recent_artifacts = _list_recent_artifacts(days=2)

    from sdk.agents import is_msx_available
    msx_available = is_msx_available()

    state = load_json_state(KNOWLEDGE_STATE_FILE, {})
    last_run = state.get("last_run")
    lookback_window = f"since {last_run}" if last_run else "48 hours"

    # Update last_run now (before enrichment starts)
    save_json_state(KNOWLEDGE_STATE_FILE, {"last_run": datetime.now().isoformat()})

    jobs = []
    for project in active:
        pid = project.get("_file", "").replace(".yaml", "")
        pname = project.get("project", pid)
        project_copy = {k: v for k, v in project.items() if k != "_file"}
        context = {
            "project_id": pid,
            "project_name": pname,
            "project_yaml": yaml.dump(project_copy, default_flow_style=False, allow_unicode=True),
            "recent_artifacts": recent_artifacts,
            "lookback_window": lookback_window,
            "msx_available": msx_available,
        }
        jobs.append({
            "type": "knowledge-project",
            "_context": context,
            "_knowledge_batch_size": len(active),
        })

    log.info(f"  Prepared {len(jobs)} knowledge-project jobs for Phase 2")
    for p in active:
        pid = p.get("_file", "").replace(".yaml", "")
        log.info(f"    - {p.get('project', pid)}")

    return jobs


# Keep the monolithic pipeline for --once / --mode knowledge CLI usage
async def run_knowledge_pipeline(client, config: dict, job_log_file: str | None = None):
    """Run the full knowledge pipeline sequentially (CLI / --once mode).

    For the daemon, knowledge is split: ``_run_knowledge_init`` in worker.py
    runs Phase 0+1, then queues individual ``knowledge-project`` jobs.
    This function preserves the old sequential behaviour for CLI usage.
    """
    await run_knowledge_init_phases(client, config, job_log_file=job_log_file)

    projects = _load_projects()
    active = [p for p in projects if p.get("status") in ("active", "blocked", None)]
    if not active:
        log.info("  No projects to enrich. Pipeline done.")
        return

    recent_artifacts = _list_recent_artifacts(days=2)
    from sdk.agents import is_msx_available, msx_install_info
    msx_available = is_msx_available()
    if msx_available:
        info = msx_install_info()
        log.info(f"  CRM tools available (node: {info['has_node']}, az: {info['has_az_cli']})")

    state = load_json_state(KNOWLEDGE_STATE_FILE, {})
    last_run = state.get("last_run")
    lookback_window = f"since {last_run}" if last_run else "48 hours"

    log.info(f"  Phase 2: Enriching {len(active)} active projects...")
    enriched = 0
    for project in active:
        pid = project.get("_file", "").replace(".yaml", "")
        pname = project.get("project", pid)
        log.info(f"  Enriching: {pname} ({pid})...")
        project_copy = {k: v for k, v in project.items() if k != "_file"}
        project_context = {
            "project_id": pid,
            "project_name": pname,
            "project_yaml": yaml.dump(project_copy, default_flow_style=False, allow_unicode=True),
            "recent_artifacts": recent_artifacts,
            "lookback_window": lookback_window,
            "msx_available": msx_available,
        }
        try:
            await asyncio.wait_for(
                run_job(client, config, "knowledge-project", context=project_context, job_log_file=job_log_file),
                timeout=600,
            )
            enriched += 1
            log.info(f"    Done: {pname}")
        except asyncio.TimeoutError:
            log.warning(f"    Timeout enriching {pname} (10 min cap)")
        except Exception as e:
            log.warning(f"    Failed enriching {pname}: {e}")

    log.info(f"=== Knowledge pipeline done — enriched {enriched}/{len(active)} projects ===")
