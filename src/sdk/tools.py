"""Custom tool definitions for the GHCP SDK agent."""

import json
import uuid
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel
from copilot import define_tool, Tool, ToolInvocation

import re

import yaml

from core.constants import (
    OUTPUT_DIR, JOBS_DIR, PROJECTS_DIR, PULSE_HOME, PULSE_TEAM_DIR,
    TRANSCRIPTS_DIR, DOCUMENTS_DIR, EMAILS_DIR, TEAMS_MESSAGES_DIR,
    DIGESTS_DIR, INTEL_DIR,
)
from core.state import load_json_state, save_json_state


# --- Tool parameter schemas ---

class WriteOutputParams(BaseModel):
    filename: str
    content: str


class QueueTaskParams(BaseModel):
    type: str = "research"  # research, digest, transcripts, intel
    task: str = ""
    description: str = ""
    priority: str = "normal"
    model: str = "claude-opus"


class DismissItemParams(BaseModel):
    item: str
    reason: str = ""


class AddNoteParams(BaseModel):
    item: str
    note: str


class ScheduleTaskParams(BaseModel):
    id: str
    type: str = "digest"  # digest, monitor, intel, transcripts, research
    pattern: str  # "daily 07:00", "weekdays 09:00", "every 6h", "every 30m"
    description: str = ""


class ListSchedulesParams(BaseModel):
    pass


class CancelScheduleParams(BaseModel):
    id: str


class UpdateScheduleParams(BaseModel):
    id: str
    pattern: str = ""  # new pattern (leave empty to keep current)
    description: str = ""  # new description (leave empty to keep current)
    enabled: bool = True


class SendTeamsMessageParams(BaseModel):
    recipient: str  # person name to message
    message: str
    chat_name: str = ""  # if set, reply to this existing chat instead of 1:1


class SendEmailReplyParams(BaseModel):
    search_query: str  # sender name or subject to find the email
    message: str


class SendTaskToAgentParams(BaseModel):
    agent: str  # alias or name from team directory
    task: str  # what to ask/request
    kind: str = "question"  # question, research, intel, review
    priority: str = "normal"
    description: str = ""


class BroadcastToTeamParams(BaseModel):
    question: str  # the question to broadcast
    project_id: str  # slug of the project this question is about (required for routing responses back)


class UpdateProjectParams(BaseModel):
    project_id: str  # slug, e.g. "qbe-foundry-migration"
    yaml_content: str  # full YAML content for the project file


class SweepInboxParams(BaseModel):
    full_sweep: bool = False  # True = mark ALL unread as read; False = smart (FYI/low only)


class SaveConfigParams(BaseModel):
    config: dict  # full standing-instructions config object


class SearchLocalFilesParams(BaseModel):
    query: str
    file_pattern: str = "*.*"  # glob pattern to filter files
    max_results: int = 5




# --- Tool handlers ---

@define_tool(
    name="write_output",
    description="Write research output or deliverables to a local file in the output/ directory.",
)
def write_output(params: WriteOutputParams, invocation: ToolInvocation) -> str:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = (OUTPUT_DIR / params.filename).resolve()
    # Prevent path traversal — output must stay inside OUTPUT_DIR
    if not str(output_path).startswith(str(OUTPUT_DIR.resolve())):
        return f"ERROR: Path traversal blocked — '{params.filename}' resolves outside output/"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(params.content, encoding="utf-8")
    return f"Written to {output_path}"


@define_tool(
    name="queue_task",
    description="Add a job to the queue. The daemon picks it up next cycle. Set type to 'research', 'digest', 'transcripts', or 'intel'.",
)
def queue_task(params: QueueTaskParams, invocation: ToolInvocation) -> str:
    pending_dir = JOBS_DIR / "pending"
    pending_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")
    slug = params.task.lower().replace(" ", "-")[:50]
    uid = uuid.uuid4().hex[:8]
    task_file = pending_dir / f"{date_str}-{slug}-{uid}.yaml"

    task_data = {
        "type": params.type,
        "task": params.task,
        "description": params.description,
        "priority": params.priority,
        "model": params.model,
        "output": {
            "local": str(PULSE_HOME),
        },
    }

    with open(task_file, "w") as f:
        yaml.dump(task_data, f, default_flow_style=False)

    return f"Task queued: {task_file}"


