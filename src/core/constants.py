"""Single source of truth for all path constants.

PULSE_HOME env var controls where all persistent data lives.
Set it to your OneDrive/Pulse folder. Falls back to PROJECT_ROOT for dev.
"""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent  # src/core/ -> project root
SRC_DIR = PROJECT_ROOT / "src"
CONFIG_DIR = PROJECT_ROOT / "config"
PROMPTS_DIR = CONFIG_DIR / "prompts"
INSTRUCTIONS_DIR = CONFIG_DIR / "instructions"

# PULSE_HOME: all persistent data (transcripts, digests, logs, state files).
# Production: set PULSE_HOME to OneDrive/Pulse folder.
# Dev/testing: defaults to PROJECT_ROOT (tests patch individual constants).
_pulse_home_env = os.environ.get("PULSE_HOME", "")
PULSE_HOME = Path(os.path.expandvars(_pulse_home_env)) if _pulse_home_env else PROJECT_ROOT

# Named data directories (flat under PULSE_HOME)
TRANSCRIPTS_DIR = PULSE_HOME / "transcripts"
DOCUMENTS_DIR = PULSE_HOME / "documents"
EMAILS_DIR = PULSE_HOME / "emails"
TEAMS_MESSAGES_DIR = PULSE_HOME / "teams-messages"
DIGESTS_DIR = PULSE_HOME / "digests"
INTEL_DIR = PULSE_HOME / "intel"
PROJECTS_DIR = PULSE_HOME / "projects"
SIGNALS_DIR = PULSE_HOME / "pulse-signals"
JOBS_DIR = PULSE_HOME / "jobs"
LOGS_DIR = PULSE_HOME / "logs"

# Backward-compat alias — write_output tool writes relative to this.
# OUTPUT_DIR = PULSE_HOME means `write_output("digests/2026-02-23.json", ...)`
# creates `PULSE_HOME/digests/2026-02-23.json` — the right place.
OUTPUT_DIR = PULSE_HOME
