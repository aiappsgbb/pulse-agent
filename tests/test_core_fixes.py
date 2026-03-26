"""Tests for core module fixes: browser lock, state error handling, scheduler logging, housekeeping stat caching."""

import asyncio
import json
import logging
import os
import stat
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# 1. Browser singleton race condition — lock prevents concurrent starts
# ---------------------------------------------------------------------------

class TestBrowserLock:
    """Verify that ensure_browser() serialises concurrent callers."""

    @pytest.mark.asyncio
    async def test_concurrent_ensure_browser_only_starts_once(self):
        """Two concurrent ensure_browser() calls should only create one BrowserManager."""
        import core.browser as browser_mod

        # Reset module state
        browser_mod._manager = None
        browser_mod._manager_lock = None

        start_count = 0

        # Create a proper fake manager that has the right attributes
        class FakeManager:
            def __init__(self):
                self._context = MagicMock()  # needed for is_alive property
                self._last_used = 0
                self._idle_task = None

            @property
            def is_alive(self):
                return self._context is not None

            def touch(self):
                pass

            async def start(self):
                nonlocal start_count
                start_count += 1
                await asyncio.sleep(0.05)
                browser_mod._manager = self

        with patch.object(browser_mod, 'BrowserManager', FakeManager):
            # Launch two concurrent ensure_browser() calls
            results = await asyncio.gather(
                browser_mod.ensure_browser(),
                browser_mod.ensure_browser(),
            )

        # Only one start should have been called — the second waiter
        # sees the manager created by the first.
        assert start_count == 1, f"Expected 1 start, got {start_count}"

        # Clean up
        browser_mod._manager = None
        browser_mod._manager_lock = None

    @pytest.mark.asyncio
    async def test_ensure_browser_returns_existing_manager(self):
        """If _manager is alive, ensure_browser returns it without starting a new one."""
        import core.browser as browser_mod

        browser_mod._manager_lock = None
        fake_mgr = MagicMock()
        fake_mgr.is_alive = True
        fake_mgr.touch = MagicMock()
        browser_mod._manager = fake_mgr

        result = await browser_mod.ensure_browser()
        assert result is fake_mgr
        fake_mgr.touch.assert_called_once()

        # Clean up
        browser_mod._manager = None
        browser_mod._manager_lock = None

    @pytest.mark.asyncio
    async def test_stop_browser_acquires_lock(self):
        """stop_browser() should acquire the lock and call stop on the manager."""
        import core.browser as browser_mod

        browser_mod._manager_lock = None
        fake_mgr = MagicMock()
        fake_mgr.stop = AsyncMock()
        browser_mod._manager = fake_mgr

        await browser_mod.stop_browser()
        fake_mgr.stop.assert_awaited_once()

        # Clean up
        browser_mod._manager = None
        browser_mod._manager_lock = None

    @pytest.mark.asyncio
    async def test_stop_browser_noop_when_no_manager(self):
        """stop_browser() does nothing when _manager is None."""
        import core.browser as browser_mod

        browser_mod._manager = None
        browser_mod._manager_lock = None

        # Should not raise
        await browser_mod.stop_browser()

        browser_mod._manager_lock = None

    @pytest.mark.asyncio
    async def test_get_lock_creates_lock_lazily(self):
        """_get_lock() creates the lock on first call, reuses on second."""
        import core.browser as browser_mod

        browser_mod._manager_lock = None
        lock1 = browser_mod._get_lock()
        lock2 = browser_mod._get_lock()
        assert lock1 is lock2
        assert isinstance(lock1, asyncio.Lock)

        browser_mod._manager_lock = None


# ---------------------------------------------------------------------------
# 2. save_json_state error handling — cleanup + context in error message
# ---------------------------------------------------------------------------

class TestSaveJsonStateErrorHandling:
    """Verify save_json_state cleans up tmp files and provides context on failure."""

    def test_successful_save(self, tmp_dir):
        """Normal save works as before."""
        from core.state import save_json_state

        path = tmp_dir / "test.json"
        save_json_state(path, {"key": "value"})
        assert path.exists()
        assert json.loads(path.read_text(encoding="utf-8")) == {"key": "value"}
        # Tmp file should not exist
        assert not path.with_suffix(".json.tmp").exists()

    def test_replace_failure_cleans_tmp(self, tmp_dir):
        """When os.replace fails, the .tmp file is cleaned up."""
        from core.state import save_json_state

        path = tmp_dir / "test.json"
        tmp_path = path.with_suffix(".json.tmp")

        with patch("core.state.os.replace", side_effect=OSError("Permission denied")):
            with pytest.raises(OSError):
                save_json_state(path, {"key": "value"})

        # The tmp file should have been cleaned up
        assert not tmp_path.exists()

    def test_replace_failure_error_includes_path(self, tmp_dir):
        """Error message should include the target file path for debugging."""
        from core.state import save_json_state
        import re

        path = tmp_dir / "test.json"

        with patch("core.state.os.replace", side_effect=OSError("disk full")):
            with pytest.raises(OSError, match=re.escape(str(path))):
                save_json_state(path, {"key": "value"})

    def test_replace_failure_error_includes_original_error(self, tmp_dir):
        """Error message should include the original OS error."""
        from core.state import save_json_state

        path = tmp_dir / "test.json"

        with patch("core.state.os.replace", side_effect=OSError("disk full")):
            with pytest.raises(OSError, match="disk full"):
                save_json_state(path, {"key": "value"})

    def test_replace_failure_chains_original_exception(self, tmp_dir):
        """The raised OSError should chain the original via __cause__."""
        from core.state import save_json_state

        path = tmp_dir / "test.json"
        original = OSError("disk full")

        with patch("core.state.os.replace", side_effect=original):
            with pytest.raises(OSError) as exc_info:
                save_json_state(path, {"key": "value"})
            assert exc_info.value.__cause__ is original

    def test_tmp_cleanup_failure_does_not_mask_original_error(self, tmp_dir):
        """If tmp cleanup also fails, the original error should still propagate."""
        from core.state import save_json_state

        path = tmp_dir / "test.json"

        with patch("core.state.os.replace", side_effect=OSError("disk full")), \
             patch("pathlib.Path.unlink", side_effect=OSError("locked")):
            with pytest.raises(OSError, match="disk full"):
                save_json_state(path, {"key": "value"})


