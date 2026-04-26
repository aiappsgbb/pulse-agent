"""First-run detection and config generation for onboarding.

The onboarding conversation happens in the Chat pane — the SDK agent asks
questions and calls the save_config tool.  This module provides the pure
logic: detection, template merging, and YAML writing.
"""

from pathlib import Path

import yaml

from core.constants import PULSE_HOME, CONFIG_DIR


# Fields that MUST be filled in (no TODO placeholders allowed)
_REQUIRED_USER_FIELDS = ("name", "email")


def is_first_run(config: dict | None) -> bool:
    """Return True when onboarding is needed.

    Triggers when:
    - config is None (no file found at all)
    - any required user field is missing or contains 'TODO:'
    """
    if config is None:
        return True
    user = config.get("user") or {}
    for field in _REQUIRED_USER_FIELDS:
        val = user.get(field, "")
        if not val or (isinstance(val, str) and "TODO" in val.upper()):
            return True
    return False


def load_template_config() -> dict:
    """Load the template config for merging with user answers."""
    template_path = CONFIG_DIR / "standing-instructions.template.yaml"
    if not template_path.exists():
        # Fallback to the main config (may already be the template)
        template_path = CONFIG_DIR / "standing-instructions.yaml"
    if not template_path.exists():
        return {}
    with open(template_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def build_config_from_answers(answers: dict, template: dict | None = None) -> dict:
    """Deep-merge agent-collected answers onto the template.

    *answers* has the same top-level keys as standing-instructions.yaml:
    user, schedule, monitoring, team, intelligence, models, digest, transcripts.

    Any key present in *answers* replaces the template value.  Keys only in
    the template (e.g. digest.input_paths) are preserved.
    """
    if template is None:
        template = load_template_config()

    merged = dict(template)  # shallow copy top level

    for key, value in answers.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            # Merge one level deep (user, monitoring, intelligence, etc.)
            merged[key] = {**merged[key], **value}
        else:
            merged[key] = value

    # Strip any remaining TODO placeholders from user section
    user = merged.get("user", {})
    for k, v in list(user.items()):
        if isinstance(v, str) and "TODO" in v.upper():
            user[k] = ""
        elif isinstance(v, list):
            user[k] = [
                item for item in v
                if not (isinstance(item, str) and "TODO" in item.upper())
            ]

    # Strip TODOs from intelligence section
    intel = merged.get("intelligence", {})
    for k in ("topics", "competitors"):
        items = intel.get(k, [])
        if isinstance(items, list):
            cleaned = []
            for item in items:
                if isinstance(item, str) and "TODO" in item.upper():
                    continue
                if isinstance(item, dict):
                    has_todo = any(
                        "TODO" in str(v).upper()
                        for v in item.values()
                    )
                    if has_todo:
                        continue
                cleaned.append(item)
            intel[k] = cleaned

    return merged


def write_config(config_dict: dict, dest: Path | None = None) -> Path:
    """Write the config dict as clean YAML (atomic via temp + rename).

    Creates parent directories if needed.  Returns the path written to.
    """
    import os

    if dest is None:
        dest = PULSE_HOME / "standing-instructions.yaml"

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dest.with_suffix(".yaml.tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            yaml.dump(
                config_dict,
                f,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )
        os.replace(tmp_path, dest)
    except BaseException:
        # Clean up partial temp file on any failure
        tmp_path.unlink(missing_ok=True)
        raise
    return dest
