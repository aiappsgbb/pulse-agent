"""Prompt loading and template interpolation."""

from pathlib import Path

from core.constants import PROJECT_ROOT, INSTRUCTIONS_DIR, PULSE_HOME

ENRICHMENTS_DIR = PROJECT_ROOT / "config" / "prompts" / "enrichments"

# Registry of enrichment prefixes and their availability checkers.
# Each entry: (file_prefix, callable_that_returns_bool).
# To add a new CRM/tool enrichment, add an entry here + drop files in enrichments/.
_ENRICHMENT_CHECKERS: list[tuple[str, str]] = [
    # (prefix, dotted import path to availability checker)
    ("msx", "sdk.agents.is_msx_available"),
]


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


def load_enrichments(base_name: str) -> str:
    """Load optional enrichment fragments for a prompt.

    Scans config/prompts/enrichments/ for feature-specific fragments that extend
    prompts at runtime. Only loads a fragment when the corresponding feature is
    available (e.g., MSX-MCP plugin installed).

    Naming convention: ``{prefix}-{base_name}.md``
      - ``msx-knowledge-miner.md``  → appended to knowledge-miner agent prompt
      - ``msx-chat.md``             → appended to chat system prompt
      - ``msx-trigger-digest.md``   → loaded as trigger variable for digest mode

    To add a new enrichment source (e.g., Salesforce):
      1. Add an availability checker to _ENRICHMENT_CHECKERS
      2. Drop ``salesforce-{base_name}.md`` files in enrichments/
    """
    if not ENRICHMENTS_DIR.exists():
        return ""

    parts: list[str] = []

    for prefix, checker_path in _ENRICHMENT_CHECKERS:
        enrichment_file = ENRICHMENTS_DIR / f"{prefix}-{base_name}.md"
        if not enrichment_file.exists():
            continue

        # Lazy-import the checker to avoid circular imports
        module_path, func_name = checker_path.rsplit(".", 1)
        import importlib
        mod = importlib.import_module(module_path)
        checker = getattr(mod, func_name)

        if checker():
            parts.append(enrichment_file.read_text(encoding="utf-8"))

    return "\n\n".join(parts)


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
