"""Tests for the concurrent worker pool + priority queue architecture.

Validates:
- Priority ordering (monitor > digest > knowledge-project)
- enqueue_job / dequeue_job helpers
- Config-driven priority overrides
- Knowledge split: knowledge-init queues individual knowledge-project jobs
- Multiple workers can run concurrently
- Batch completion tracking for knowledge-project jobs
"""

import asyncio
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from pathlib import Path

from daemon.worker import (
    DEFAULT_JOB_PRIORITIES,
    enqueue_job,
    dequeue_job,
    _get_priorities,
    _knowledge_project_done,
)


# ---------------------------------------------------------------------------
# Priority queue helpers
# ---------------------------------------------------------------------------


class TestPriorityQueue:
    """enqueue_job / dequeue_job with correct priority ordering."""

    def test_monitor_before_digest(self):
        """Monitor (p1) should dequeue before digest (p3)."""
        q = asyncio.PriorityQueue()
        enqueue_job(q, {"type": "digest"})
        enqueue_job(q, {"type": "monitor"})

        pri1, _, job1 = q.get_nowait()
        pri2, _, job2 = q.get_nowait()
        assert job1["type"] == "monitor"
        assert job2["type"] == "digest"
        assert pri1 < pri2

    def test_digest_before_knowledge_project(self):
        """Digest (p3) should dequeue before knowledge-project (p8)."""
        q = asyncio.PriorityQueue()
        enqueue_job(q, {"type": "knowledge-project"})
        enqueue_job(q, {"type": "digest"})

        _, _, job1 = q.get_nowait()
        _, _, job2 = q.get_nowait()
        assert job1["type"] == "digest"
        assert job2["type"] == "knowledge-project"

    def test_fifo_within_same_priority(self):
        """Jobs with the same priority should dequeue FIFO."""
        q = asyncio.PriorityQueue()
        enqueue_job(q, {"type": "monitor", "id": "first"})
        enqueue_job(q, {"type": "monitor", "id": "second"})

        _, _, job1 = q.get_nowait()
        _, _, job2 = q.get_nowait()
        assert job1["id"] == "first"
        assert job2["id"] == "second"

    def test_unknown_type_gets_default_priority(self):
        """Unknown job types get _DEFAULT_PRIORITY (5)."""
        q = asyncio.PriorityQueue()
        enqueue_job(q, {"type": "mystery_job"})
        enqueue_job(q, {"type": "housekeeping"})  # priority 7

        _, _, job1 = q.get_nowait()
        _, _, job2 = q.get_nowait()
        assert job1["type"] == "mystery_job"  # p5 < p7
        assert job2["type"] == "housekeeping"

    @pytest.mark.asyncio
    async def test_dequeue_job_strips_wrapper(self):
        """dequeue_job returns the raw job dict, not the priority tuple."""
        q = asyncio.PriorityQueue()
        enqueue_job(q, {"type": "monitor", "data": "test"})
        job = await dequeue_job(q)
        assert job == {"type": "monitor", "data": "test"}

    def test_all_known_types_have_priorities(self):
        """Every type in DEFAULT_JOB_PRIORITIES should have a numeric value."""
        for job_type, priority in DEFAULT_JOB_PRIORITIES.items():
            assert isinstance(priority, int), f"{job_type} priority is not int: {priority}"
            assert 1 <= priority <= 10, f"{job_type} priority {priority} out of range"

    def test_sends_are_highest_priority(self):
        """teams_send and email_reply should have priority 1 (same as monitor)."""
        assert DEFAULT_JOB_PRIORITIES["teams_send"] == 1
        assert DEFAULT_JOB_PRIORITIES["email_reply"] == 1
        assert DEFAULT_JOB_PRIORITIES["monitor"] == 1

    def test_knowledge_project_is_lowest(self):
        """knowledge-project should have the lowest priority among standard types."""
        kp_priority = DEFAULT_JOB_PRIORITIES["knowledge-project"]
        for jt, pri in DEFAULT_JOB_PRIORITIES.items():
            if jt == "knowledge-project":
                continue
            assert pri <= kp_priority, f"{jt} (p{pri}) has higher number than knowledge-project (p{kp_priority})"


