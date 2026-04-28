"""Single source of truth for all path constants.

Zero-config path resolution:
  1. PULSE_HOME env var (explicit override, used by tests)
  2. OneDriveCommercial env var + /Documents/Pulse (auto-detected on Windows)
  3. PROJECT_ROOT (dev fallback)

Convention: every team member's data lives at the SAME relative path under
their OneDrive. Inter-agent paths are derived from alias — no per-member
config needed.
"""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent  # src/core/ -> project root
SRC_DIR = PROJECT_ROOT / "src"
CONFIG_DIR = PROJECT_ROOT / "config"
PROMPTS_DIR = CONFIG_DIR / "prompts"
INSTRUCTIONS_DIR = CONFIG_DIR / "instructions"

# --- PULSE_HOME: all persistent data (transcripts, digests, logs, state) ---
# Resolution: PULSE_HOME env > OneDriveCommercial/Documents/Pulse > PROJECT_ROOT
_pulse_home_env = os.environ.get("PULSE_HOME", "")
_onedrive_biz = os.environ.get("OneDriveCommercial", "")

if _pulse_home_env:
    PULSE_HOME = Path(os.path.expandvars(_pulse_home_env))
elif _onedrive_biz:
    PULSE_HOME = Path(_onedrive_biz) / "Documents" / "Pulse"
else:
    PULSE_HOME = PROJECT_ROOT  # dev/testing fallback

# --- PULSE_TEAM_DIR: shared team folder for inter-agent communication ---
# Convention: OneDrive/Documents/Pulse-Team/{alias}/ per team member.
# Each member's jobs folder: PULSE_TEAM_DIR / alias / "jobs" / "pending"
if _pulse_home_env:
    # PULSE_HOME is either a sibling of Pulse-Team (normal install:
    # Documents/Pulse + Documents/Pulse-Team) or a child of it (dev/test
    # instances like Pulse-Team/alpha). Detect which so the shared team
    # root isn't mis-derived as Pulse-Team/Pulse-Team.
    if PULSE_HOME.parent.name == "Pulse-Team":
        PULSE_TEAM_DIR = PULSE_HOME.parent
    else:
        PULSE_TEAM_DIR = PULSE_HOME.parent / "Pulse-Team"
elif _onedrive_biz:
    PULSE_TEAM_DIR = Path(_onedrive_biz) / "Documents" / "Pulse-Team"
else:
    PULSE_TEAM_DIR = PROJECT_ROOT / "Pulse-Team"  # dev fallback

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

# Safety net for agent_response ingests whose target project YAML has vanished
# (e.g. project deleted between broadcast and reply). Preserves the raw job
# YAML so nothing is silently lost — see _ingest_agent_response.
BROADCAST_ORPHANS_DIR = PULSE_HOME / ".broadcast-orphans"

# State files (dotfiles under PULSE_HOME)
TRANSCRIPT_STATUS_FILE = PULSE_HOME / ".transcript-collection-status.json"

# Backward-compat alias — write_output tool writes relative to this.
# OUTPUT_DIR = PULSE_HOME means `write_output("digests/2026-02-23.json", ...)`
# creates `PULSE_HOME/digests/2026-02-23.json` — the right place.
OUTPUT_DIR = PULSE_HOME