# --- Digest actions (dismiss/note) ---

ACTIONS_FILE = PULSE_HOME / ".digest-actions.json"


def load_actions() -> dict:
    """Load digest actions (dismissed items, notes). Public for digest module."""
    return load_json_state(ACTIONS_FILE, {"dismissed": [], "notes": {}})


def _save_actions(actions: dict):
    save_json_state(ACTIONS_FILE, actions)


@define_tool(
    name="dismiss_item",
    description="Mark a digest item as handled/done so it won't appear in future digests. Use when the user says they've dealt with something.",
)
def dismiss_item(params: DismissItemParams, invocation: ToolInvocation) -> str:
    actions = load_actions()
    entry = {
        "item": params.item,
        "dismissed_at": datetime.now().isoformat(),
        "status": "archived",  # agent dismissals are permanent
    }
    if params.reason:
        entry["reason"] = params.reason
    actions["dismissed"].append(entry)
    try:
        _save_actions(actions)
    except OSError as e:
        return f"ERROR: Failed to save dismissal: {e}"
    return f"Archived: {params.item}"


@define_tool(
    name="add_note",
    description="Add a note to a digest item for future reference. Use when the user wants to annotate something.",
)
def add_note(params: AddNoteParams, invocation: ToolInvocation) -> str:
    actions = load_actions()
    actions["notes"][params.item] = {
        "note": params.note,
        "added_at": datetime.now().isoformat(),
    }
    try:
        _save_actions(actions)
    except OSError as e:
        return f"ERROR: Failed to save note: {e}"
    return f"Note added to: {params.item}"


# --- Scheduler tools ---

@define_tool(
    name="schedule_task",
    description=(
        "Create a recurring schedule. Patterns: 'daily HH:MM', 'weekdays HH:MM', "
        "'every Nh', 'every Nm'. Example: schedule_task(id='morning-digest', type='digest', "
        "pattern='weekdays 07:00', description='Morning digest on weekdays')."
    ),
)
def schedule_task(params: ScheduleTaskParams, invocation: ToolInvocation) -> str:
    from core.scheduler import add_schedule
    try:
        entry = add_schedule(params.id, params.type, params.pattern, params.description)
        return f"Scheduled: '{entry['id']}' — {entry['pattern']} ({entry['type']})"
    except ValueError as e:
        return f"ERROR: {e}"


@define_tool(
    name="list_schedules",
    description="List all configured recurring schedules.",
)
def list_schedules_tool(params: ListSchedulesParams, invocation: ToolInvocation) -> str:
    from core.scheduler import list_schedules
    schedules = list_schedules()
    if not schedules:
        return "No schedules configured."
    lines = []
    for s in schedules:
        status = "enabled" if s.get("enabled", True) else "disabled"
        last = s.get("last_run", "never")
        lines.append(f"- [{status}] {s['id']}: {s['pattern']} ({s['type']}) — last run: {last}")
    return "\n".join(lines)


@define_tool(
    name="update_schedule",
    description=(
        "Update an existing schedule's pattern, description, or enabled status. "
        "Example: update_schedule(id='triage', pattern='every 15m') to change triage frequency."
    ),
)
def update_schedule_tool(params: UpdateScheduleParams, invocation: ToolInvocation) -> str:
    from core.scheduler import update_schedule
    try:
        entry = update_schedule(params.id, params.pattern, params.description, params.enabled)
        if entry:
            status = "enabled" if entry.get("enabled", True) else "disabled"
            return f"Updated: '{entry['id']}' — {entry['pattern']} [{status}]"
        return f"Schedule '{params.id}' not found."
    except ValueError as e:
        return f"ERROR: {e}"