class TestConfigPriorityOverrides:
    """Config-driven priority overrides via job_priorities."""

    def setup_method(self):
        # Reset the cached resolved priorities between tests
        import daemon.worker
        daemon.worker._resolved_priorities = None

    def teardown_method(self):
        import daemon.worker
        daemon.worker._resolved_priorities = None

    def test_config_overrides_default(self):
        """Config job_priorities should override DEFAULT_JOB_PRIORITIES."""
        config = {"job_priorities": {"monitor": 5, "digest": 1}}
        priorities = _get_priorities(config)
        assert priorities["monitor"] == 5
        assert priorities["digest"] == 1

    def test_config_preserves_unset_defaults(self):
        """Types not in config should keep their defaults."""
        config = {"job_priorities": {"monitor": 5}}
        priorities = _get_priorities(config)
        assert priorities["monitor"] == 5
        assert priorities["digest"] == DEFAULT_JOB_PRIORITIES["digest"]

    def test_no_config_uses_defaults(self):
        """Without config, all defaults should apply."""
        priorities = _get_priorities(None)
        assert priorities == DEFAULT_JOB_PRIORITIES

    def test_enqueue_respects_config_override(self):
        """enqueue_job should use config-overridden priorities."""
        config = {"job_priorities": {"digest": 1, "monitor": 9}}
        q = asyncio.PriorityQueue()
        enqueue_job(q, {"type": "monitor"}, config)
        enqueue_job(q, {"type": "digest"}, config)

        _, _, job1 = q.get_nowait()
        _, _, job2 = q.get_nowait()
        # With overrides, digest (p1) should come before monitor (p9)
        assert job1["type"] == "digest"
        assert job2["type"] == "monitor"


# ---------------------------------------------------------------------------
# Knowledge split: prepare_knowledge_projects
# ---------------------------------------------------------------------------


class TestKnowledgeSplit:
    """prepare_knowledge_projects returns job dicts for Phase 2."""

    def test_returns_job_per_active_project(self, tmp_path):
        """One knowledge-project job per active project."""
        from sdk.runner import prepare_knowledge_projects

        projects_dir = tmp_path / "projects"
        projects_dir.mkdir()
        (projects_dir / "project-a.yaml").write_text(
            "project: Project A\nstatus: active\n", encoding="utf-8"
        )
        (projects_dir / "project-b.yaml").write_text(
            "project: Project B\nstatus: blocked\n", encoding="utf-8"
        )
        (projects_dir / "project-c.yaml").write_text(
            "project: Project C\nstatus: completed\n", encoding="utf-8"
        )

        with patch("sdk.runner.PROJECTS_DIR", projects_dir), \
             patch("sdk.agents.is_msx_available", return_value=False), \
             patch("sdk.runner.KNOWLEDGE_STATE_FILE", tmp_path / ".knowledge-state.json"), \
             patch("sdk.runner._list_recent_artifacts", return_value=""):
            jobs = prepare_knowledge_projects({})

        assert len(jobs) == 2  # project-a (active) + project-b (blocked)
        assert all(j["type"] == "knowledge-project" for j in jobs)
        names = {j["_context"]["project_name"] for j in jobs}
        assert "Project A" in names
        assert "Project B" in names
        assert "Project C" not in names

    def test_returns_empty_when_no_projects(self, tmp_path):
        """Returns empty list when no project files exist."""
        from sdk.runner import prepare_knowledge_projects

        projects_dir = tmp_path / "projects"
        projects_dir.mkdir()

        with patch("sdk.runner.PROJECTS_DIR", projects_dir):
            jobs = prepare_knowledge_projects({})

        assert jobs == []

    def test_context_includes_project_yaml(self, tmp_path):
        """Each job's _context includes the serialized project YAML."""
        from sdk.runner import prepare_knowledge_projects

        projects_dir = tmp_path / "projects"
        projects_dir.mkdir()
        (projects_dir / "test-proj.yaml").write_text(
            "project: Test Project\nstatus: active\nsummary: A test\n", encoding="utf-8"
        )

        with patch("sdk.runner.PROJECTS_DIR", projects_dir), \
             patch("sdk.agents.is_msx_available", return_value=False), \
             patch("sdk.runner.KNOWLEDGE_STATE_FILE", tmp_path / ".knowledge-state.json"), \
             patch("sdk.runner._list_recent_artifacts", return_value=""):
            jobs = prepare_knowledge_projects({})

        assert len(jobs) == 1
        ctx = jobs[0]["_context"]
        assert ctx["project_id"] == "test-proj"
        assert ctx["project_name"] == "Test Project"
        assert "summary: A test" in ctx["project_yaml"]

    def test_batch_size_in_job(self, tmp_path):
        """Each job carries _knowledge_batch_size for tracking."""
        from sdk.runner import prepare_knowledge_projects

        projects_dir = tmp_path / "projects"
        projects_dir.mkdir()
        for i in range(3):
            (projects_dir / f"proj-{i}.yaml").write_text(
                f"project: Project {i}\nstatus: active\n", encoding="utf-8"
            )

        with patch("sdk.runner.PROJECTS_DIR", projects_dir), \
             patch("sdk.agents.is_msx_available", return_value=False), \
             patch("sdk.runner.KNOWLEDGE_STATE_FILE", tmp_path / ".knowledge-state.json"), \
             patch("sdk.runner._list_recent_artifacts", return_value=""):
            jobs = prepare_knowledge_projects({})

        assert all(j["_knowledge_batch_size"] == 3 for j in jobs)


