"""Generic JSON state persistence — replaces duplicated load/save patterns."""

import copy
import json
from pathlib import Path


def load_json_state(path: Path, default: dict) -> dict:
    """Load JSON state file, returning default if missing/corrupt."""
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return copy.deepcopy(default)


def save_json_state(path: Path, data: dict):
    """Save JSON state file, creating parent dirs as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