@define_tool(
    name="cancel_schedule",
    description="Remove a recurring schedule by ID.",
)
def cancel_schedule(params: CancelScheduleParams, invocation: ToolInvocation) -> str:
    from core.scheduler import remove_schedule
    if remove_schedule(params.id):
        return f"Cancelled: '{params.id}'"
    return f"Schedule '{params.id}' not found."


# --- Inter-agent communication ---

@define_tool(
    name="send_task_to_agent",
    description=(
        "Send a task or question to another team member's Pulse Agent. "
        "The task is delivered via their shared OneDrive folder (convention-based path). "
        "Their agent will process it and send back a response. "
        "Use 'agent' to specify the team member (alias from team directory). "
        "Use 'kind' to specify the type: question, research, intel, or review."
    ),
)
def send_task_to_agent(params: SendTaskToAgentParams, invocation: ToolInvocation) -> str:
    import uuid
    from core.config import load_config

    config = load_config()

    # Look up agent in team directory (by alias or name)
    team = config.get("team", [])
    agent_entry = None
    for member in team:
        if member.get("alias", "").lower() == params.agent.lower():
            agent_entry = member
            break
        if member.get("name", "").lower() == params.agent.lower():
            agent_entry = member
            break

    if not agent_entry:
        available = ", ".join(m.get("alias", m.get("name", "?")) for m in team)
        return f"ERROR: Agent '{params.agent}' not found in team directory. Available: {available}"

    alias = agent_entry.get("alias", "").lower()
    if not alias:
        return f"ERROR: Agent '{params.agent}' has no alias configured."

    # Convention-based path: PULSE_TEAM_DIR/{alias}/jobs/pending/
    # Falls back to explicit agent_path if set (backward compat)
    explicit_path = agent_entry.get("agent_path")
    if explicit_path:
        agent_path = Path(explicit_path)
        jobs_dir = agent_path / "Jobs"
    else:
        agent_path = PULSE_TEAM_DIR / alias
        jobs_dir = agent_path / "jobs" / "pending"

    if not agent_path.exists():
        return (
            f"ERROR: Path for agent '{alias}' not accessible: {agent_path}. "
            f"Make sure the Pulse-Team OneDrive folder is shared and synced."
        )

    jobs_dir.mkdir(parents=True, exist_ok=True)

    # Build reply_to path — convention-based: PULSE_TEAM_DIR/{my_alias}/jobs/pending/
    user_cfg = config.get("user", {})
    from_name = user_cfg.get("name", "Unknown")
    from_alias = user_cfg.get("alias", from_name.lower().split()[0] if from_name else "unknown")
    my_team_dir = PULSE_TEAM_DIR / from_alias / "jobs" / "pending"
    my_team_dir.mkdir(parents=True, exist_ok=True)
    reply_to = str(my_team_dir)

    request_id = str(uuid.uuid4())
    timestamp = datetime.now().isoformat()
    slug = re.sub(r"[^a-z0-9-]", "", params.task.lower().replace(" ", "-"))[:40]

    task_data = {
        "type": "agent_request",
        "kind": params.kind,
        "task": params.task,
        "description": params.description or params.task,
        "from": from_name,
        "from_alias": from_alias,
        "reply_to": reply_to,
        "request_id": request_id,
        "priority": params.priority,
        "created_at": timestamp,
    }

    date_str = datetime.now().strftime("%Y-%m-%d")
    task_file = jobs_dir / f"{date_str}-from-{from_alias}-{slug}.yaml"

    with open(task_file, "w") as f:
        yaml.dump(task_data, f, default_flow_style=False)

    return (
        f"Task sent to {agent_entry['name']} ({params.kind}): {params.task[:80]}\n"
        f"Request ID: {request_id}\n"
        f"Written to: {task_file}"
    )


