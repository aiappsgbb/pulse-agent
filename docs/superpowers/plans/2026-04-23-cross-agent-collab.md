# Cross-Agent Collaboration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the cross-agent collaboration MVP so two Pulse Agent daemons running side-by-side can demonstrate a broadcast-question / guardian-mode-answer / async-ingest loop end-to-end, with context accumulating into project YAMLs.

**Architecture:** LLM-driven receive (Guardian Mode) + deterministic broadcast and ingestion. Sender fires a `broadcast_to_team` tool that fans out YAML drops to every configured teammate. Each receiver runs a Guardian SDK session that searches locally, drafts an answer, and judges what is safe to share. The worker parses the session's structured output and writes a response YAML back. When responses arrive on the sender's side, the worker deterministically appends them to the right project YAML's `team_context[]`. Polling interval drops from 60s to 30s. Chat and digest prompts are updated to trigger broadcasts.

**Tech Stack:** Python 3.12, GitHub Copilot SDK (`copilot.define_tool`), Pydantic v2 (`BaseModel`), PyYAML, pytest + pytest-asyncio, existing `asyncio.PriorityQueue` worker.

---

## Spec reference

See [../specs/2026-04-23-cross-agent-collab-shipping-design.md](../specs/2026-04-23-cross-agent-collab-shipping-design.md) for the full design. Key decisions encoded in this plan:

- Broadcast fan-out, receivers self-filter via Guardian prompt (not determinism).
- Fire-and-forget: sender never blocks waiting for responses.
- Guardian LLM outputs structured JSON, worker writes YAML deterministically.
- Project YAML gets three new fields: `team_context`, `questions`, `last_team_enrichment`.
- Ask once per project; re-broadcast only if a new `questions[]` entry is added after `last_team_enrichment`.
- Poll interval 30s (change default, not a new schedule).

## File structure

**New files:**
- `config/prompts/system/guardian.md` — Guardian Mode receiver prompt
- `scripts/seed_demo_data.py` — populate mock transcripts/docs/projects into a target `$PULSE_HOME` for demo
- `tests/test_broadcast.py` — broadcast_to_team tool
- `tests/test_guardian.py` — Guardian prompt + response parser contract
- `tests/test_team_ingest.py` — agent_response → project YAML ingestion
- `tests/test_cross_agent_e2e.py` — end-to-end integration with two temp PULSE_HOMEs

**Modified files:**
- `src/sdk/tools.py` — add `BroadcastToTeamParams`, `broadcast_to_team` tool; register in `get_tools()`
- `src/daemon/worker.py` — replace `_handle_agent_request` body with Guardian session flow; add `_parse_guardian_output`, `_write_guardian_response`, `_ingest_agent_response`; rewrite `agent_response` branch to route through ingestion
- `src/core/scheduler.py` — change `scheduler_loop` default `check_interval` from 60 to 30
- `config/modes.yaml` — add `guardian` mode entry (replace-mode system prompt, no agents)
- `config/prompts/agents/digest-writer.md` — add Team Enrichment directive
- `config/prompts/system/chat.md` — add broadcast routing instruction

**Untouched (intentionally):** `src/daemon/sync.py`, `src/sdk/session.py`, `src/sdk/runner.py`, project YAML loading/saving utilities, MCP config. The change surface is contained.

---

## Test-running shortcut

All tasks use this pytest command pattern:

```bash
python -m pytest tests/<testfile>.py::<testname> -v --tb=short
```

Running the whole new test suite in one pass:

```bash
python -m pytest tests/test_broadcast.py tests/test_guardian.py tests/test_team_ingest.py tests/test_cross_agent_e2e.py -v
```

Full suite sanity check before task commits:

```bash
python -m pytest tests/ -x --tb=short
```

---

## Task 1: Add `broadcast_to_team` tool

**Files:**
- Modify: `src/sdk/tools.py` (add schema around line 86, add tool definition after `send_task_to_agent` around line 365, register in `get_tools()` at line 700)
- Create: `tests/test_broadcast.py`

### Step 1: Write the failing test

Create `tests/test_broadcast.py`:

```python
"""Tests for broadcast_to_team tool — fan-out to all configured teammates."""
from unittest.mock import patch

import pytest
import yaml

from sdk.tools import broadcast_to_team


@pytest.fixture
def tmp_team(tmp_path):
    """Two teammates with PULSE_TEAM_DIR/{alias}/ folders ready."""
    team_dir = tmp_path / "Pulse-Team"
    (team_dir / "alpha").mkdir(parents=True)
    (team_dir / "beta").mkdir(parents=True)
    config = {
        "team": [
            {"name": "Alpha User", "alias": "alpha"},
            {"name": "Beta User", "alias": "beta"},
        ],
        "user": {"name": "Artur Zielinski", "alias": "artur"},
    }
    return team_dir, config


@pytest.mark.asyncio
async def test_broadcast_fans_out_to_all_teammates(tmp_team):
    team_dir, config = tmp_team
    with patch("core.config.load_config", return_value=config), \
         patch("sdk.tools.PULSE_TEAM_DIR", team_dir):
        result = await broadcast_to_team.handler({"arguments": {
            "question": "What do we know about Fabric-on-SAP objections?",
            "project_id": "fabric-sap-engagement",
        }})

    assert result["resultType"] == "success"
    assert "2 teammates" in result["textResultForLlm"]

    for alias in ("alpha", "beta"):
        jobs_dir = team_dir / alias / "jobs" / "pending"
        files = list(jobs_dir.glob("*.yaml"))
        assert len(files) == 1, f"expected 1 yaml for {alias}, got {len(files)}"
        data = yaml.safe_load(files[0].read_text())
        assert data["type"] == "agent_request"
        assert data["kind"] == "broadcast"
        assert data["project_id"] == "fabric-sap-engagement"
        assert data["from_alias"] == "artur"
        assert "Fabric-on-SAP" in data["task"]
        assert data["request_id"]  # UUID set


@pytest.mark.asyncio
async def test_broadcast_rejects_missing_project_id(tmp_team):
    _, config = tmp_team
    with patch("core.config.load_config", return_value=config):
        result = await broadcast_to_team.handler({"arguments": {
            "question": "anything",
            "project_id": "",
        }})
    assert "ERROR" in result["textResultForLlm"]
    assert "project_id" in result["textResultForLlm"]


@pytest.mark.asyncio
async def test_broadcast_empty_team_returns_clear_error(tmp_path):
    config = {"team": [], "user": {"name": "X", "alias": "x"}}
    with patch("core.config.load_config", return_value=config):
        result = await broadcast_to_team.handler({"arguments": {
            "question": "hello",
            "project_id": "any-project",
        }})
    assert "ERROR" in result["textResultForLlm"]
    assert "no teammates" in result["textResultForLlm"].lower()


@pytest.mark.asyncio
async def test_broadcast_skips_inaccessible_teammate_folders(tmp_path):
    """If one teammate's folder does not exist, skip and continue."""
    team_dir = tmp_path / "Pulse-Team"
    (team_dir / "alpha").mkdir(parents=True)  # alpha exists
    # beta does NOT exist — should be skipped, not crash
    config = {
        "team": [
            {"name": "Alpha", "alias": "alpha"},
            {"name": "Beta", "alias": "beta"},
        ],
        "user": {"name": "Artur", "alias": "artur"},
    }
    with patch("core.config.load_config", return_value=config), \
         patch("sdk.tools.PULSE_TEAM_DIR", team_dir):
        result = await broadcast_to_team.handler({"arguments": {
            "question": "hi",
            "project_id": "some-project",
        }})

    # Succeeded for alpha; beta was skipped
    assert result["resultType"] == "success"
    assert "1 teammate" in result["textResultForLlm"]
    assert "skipped" in result["textResultForLlm"].lower()
    assert "beta" in result["textResultForLlm"]
    # Verify only alpha got a YAML
    alpha_files = list((team_dir / "alpha" / "jobs" / "pending").glob("*.yaml"))
    assert len(alpha_files) == 1
```

