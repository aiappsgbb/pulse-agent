"""Single source of truth for all path constants."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent  # src/core/ -> project root
SRC_DIR = PROJECT_ROOT / "src"
CONFIG_DIR = PROJECT_ROOT / "config"
INPUT_DIR = PROJECT_ROOT / "input"
OUTPUT_DIR = PROJECT_ROOT / "output"
LOGS_DIR = PROJECT_ROOT / "logs"
TASKS_DIR = PROJECT_ROOT / "tasks"
PROJECTS_DIR = OUTPUT_DIR / "projects"
PROMPTS_DIR = CONFIG_DIR / "prompts"
INSTRUCTIONS_DIR = CONFIG_DIR / "instructions"