# ---------------------------------------------------------------------------
# Batch completion tracking
# ---------------------------------------------------------------------------


class TestKnowledgeBatchCompletion:
    """_knowledge_project_done sends notification when last project finishes."""

    def test_notification_on_last_project(self):
        """When remaining hits 0, notify desktop + TUI."""
        import daemon.worker as w
        w._knowledge_batch_total = 2
        w._knowledge_batch_remaining = 1  # one left

        with patch("daemon.worker.notify_desktop") as mock_notify, \
             patch("daemon.worker.write_job_notification") as mock_write:
            _knowledge_project_done({})

        mock_notify.assert_called_once()
        mock_write.assert_called_once()
        assert w._knowledge_batch_remaining == 0

    def test_no_notification_while_remaining(self):
        """No notification when there are still projects left."""
        import daemon.worker as w
        w._knowledge_batch_total = 5
        w._knowledge_batch_remaining = 3

        with patch("daemon.worker.notify_desktop") as mock_notify, \
             patch("daemon.worker.write_job_notification") as mock_write:
            _knowledge_project_done({})

        mock_notify.assert_not_called()
        mock_write.assert_not_called()
        assert w._knowledge_batch_remaining == 2


# ---------------------------------------------------------------------------
# Concurrent workers
# ---------------------------------------------------------------------------


class TestConcurrentWorkers:
    """Multiple worker coroutines can run simultaneously."""

    @pytest.mark.asyncio
    async def test_two_workers_process_concurrently(self):
        """Two workers should be able to process jobs at the same time."""
        from daemon.worker import job_worker, enqueue_job

        processing = []
        events = {}

        async def mock_run_job(client, config, mode, **kwargs):
            event = asyncio.Event()
            events[mode] = event
            processing.append(mode)
            await event.wait()  # Block until test releases

        q = asyncio.PriorityQueue()
        enqueue_job(q, {"type": "digest"})
        enqueue_job(q, {"type": "monitor"})

        with patch("sdk.runner.run_job", mock_run_job), \
             patch("daemon.worker.build_toast_summary", return_value=("", "")), \
             patch("daemon.worker.notify_desktop"), \
             patch("daemon.worker.write_job_notification"), \
             patch("daemon.worker.append_job_event"), \
             patch("daemon.worker.LOGS_DIR", Path("/tmp/test-logs")), \
             patch("daemon.tasks.active_workers", {}):

            w0 = asyncio.create_task(job_worker(MagicMock(), {}, q, worker_id=0))
            w1 = asyncio.create_task(job_worker(MagicMock(), {}, q, worker_id=1))

            # Wait for both workers to pick up jobs
            for _ in range(50):
                if len(processing) >= 2:
                    break
                await asyncio.sleep(0.05)

            # Both should be processing simultaneously
            assert len(processing) == 2, f"Expected 2 concurrent, got {processing}"

            # Release both
            for ev in events.values():
                ev.set()
            await asyncio.sleep(0.1)

            w0.cancel()
            w1.cancel()
            for t in (w0, w1):
                try:
                    await t
                except asyncio.CancelledError:
                    pass

    @pytest.mark.asyncio
    async def test_priority_ordering_with_workers(self):
        """Higher-priority jobs should be picked up first."""
        from daemon.worker import enqueue_job

        q = asyncio.PriorityQueue()
        # Queue in reverse priority order
        enqueue_job(q, {"type": "knowledge-project", "id": "low"})
        enqueue_job(q, {"type": "digest", "id": "medium"})
        enqueue_job(q, {"type": "monitor", "id": "high"})

        # Dequeue and verify order
        _, _, j1 = q.get_nowait()
        _, _, j2 = q.get_nowait()
        _, _, j3 = q.get_nowait()

        assert j1["id"] == "high"
        assert j2["id"] == "medium"
        assert j3["id"] == "low"
