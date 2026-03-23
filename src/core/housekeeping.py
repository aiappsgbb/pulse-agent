"""Daily housekeeping — prune old files from PULSE_HOME to prevent OneDrive bloat.

Runs as a scheduled job (typically daily at 03:00). Configurable retention
periods for each data type. Files older than the retention period are deleted.

Default retention (days):
  - monitoring reports: 3
  - digests: 30
  - intel briefs: 14
  - logs (daily JSONL): 7
  - per-job logs: 3
  - completed jobs: 3
  - job history (.job-history.jsonl): truncate to 30 days
  - digest state (.digest-state.json): prune entries older than 30 days
  - intel state (.intel-state.json): prune entries older than 30 days
  - digest actions (.digest-actions.json): prune expired snoozed (>1d) and archived (>30d)
"""

import json
import time
from datetime import datetime, timedelta
from pathlib import Path

from core.constants import PULSE_HOME, LOGS_DIR, DIGESTS_DIR, INTEL_DIR, JOBS_DIR
from core.logging import log

# Default retention periods (days)
DEFAULT_RETENTION = {
    "monitoring": 3,
    "digests": 30,
    "intel": 14,
    "logs": 7,
    "job_logs": 3,
    "completed_jobs": 3,
    "job_history": 30,
    "state_files": 30,
}


def _age_days(path: Path) -> float:
    """Return the age of a file in days based on modification time."""
    try:
        return (time.time() - path.stat().st_mtime) / 86400
    except (OSError, ValueError):
        return 0


def _delete_old_files(directory: Path, pattern: str, max_age_days: int) -> int:
    """Delete files matching pattern older than max_age_days. Returns count deleted."""
    if not directory.exists():
        return 0
    deleted = 0
    for f in directory.glob(pattern):
        if f.is_file() and _age_days(f) > max_age_days:
            try:
                f.unlink()
                deleted += 1
            except OSError as e:
                log.debug(f"Housekeeping: could not delete {f.name}: {e}")
    return deleted


def _truncate_jsonl(path: Path, max_age_days: int) -> int:
    """Remove lines from a JSONL file where the 'ts' field is older than max_age_days.

    Returns the number of lines removed.
    """
    if not path.exists():
        return 0
    try:
        cutoff = (datetime.now() - timedelta(days=max_age_days)).isoformat()
        lines = path.read_text(encoding="utf-8").splitlines()
        original_count = len(lines)
        kept = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                ts = entry.get("ts", entry.get("timestamp", ""))
                if ts and ts < cutoff:
                    continue  # too old, drop it
            except (json.JSONDecodeError, TypeError):
                pass  # keep malformed lines
            kept.append(line)

        if len(kept) < original_count:
            path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
            return original_count - len(kept)
    except OSError as e:
        log.debug(f"Housekeeping: could not truncate {path.name}: {e}")
    return 0


def _prune_state_file(path: Path, max_age_days: int) -> int:
    """Prune old entries from a JSON state file (digest-state, intel-state).

    These files track processed content as {key: {timestamp, ...}} dicts.
    Returns the number of entries removed.
    """
    if not path.exists():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return 0

        cutoff = (datetime.now() - timedelta(days=max_age_days)).isoformat()
        original_count = 0
        pruned = {}

        for key, value in data.items():
            if isinstance(value, dict):
                original_count += 1
                ts = value.get("processed_at", value.get("ts", value.get("timestamp", "")))
                if ts and ts < cutoff:
                    continue  # too old
            pruned[key] = value

        removed = original_count - len([v for v in pruned.values() if isinstance(v, dict)])
        if removed > 0:
            path.write_text(json.dumps(pruned, indent=2), encoding="utf-8")
        return removed
    except (OSError, json.JSONDecodeError) as e:
        log.debug(f"Housekeeping: could not prune {path.name}: {e}")
    return 0


