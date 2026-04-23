"""Tests for scripts/seed_demo_data.py -- idempotent demo corpus seeding."""
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SEED_SCRIPT = REPO_ROOT / "src" / "seed_demo_data.py"


def _run_seeder(target: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SEED_SCRIPT), "--target-pulse-home", str(target)],
        capture_output=True, text=True, check=False,
    )


def test_seed_populates_expected_files(tmp_path):
    result = _run_seeder(tmp_path)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "transcripts").is_dir()
    assert len(list((tmp_path / "transcripts").glob("*.md"))) >= 2
    assert len(list((tmp_path / "emails").glob("*.eml"))) >= 1
    assert len(list((tmp_path / "projects").glob("*.yaml"))) >= 1


def test_seed_is_idempotent(tmp_path):
    """Running twice should not duplicate content or fail."""
    r1 = _run_seeder(tmp_path)
    assert r1.returncode == 0
    transcripts_before = sorted((tmp_path / "transcripts").glob("*.md"))
    r2 = _run_seeder(tmp_path)
    assert r2.returncode == 0
    transcripts_after = sorted((tmp_path / "transcripts").glob("*.md"))
    assert transcripts_before == transcripts_after
