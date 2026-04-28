"""Subprocess descendant cleanup on daemon shutdown.

The Copilot SDK launches MCP servers (workiq, playwright/mcp, context7, etc.)
as subprocesses of the `copilot` CLI binary, which itself is a subprocess of
this Python process. On Windows, signals do NOT cascade through process
groups, so when ``client.stop()`` returns, the immediate child (`copilot`
CLI) terminates but its grandchildren (`npx`, `tsx`, `node` running each MCP
server) get reparented and live on indefinitely.

Across many daemon restarts these grandchildren accumulate, race for shared
state (MSAL token cache, Playwright browser profile lock), and start causing
``MCP error -32001: Request timed out`` failures that cascade into a frozen
TUI.

## Why we snapshot BEFORE shutdown

Once the immediate child (`copilot` CLI) has exited, its descendants reparent
on Windows and ``psutil.Process().children(recursive=True)`` from us no longer
sees them — they have effectively been "lost." So we must capture the full
descendant PID list while the tree is still intact, *then* let the SDK
shut down, *then* kill any survivors from the snapshot.

The intended call site is:

    snapshot = snapshot_descendant_pids()
    await client.stop()             # may or may not cascade — we don't trust it
    kill_pids(snapshot)              # nukes anything still alive

This module also exposes ``kill_subprocess_descendants`` as a convenience
that combines both for cases where the tree is still intact at call time.
"""

import time
from core.logging import log


def _import_psutil():
    """Import psutil lazily so the module loads without it.

    Returns the module or None. The bare import lives behind a function so
    tests can monkeypatch ``builtins.__import__`` to simulate a missing
    optional dep.
    """
    try:
        import psutil
        return psutil
    except ImportError:
        log.debug("psutil not installed — subprocess cleanup will no-op")
        return None


def snapshot_descendant_pids() -> list[int]:
    """Capture the PIDs of every subprocess descendant of this process.

    Call this *before* graceful shutdown of the SDK / Copilot CLI. The
    returned list is a frozen view of the tree at this moment; it stays
    valid even after the immediate children exit, which is the whole point.

    Returns an empty list on any error or when psutil is unavailable.
    """
    psutil = _import_psutil()
    if psutil is None:
        return []
    try:
        me = psutil.Process()
        return [p.pid for p in me.children(recursive=True)]
    except Exception as e:
        log.debug(f"snapshot_descendant_pids: {e}")
        return []


def kill_pids(pids: list[int], timeout: float = 5.0) -> int:
    """Terminate every PID in the list, escalating to SIGKILL after timeout.

    Best-effort: any psutil error per-process is swallowed so a cleanup glitch
    never blocks daemon shutdown. Returns the count of PIDs that were both
    targeted and confirmed dead (or were already gone) by the end of the call.
    """
    psutil = _import_psutil()
    if psutil is None or not pids:
        return 0

    procs = []
    for pid in pids:
        try:
            procs.append(psutil.Process(pid))
        except psutil.NoSuchProcess:
            # Already gone — counts as "cleaned"
            continue
        except psutil.Error:
            continue

    if not procs:
        return len(pids)  # all already dead, all clean

    log.info(f"Killing {len(procs)} leaked subprocess descendant(s) on shutdown")

    # Polite first — gives MCP servers a chance to flush state.
    for p in procs:
        try:
            p.terminate()
        except psutil.Error:
            pass

    try:
        gone, alive = psutil.wait_procs(procs, timeout=timeout)
    except psutil.Error:
        gone, alive = [], procs

    # Force survivors.
    for p in alive:
        try:
            p.kill()
        except psutil.Error:
            pass

    if alive:
        try:
            psutil.wait_procs(alive, timeout=2.0)
        except psutil.Error:
            pass

    # Confirm what's gone.
    cleaned = 0
    for pid in pids:
        try:
            if not psutil.pid_exists(pid):
                cleaned += 1
                continue
            p = psutil.Process(pid)
            if p.status() == psutil.STATUS_ZOMBIE:
                cleaned += 1
        except psutil.Error:
            cleaned += 1
    return cleaned


def kill_subprocess_descendants(timeout: float = 5.0) -> int:
    """Snapshot + kill convenience: enumerate descendants then terminate them.

    Suitable for callers that hold the tree intact through this call. For
    SDK shutdown — where the chain unravels mid-flight — use
    ``snapshot_descendant_pids`` before the unravel and ``kill_pids`` after.
    """
    return kill_pids(snapshot_descendant_pids(), timeout=timeout)
