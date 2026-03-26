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
        "user": {"name": "Test User"},
        "onedrive": {"path": "/tmp/test-onedrive"},
        "team": [
            {"name": "Alice Test", "alias": "alice"},
            {"name": "Bob Test", "alias": "bob"},
        ],
    }


@pytest.fixture
def sample_digest_json():
    """Realistic digest JSON matching the schema LLM outputs."""
    return {
        "items": [
            {
                "id": "test-item-1",
                "title": "Review Q1 proposal",
                "source": "Teams chat",
                "priority": "high",
                "status": "outstanding",
                "reply_needed": True,
                "suggested_actions": [{
                    "action_type": "teams_reply",
                    "target": "John Smith",
                    "chat_name": "John Smith",
                    "draft": "Thanks, I'll review by EOD."
                }]
            },
            {
                "id": "test-item-2",
                "title": "FYI: Updated pricing doc",
                "source": "Email",
                "priority": "low",
                "status": "new",
                "reply_needed": False,
                "suggested_actions": []
            }
        ],
        "stats": {"outstanding": 1, "new": 1, "resolved": 0}
    }


@pytest.fixture
def sample_monitoring_json():
    """Realistic monitoring/triage JSON."""
    return {
        "items": [
            {
                "id": "mon-1",
                "title": "Urgent: Customer escalation",
                "source": "Teams",
                "urgency": "high",
                "reply_needed": True,
                "suggested_actions": [{
                    "action_type": "draft_teams_reply",
                    "target": "Esther Barthel",
                    "chat_name": "Esther Barthel",
                    "draft": "On it — checking now."
                }]
            }
        ]
    }


@pytest.fixture
def sample_job_yaml():
    """Realistic job YAML content."""
    return {
        "type": "digest",
        "description": "Morning digest",
        "created_at": "2026-03-25T07:00:00",
        "priority": "normal"
    }


@pytest.fixture
def pulse_home_structure(tmp_path):
    """Create a realistic PULSE_HOME directory structure."""
    dirs = ["transcripts", "documents", "emails", "digests", "intel",
            "projects", "pulse-signals", "jobs/pending", "jobs/completed", "logs"]
    for d in dirs:
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    return tmp_path