### Step 2: Run test to verify it fails

```bash
python -m pytest tests/test_broadcast.py -v --tb=short
```

Expected: all four tests FAIL with `ImportError: cannot import name 'broadcast_to_team' from 'sdk.tools'`.

### Step 3: Add the parameter schema

In `src/sdk/tools.py`, after `class SendTaskToAgentParams(BaseModel)` (around line 86), add:

```python
class BroadcastToTeamParams(BaseModel):
    question: str  # the question to broadcast
    project_id: str  # slug of the project this question is about (required for routing responses back)
```

### Step 4: Add the tool implementation

In `src/sdk/tools.py`, immediately after the `send_task_to_agent` tool ends (after line 364), add:

```python
@define_tool(
    name="broadcast_to_team",
    description=(
        "Broadcast a question to ALL configured teammates at once. Drops an "
        "agent_request YAML into each teammate's shared OneDrive jobs/pending/ "
        "folder. Each teammate's agent will decide (via its Guardian prompt) "
        "whether it has relevant local context and respond asynchronously. "
        "Responses accrete into the named project's team_context field. "
        "Use this (not send_task_to_agent) when the question is about a specific "
        "project and you want to reach anyone on the team who might know."
    ),
)
def broadcast_to_team(params: BroadcastToTeamParams, invocation: ToolInvocation) -> str:
    from core.config import load_config

    if not params.project_id.strip():
        return "ERROR: project_id is required for broadcast_to_team (responses must route back to a project)."

    config = load_config()
    team = config.get("team", [])
    if not team:
        return "ERROR: no teammates configured — add entries under `team:` in standing-instructions.yaml."

    user_cfg = config.get("user", {})
    from_name = user_cfg.get("name", "Unknown")
    from_alias = user_cfg.get("alias", from_name.lower().split()[0] if from_name else "unknown")

    # Sender's own inbox for responses
    my_team_dir = PULSE_TEAM_DIR / from_alias / "jobs" / "pending"
    my_team_dir.mkdir(parents=True, exist_ok=True)
    reply_to = str(my_team_dir)

    request_id = str(uuid.uuid4())
    timestamp = datetime.now().isoformat()
    slug = re.sub(r"[^a-z0-9-]", "", params.question.lower().replace(" ", "-"))[:40]
    date_str = datetime.now().strftime("%Y-%m-%d")

    sent: list[str] = []
    skipped: list[str] = []

    for member in team:
        alias = (member.get("alias") or "").lower()
        if not alias:
            skipped.append(member.get("name", "?") + " (no alias)")
            continue

        # Convention-based path; fall back to explicit agent_path for backward compat
        explicit = member.get("agent_path")
        if explicit:
            agent_path = Path(explicit)
            jobs_dir = agent_path / "Jobs"
        else:
            agent_path = PULSE_TEAM_DIR / alias
            jobs_dir = agent_path / "jobs" / "pending"

        if not agent_path.exists():
            skipped.append(alias)
            continue

        jobs_dir.mkdir(parents=True, exist_ok=True)
        task_data = {
            "type": "agent_request",
            "kind": "broadcast",
            "task": params.question,
            "project_id": params.project_id,
            "from": from_name,
            "from_alias": from_alias,
            "reply_to": reply_to,
            "request_id": request_id,
            "priority": "normal",
            "created_at": timestamp,
        }
        task_file = jobs_dir / f"{date_str}-from-{from_alias}-broadcast-{slug}.yaml"
        with open(task_file, "w") as f:
            yaml.dump(task_data, f, default_flow_style=False)
        sent.append(alias)

    msg = f"Broadcasted to {len(sent)} teammate{'s' if len(sent) != 1 else ''}: {', '.join(sent) or '(none)'}"
    if skipped:
        msg += f" | Skipped (folder not accessible): {', '.join(skipped)}"
    msg += f" | Request ID: {request_id} | Responses will accrete into project '{params.project_id}'."
    return msg
```

### Step 5: Register the tool

In `src/sdk/tools.py`, edit `get_tools()` at line 700-707 to include `broadcast_to_team`:

```python
def get_tools() -> list[Tool]:
    """Return custom tools for registration on a session."""
    return [
        write_output, queue_task, dismiss_item, add_note,
        schedule_task, list_schedules_tool, update_schedule_tool, cancel_schedule,
        search_local_files, update_project,
        send_teams_message, send_email_reply,
        send_task_to_agent, broadcast_to_team, save_config_tool,
        sweep_inbox,
    ]
```

### Step 6: Run tests to verify they pass

```bash
python -m pytest tests/test_broadcast.py -v --tb=short
```

Expected: 4 passed.

### Step 7: Commit

```bash
git add src/sdk/tools.py tests/test_broadcast.py
git commit -m "feat: add broadcast_to_team tool for fan-out to configured teammates"
```

---

## Task 2: Guardian system prompt

**Files:**
- Create: `config/prompts/system/guardian.md`
- Create: `tests/test_guardian.py` (prompt-existence portion only; parser comes in Task 3)

### Step 1: Write the failing test

Create `tests/test_guardian.py`:

```python
"""Tests for Guardian Mode — system prompt + structured response parser."""
from pathlib import Path

from core.constants import PROJECT_ROOT


GUARDIAN_PROMPT_PATH = PROJECT_ROOT / "config" / "prompts" / "system" / "guardian.md"


def test_guardian_prompt_file_exists():
    assert GUARDIAN_PROMPT_PATH.exists(), f"Missing Guardian prompt at {GUARDIAN_PROMPT_PATH}"


def test_guardian_prompt_contains_required_directives():
    """The Guardian prompt must clearly instruct: search, judge, output JSON.

    Failing these means the LLM has no scaffolding for safe-sharing behavior.
    """
    text = GUARDIAN_PROMPT_PATH.read_text(encoding="utf-8")

    # Must tell the LLM it is acting as the user's guardian
    assert "guardian" in text.lower()
    # Must require a JSON payload as the structured output
    assert '"status"' in text
    assert '"answered"' in text and '"no_context"' in text and '"declined"' in text
    # Must require source citations on answered responses
    assert '"sources"' in text
    # Must mention PII / personal / sensitive as judgment criteria
    assert any(word in text.lower() for word in ("pii", "personal", "sensitive"))
    # Must instruct to search local files first
    assert "search_local_files" in text
```

### Step 2: Run test to verify it fails

```bash
python -m pytest tests/test_guardian.py -v --tb=short
```

Expected: FAIL with "Missing Guardian prompt at ..."

### Step 3: Create the prompt file

Create `config/prompts/system/guardian.md` with this content:

