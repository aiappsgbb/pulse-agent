"""Load standing instructions and task queue configuration."""

import os
from pathlib import Path

import yaml

from core.constants import CONFIG_DIR, JOBS_DIR, PULSE_HOME, PULSE_TEAM_DIR


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

    for member in config.get("team", []):
        if not member.get("alias"):
            warnings.append(f"Team member '{member.get('name', '?')}' missing 'alias'")
        # agent_path is optional — convention-based paths (PULSE_TEAM_DIR/alias) are preferred

    return warnings


def load_config() -> dict:
    """Load standing instructions from YAML config.

    Resolution order:
    1. --config CLI flag / PULSE_CONFIG env var (explicit override)
    2. $PULSE_HOME/standing-instructions.yaml (user's OneDrive copy)
    3. config/standing-instructions.yaml (repo template fallback)
    """
    override = os.environ.get("PULSE_CONFIG")
    if override:
        config_path = Path(override)
    elif (PULSE_HOME / "standing-instructions.yaml").exists():
        config_path = PULSE_HOME / "standing-instructions.yaml"
    else:
        config_path = CONFIG_DIR / "standing-instructions.yaml"

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    if not config or not isinstance(config, dict):
        raise ValueError(f"Config file is empty or invalid: {config_path}")

    # Coalesce None values to empty dicts for top-level sections that
    # downstream code chains .get() on.  YAML parses "key:" with no value
    # as None, and dict.get("key", {}) returns None (not {}) when the key
    # exists, which crashes chained .get() calls.
    for key in ("mcp_servers", "digest", "monitoring", "transcripts",
                "housekeeping", "models", "intel"):
        if key in config and config[key] is None:
            config[key] = {}

    # Expand environment variables in all string values
    config = _expand_env_vars(config)

    return config


def load_template_config() -> dict:
    """Load the standing-instructions template for onboarding.

    Used by the onboarding wizard to pre-fill defaults and preserve
    sections the user doesn't touch (feeds, input_paths, models).
    """
    template_path = CONFIG_DIR / "standing-instructions.template.yaml"
    if not template_path.exists():
        template_path = CONFIG_DIR / "standing-instructions.yaml"
    if not template_path.exists():
        return {}
    with open(template_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _team_inbox_pending_dir() -> Path | None:
    """Resolve this agent's inter-agent inbox: PULSE_TEAM_DIR/{my_alias}/jobs/pending.

    Returns None when there is no configured alias or the folder does not exist —
    that is the normal case for solo users and prevents a missing-folder warning.
    """
    try:
        alias = (load_config().get("user") or {}).get("alias")
    except Exception:
        alias = None
    if not alias:
        return None
    candidate = PULSE_TEAM_DIR / alias / "jobs" / "pending"
    return candidate if candidate.exists() else None


def load_pending_tasks() -> list[dict]:
    """Load all pending jobs from the local queue AND this agent's team inbox.

    Scans both ``PULSE_HOME/jobs/pending/`` (local/agent-generated work) and
    ``PULSE_TEAM_DIR/{my_alias}/jobs/pending/`` (inter-agent requests synced
    from teammates' OneDrives). Skips retry jobs whose ``_retry_after``
    timestamp has not yet passed.
    """
    from datetime import datetime

    pending_dirs = [JOBS_DIR / "pending"]
    team_inbox = _team_inbox_pending_dir()
    if team_inbox is not None:
        pending_dirs.append(team_inbox)

    tasks = []
    now = datetime.now()
    for pending_dir in pending_dirs:
        if not pending_dir.exists():
            continue
        for task_file in sorted(pending_dir.glob("*.yaml")):
            with open(task_file, "r") as f:
                task = yaml.safe_load(f)
            retry_after = task.get("_retry_after")
            if retry_after:
                try:
                    if datetime.fromisoformat(retry_after) > now:
                        continue  # Not yet due — skip until next sync cycle
                except (ValueError, TypeError):
                    pass  # Malformed timestamp — proceed anyway
            task["_file"] = str(task_file)
            tasks.append(task)
    return tasks


def mark_task_completed(task: dict):
    """Move a task file from pending/ to its sibling completed/ folder.

    Supports both the local queue (``PULSE_HOME/jobs/``) and team inboxes
    (``PULSE_TEAM_DIR/{alias}/jobs/``) by deriving the destination from the
    source file's parent, not from the static JOBS_DIR.
    """
    src = Path(task["_file"])
    completed_dir = src.parent.parent / "completed"
    completed_dir.mkdir(parents=True, exist_ok=True)
    src.rename(completed_dir / src.name)