def _prune_digest_actions(path: Path) -> int:
    """Prune expired entries from .digest-actions.json.

    Removes:
      - Snoozed entries older than 1 day
      - Archived entries older than 30 days
      - Notes for items that no longer exist in the dismissed list
    Keeps:
      - Resolved entries (no expiry — permanently done)
      - Active snoozed/archived entries within TTL
    Returns the number of entries removed.
    """
    if not path.exists():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return 0

        dismissed = data.get("dismissed", [])
        original_count = len(dismissed)
        now = datetime.now()
        kept = []

        for d in dismissed:
            status = d.get("status", "archived")
            # Resolved items never expire
            if status == "resolved":
                kept.append(d)
                continue
            try:
                dismissed_at = datetime.fromisoformat(d.get("dismissed_at", ""))
                age_days = (now - dismissed_at).days
            except (ValueError, TypeError):
                age_days = 0

            if status == "dismissed" and age_days > 1:
                continue  # expired snooze
            if status in ("archived", "") and age_days > 30:
                continue  # expired archive (includes legacy entries)
            kept.append(d)

        removed = original_count - len(kept)

        # Also prune orphaned notes (notes for items no longer in dismissed list)
        kept_ids = {d.get("item") for d in kept}
        notes = data.get("notes", {})
        pruned_notes = {k: v for k, v in notes.items() if k in kept_ids}
        notes_removed = len(notes) - len(pruned_notes)

        if removed > 0 or notes_removed > 0:
            data["dismissed"] = kept
            data["notes"] = pruned_notes
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            import os
            os.replace(str(tmp), str(path))

        return removed + notes_removed
    except (OSError, json.JSONDecodeError) as e:
        log.debug(f"Housekeeping: could not prune {path.name}: {e}")
    return 0


def run_housekeeping(config: dict | None = None) -> dict:
    """Run daily housekeeping. Returns a summary of what was cleaned up.

    Retention periods can be overridden via config:
      housekeeping:
        retention:
          monitoring: 3
          digests: 30
          ...
    """
    retention = dict(DEFAULT_RETENTION)
    if config:
        overrides = config.get("housekeeping", {}).get("retention", {})
        retention.update({k: v for k, v in overrides.items() if isinstance(v, int) and v > 0})

    summary = {}

    # 1. Monitoring reports (monitoring-*.json and .md in PULSE_HOME root)
    for ext in ("json", "md"):
        n = _delete_old_files(PULSE_HOME, f"monitoring-*.{ext}", retention["monitoring"])
        summary[f"monitoring_{ext}"] = n

    # 2. Digests
    for ext in ("json", "md"):
        n = _delete_old_files(DIGESTS_DIR, f"*.{ext}", retention["digests"])
        summary[f"digests_{ext}"] = n

    # 3. Intel briefs
    n = _delete_old_files(INTEL_DIR, "*.md", retention["intel"])
    summary["intel"] = n

    # 4. Daily audit logs
    n = _delete_old_files(LOGS_DIR, "????-??-??.jsonl", retention["logs"])
    summary["daily_logs"] = n

    # 5. Per-job activity logs
    n = _delete_old_files(LOGS_DIR, "job-*.jsonl", retention["job_logs"])
    summary["job_logs"] = n

    # 6. Completed job YAMLs
    completed_dir = JOBS_DIR / "completed"
    n = _delete_old_files(completed_dir, "*.yaml", retention["completed_jobs"])
    summary["completed_jobs"] = n

    # 7. Job history JSONL — truncate old entries
    job_history = PULSE_HOME / ".job-history.jsonl"
    n = _truncate_jsonl(job_history, retention["job_history"])
    summary["job_history_lines"] = n

    # 8. State files — prune old entries
    for state_file in (".digest-state.json", ".intel-state.json"):
        n = _prune_state_file(PULSE_HOME / state_file, retention["state_files"])
        summary[state_file] = n

    # 9. Digest actions — prune expired dismissed/archived entries
    n = _prune_digest_actions(PULSE_HOME / ".digest-actions.json")
    summary["digest_actions"] = n

    total = sum(summary.values())
    if total > 0:
        log.info(f"Housekeeping complete: {summary}")
    else:
        log.info("Housekeeping: nothing to clean up")

    return summary