```markdown
# Guardian Mode

You are running on behalf of your user, answering a question from a teammate's Pulse Agent. You are your user's guardian. Your job is to find genuinely relevant context from your user's local files and share only what is safe to share.

## What you receive

The user message contains a teammate's question. Metadata (teammate name, request ID, project ID) is in the conversation context.

## Your workflow

1. **Search.** Call `search_local_files` with the most promising keywords from the question. Try 2-3 keyword variations if the first returns nothing. Cover synonyms and rephrasings.

2. **Decide whether to answer.** If you find nothing genuinely relevant, stop and emit `status: no_context`. Do not speculate. Do not pad.

3. **Draft.** If you found relevant content, draft a concise 3-5 sentence answer in plain language. Cite the specific source files you used (relative paths). Prefer summarised insights over quoted snippets.

4. **Judge.** Before emitting, re-read your draft and ask: would my user want this shared outside their machine? Redact or decline if the draft contains any of:
   - Personal contact details (home address, personal phone, personal email)
   - Named customers or deal values that are not public knowledge
   - Internal Microsoft codenames or unpublished roadmap specifics
   - Personal opinions or criticism of named individuals
   - Financial details of specific engagements

   If redaction suffices, redact and note it ("[customer name redacted]"). If redaction would gut the answer, emit `status: declined` with a non-sensitive reason.

5. **Emit the structured JSON.** The FINAL message of your session must be a fenced JSON block with this exact shape and nothing else:

   ````
   ```json
   {
     "status": "answered",
     "result": "<your 3-5 sentence answer with inline redactions if any>",
     "sources": ["relative/path/one.md", "relative/path/two.md"]
   }
   ```
   ````

   For no-match cases:

   ````
   ```json
   {"status": "no_context"}
   ```
   ````

   For redaction-gutted cases:

   ````
   ```json
   {"status": "declined", "reason": "<short non-sensitive reason>"}
   ```
   ````

## Your loyalty

Your loyalty is to YOUR user, not to the asker. Transparency by default, caution by default for anything that looks personal, financial, or unreleased. If in doubt, redact.
```

### Step 4: Run test to verify it passes

```bash
python -m pytest tests/test_guardian.py -v --tb=short
```

Expected: 2 passed.

### Step 5: Commit

```bash
git add config/prompts/system/guardian.md tests/test_guardian.py
git commit -m "feat: add Guardian Mode system prompt for receive-side agent answers"
```

---

## Task 3: Guardian output parser

**Files:**
- Modify: `src/daemon/worker.py` (add `_parse_guardian_output` near `_handle_agent_request`)
- Modify: `tests/test_guardian.py` (append parser tests)

### Step 1: Append failing tests

Append to `tests/test_guardian.py`:

