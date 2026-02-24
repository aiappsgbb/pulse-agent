"""Custom tool definitions for the GHCP SDK agent."""

import json
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel
from copilot import define_tool, Tool, ToolInvocation

import re

import yaml

from core.constants import (
    OUTPUT_DIR, JOBS_DIR, PROJECTS_DIR, PULSE_HOME,
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


class UpdateProjectParams(BaseModel):
    project_id: str  # slug, e.g. "qbe-foundry-migration"
    yaml_content: str  # full YAML content for the project file


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
    task_file = pending_dir / f"{date_str}-{slug}.yaml"

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
    entry = {"item": params.item, "dismissed_at": datetime.now().isoformat()}
    if params.reason:
        entry["reason"] = params.reason
    actions["dismissed"].append(entry)
    _save_actions(actions)
    return f"Dismissed: {params.item}"


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
    _save_actions(actions)
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
        "The task is delivered via their shared OneDrive folder. "
        "Their agent will process it and send back a response. "
        "Use 'agent' to specify the team member (alias from team directory). "
        "Use 'kind' to specify the type: question, research, intel, or review."
    ),
)
def send_task_to_agent(params: SendTaskToAgentParams, invocation: ToolInvocation) -> str:
    import uuid
    from core.config import load_config

    config = load_config()

    # Look up agent in team directory
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

    agent_path = Path(agent_entry["agent_path"])
    jobs_dir = agent_path / "Jobs"

    if not agent_path.exists():
        return (
            f"ERROR: Path for agent '{params.agent}' not accessible: {agent_path}. "
            f"Is their OneDrive folder shared and synced on this machine?"
        )

    jobs_dir.mkdir(parents=True, exist_ok=True)

    # Build reply_to path (this agent's own incoming jobs folder)
    reply_to = str(JOBS_DIR)

    # Build this agent's identity
    user_cfg = config.get("user", {})
    from_name = user_cfg.get("name", "Unknown")
    from_alias = from_name.lower().split()[0] if from_name else "unknown"

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


# --- Local file search ---

_TEXT_EXTENSIONS = {
    ".txt", ".md", ".vtt", ".csv", ".json", ".yaml", ".yml",
    ".eml", ".html", ".htm", ".xml", ".log", ".rst", ".ini", ".cfg",
}


@define_tool(
    name="search_local_files",
    description=(
        "Search local files for a keyword or phrase. Searches transcripts, documents, "
        "emails, teams-messages, digests, intel reports, and project files. "
        "Searches all text-based files (.md, .txt, .json, .yaml, etc.) recursively. "
        "Use this to find context before responding — e.g., search for a person's name, "
        "project name, or topic across meeting transcripts, emails, Teams messages, "
        "digests, and project memory. Returns matching snippets with surrounding context."
    ),
)
def search_local_files(params: SearchLocalFilesParams, invocation: ToolInvocation) -> str:
    # Prevent path traversal in glob pattern
    if ".." in params.file_pattern:
        return "ERROR: Invalid file pattern."

    # Search all named data directories
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

    if not search_dirs:
        return "No data directories found."

    query_lower = params.query.lower()
    results = []

    for dir_label, search_dir in search_dirs:
        for filepath in sorted(search_dir.rglob(params.file_pattern)):
            if not filepath.is_file():
                continue
            # Skip binary files when using broad glob patterns
            if filepath.suffix.lower() not in _TEXT_EXTENSIONS:
                continue
            try:
                text = filepath.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            if query_lower not in text.lower():
                continue

            # Extract matching lines with context (2 lines before/after)
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

            rel_path = filepath.relative_to(search_dir)
            match_text = "\n---\n".join(snippets)
            results.append(f"### {dir_label}/{rel_path}\n{match_text}")

            if len(results) >= params.max_results:
                break

        if len(results) >= params.max_results:
            break

    if not results:
        return f"No matches for '{params.query}' in {params.file_pattern} files."

    return f"Found {len(results)} file(s) matching '{params.query}':\n\n" + "\n\n".join(results)


# --- Project memory ---

@define_tool(
    name="update_project",
    description=(
        "Create or update a project memory file. Takes a project_id (slug) and the "
        "full YAML content. Use this to track active projects, stakeholders, commitments, "
        "and timeline. Read the existing file first (output/projects/{id}.yaml), modify, "
        "then write back the full content. The tool auto-sets updated_at timestamp."
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
    timestamp = datetime.now().strftime("%H%M%S")
    action_file = PENDING_ACTIONS_DIR / f"teams-send-{timestamp}.json"
    action_data = {
        "type": "teams_send",
        "recipient": params.recipient,
        "message": params.message,
        "chat_name": params.chat_name or "",
        "queued_at": datetime.now().isoformat(),
    }
    action_file.write_text(json.dumps(action_data), encoding="utf-8")
    target = params.chat_name or params.recipient
    return f"Teams message to {target} queued for delivery. User will receive confirmation via Telegram."


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
    timestamp = datetime.now().strftime("%H%M%S")
    action_file = PENDING_ACTIONS_DIR / f"email-reply-{timestamp}.json"
    action_data = {
        "type": "email_reply",
        "search_query": params.search_query,
        "message": params.message,
        "queued_at": datetime.now().isoformat(),
    }
    action_file.write_text(json.dumps(action_data), encoding="utf-8")
    return f"Email reply for '{params.search_query}' queued for delivery. User will receive confirmation via Telegram."


# --- Tool set builder ---

def get_tools() -> list[Tool]:
    """Return custom tools for registration on a session."""
    return [
        write_output, queue_task, dismiss_item, add_note,
        schedule_task, list_schedules_tool, update_schedule_tool, cancel_schedule,
        search_local_files, update_project,
        send_teams_message, send_email_reply,
        send_task_to_agent,
    ]
