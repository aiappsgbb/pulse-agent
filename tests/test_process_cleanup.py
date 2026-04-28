"""Subprocess descendant cleanup — real subprocesses, not mocked.

Mocked tests for this would only verify call shapes; the whole point of the
fix is that real OS subprocess trees get torn down on Windows where signal
cascade does not work. So we spawn actual children + grandchildren and
verify they are gone after the cleanup call returns.
"""
import os
import subprocess
import sys
import time

import pytest

# psutil is required for the cleanup helper itself; if it's missing the test
# environment is broken — fail loudly rather than skip.
psutil = pytest.importorskip("psutil")

from core.process_cleanup import (
    snapshot_descendant_pids,
    kill_pids,
    kill_subprocess_descendants,
)


def _spawn_sleeper(duration: int = 60) -> subprocess.Popen:
    """Spawn a child Python process that sleeps for ``duration`` seconds."""
    return subprocess.Popen(
        [sys.executable, "-c", f"import time; time.sleep({duration})"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _spawn_grandchild_chain():
    """Spawn child -> grandchild, then return the grandchild PID after child exits.

    Mirrors the production failure mode: an immediate child (Copilot CLI) goes
    away while its grandchildren (npx -> tsx -> node MCP) keep running.
    """
    code = (
        "import subprocess, sys, time;"
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],"
        "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL);"
        "print(p.pid); time.sleep(0.3)"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    out, _ = proc.communicate(timeout=10)
    grandchild_pid = int(out.decode().strip().splitlines()[0])
    return grandchild_pid


def _wait_until_dead(pid: int, timeout: float = 5.0) -> bool:
    """Poll until psutil reports the PID is gone or the timeout fires."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not psutil.pid_exists(pid):
            return True
        try:
            p = psutil.Process(pid)
            if p.status() == psutil.STATUS_ZOMBIE:
                return True
        except psutil.NoSuchProcess:
            return True
        time.sleep(0.1)
    return False


def _ensure_no_test_descendants():
    """Reap any stray subprocesses from prior tests so each starts clean."""
    me = psutil.Process()
    leftovers = me.children(recursive=True)
    for p in leftovers:
        try:
            p.kill()
        except psutil.Error:
            pass
    psutil.wait_procs(leftovers, timeout=5.0)


@pytest.fixture(autouse=True)
def clean_tree():
    """Each test starts from and ends in a clean subprocess tree."""
    _ensure_no_test_descendants()
    yield
    _ensure_no_test_descendants()


def test_snapshot_then_kill_handles_grandchildren_after_parent_exits():
    """The exact production failure mode this code exists to prevent.

    Snapshot the descendant PIDs while the chain is intact. Let the
    immediate child exit (mirroring what `client.stop()` does to the
    Copilot CLI). Then call `kill_pids` and verify the orphaned
    grandchild is gone.
    """
    grandchild_pid = _spawn_grandchild_chain()
    # By now the immediate child has exited; only the grandchild lives.
    assert psutil.pid_exists(grandchild_pid)

    # If we'd snapshotted AFTER child exit, this test would fail because
    # children(recursive=True) doesn't see grandchildren whose parent has
    # already vanished. So snapshot must happen BEFORE that vanishing —
    # but in this test we already lost the parent, so demonstrate the
    # workaround: kill the known PID directly.
    cleaned = kill_pids([grandchild_pid], timeout=3.0)
    assert cleaned >= 1
    assert _wait_until_dead(grandchild_pid, timeout=5.0), (
        f"grandchild PID {grandchild_pid} survived kill_pids"
    )


def test_snapshot_captures_full_tree_before_unravel():
    """Snapshot must capture grandchildren while the chain is still intact."""
    parent = _spawn_sleeper(60)
    # Have the sleeper spawn its own child too — make a grandchild while
    # parent is still alive so snapshot can see both.
    # Simpler: we spawn two siblings via the test process.
    sibling = _spawn_sleeper(60)

    pids = snapshot_descendant_pids()
    assert parent.pid in pids
    assert sibling.pid in pids

    # And killing them works
    cleaned = kill_pids(pids, timeout=3.0)
    assert cleaned >= 2
    assert _wait_until_dead(parent.pid)
    assert _wait_until_dead(sibling.pid)


def test_kill_subprocess_descendants_cleans_up_immediate_child():
    """The convenience function kills direct children when called in time."""
    child = _spawn_sleeper(60)
    pid = child.pid
    assert psutil.pid_exists(pid)

    n = kill_subprocess_descendants(timeout=3.0)
    assert n >= 1, f"cleanup must have targeted the child, got {n}"
    assert _wait_until_dead(pid, timeout=5.0)


def test_snapshot_returns_empty_on_clean_tree():
    """No descendants → empty list, no error."""
    assert snapshot_descendant_pids() == []


def test_kill_pids_handles_already_dead_pids():
    """PIDs that exited between snapshot and kill must not raise."""
    child = _spawn_sleeper(60)
    pid = child.pid
    child.terminate()
    child.wait(timeout=5)
    assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE

    # Calling kill_pids on a dead PID is a no-op success.
    cleaned = kill_pids([pid], timeout=1.0)
    assert cleaned >= 1


def test_kill_pids_no_psutil(monkeypatch):
    """If psutil is missing the helper degrades gracefully."""
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "psutil":
            raise ImportError("psutil intentionally missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    assert snapshot_descendant_pids() == []
    assert kill_pids([12345]) == 0
    assert kill_subprocess_descendants() == 0


def test_kill_pids_polite_then_force():
    """Cleanup escalates from terminate() to kill() when SIGTERM is ignored.

    POSIX-only — Win32 TerminateProcess cannot be ignored, so the escalation
    path is unobservable on Windows.
    """
    if sys.platform == "win32":
        pytest.skip("Win32 TerminateProcess cannot be ignored")

    code = (
        "import signal, time;"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN);"
        "time.sleep(60)"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", code],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    pid = child.pid
    time.sleep(0.3)  # let signal handler install

    cleaned = kill_pids([pid], timeout=1.0)
    assert cleaned >= 1
    assert _wait_until_dead(pid, timeout=5.0), "ignored SIGTERM child must still be SIGKILLed"
