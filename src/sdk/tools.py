"""Custom tool definitions for the GHCP SDK agent."""

import json
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel
from copilot import define_tool, Tool, ToolInvocation

from core.constants import LOGS_DIR, OUTPUT_DIR, TASKS_DIR
from core.state import load_json_state, save_json_state


# --- Tool parameter schemas ---

class LogActionParams(BaseModel):
    action: str
    reasoning: str
    category: str = "general"


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


class SearchLocalFilesParams(BaseModel):
    query: str
    file_pattern: str = "*.txt"  # glob pattern to filter files
    max_results: int = 5


# --- Tool handlers ---

@define_tool(
    name="log_action",
    description="Log an action the agent took, with reasoning. Used for audit trail and M365 Copilot discoverability.",
)
def log_action(params: LogActionParams, invocation: ToolInvocation) -> str:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().isoformat()
    log_entry = {
        "timestamp": timestamp,
        "action": params.action,
        "reasoning": params.reasoning,
        "category": params.category,
    }

    # Append to daily log file
    date_str = datetime.now().strftime("%Y-%m-%d")
    log_file = LOGS_DIR / f"{date_str}.jsonl"
    with open(log_file, "a") as f:
        f.write(json.dumps(log_entry) + "\n")

    return f"Logged: {params.action}"


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
    pending_dir = TASKS_DIR / "pending"
    pending_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")
    slug = params.task.lower().replace(" ", "-")[:50]
    task_file = pending_dir / f"{date_str}-{slug}.yaml"

    import yaml
    task_data = {
        "type": params.type,
        "task": params.task,
        "description": params.description,
        "priority": params.priority,
        "model": params.model,
        "output": {
            "local": f"./output/{slug}/",
        },
    }

    with open(task_file, "w") as f:
        yaml.dump(task_data, f, default_flow_style=False)

    return f"Task queued: {task_file}"


# --- Digest actions (dismiss/note) ---

ACTIONS_FILE = OUTPUT_DIR / ".digest-actions.json"


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
    name="cancel_schedule",
    description="Remove a recurring schedule by ID.",
)
def cancel_schedule(params: CancelScheduleParams, invocation: ToolInvocation) -> str:
    from core.scheduler import remove_schedule
    if remove_schedule(params.id):
        return f"Cancelled: '{params.id}'"
    return f"Schedule '{params.id}' not found."


# --- Local file search ---

@define_tool(
    name="search_local_files",
    description=(
        "Search local input files (transcripts, documents, emails) for a keyword or phrase. "
        "Use this to find context before responding — e.g., search for a person's name, "
        "project name, or topic across recent meeting transcripts and documents. "
        "Returns matching snippets with surrounding context."
    ),
)
def search_local_files(params: SearchLocalFilesParams, invocation: ToolInvocation) -> str:
    from core.constants import INPUT_DIR

    if not INPUT_DIR.exists():
        return "No input directory found."

    # Prevent path traversal in glob pattern
    if ".." in params.file_pattern:
        return "ERROR: Invalid file pattern."

    query_lower = params.query.lower()
    results = []

    # Search recursively across all input subdirs
    for filepath in sorted(INPUT_DIR.rglob(params.file_pattern)):
        if not filepath.is_file():
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

        rel_path = filepath.relative_to(INPUT_DIR)
        match_text = "\n---\n".join(snippets)
        results.append(f"### {rel_path}\n{match_text}")

        if len(results) >= params.max_results:
            break

    if not results:
        return f"No matches for '{params.query}' in {params.file_pattern} files."

    return f"Found {len(results)} file(s) matching '{params.query}':\n\n" + "\n\n".join(results)


# --- Tool set builder ---

def get_tools() -> list[Tool]:
    """Return custom tools for registration on a session."""
    return [
        log_action, write_output, queue_task, dismiss_item, add_note,
        schedule_task, list_schedules_tool, cancel_schedule,
        search_local_files,
    ]