@define_tool(
    name="broadcast_to_team",
    description=(
        "Broadcast a question to ALL configured teammates at once. Drops an "
        "agent_request YAML into each teammate's shared OneDrive jobs/pending/ "
        "folder. Each teammate's agent will decide (via its Guardian prompt) "
        "whether it has relevant local context and respond asynchronously. "
        "Responses accrete into the named project's team_context field. "
        "Use this (not send_task_to_agent) when the question is about a specific "
        "project and you want to reach anyone on the team who might know."
    ),
)
def broadcast_to_team(params: BroadcastToTeamParams, invocation: ToolInvocation) -> str:
    from core.config import load_config

    if not params.project_id.strip():
        return "ERROR: project_id is required for broadcast_to_team (responses must route back to a project)."

    config = load_config()
    team = config.get("team", [])
    if not team:
        return "ERROR: no teammates configured, add entries under `team:` in standing-instructions.yaml."

    user_cfg = config.get("user", {})
    from_name = user_cfg.get("name", "Unknown")
    from_alias = user_cfg.get("alias", from_name.lower().split()[0] if from_name else "unknown")

    # Sender's own inbox for responses
    my_team_dir = PULSE_TEAM_DIR / from_alias / "jobs" / "pending"
    my_team_dir.mkdir(parents=True, exist_ok=True)
    reply_to = str(my_team_dir)

    request_id = str(uuid.uuid4())
    timestamp = datetime.now().isoformat()
    slug = re.sub(r"[^a-z0-9-]", "", params.question.lower().replace(" ", "-"))[:40]
    date_str = datetime.now().strftime("%Y-%m-%d")

    sent: list[str] = []
    skipped: list[str] = []

    for member in team:
        alias = (member.get("alias") or "").lower()
        if not alias:
            skipped.append(member.get("name", "?") + " (no alias)")
            continue

        # Convention-based path; fall back to explicit agent_path for backward compat
        explicit = member.get("agent_path")
        if explicit:
            agent_path = Path(explicit)
            jobs_dir = agent_path / "Jobs"
        else:
            agent_path = PULSE_TEAM_DIR / alias
            jobs_dir = agent_path / "jobs" / "pending"

        if not agent_path.exists():
            skipped.append(alias)
            continue

        jobs_dir.mkdir(parents=True, exist_ok=True)
        task_data = {
            "type": "agent_request",
            "kind": "broadcast",
            "task": params.question,
            "project_id": params.project_id,
            "from": from_name,
            "from_alias": from_alias,
            "reply_to": reply_to,
            "request_id": request_id,
            "priority": "normal",
            "created_at": timestamp,
        }
        task_file = jobs_dir / f"{date_str}-from-{from_alias}-broadcast-{slug}.yaml"
        with open(task_file, "w") as f:
            yaml.dump(task_data, f, default_flow_style=False)
        sent.append(alias)

    msg = f"Broadcasted to {len(sent)} teammate{'s' if len(sent) != 1 else ''}: {', '.join(sent) or '(none)'}"
    if skipped:
        msg += f" | Skipped (folder not accessible): {', '.join(skipped)}"
    msg += f" | Request ID: {request_id} | Responses will accrete into project '{params.project_id}'."
    return msg


# --- Local file search ---

_TEXT_EXTENSIONS = {
    ".txt", ".md", ".vtt", ".csv", ".json", ".yaml", ".yml",
    ".eml", ".html", ".htm", ".xml", ".log", ".rst", ".ini", ".cfg",
}


