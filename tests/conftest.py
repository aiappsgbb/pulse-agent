"""Shared fixtures for Pulse Agent tests."""

import json
import sys
import tempfile
from pathlib import Path

import pytest

# Add src/ to path once for all tests
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


@pytest.fixture
def tmp_dir():
    """Provide a temporary directory, cleaned up automatically."""
    with tempfile.TemporaryDirectory() as td:
        yield Path(td)


@pytest.fixture
def sample_config():
    """Minimal standing-instructions config for testing."""
    return {
        "models": {
            "default": "gpt-4.1",
            "triage": "gpt-4.1",
            "digest": "claude-sonnet",
            "intel": "claude-sonnet",
            "research": "claude-opus",
            "chat": "gpt-4.1",
        },
        "monitoring": {
            "priorities": ["Customer escalations", "Deal blockers"],
            "vip_contacts": ["Alice", "Bob"],
            "interval": "30m",
            "office_hours": {"start": "08:00", "end": "18:00", "days": [1, 2, 3, 4, 5]},
        },
        "digest": {
            "priorities": ["Revenue deals", "Escalations"],
            "input_paths": [],
        },
        "intelligence": {
            "topics": ["AI", "Cloud"],
            "competitors": [{"company": "Acme", "watch": ["pricing", "features"]}],
            "feeds": [],
        },
        "telegram": {"allowed_users": [12345]},
    }
