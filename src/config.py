"""Load standing instructions and task queue configuration."""

from pathlib import Path

import yaml

CONFIG_DIR = Path(__file__).parent.parent / "config"
TASKS_DIR = Path(__file__).parent.parent / "tasks"


def load_config() -> dict:
    """Load standing instructions from YAML config."""
    config_path = CONFIG_DIR / "standing-instructions.yaml"
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def load_pending_tasks() -> list[dict]:
    """Load all pending research tasks from tasks/pending/."""
    pending_dir = TASKS_DIR / "pending"
    tasks = []
    if not pending_dir.exists():
        return tasks
    for task_file in sorted(pending_dir.glob("*.yaml")):
        with open(task_file, "r") as f:
            task = yaml.safe_load(f)
            task["_file"] = str(task_file)
            tasks.append(task)
    return tasks


def mark_task_completed(task: dict):
    """Move a task file from pending/ to completed/."""
    src = Path(task["_file"])
    dest = TASKS_DIR / "completed" / src.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dest)
