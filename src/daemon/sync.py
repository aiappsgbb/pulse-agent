"""OneDrive sync — pull jobs in, push output out."""

import asyncio
import shutil
from pathlib import Path

from core.constants import PROJECT_ROOT, OUTPUT_DIR, TASKS_DIR
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
    """Pull new job files from OneDrive Jobs/ into tasks/pending/ and enqueue them."""
    onedrive_cfg = config.get("onedrive", {})
    if not onedrive_cfg.get("sync_enabled", False):
        return

    dest_root = Path(onedrive_cfg.get("path", ""))
    if not dest_root or str(dest_root) == ".":
        return

    jobs_src = dest_root / "Jobs"
    if not jobs_src.exists():
        jobs_src.mkdir(parents=True, exist_ok=True)
        return

    pending_dir = TASKS_DIR / "pending"
    pending_dir.mkdir(parents=True, exist_ok=True)
    pulled = 0

    for f in jobs_src.glob("*.yaml"):
        dest_file = pending_dir / f.name
        if not dest_file.exists():
            shutil.copy2(f, dest_file)
            pulled += 1

    if pulled:
        log.info(f"Pulled {pulled} new job(s) from OneDrive")

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
    """Copy output files to OneDrive so M365 Copilot can read them."""
    onedrive_cfg = config.get("onedrive", {})
    if not onedrive_cfg.get("sync_enabled", False):
        return

    dest_root = Path(onedrive_cfg.get("path", ""))
    if not dest_root or str(dest_root) == ".":
        log.warning("OneDrive sync enabled but no path configured")
        return

    synced = 0

    # Sync output subdirectories
    for subdir in ("digests", "intel", "pulse-signals"):
        src = OUTPUT_DIR / subdir
        if not src.exists():
            continue
        dest = dest_root / subdir
        dest.mkdir(parents=True, exist_ok=True)
        for f in src.iterdir():
            if f.is_file() and not f.name.startswith("."):
                dest_file = dest / f.name
                if not dest_file.exists() or f.stat().st_mtime > dest_file.stat().st_mtime:
                    shutil.copy2(f, dest_file)
                    synced += 1

    # Sync monitoring reports
    for f in OUTPUT_DIR.glob("monitoring-*.md"):
        dest_file = dest_root / f.name
        if not dest_file.exists() or f.stat().st_mtime > dest_file.stat().st_mtime:
            shutil.copy2(f, dest_file)
            synced += 1

    # Seed Agent Instructions (local defaults -> OneDrive, never overwrite)
    instructions_src = PROJECT_ROOT / "config" / "instructions"
    instructions_dest = dest_root / "Agent Instructions"
    if instructions_src.exists():
        instructions_dest.mkdir(parents=True, exist_ok=True)
        for f in instructions_src.glob("*.md"):
            dest_file = instructions_dest / f.name
            if not dest_file.exists():
                shutil.copy2(f, dest_file)
                synced += 1

    # Clean up completed jobs from OneDrive Jobs/ folder
    jobs_onedrive = dest_root / "Jobs"
    completed_dir = TASKS_DIR / "completed"
    if jobs_onedrive.exists() and completed_dir.exists():
        for f in list(jobs_onedrive.glob("*.yaml")):
            if (completed_dir / f.name).exists():
                f.unlink()
                synced += 1

    if synced:
        log.info(f"Synced {synced} file(s) to OneDrive: {dest_root}")