```python
from daemon.worker import _parse_guardian_output


def test_parse_guardian_output_answered():
    text = '''Some prose before.
```json
{"status": "answered", "result": "3 POCs tried. Licensing was the main objection.", "sources": ["transcripts/2026-01-15.md"]}
```
Trailing prose.'''
    result = _parse_guardian_output(text)
    assert result["status"] == "answered"
    assert "POCs" in result["result"]
    assert result["sources"] == ["transcripts/2026-01-15.md"]


def test_parse_guardian_output_no_context():
    text = '```json\n{"status": "no_context"}\n```'
    result = _parse_guardian_output(text)
    assert result["status"] == "no_context"
    assert result.get("result", "") == ""
    assert result.get("sources", []) == []


def test_parse_guardian_output_declined():
    text = '```json\n{"status": "declined", "reason": "too sensitive"}\n```'
    result = _parse_guardian_output(text)
    assert result["status"] == "declined"
    assert result["reason"] == "too sensitive"


def test_parse_guardian_output_no_json_block():
    """No fenced JSON → fall back to no_context (defensive default)."""
    text = "The LLM forgot to produce JSON, just wrote prose."
    result = _parse_guardian_output(text)
    assert result["status"] == "no_context"


def test_parse_guardian_output_malformed_json():
    """Malformed JSON → fall back to no_context, do not crash."""
    text = '```json\n{"status": "answered", "result":\n```'
    result = _parse_guardian_output(text)
    assert result["status"] == "no_context"


def test_parse_guardian_output_bare_json_no_fence():
    """Accept raw JSON without fence as a fallback."""
    text = '{"status": "answered", "result": "answer", "sources": ["a.md"]}'
    result = _parse_guardian_output(text)
    assert result["status"] == "answered"
    assert result["result"] == "answer"
```

### Step 2: Run tests to verify they fail

```bash
python -m pytest tests/test_guardian.py -v --tb=short
```

Expected: new tests FAIL with `ImportError: cannot import name '_parse_guardian_output'`.

### Step 3: Implement the parser

In `src/daemon/worker.py`, after the existing `_handle_agent_request` function (around line 781), add:

```python
def _parse_guardian_output(text: str) -> dict:
    """Extract the structured JSON payload the Guardian LLM emits.

    Accepts fenced ```json blocks (preferred) or raw JSON. Falls back to
    {"status": "no_context"} on any parse failure — defensive default so a
    misbehaving session does not crash the worker.
    """
    import re as _re

    if not text:
        return {"status": "no_context"}

    # Prefer the last fenced json block
    fenced = _re.findall(r"```json\s*(\{.*?\})\s*```", text, flags=_re.DOTALL)
    if fenced:
        candidate = fenced[-1]
    else:
        # Fallback: largest-looking {...} span
        m = _re.search(r"\{.*\}", text, flags=_re.DOTALL)
        candidate = m.group(0) if m else ""

    if not candidate:
        return {"status": "no_context"}

    try:
        data = json.loads(candidate)
    except (json.JSONDecodeError, ValueError):
        return {"status": "no_context"}

    if not isinstance(data, dict) or "status" not in data:
        return {"status": "no_context"}

    status = data.get("status")
    if status not in ("answered", "no_context", "declined"):
        return {"status": "no_context"}

    return data
```

### Step 4: Run tests to verify they pass

```bash
python -m pytest tests/test_guardian.py -v --tb=short
```

Expected: all 8 tests pass (2 file + 6 parser).

### Step 5: Commit

```bash
git add src/daemon/worker.py tests/test_guardian.py
git commit -m "feat: add Guardian LLM structured-output parser"
```

---

## Task 4: Replace `_handle_agent_request` with Guardian session

**Files:**
- Modify: `src/daemon/worker.py` (rewrite `_handle_agent_request` body around line 758-781, rewrite `_write_agent_response` around line 784-824, update the `agent_request` branch at line 400-404 to not call `_write_agent_response` separately)

### Step 1: Write the failing test

Append to `tests/test_guardian.py`:

```python
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_handle_agent_request_writes_response_yaml(tmp_path, monkeypatch):
    """Guardian session flow: fake LLM returns structured JSON, worker writes YAML."""
    from daemon.worker import _handle_agent_request

    reply_to = tmp_path / "reply"
    reply_to.mkdir()

    job = {
        "type": "agent_request",
        "kind": "broadcast",
        "task": "Any context on Fabric-on-SAP?",
        "project_id": "fabric-sap-engagement",
        "from": "Artur Zielinski",
        "from_alias": "artur",
        "reply_to": str(reply_to),
        "request_id": "test-req-123",
        "created_at": "2026-04-23T10:00:00",
    }
    config = {"user": {"name": "Beta User", "alias": "beta"}}

    # Mock the Guardian session to return a happy-path answer
    fake_output = '''```json
{"status": "answered", "result": "Found 2 POCs in my notes.", "sources": ["transcripts/a.md"]}
```'''
    fake_run = AsyncMock(return_value=fake_output)
    monkeypatch.setattr("daemon.worker._run_guardian_session", fake_run)

    client = MagicMock()
    await _handle_agent_request(client, config, job)

    yaml_files = list(reply_to.glob("*.yaml"))
    assert len(yaml_files) == 1
    data = yaml.safe_load(yaml_files[0].read_text())
    assert data["type"] == "agent_response"
    assert data["status"] == "answered"
    assert data["project_id"] == "fabric-sap-engagement"
    assert data["request_id"] == "test-req-123"
    assert data["from"] == "Beta User"
    assert data["result"] == "Found 2 POCs in my notes."
    assert data["sources"] == ["transcripts/a.md"]


@pytest.mark.asyncio
async def test_handle_agent_request_no_context_writes_minimal_response(tmp_path, monkeypatch):
    """no_context responses still write a YAML so the sender can log+dedup."""
    from daemon.worker import _handle_agent_request

    reply_to = tmp_path / "reply"
    reply_to.mkdir()
    job = {
        "type": "agent_request",
        "task": "something obscure",
        "project_id": "some-project",
        "from": "Artur",
        "from_alias": "artur",
        "reply_to": str(reply_to),
        "request_id": "test-req-456",
        "created_at": "2026-04-23T10:00:00",
    }
    config = {"user": {"name": "Beta", "alias": "beta"}}

    fake_run = AsyncMock(return_value='```json\n{"status": "no_context"}\n```')
    monkeypatch.setattr("daemon.worker._run_guardian_session", fake_run)

    await _handle_agent_request(MagicMock(), config, job)

    yaml_files = list(reply_to.glob("*.yaml"))
    assert len(yaml_files) == 1
    data = yaml.safe_load(yaml_files[0].read_text())
    assert data["status"] == "no_context"
    assert data["project_id"] == "some-project"
    assert data.get("result", "") == ""
```

### Step 2: Run tests to verify they fail

```bash
python -m pytest tests/test_guardian.py::test_handle_agent_request_writes_response_yaml tests/test_guardian.py::test_handle_agent_request_no_context_writes_minimal_response -v
```

Expected: FAIL. The current `_handle_agent_request` does not route through `_run_guardian_session` and the response YAML does not carry `project_id`/`status`/`sources`.

### Step 3a: Add a `guardian` mode to modes.yaml

In `config/modes.yaml`, after the `chat:` mode block, add:

```yaml
guardian:
  model_key: chat
  working_dir: root
  agents: []
  system_prompt: config/prompts/system/guardian.md
  system_prompt_mode: replace
  trigger_prompt: null
```

This is an internal mode used by the worker when processing inter-agent requests. It is not user-facing.

### Step 3b: Replace `_handle_agent_request` and add `_run_guardian_session`

In `src/daemon/worker.py`, replace the existing `_handle_agent_request` function (lines 758-781) with this block:

```python
async def _run_guardian_session(client, config: dict, job: dict) -> str:
    """Open an SDK session in 'guardian' mode and return the final text.

    Returns an empty string on timeout or error — parser will default to no_context.
    """
    from sdk.session import agent_session
    from sdk.tools import get_tools

    task_text = job.get("task", "")
    from_name = job.get("from", "Unknown")
    project_id = job.get("project_id", "")

    user_prompt = (
        f"Teammate: {from_name}\n"
        f"Project context: {project_id or '(unspecified)'}\n"
        f"Question: {task_text}\n\n"
        f"Follow the Guardian Mode workflow. End with the structured JSON."
    )

    tools = get_tools()
    async with agent_session(client, config, "guardian", tools=tools) as (session, handler):
        await session.send(user_prompt)
        try:
            await asyncio.wait_for(handler.done.wait(), timeout=120)
        except asyncio.TimeoutError:
            log.warning(f"  Guardian session timed out for req {str(job.get('request_id', '?'))[:8]}")
        return handler.final_text or ""


async def _handle_agent_request(client, config: dict, job: dict) -> None:
    """Process an incoming agent_request via Guardian Mode and write response YAML."""
    task_text = job.get("task", "")
    from_name = job.get("from", "Unknown")
    kind = job.get("kind", "question")

    log.info(f"  Guardian for {from_name} ({kind}): {task_text[:80]}...")

    output_text = await _run_guardian_session(client, config, job)
    parsed = _parse_guardian_output(output_text)
    _write_guardian_response(config, job, parsed)
```

### Step 4: Rewrite `_write_agent_response` as `_write_guardian_response`

In `src/daemon/worker.py`, replace the existing `_write_agent_response` function (lines 784-824) with:

```python
def _write_guardian_response(config: dict, original_job: dict, parsed: dict) -> None:
    """Write a structured response YAML to the requester's reply_to path.

    ``parsed`` is the Guardian LLM's output dict, at minimum containing 'status'.
    """
    reply_to = original_job.get("reply_to", "")
    if not reply_to:
        log.warning("  Agent request has no reply_to — cannot send response")
        return

    reply_dir = Path(reply_to)
    try:
        reply_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        log.error(f"  Cannot create reply_to path: {e}")
        return

    user_cfg = config.get("user", {})
    from_name = user_cfg.get("name", "Unknown")
    from_alias = user_cfg.get("alias") or (from_name.lower().split()[0] if from_name else "unknown")

    request_id = original_job.get("request_id", "unknown")
    project_id = original_job.get("project_id", "")
    timestamp = datetime.now().isoformat()
    date_str = datetime.now().strftime("%Y-%m-%d")
    slug = str(request_id)[:8]

    response_data = {
        "type": "agent_response",
        "kind": "response",
        "request_id": request_id,
        "project_id": project_id,
        "from": from_name,
        "from_alias": from_alias,
        "original_task": original_job.get("task", "")[:200],
        "status": parsed.get("status", "no_context"),
        "result": parsed.get("result", ""),
        "sources": parsed.get("sources", []),
        "created_at": timestamp,
    }
    if parsed.get("status") == "declined" and "reason" in parsed:
        response_data["reason"] = parsed["reason"]

    response_file = reply_dir / f"{date_str}-response-{from_alias}-{slug}.yaml"
    with open(response_file, "w", encoding="utf-8") as f:
        yaml.dump(response_data, f, default_flow_style=False)

    log.info(f"  Guardian response written: status={parsed.get('status')} to {response_file}")
```

### Step 5: Update the `agent_request` branch to drop the now-unused helper call

In `src/daemon/worker.py`, find the `elif job_type == "agent_request":` block around line 400-404 and replace:

```python
elif job_type == "agent_request":
    result_text = await _handle_agent_request(client, config, job)
    if "_file" in job:
        mark_task_completed(job)
    _write_agent_response(config, job, result_text)
```

with:

```python
elif job_type == "agent_request":
    await _handle_agent_request(client, config, job)
    if "_file" in job:
        mark_task_completed(job)
```

(The new `_handle_agent_request` writes the response internally.)

### Step 6: Run tests to verify they pass

```bash
python -m pytest tests/test_guardian.py -v --tb=short
```

Expected: all Guardian tests pass.

Also run the existing daemon tests to make sure nothing broke:

```bash
python -m pytest tests/test_daemon.py -v --tb=short
```

Expected: the old `test_write_agent_response_*` tests will FAIL because the function is renamed. That is intended — those tests need to move or update. Do this as part of Step 6.5:

### Step 6.5: Update existing daemon tests that referenced the old function

In `tests/test_daemon.py`, find any tests that import or call `_write_agent_response` (there are three per audit, around line 101-155). Replace with calls to `_write_guardian_response`, adjusting the fixture data to include the new fields:

```python
from daemon.worker import _write_guardian_response  # replace _write_agent_response

def test_write_guardian_response_writes_yaml(tmp_path):
    reply_to = tmp_path / "reply"
    reply_to.mkdir()
    job = {
        "task": "original question?",
        "project_id": "some-project",
        "reply_to": str(reply_to),
        "request_id": "abc12345",
    }
    config = {"user": {"name": "Beta User", "alias": "beta"}}
    parsed = {
        "status": "answered",
        "result": "Some answer.",
        "sources": ["transcripts/x.md"],
    }
    _write_guardian_response(config, job, parsed)

    files = list(reply_to.glob("*.yaml"))
    assert len(files) == 1
    data = yaml.safe_load(files[0].read_text())
    assert data["status"] == "answered"
    assert data["project_id"] == "some-project"
    assert data["result"] == "Some answer."
    assert data["sources"] == ["transcripts/x.md"]


def test_write_guardian_response_empty_reply_to_no_crash(tmp_path, caplog):
    _write_guardian_response(
        {"user": {"name": "X", "alias": "x"}},
        {"task": "q", "reply_to": "", "request_id": "r"},
        {"status": "no_context"},
    )
    # Should log warning, not raise


def test_write_guardian_response_creates_missing_reply_dir(tmp_path):
    reply_to = tmp_path / "new" / "deep"  # does not exist yet
    _write_guardian_response(
        {"user": {"name": "B", "alias": "b"}},
        {"task": "q", "project_id": "p", "reply_to": str(reply_to), "request_id": "abc123"},
        {"status": "no_context"},
    )
    assert reply_to.exists()
    assert len(list(reply_to.glob("*.yaml"))) == 1
```

Remove the old `test_write_agent_response_*` tests in the same file.

### Step 7: Run the full test suite

```bash
python -m pytest tests/ -x --tb=short
```

Expected: all tests pass (or at minimum, no new failures beyond already-known ignored ones).

### Step 8: Commit

```bash
git add src/daemon/worker.py tests/test_guardian.py tests/test_daemon.py config/modes.yaml
git commit -m "feat: route agent requests through Guardian Mode SDK session"
```

---

## Task 5: Ingest agent responses into project YAML

**Files:**
- Modify: `src/daemon/worker.py` (rewrite the `agent_response` branch around line 406-416, add `_ingest_agent_response` helper)
- Create: `tests/test_team_ingest.py`

### Step 1: Write the failing test

Create `tests/test_team_ingest.py`:

```python
"""Tests for agent_response → project YAML ingestion."""
from unittest.mock import patch

import pytest
import yaml

from daemon.worker import _ingest_agent_response


def _write_project(projects_dir, project_id, data=None):
    data = data or {"project": project_id, "status": "active"}
    (projects_dir / f"{project_id}.yaml").write_text(
        yaml.dump(data, default_flow_style=False), encoding="utf-8"
    )


@pytest.fixture
def project_dir(tmp_path, monkeypatch):
    d = tmp_path / "projects"
    d.mkdir()
    monkeypatch.setattr("daemon.worker.PROJECTS_DIR", d)
    return d


def test_ingest_answered_response_appends_team_context(project_dir):
    _write_project(project_dir, "fabric-sap", {
        "project": "Fabric on SAP", "status": "active",
    })
    job = {
        "type": "agent_response",
        "status": "answered",
        "project_id": "fabric-sap",
        "request_id": "req-abc",
        "from": "Beta User",
        "from_alias": "beta",
        "original_task": "any prior objections data?",
        "result": "3 POCs; licensing was the main objection.",
        "sources": ["transcripts/2026-01-15.md"],
        "created_at": "2026-04-23T10:00:00",
    }

    _ingest_agent_response(job)

    data = yaml.safe_load((project_dir / "fabric-sap.yaml").read_text())
    assert "team_context" in data
    assert len(data["team_context"]) == 1
    entry = data["team_context"][0]
    assert entry["from"] == "Beta User"
    assert entry["request_id"] == "req-abc"
    assert entry["answer"] == "3 POCs; licensing was the main objection."
    assert entry["sources"] == ["transcripts/2026-01-15.md"]


def test_ingest_no_context_response_is_dropped(project_dir):
    _write_project(project_dir, "fabric-sap")
    job = {
        "status": "no_context",
        "project_id": "fabric-sap",
        "request_id": "req-skip",
    }
    _ingest_agent_response(job)
    data = yaml.safe_load((project_dir / "fabric-sap.yaml").read_text())
    assert data.get("team_context", []) == []


def test_ingest_duplicate_request_id_is_deduped(project_dir):
    _write_project(project_dir, "fabric-sap", {
        "project": "Fabric on SAP",
        "status": "active",
        "team_context": [
            {"from": "Beta", "request_id": "req-abc", "answer": "existing", "sources": []}
        ],
    })
    job = {
        "status": "answered",
        "project_id": "fabric-sap",
        "request_id": "req-abc",
        "from": "Beta",
        "from_alias": "beta",
        "result": "new answer (should be ignored)",
        "sources": [],
        "created_at": "2026-04-23T10:00:00",
    }
    _ingest_agent_response(job)
    data = yaml.safe_load((project_dir / "fabric-sap.yaml").read_text())
    assert len(data["team_context"]) == 1
    assert data["team_context"][0]["answer"] == "existing"


def test_ingest_missing_project_is_logged_not_crashed(project_dir, caplog):
    job = {
        "status": "answered",
        "project_id": "nonexistent-project",
        "request_id": "req-orphan",
        "from": "Beta",
        "from_alias": "beta",
        "result": "answer",
        "sources": [],
        "created_at": "2026-04-23T10:00:00",
    }
    # Should not raise
    _ingest_agent_response(job)
    # No project file should be created
    assert not (project_dir / "nonexistent-project.yaml").exists()


def test_ingest_preserves_other_project_fields(project_dir):
    _write_project(project_dir, "fabric-sap", {
        "project": "Fabric on SAP",
        "status": "active",
        "stakeholders": [{"name": "Jane Doe", "role": "PM"}],
        "commitments": [{"what": "send proposal", "due": "2026-05-01"}],
    })
    job = {
        "status": "answered",
        "project_id": "fabric-sap",
        "request_id": "req-new",
        "from": "Beta",
        "from_alias": "beta",
        "result": "something new",
        "sources": ["a.md"],
        "created_at": "2026-04-23T10:00:00",
    }
    _ingest_agent_response(job)
    data = yaml.safe_load((project_dir / "fabric-sap.yaml").read_text())
    assert data["project"] == "Fabric on SAP"
    assert data["status"] == "active"
    assert data["stakeholders"] == [{"name": "Jane Doe", "role": "PM"}]
    assert data["commitments"] == [{"what": "send proposal", "due": "2026-05-01"}]
    assert len(data["team_context"]) == 1
```

### Step 2: Run tests to verify they fail

```bash
python -m pytest tests/test_team_ingest.py -v --tb=short
```

Expected: FAIL with `ImportError: cannot import name '_ingest_agent_response'`.

### Step 3: Add the ingestion helper

In `src/daemon/worker.py`, after `_write_guardian_response` (the function added in Task 4), add:

```python
from core.constants import PROJECTS_DIR  # add near the top imports if not already present


def _ingest_agent_response(job: dict) -> None:
    """Fold an agent_response into its target project YAML's team_context[].

    Silent skip on:
      - status != "answered" (no_context / declined)
      - missing project_id
      - missing project YAML
      - duplicate request_id (already ingested)

    Atomic write: writes to a temp path and renames.
    """
    status = job.get("status", "")
    if status != "answered":
        log.info(f"  Ingest: skipping response with status='{status}' (req={str(job.get('request_id','?'))[:8]})")
        return

    project_id = job.get("project_id", "")
    if not project_id:
        log.warning(f"  Ingest: response has no project_id — dropping (req={str(job.get('request_id','?'))[:8]})")
        return

    project_path = PROJECTS_DIR / f"{project_id}.yaml"
    if not project_path.exists():
        log.warning(f"  Ingest: project '{project_id}' not found — dropping response")
        return

    try:
        with open(project_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception as e:
        log.error(f"  Ingest: cannot read project '{project_id}': {e}")
        return

    team_context = data.get("team_context") or []
    request_id = job.get("request_id", "")
    if any(entry.get("request_id") == request_id for entry in team_context):
        log.info(f"  Ingest: request_id {request_id[:8]} already present — dedup skip")
        return

    entry = {
        "from": job.get("from", "Unknown"),
        "from_alias": job.get("from_alias", ""),
        "contributed_at": job.get("created_at", datetime.now().isoformat()),
        "question": job.get("original_task", "")[:200],
        "answer": job.get("result", ""),
        "sources": job.get("sources", []),
        "request_id": request_id,
    }
    team_context.append(entry)
    data["team_context"] = team_context

    # Atomic write via temp file + rename
    tmp_path = project_path.with_suffix(".yaml.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    tmp_path.replace(project_path)

    log.info(f"  Ingest: added team_context entry to project '{project_id}' from {entry['from']}")
```

### Step 4: Wire the ingestion into the `agent_response` branch

In `src/daemon/worker.py`, find the `elif job_type == "agent_response":` block at line 406-416 and replace with:

```python
elif job_type == "agent_response":
    from_name = job.get("from", "Unknown")
    project_id = job.get("project_id", "")
    log.info(f"  Agent response from {from_name} (project: {project_id or 'n/a'}, req: {str(job.get('request_id') or '?')[:8]})")
    _ingest_agent_response(job)
    if "_file" in job:
        mark_task_completed(job)
    if job.get("status") == "answered":
        original_task = job.get("original_task", "")
        notify_desktop(
            f"Pulse — {from_name} contributed",
            f"Project: {project_id or 'n/a'} | Re: {original_task[:60]}",
            urgency="normal",
        )
```

### Step 5: Run tests to verify they pass

```bash
python -m pytest tests/test_team_ingest.py -v --tb=short
```

Expected: 5 passed.

Also sanity-check:

```bash
python -m pytest tests/test_daemon.py tests/test_guardian.py tests/test_broadcast.py -v --tb=short
```

Expected: no regressions.

### Step 6: Commit

```bash
git add src/daemon/worker.py tests/test_team_ingest.py
git commit -m "feat: ingest agent responses into project team_context"
```

---

## Task 6: Reduce scheduler poll interval to 30s

**Files:**
- Modify: `src/core/scheduler.py` (change `scheduler_loop` default `check_interval` from 60 to 30)
- Modify: existing scheduler test file if any, or create a small assertion in `tests/test_scheduler.py`

### Step 1: Write the failing test

Append to `tests/test_scheduler.py` (or create the file if missing):

```python
from inspect import signature
from core.scheduler import scheduler_loop


def test_scheduler_loop_default_check_interval_is_30s():
    """Cross-agent poll cadence depends on this. 60s was too slow for demo."""
    sig = signature(scheduler_loop)
    assert sig.parameters["check_interval"].default == 30
```

### Step 2: Run test to verify it fails

```bash
python -m pytest tests/test_scheduler.py::test_scheduler_loop_default_check_interval_is_30s -v
```

Expected: FAIL — current default is 60.

### Step 3: Change the default

In `src/core/scheduler.py`, line 298-303, change:

```python
async def scheduler_loop(
    config: dict,
    job_queue,
    shutdown_event: asyncio.Event,
    check_interval: int = 60,
):
```

to:

```python
async def scheduler_loop(
    config: dict,
    job_queue,
    shutdown_event: asyncio.Event,
    check_interval: int = 30,
):
```

Also update the log line at line 316 if it references the interval value in a way that would read awkwardly. The current line `log.info(f"Scheduler started (checking every {check_interval}s)")` is fine — no change needed.

### Step 4: Run test to verify it passes

```bash
python -m pytest tests/test_scheduler.py::test_scheduler_loop_default_check_interval_is_30s -v
```

Expected: PASS.

Also run the full scheduler test file:

```bash
python -m pytest tests/test_scheduler.py -v
```

Expected: no regressions.

### Step 5: Commit

```bash
git add src/core/scheduler.py tests/test_scheduler.py
git commit -m "fix: reduce scheduler poll interval from 60s to 30s for cross-agent cadence"
```

---

## Task 7: Add team-enrichment directive to digest-writer prompt

**Files:**
- Modify: `config/prompts/agents/digest-writer.md`
- Modify: `tests/test_sdk.py` or create `tests/test_prompts.py` — an assertion that the digest-writer file contains the directive

### Step 1: Write the failing test

Create `tests/test_prompts.py` (if no equivalent exists):

```python
"""Assertions about prompt file contents — guardrails against silent drift."""
from pathlib import Path

from core.constants import PROJECT_ROOT


def _read(rel_path: str) -> str:
    return (PROJECT_ROOT / rel_path).read_text(encoding="utf-8")


def test_digest_writer_has_team_enrichment_directive():
    text = _read("config/prompts/agents/digest-writer.md")
    assert "Team Enrichment" in text or "team enrichment" in text.lower()
    assert "broadcast_to_team" in text
    assert "last_team_enrichment" in text
    assert "questions" in text.lower()


def test_chat_has_broadcast_routing_instruction():
    text = _read("config/prompts/system/chat.md")
    assert "broadcast_to_team" in text
    assert "project_id" in text
```

### Step 2: Run tests to verify they fail

```bash
python -m pytest tests/test_prompts.py -v --tb=short
```

Expected: both fail — prompts don't mention these yet.

### Step 3: Update `digest-writer.md`

Append this section to `config/prompts/agents/digest-writer.md`:

```markdown

## Team Enrichment

While producing the digest, check each active project for team-input gaps. A project needs team input when:

  - `last_team_enrichment` is null (never asked), OR
  - `questions: [...]` contains an entry with `added_at` more recent than `last_team_enrichment`

For each project that qualifies (maximum 3 per digest), produce a concise one-sentence question for teammates:

  - If `questions[0]` is populated, use it verbatim.
  - Otherwise, generate one from project context focusing on prior objections, customer-specific context, or tech-specific learnings.

Call `broadcast_to_team(question, project_id)` for each selected project. Then call `update_project` on that project to stamp `last_team_enrichment` with the current ISO timestamp.

**Do NOT wait for responses.** Fire the broadcasts and continue the digest. Responses will be ingested asynchronously into `team_context` as they arrive, and the NEXT digest will synthesize them.
```

### Step 4: Update `chat.md`

In `config/prompts/system/chat.md`, append:

```markdown

## Team questions

When the user asks you to "check with the team," "ask colleagues," or "find context from the team" about a specific project or topic:

1. Use `search_local_files` to look up existing project YAMLs under `projects/` and match the user's topic to an existing `project_id`.
2. If you cannot confidently match, ask the user which project_id to attach the question to before calling the tool.
3. Call `broadcast_to_team(question, project_id)` once. Do not call it multiple times for the same question (the tool broadcasts to all configured teammates in one call).
4. Reply to the user with something like: "Broadcasted to N teammates. Responses will fold into the project as they arrive."
```

### Step 5: Run tests to verify they pass

```bash
python -m pytest tests/test_prompts.py -v --tb=short
```

Expected: 2 passed.

### Step 6: Commit

```bash
git add config/prompts/agents/digest-writer.md config/prompts/system/chat.md tests/test_prompts.py
git commit -m "feat: wire team-enrichment directive into digest and chat prompts"
```

---

## Task 8: Demo data seeding script

**Files:**
- Create: `scripts/seed_demo_data.py`
- Create: `tests/test_seed_demo.py`

### Step 1: Write the failing test

Create `tests/test_seed_demo.py`:

```python
"""Tests for scripts/seed_demo_data.py — idempotent demo corpus seeding."""
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SEED_SCRIPT = REPO_ROOT / "scripts" / "seed_demo_data.py"


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
```

### Step 2: Run tests to verify they fail

```bash
python -m pytest tests/test_seed_demo.py -v --tb=short
```

Expected: FAIL — script does not exist.

### Step 3: Create the seeder

Create `scripts/seed_demo_data.py`:

```python
"""Populate a target PULSE_HOME with mock data for the cross-agent demo.

Usage:
    python scripts/seed_demo_data.py --target-pulse-home /path/to/demo/PulseHome

Idempotent: re-running overwrites the same files with the same content.
"""
from __future__ import annotations

import argparse
from pathlib import Path


TRANSCRIPT_1 = """# Fabric-on-SAP POV — Contoso kickoff

**Date:** 2026-01-15
**Attendees:** Beta Demo (MS), Jordan Sales (Contoso), Alex Architect (Contoso)

## Summary

Kickoff for Fabric-on-SAP POV with Contoso. Jordan flagged licensing complexity
as the #1 objection from their prior attempts. Alex wants a working SAP HANA
to Fabric ingestion demo before committing to a pilot.

## Decisions

- Demo scope: SAP HANA to Fabric OneLake via OpenHub export.
- POV duration: 4 weeks, ending 2026-02-12.
- Target objection answer: total-cost-of-ownership deck addressing licensing.

## Action items

- [Beta Demo] prepare SAP HANA → OneLake walkthrough by 2026-01-22
- [Alex Architect] provide sample HANA dataset by 2026-01-18
- [Jordan Sales] escalate pricing question to Contoso legal

## Quotes

> "We tried this with a competitor last quarter — their licensing math didn't
> hold up in procurement review. That's our biggest risk."
> — Jordan Sales
"""

TRANSCRIPT_2 = """# Contoso Fabric-on-SAP follow-up

**Date:** 2026-02-08
**Attendees:** Beta Demo, Jordan Sales, Alex Architect

## Summary

Second demo session. The HANA ingestion walkthrough landed well. Licensing
objection resurfaced — Jordan wants a concrete TCO comparison vs the
competitor (name redacted) before the procurement meeting.

## Decisions

- Beta Demo will produce a 3-year TCO spreadsheet tied to Contoso's workload volume.
- Technical POV declared complete and viable.
- Commercial next step: TCO review on 2026-02-20.

## Key learning

The licensing objection is not about absolute cost — it's about predictability.
Contoso's procurement team burned before on a similar deal where unit counts
scaled unexpectedly. A capped/committed pricing model would likely close it.
"""

EMAIL_1 = """From: Jordan Sales <jordan@contoso.example>
To: Beta Demo <beta.demo@microsoft.example>
Subject: Re: Fabric licensing — follow up
Date: Thu, 20 Feb 2026 14:30:00 +0000

Beta,

Circling back on the licensing question. Our procurement team will need to
see a 3-year commitment option with capped units before we can green-light
the pilot. The per-transaction uncertainty is the blocker, not the headline
price.

Can you get me something by end of next week?

Thanks,
Jordan
"""

PROJECT_YAML = """project: Contoso Fabric-on-SAP
status: active
risk_level: medium
summary: Fabric on SAP HANA POV for Contoso. Technical POV complete, commercial blocked on licensing/TCO clarity.
stakeholders:
  - name: Jordan Sales
    role: Contoso sales lead
  - name: Alex Architect
    role: Contoso technical architect
commitments:
  - what: Deliver 3-year TCO spreadsheet with capped units
    who: Beta Demo
    to: Contoso
    due: 2026-02-27
    status: open
    source: 2026-02-08 follow-up + Jordan email 2026-02-20
next_meeting: 2026-02-27 15:00
key_dates:
  - date: 2026-03-05
    event: Contoso procurement review
"""


FILES = {
    "transcripts/2026-01-15_contoso-fabric-sap-kickoff.md": TRANSCRIPT_1,
    "transcripts/2026-02-08_contoso-fabric-sap-followup.md": TRANSCRIPT_2,
    "emails/2026-02-20_jordan-fabric-licensing.eml": EMAIL_1,
    "projects/contoso-fabric-sap.yaml": PROJECT_YAML,
}


def seed(target: Path) -> int:
    """Populate target with mock files. Returns number of files written."""
    count = 0
    for rel, content in FILES.items():
        path = target / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed demo data for cross-agent demo.")
    parser.add_argument(
        "--target-pulse-home", required=True,
        help="Path to the teammate's PULSE_HOME (will be created if missing).",
    )
    args = parser.parse_args()

    target = Path(args.target_pulse_home).resolve()
    target.mkdir(parents=True, exist_ok=True)
    n = seed(target)
    print(f"Seeded {n} files into {target}")


if __name__ == "__main__":
    main()
```

### Step 4: Run tests to verify they pass

```bash
python -m pytest tests/test_seed_demo.py -v --tb=short
```

Expected: 2 passed.

### Step 5: Commit

```bash
git add scripts/seed_demo_data.py tests/test_seed_demo.py
git commit -m "feat: demo data seeder for cross-agent two-terminal demo"
```

---

## Task 9: End-to-end integration test

**Files:**
- Create: `tests/test_cross_agent_e2e.py`

Validates the file-plumbing contract across both sides: broadcast → YAML lands in teammate's pending, simulate Guardian response, verify ingestion updates project YAML. The SDK is mocked.

### Step 1: Write the test

Create `tests/test_cross_agent_e2e.py`:

```python
"""End-to-end contract test for the cross-agent flow.

Two fake PULSE_HOMEs in temp dirs. The SDK is mocked — this test validates
the file-plumbing and data contracts, not LLM behavior.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from sdk.tools import broadcast_to_team
from daemon.worker import _handle_agent_request, _ingest_agent_response


@pytest.mark.asyncio
async def test_full_loop_broadcast_guardian_ingest(tmp_path, monkeypatch):
    """
    Sender (artur) broadcasts → teammate (beta) YAML dropped →
    Guardian session (mocked) drafts answer → response YAML lands in artur's inbox →
    ingestion appends to artur's project YAML.
    """
    # --- Set up sender's world (artur) ---
    artur_home = tmp_path / "artur-home"
    artur_home.mkdir()
    (artur_home / "projects").mkdir()
    artur_projects = artur_home / "projects"
    (artur_projects / "fabric-sap.yaml").write_text(
        yaml.dump({"project": "Fabric SAP", "status": "active"}, default_flow_style=False),
        encoding="utf-8",
    )

    # --- Set up teammate's world (beta) ---
    team_dir = tmp_path / "Pulse-Team"
    (team_dir / "beta").mkdir(parents=True)
    (team_dir / "artur").mkdir(parents=True)  # artur's own inbox for responses

    # --- Config seen by sender ---
    sender_config = {
        "team": [{"name": "Beta User", "alias": "beta"}],
        "user": {"name": "Artur Zielinski", "alias": "artur"},
    }

    # --- Step 1: Broadcast from artur ---
    with patch("core.config.load_config", return_value=sender_config), \
         patch("sdk.tools.PULSE_TEAM_DIR", team_dir):
        result = await broadcast_to_team.handler({"arguments": {
            "question": "Prior Fabric-on-SAP objections?",
            "project_id": "fabric-sap",
        }})
    assert result["resultType"] == "success"

    # Verify teammate got the YAML
    beta_pending = team_dir / "beta" / "jobs" / "pending"
    beta_files = list(beta_pending.glob("*.yaml"))
    assert len(beta_files) == 1
    beta_job = yaml.safe_load(beta_files[0].read_text())
    assert beta_job["kind"] == "broadcast"
    assert beta_job["project_id"] == "fabric-sap"
    assert beta_job["reply_to"].endswith("artur/jobs/pending")

    # --- Step 2: Beta's worker runs Guardian session (mocked LLM response) ---
    # The Guardian fake returns a happy-path JSON answer.
    fake_guardian_output = '''```json
{"status": "answered", "result": "3 POCs; licensing main objection.", "sources": ["transcripts/demo.md"]}
```'''

    beta_config = {"user": {"name": "Beta User", "alias": "beta"}}
    # Pass the job we just wrote (simulates worker picking it up)
    beta_job["_file"] = str(beta_files[0])

    fake_run = AsyncMock(return_value=fake_guardian_output)
    monkeypatch.setattr("daemon.worker._run_guardian_session", fake_run)

    await _handle_agent_request(MagicMock(), beta_config, beta_job)

    # Response YAML should have landed in artur's inbox
    artur_pending = team_dir / "artur" / "jobs" / "pending"
    response_files = list(artur_pending.glob("*-response-*.yaml"))
    assert len(response_files) == 1
    response = yaml.safe_load(response_files[0].read_text())
    assert response["type"] == "agent_response"
    assert response["status"] == "answered"
    assert response["project_id"] == "fabric-sap"
    assert "POCs" in response["result"]

    # --- Step 3: Artur ingests the response ---
    response["_file"] = str(response_files[0])
    monkeypatch.setattr("daemon.worker.PROJECTS_DIR", artur_projects)
    _ingest_agent_response(response)

    # Project YAML should now have team_context entry
    final = yaml.safe_load((artur_projects / "fabric-sap.yaml").read_text())
    assert "team_context" in final
    assert len(final["team_context"]) == 1
    entry = final["team_context"][0]
    assert entry["from"] == "Beta User"
    assert entry["answer"].startswith("3 POCs")
    assert entry["sources"] == ["transcripts/demo.md"]
```

### Step 2: Run test to verify it passes

```bash
python -m pytest tests/test_cross_agent_e2e.py -v --tb=short
```

Expected: PASS. If it fails, the failure should point at a specific contract mismatch between the components built in Tasks 1-5.

### Step 3: Run the full suite one more time

```bash
python -m pytest tests/ -x --tb=short
```

Expected: all tests pass.

### Step 4: Commit

```bash
git add tests/test_cross_agent_e2e.py
git commit -m "test: add end-to-end contract test for cross-agent broadcast loop"
```

---

## Task 10: Manual demo rehearsal (no code)

This is a non-code task. Skip if running via automation; execute manually before the pitch.

### Step 1: Prepare two daemons

Create an alpha config file at `config/standing-instructions-alpha.yaml` if it does not already exist, with:
- `user.name`, `user.alias` set to a distinct teammate persona (you pick the name)
- `team:` includes your primary alias (`artur` or whatever your primary config uses)
- `PULSE_HOME` env var pointing at a separate OneDrive-like dir (e.g., `$USERPROFILE/PulseDemoTeammate`)

### Step 2: Seed the teammate's data

```bash
python scripts/seed_demo_data.py --target-pulse-home "$USERPROFILE/PulseDemoTeammate"
```

### Step 3: Start the primary daemon

```bash
python src/pulse.py
```

### Step 4: Start the secondary daemon in a second terminal

```bash
PULSE_CONFIG=config/standing-instructions-alpha.yaml PULSE_HOME="$USERPROFILE/PulseDemoTeammate" python src/pulse.py
```

### Step 5: Trigger the demo

In the primary daemon's TUI, open Chat tab and type:

```
check with the team about Fabric-on-SAP, I have a customer call Thursday
```

### Step 6: Observe

- Within 30s, teammate's Jobs tab should show an incoming `agent_request`.
- Within ~60-90s, primary terminal should show a toast "... contributed to Fabric-on-SAP".
- Primary's Projects tab, when refreshed, should show a new `team_context` entry on the fabric-sap project.

### Step 7: Capture

Screenshot or screen-record for the pitch. Note the wall-clock times between broadcast and ingestion.

---

## Self-review (completed)

**Spec coverage:**

| Spec section | Covered by |
|---|---|
| `broadcast_to_team` tool | Task 1 |
| Guardian system prompt | Task 2 |
| Guardian output parser | Task 3 |
| `_run_guardian_session` + `_handle_agent_request` rewrite | Task 4 |
| `_write_guardian_response` (successor to `_write_agent_response`) | Task 4 |
| `_handle_agent_response` / `_ingest_agent_response` | Task 5 |
| Project YAML `team_context` field | Task 5 (schema enforced by ingestion code) |
| 30s poll interval | Task 6 |
| Digest-writer team-enrichment directive | Task 7 |
| Chat broadcast-routing instruction | Task 7 |
| Mock data seeder | Task 8 |
| End-to-end integration test | Task 9 |
| Demo rehearsal | Task 10 |

**Spec items intentionally deferred (noted in spec's Non-goals):**
- `questions: []` field as a first-class schema addition — the digest prompt references it, but no explicit Python schema change is needed for MVP. The field is read by the LLM from YAML and acted on. Can be formalized in a v2.
- `last_team_enrichment` stamp — written by the digest LLM via `update_project`, no new Python support needed. Tested manually in demo, not in automation.
- Inter-agent audit log — not in MVP.
- Standing subscriptions — explicitly out of scope.

**Placeholder scan:** No TBDs, TODOs, or "implement later" markers. Every step has actual code.

**Type consistency check:**
- `_run_guardian_session` returns `str` (raw LLM output) — consistent across Task 4.
- `_parse_guardian_output` returns `dict` with `status`/`result`/`sources`/`reason` keys — consistent across Tasks 3, 4.
- `_write_guardian_response` accepts `parsed: dict` — consistent with parser output.
- `_ingest_agent_response` accepts `job: dict` with `status`/`project_id`/`request_id`/`from`/etc. — matches YAML schema written by `_write_guardian_response`.
- `broadcast_to_team` writes YAML with `kind: broadcast` and `project_id` — receiver reads both, ingestion reads `project_id` back.

**Ambiguity check:** Task 4 adds a `guardian` mode to `modes.yaml` rather than mutating session config at runtime. This is the same pattern every other mode (chat, digest, intel, etc.) uses and is fully supported by the existing `build_session_config` path. `handler.final_text` is confirmed on `EventHandler` at `src/sdk/event_handler.py:70`.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-23-cross-agent-collab.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
