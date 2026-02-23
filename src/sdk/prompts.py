"""Prompt loading and template interpolation."""

from pathlib import Path

from core.constants import PROJECT_ROOT, INSTRUCTIONS_DIR, PULSE_HOME


def load_prompt(path: str, variables: dict | None = None) -> str:
    """Load a prompt from a config file and interpolate {{variables}}.

    Args:
        path: Relative path from project root (e.g. 'config/prompts/system/base.md')
        variables: Dict of variable_name -> value for {{variable_name}} replacement
    """
    full_path = PROJECT_ROOT / path
    text = full_path.read_text(encoding="utf-8")
    if variables:
        for key, value in variables.items():
            text = text.replace("{{" + key + "}}", str(value))
    return text


def load_instruction(name: str, config: dict) -> str:
    """Load an instruction file — checks PULSE_HOME first, then local defaults.

    Users can edit instructions from their data folder; changes are picked up next run.
    """
    pulse_file = PULSE_HOME / "Agent Instructions" / f"{name}.md"
    if pulse_file.exists():
        return pulse_file.read_text(encoding="utf-8")

    local_file = INSTRUCTIONS_DIR / f"{name}.md"
    if local_file.exists():
        return local_file.read_text(encoding="utf-8")

    return ""
