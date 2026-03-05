"""Sync — enqueue pending jobs, seed instructions, clean up completed jobs.

With PULSE_HOME pointing to OneDrive, data is already synced by OneDrive client.
This module handles: (1) enqueuing pending job files, (2) seeding instruction
defaults from the repo, (3) cleaning up completed jobs.
"""

import asyncio
import shutil
from pathlib import Path

from core.constants import PULSE_HOME, JOBS_DIR, INSTRUCTIONS_DIR
from core.config import load_pending_tasks
from core.logging import log


def _get_enqueued_files(job_queue: asyncio.Queue) -> set[str]:
    """Get/create in-memory tracking set for file-based jobs already enqueued."""
    enqueued = getattr(job_queue, "_enqueued_files", None)
    if enqueued is None:
        enqueued = set()
        setattr(job_queue, "_enqueued_files", enqueued)
    return enqueued


def sync_jobs_from_onedrive(config: dict, job_queue: asyncio.Queue):
    """Enqueue any pending job files from JOBS_DIR/pending/.

    Inter-agent requests land directly in JOBS_DIR (which IS on OneDrive).
    This function just checks for new files and enqueues them.
    """
    # Enqueue any pending file-based jobs
    enqueued_files = _get_enqueued_files(job_queue)
    for job in load_pending_tasks():
        job_file = job.get("_file")
        if not job_file:
            continue
        if job_file in enqueued_files:
            continue
        job_queue.put_nowait(job)
        enqueued_files.add(job_file)


def sync_to_onedrive(config: dict):
    """Seed instructions and clean up completed jobs.

    Data already lives on OneDrive (PULSE_HOME) — no copy needed.
    """
    synced = 0

    # Seed Agent Instructions (repo defaults -> PULSE_HOME, never overwrite)
    instructions_dest = PULSE_HOME / "Agent Instructions"
    if INSTRUCTIONS_DIR.exists():
        instructions_dest.mkdir(parents=True, exist_ok=True)
        for f in INSTRUCTIONS_DIR.glob("*.md"):
            dest_file = instructions_dest / f.name
            if not dest_file.exists():
                shutil.copy2(f, dest_file)
                synced += 1

    # Clean up completed jobs
    jobs_dir = JOBS_DIR
    completed_dir = JOBS_DIR / "completed"
    pending_dir = JOBS_DIR / "pending"
    if pending_dir.exists() and completed_dir.exists():
        for f in list(pending_dir.glob("*.yaml")):
            if (completed_dir / f.name).exists():
                # Job was completed — remove from pending
                f.unlink()
                synced += 1

    if synced:
        log.info(f"Synced {synced} file(s)")
