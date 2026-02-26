"""Desktop notification helper for Pulse Agent.

Fires Windows toast notifications via winotify. Silent failure — a notification
error must never crash the daemon. Falls back to logging if winotify unavailable.
"""

import json
from pathlib import Path

from core.logging import log


def notify_desktop(title: str, body: str, urgency: str = "normal") -> None:
    """Fire a Windows toast notification.

    Args:
        title: Notification title (app name + short label).
        body: Notification body text (1-2 sentences).
        urgency: "normal" (short duration) or "urgent" (long + alarm sound).
    """
    try:
        from winotify import Notification, audio
        toast = Notification(
            app_id="Pulse Agent",
            title=title,
            msg=body,
            duration="long" if urgency == "urgent" else "short",
        )
        if urgency == "urgent":
            toast.set_audio(audio.LoopingAlarm, loop=False)
        toast.show()
        log.debug(f"Toast: [{urgency}] {title} — {body[:60]}")
    except ImportError:
        log.debug(f"Notification (winotify unavailable): {title} — {body[:60]}")
    except Exception as e:
        log.debug(f"Toast failed (non-fatal): {e}")


def build_toast_summary(job_type: str, pulse_home: Path) -> tuple[str, str]:
    """Read the freshly-written job output and build (title, body) for a toast.

    Returns a generic fallback if the output file cannot be read.
    """
    try:
        if job_type == "monitor":
            files = sorted(pulse_home.glob("monitoring-*.json"), reverse=True)
            if files:
                data = json.loads(files[0].read_text(encoding="utf-8"))
                items = data.get("items", [])
                count = len(items)
                if count == 0:
                    return "Pulse — Triage complete", "No items need attention."
                top = items[0]
                priority = top.get("priority", "").upper()
                title_text = top.get("title") or top.get("summary", "")[:60]
                body = f"{count} item{'s' if count != 1 else ''} need attention"
                if title_text:
                    body += f"\n[{priority}] {title_text}"
                return "Pulse — Triage", body

        elif job_type == "digest":
            from core.constants import DIGESTS_DIR
            files = sorted(DIGESTS_DIR.glob("*.json"), reverse=True)
            if files:
                data = json.loads(files[0].read_text(encoding="utf-8"))
                items = data.get("items", [])
                count = len(items)
                if count == 0:
                    return "Pulse — Morning Digest", "Nothing flagged today."
                urgent = sum(1 for i in items if i.get("priority") in ("urgent", "high"))
                body = f"{count} item{'s' if count != 1 else ''} flagged"
                if urgent:
                    body += f" ({urgent} high priority)"
                return "Pulse — Morning Digest", body

        elif job_type == "intel":
            return "Pulse — Intel Brief", "External intelligence brief is ready."

    except Exception as e:
        log.debug(f"build_toast_summary failed for {job_type}: {e}")

    labels = {
        "monitor": ("Pulse — Triage", "Triage cycle complete."),
        "digest": ("Pulse — Digest", "Morning digest is ready."),
        "intel": ("Pulse — Intel", "Intel brief is ready."),
        "knowledge": ("Pulse — Knowledge", "Knowledge mining complete."),
        "transcripts": ("Pulse — Transcripts", "Transcript collection complete."),
    }
    return labels.get(job_type, ("Pulse", f"{job_type} complete."))
