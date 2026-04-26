"""Core infrastructure — constants, config, state, logging, browser."""

from core.constants import (
    PROJECT_ROOT, SRC_DIR, CONFIG_DIR, PULSE_HOME, OUTPUT_DIR,
    TRANSCRIPTS_DIR, DOCUMENTS_DIR, EMAILS_DIR, DIGESTS_DIR,
    INTEL_DIR, PROJECTS_DIR, SIGNALS_DIR, JOBS_DIR,
    LOGS_DIR, PROMPTS_DIR, INSTRUCTIONS_DIR,
)
from core.config import load_config, validate_config, load_pending_tasks, mark_task_completed
from core.state import load_json_state, save_json_state
from core.logging import setup_logging, new_run_id, log, log_event, safe_encode
