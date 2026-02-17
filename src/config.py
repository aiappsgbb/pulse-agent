"""Load standing instructions and task queue configuration."""

import os
from pathlib import Path

import yaml

CONFIG_DIR = Path(__file__).parent.parent / "config"
TASKS_DIR = Path(__file__).parent.parent / "tasks"



def _expand_env_vars(obj):
    """Recursively expand environment variables in string values.

    Supports $VAR, ${VAR}, and ~ (home directory) in strings.
    """
    if isinstance(obj, str):
        # Expand ~ to home directory
        if obj.startswith("~"):
            obj = str(Path.home()) + obj[1:]
        # Expand $VAR and ${VAR}
        return os.path.expandvars(obj)
    if isinstance(obj, dict):
        return {k: _expand_env_vars(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand_env_vars(item) for item in obj]
    return obj


def validate_config(config: dict) -> list[str]:
    """Validate config and return a list of warnings (empty = all good)."""
    warnings = []

    if not config.get("models"):
        warnings.append("No 'models' section in config — will use defaults")

    for path_cfg in config.get("digest", {}).get("input_paths", []):
        if not path_cfg.get("path"):
            warnings.append("Digest input_paths entry missing 'path' field")

    return warnings


def load_config() -> dict:
    """Load standing instructions from YAML config.

    Expands environment variables and validates required fields.
    """
    config_path = CONFIG_DIR / "standing-instructions.yaml"
    with open(config_path, "r") as f:
        config = yaml.safe_load(f)

    if not config or not isinstance(config, dict):
        raise ValueError(f"Config file is empty or invalid: {config_path}")

    # Expand environment variables in all string values
    config = _expand_env_vars(config)

    return config


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