@define_tool(
    name="search_local_files",
    description=(
        "Search local files for a keyword or phrase. Searches transcripts, documents, "
        "emails, teams-messages, digests, intel reports, project files, AND monitoring/"
        "triage reports in PULSE_HOME root. "
        "Searches all text-based files (.md, .txt, .json, .yaml, etc.) recursively. "
        "Use this to find context before responding — e.g., search for a person's name, "
        "project name, or topic across meeting transcripts, emails, Teams messages, "
        "digests, triage results, and project memory. Returns matching snippets with "
        "surrounding context. If no local matches found, try WorkIQ for live M365 data."
    ),
)
def search_local_files(params: SearchLocalFilesParams, invocation: ToolInvocation) -> str:
    # Prevent path traversal in glob pattern
    if ".." in params.file_pattern:
        return "ERROR: Invalid file pattern."

    # Search all named data directories (recursive)
    search_dirs = []
    for label, d in [
        ("transcripts", TRANSCRIPTS_DIR),
        ("documents", DOCUMENTS_DIR),
        ("emails", EMAILS_DIR),
        ("teams-messages", TEAMS_MESSAGES_DIR),
        ("digests", DIGESTS_DIR),
        ("intel", INTEL_DIR),
        ("projects", PROJECTS_DIR),
    ]:
        if d.exists():
            search_dirs.append((label, d))

    if not search_dirs and not PULSE_HOME.exists():
        return "No data directories found."

    query_lower = params.query.lower()
    results = []

    def _extract_snippets(filepath: Path, label: str, rel_name: str):
        """Extract matching snippets from a file and append to results."""
        if not filepath.is_file():
            return
        if filepath.suffix.lower() not in _TEXT_EXTENSIONS:
            return
        try:
            text = filepath.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return
        if query_lower not in text.lower():
            return
        lines = text.splitlines()
        snippets = []
        for i, line in enumerate(lines):
            if query_lower in line.lower():
                start = max(0, i - 2)
                end = min(len(lines), i + 3)
                snippet = "\n".join(lines[start:end])
                snippets.append(snippet)
                if len(snippets) >= 3:  # max 3 snippets per file
                    break
        match_text = "\n---\n".join(snippets)
        results.append(f"### {label}/{rel_name}\n{match_text}")

    # 1. Recursive search in named data directories
    for dir_label, search_dir in search_dirs:
        for filepath in sorted(search_dir.rglob(params.file_pattern)):
            _extract_snippets(filepath, dir_label, str(filepath.relative_to(search_dir)))
            if len(results) >= params.max_results:
                break
        if len(results) >= params.max_results:
            break

    # 2. Non-recursive search in PULSE_HOME root for monitoring reports,
    #    knowledge runs, and chat history (these live at root, not in subdirs)
    if len(results) < params.max_results and PULSE_HOME.exists():
        for filepath in sorted(PULSE_HOME.glob(params.file_pattern)):
            if not filepath.is_file():
                continue
            # Only include root-level report files, skip dot-files and subdirs
            name = filepath.name
            if name.startswith("."):
                continue
            _extract_snippets(filepath, "reports", name)
            if len(results) >= params.max_results:
                break

    if not results:
        return (
            f"No matches for '{params.query}' in {params.file_pattern} files. "
            f"TIP: This only searches local files. For live Teams/email/calendar data, "
            f"query WorkIQ via the m365-query agent."
        )

    return f"Found {len(results)} file(s) matching '{params.query}':\n\n" + "\n\n".join(results)



# --- Project memory ---

def _find_similar_projects(project_id: str) -> list[str]:
    """Find existing project files whose slugs share 2+ tokens with the new ID.

    Returns a list of similar slug names (without .yaml extension).
    """
    if not PROJECTS_DIR.exists():
        return []

    new_tokens = set(project_id.split("-"))
    # Ignore very short tokens (single char) that cause false positives
    new_tokens = {t for t in new_tokens if len(t) > 1}
    if not new_tokens:
        return []

    similar = []
    for path in PROJECTS_DIR.glob("*.yaml"):
        existing_slug = path.stem
        if existing_slug == project_id:
            continue  # exact match — this is an update, not a conflict
        existing_tokens = {t for t in existing_slug.split("-") if len(t) > 1}
        overlap = new_tokens & existing_tokens
        if len(overlap) >= 2:
            similar.append(existing_slug)
    return sorted(similar)