# ---------------------------------------------------------------------------
# 3. Scheduler logs cleanup failures instead of silently swallowing
# ---------------------------------------------------------------------------

class TestSchedulerCleanupLogging:
    """Verify scheduler logs warnings on cleanup failure instead of bare pass."""

    @pytest.mark.asyncio
    async def test_cleanup_failure_is_logged(self, caplog):
        """When cleanup_orphaned_jobs raises, a warning should be logged."""
        from core.scheduler import scheduler_loop

        shutdown = asyncio.Event()
        queue = asyncio.Queue()

        def mock_sync(config, q):
            pass

        def mock_cleanup():
            raise RuntimeError("cleanup exploded")

        with patch("daemon.sync.sync_jobs_from_onedrive", mock_sync), \
             patch("tui.ipc.cleanup_orphaned_jobs", mock_cleanup), \
             patch("core.scheduler._load_schedules", return_value=[]):

            async def run_short():
                task = asyncio.create_task(
                    scheduler_loop(config={}, job_queue=queue, shutdown_event=shutdown, check_interval=1)
                )
                # Wait enough for 5 ticks (the cleanup counter threshold)
                await asyncio.sleep(6)
                shutdown.set()
                await task

            with caplog.at_level(logging.WARNING, logger="pulse"):
                await run_short()

        # Verify the warning was logged
        assert any("cleanup failed" in r.message.lower() for r in caplog.records), \
            f"Expected cleanup warning in logs, got: {[r.message for r in caplog.records]}"

    @pytest.mark.asyncio
    async def test_scheduler_tick_failure_is_logged(self, caplog):
        """General scheduler tick errors should log a warning."""
        from core.scheduler import scheduler_loop

        shutdown = asyncio.Event()
        queue = asyncio.Queue()

        call_count = 0

        def boom_sync(config, q):
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                raise RuntimeError("sync exploded")

        with patch("daemon.sync.sync_jobs_from_onedrive", boom_sync), \
             patch("tui.ipc.cleanup_orphaned_jobs", return_value=0), \
             patch("core.scheduler._load_schedules", return_value=[]):

            task = asyncio.create_task(
                scheduler_loop(config={}, job_queue=queue, shutdown_event=shutdown, check_interval=1)
            )
            with caplog.at_level(logging.WARNING, logger="pulse"):
                await asyncio.sleep(2)
                shutdown.set()
                await task

        assert any("tick failed" in r.message.lower() for r in caplog.records), \
            f"Expected tick failure warning, got: {[r.message for r in caplog.records]}"


# ---------------------------------------------------------------------------
# 4. Housekeeping — stat() caching (single stat per file)
# ---------------------------------------------------------------------------

class TestHousekeepingStatCaching:
    """Verify _delete_old_files uses cached stat results."""

    def test_delete_old_files_uses_single_stat_per_file(self, tmp_dir):
        """Each file should be stat()'d only once during deletion check."""
        from core.housekeeping import _delete_old_files

        # Create test files — make them old
        old_file = tmp_dir / "monitoring-2026-01-01.json"
        old_file.write_text("{}", encoding="utf-8")
        old_mtime = time.time() - (5 * 86400)  # 5 days old
        os.utime(old_file, (old_mtime, old_mtime))

        new_file = tmp_dir / "monitoring-2026-03-25.json"
        new_file.write_text("{}", encoding="utf-8")

        deleted = _delete_old_files(tmp_dir, "monitoring-*.json", max_age_days=3)
        assert deleted == 1
        assert not old_file.exists()
        assert new_file.exists()

    def test_delete_old_files_skips_directories(self, tmp_dir):
        """Directories matching the glob should be skipped."""
        from core.housekeeping import _delete_old_files

        # Create a directory that matches the pattern
        subdir = tmp_dir / "monitoring-fake.json"
        subdir.mkdir()

        deleted = _delete_old_files(tmp_dir, "monitoring-*.json", max_age_days=0)
        assert deleted == 0
        assert subdir.exists()

    def test_delete_old_files_handles_stat_failure(self, tmp_dir):
        """Files that fail stat() should be skipped, not crash."""
        from core.housekeeping import _delete_old_files

        f = tmp_dir / "monitoring-test.json"
        f.write_text("{}", encoding="utf-8")

        original_stat = Path.stat

        def failing_stat(self_path, *args, **kwargs):
            if self_path.name == "monitoring-test.json":
                raise OSError("access denied")
            return original_stat(self_path, *args, **kwargs)

        with patch.object(Path, 'stat', failing_stat):
            deleted = _delete_old_files(tmp_dir, "monitoring-*.json", max_age_days=0)
        assert deleted == 0

    def test_age_days_from_stat_helper(self):
        """_age_days_from_stat calculates age correctly."""
        from core.housekeeping import _age_days_from_stat

        two_days_ago = time.time() - (2 * 86400)
        age = _age_days_from_stat(two_days_ago)
        assert 1.9 < age < 2.1
