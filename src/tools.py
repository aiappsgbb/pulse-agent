"""Custom tool definitions for the GHCP SDK agent."""

import json
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel
from copilot import define_tool, Tool, ToolInvocation

PROJECT_ROOT = Path(__file__).parent.parent
LOGS_DIR = PROJECT_ROOT / "logs"
OUTPUT_DIR = PROJECT_ROOT / "output"
TASKS_DIR = PROJECT_ROOT / "tasks" / "pending"


# --- Tool parameter schemas ---

class LogActionParams(BaseModel):
    action: str
    reasoning: str
    category: str = "general"


class WriteOutputParams(BaseModel):
    filename: str
    content: str


class QueueTaskParams(BaseModel):
    task: str
    description: str
    priority: str = "normal"
    model: str = "claude-opus"


class DismissItemParams(BaseModel):
    item: str
    reason: str = ""


class AddNoteParams(BaseModel):
    item: str
    note: str


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
    description="Add a new deep research task to the queue. The agent will pick it up in the next research cycle.",
)
def queue_task(params: QueueTaskParams, invocation: ToolInvocation) -> str:
    TASKS_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")
    slug = params.task.lower().replace(" ", "-")[:50]
    task_file = TASKS_DIR / f"{date_str}-{slug}.yaml"

    import yaml
    task_data = {
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


ACTIONS_FILE = OUTPUT_DIR / ".digest-actions.json"


def _load_actions() -> dict:
    if ACTIONS_FILE.exists():
        return json.loads(ACTIONS_FILE.read_text(encoding="utf-8"))
    return {"dismissed": [], "notes": {}}


def _save_actions(actions: dict):
    ACTIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    ACTIONS_FILE.write_text(json.dumps(actions, indent=2), encoding="utf-8")


@define_tool(
    name="dismiss_item",
    description="Mark a digest item as handled/done so it won't appear in future digests. Use when the user says they've dealt with something.",
)
def dismiss_item(params: DismissItemParams, invocation: ToolInvocation) -> str:
    actions = _load_actions()
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
    actions = _load_actions()
    actions["notes"][params.item] = {
        "note": params.note,
        "added_at": datetime.now().isoformat(),
    }
    _save_actions(actions)
    return f"Note added to: {params.item}"


def get_tools() -> list[Tool]:
    """Return all custom tools for registration on a session."""
    return [log_action, write_output, queue_task, dismiss_item, add_note]