@define_tool(
    name="update_project",
    description=(
        "Create or update a project memory file. Takes a project_id (slug) and the "
        "full YAML content. Use this to track active projects, stakeholders, commitments, "
        "and timeline. Read the existing file first (output/projects/{id}.yaml), modify, "
        "then write back the full content. The tool auto-sets updated_at timestamp. "
        "IMPORTANT: One project file per customer engagement. Before creating a NEW file, "
        "check if a similar project already exists — the tool will warn you if it finds one."
    ),
)
def update_project(params: UpdateProjectParams, invocation: ToolInvocation) -> str:
    # Validate project_id — lowercase alphanumeric + hyphens, no path traversal
    if not re.match(r"^[a-z0-9][a-z0-9-]{0,80}$", params.project_id):
        return "ERROR: project_id must be lowercase alphanumeric with hyphens (e.g. 'qbe-foundry-migration')"

    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    project_path = (PROJECTS_DIR / f"{params.project_id}.yaml").resolve()

    # Path traversal guard (same pattern as write_output)
    if not str(project_path).startswith(str(PROJECTS_DIR.resolve())):
        return f"ERROR: Path traversal blocked — '{params.project_id}' resolves outside projects/"

    # --- Duplicate detection: warn on NEW files with similar slugs ---
    is_new_file = not project_path.exists()
    if is_new_file:
        similar = _find_similar_projects(params.project_id)
        if similar:
            similar_list = ", ".join(similar)
            return (
                f"BLOCKED: Similar project(s) already exist: {similar_list}. "
                f"You are probably creating a duplicate. Instead, read the existing file "
                f"(use search_local_files to find it), merge your new information into it, "
                f"and call update_project with the EXISTING project_id. "
                f"If this is genuinely a separate project, add a distinguishing word to "
                f"make the slug clearly different."
            )

    # Validate YAML content
    try:
        data = yaml.safe_load(params.yaml_content)
    except yaml.YAMLError as e:
        return f"ERROR: Invalid YAML — {e}"

    if not isinstance(data, dict):
        return "ERROR: YAML content must be a mapping (dict), not a list or scalar"

    # Auto-set updated_at
    data["updated_at"] = datetime.now().isoformat()

    with open(project_path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    return f"Project '{params.project_id}' updated at {project_path}"


# --- Browser action tools ---
# These queue browser actions for the worker to execute.
# The worker runs in the main async event loop where Playwright lives.

PENDING_ACTIONS_DIR = PULSE_HOME / ".pending-actions"


@define_tool(
    name="send_teams_message",
    description=(
        "Send a message to someone on Microsoft Teams. Queues the action for "
        "immediate execution via browser automation. The message will be sent "
        "shortly and the user will receive a Telegram confirmation. "
        "Use for 1:1 messages (set recipient) or replies to existing chats (set chat_name)."
    ),
)
def send_teams_message(params: SendTeamsMessageParams, invocation: ToolInvocation) -> str:
    PENDING_ACTIONS_DIR.mkdir(parents=True, exist_ok=True)
    target = params.chat_name or params.recipient

    # Dedup: reject if an identical send is already pending
    try:
        for existing in PENDING_ACTIONS_DIR.glob("teams-send-*.json"):
            data = json.loads(existing.read_text(encoding="utf-8"))
            existing_target = data.get("chat_name") or data.get("recipient", "")
            if (existing_target.lower() == target.lower()
                    and data.get("message", "").strip() == params.message.strip()):
                return f"Message to {target} is already queued — not sending again."
    except Exception:
        pass  # dedup is best-effort; proceed if check fails

    timestamp = datetime.now().strftime("%H%M%S")
    uid = uuid.uuid4().hex[:8]
    action_file = PENDING_ACTIONS_DIR / f"teams-send-{timestamp}-{uid}.json"
    action_data = {
        "type": "teams_send",
        "recipient": params.recipient,
        "message": params.message,
        "chat_name": params.chat_name or "",
        "queued_at": datetime.now().isoformat(),
    }
    action_file.write_text(json.dumps(action_data), encoding="utf-8")
    return f"Teams message to {target} queued for delivery. Do NOT call this tool again for the same message."


@define_tool(
    name="send_email_reply",
    description=(
        "Reply to an email in Outlook. Queues the action for immediate execution "
        "via browser automation. Searches for the email by sender name or subject, "
        "opens the thread, and sends the reply."
    ),
)
def send_email_reply(params: SendEmailReplyParams, invocation: ToolInvocation) -> str:
    PENDING_ACTIONS_DIR.mkdir(parents=True, exist_ok=True)

    # Dedup: reject if an identical reply is already pending
    try:
        for existing in PENDING_ACTIONS_DIR.glob("email-reply-*.json"):
            data = json.loads(existing.read_text(encoding="utf-8"))
            if (data.get("search_query", "").lower() == params.search_query.lower()
                    and data.get("message", "").strip() == params.message.strip()):
                return f"Email reply for '{params.search_query}' is already queued — not sending again."
    except Exception:
        pass

    timestamp = datetime.now().strftime("%H%M%S")
    uid = uuid.uuid4().hex[:8]
    action_file = PENDING_ACTIONS_DIR / f"email-reply-{timestamp}-{uid}.json"
    action_data = {
        "type": "email_reply",
        "search_query": params.search_query,
        "message": params.message,
        "queued_at": datetime.now().isoformat(),
    }
    action_file.write_text(json.dumps(action_data), encoding="utf-8")
    return f"Email reply for '{params.search_query}' queued for delivery. Do NOT call this tool again for the same message."


# --- Onboarding config tool ---

@define_tool(
    name="save_config",
    description=(
        "Save the standing instructions configuration file. Used during onboarding "
        "to persist user preferences. Takes a complete config object with sections: "
        "user (name, email, role, org, focus, what_matters, what_is_noise), "
        "schedule, monitoring, team, intelligence. "
        "The tool merges answers onto the template (preserving defaults for "
        "digest, transcripts, models, and RSS feeds) and writes to PULSE_HOME."
    ),
)
def save_config_tool(params: SaveConfigParams, invocation: ToolInvocation) -> str:
    from core.onboarding import build_config_from_answers, write_config, load_template_config

    if not params.config:
        return "ERROR: config object is empty"

    # Validate required fields
    user = params.config.get("user", {})
    if not user.get("name") or "TODO" in str(user.get("name", "")).upper():
        return "ERROR: user.name is required"
    if not user.get("email") or "TODO" in str(user.get("email", "")).upper():
        return "ERROR: user.email is required"

    template = load_template_config()
    merged = build_config_from_answers(params.config, template)

    dest = write_config(merged)
    return f"Configuration saved to {dest}. Pulse Agent is now configured and ready."


# --- Inbox sweep tool ---

@define_tool(
    name="sweep_inbox",
    description=(
        "Mark unimportant unread messages as read in Teams and Outlook. "
        "Use when the user says 'clear my messages', 'sweep inbox', 'mark everything as read', "
        "or similar. Queues a sweep job that marks items as read via browser automation. "
        "Set full_sweep=true to mark ALL unread items as read. "
        "Set full_sweep=false (default) for smart sweep — only marks FYI/low-priority items."
    ),
)
def sweep_inbox(params: SweepInboxParams, invocation: ToolInvocation) -> str:
    pending_dir = JOBS_DIR / "pending"
    pending_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    uid = uuid.uuid4().hex[:8]
    file_path = pending_dir / f"{ts}-inbox-sweep-{uid}.yaml"
    data = {
        "type": "inbox_sweep",
        "full_sweep": params.full_sweep,
        "_source": "chat",
    }
    file_path.write_text(yaml.dump(data, default_flow_style=False), encoding="utf-8")
    mode = "full" if params.full_sweep else "smart"
    return f"Inbox sweep ({mode}) queued. Will mark unread items as read shortly."


# --- Tool set builder ---

def get_tools() -> list[Tool]:
    """Return custom tools for registration on a session."""
    return [
        write_output, queue_task, dismiss_item, add_note,
        schedule_task, list_schedules_tool, update_schedule_tool, cancel_schedule,
        search_local_files, update_project,
        send_teams_message, send_email_reply,
        send_task_to_agent, broadcast_to_team, save_config_tool,
        sweep_inbox,
    ]
