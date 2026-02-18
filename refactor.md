# Pulse Agent — Refactoring Plan

## Goals

1. **No prompt/instruction text in Python** — all LLM-facing content lives in `config/` as `.md` files
2. **Clean folder structure** — logical domains, no file over ~200 lines, single responsibility
3. **Config-driven modes** — modes defined in YAML, not hardcoded if/elif chains
4. **Kill DRY violations** — path constants, state persistence, message chunking, research prompts
5. **Unit tests** — testable pure functions, proper test structure
6. **Competition-ready** — self-documenting architecture a judge can understand in 60 seconds

---

## Current State (problems)

### Prompt text in Python (~340 lines)

| File | Lines | Content |
|------|-------|---------|
| `session.py:91-113` | 22 | pulse-reader agent prompt |
| `session.py:123-147` | 24 | m365-query agent prompt |
| `session.py:157-170` | 13 | digest-writer agent prompt |
| `session.py:199-232` | 33 | signal-drafter agent prompt |
| `session.py:335-381` | 46 | System prompt base + per-mode blocks |
| `session.py:384-510` | 126 | Transcript prompt with embedded JavaScript |
| `monitor.py:21-28` | 7 | Monitoring trigger prompt |
| `digest.py:370-383` | 13 | Digest trigger prompt header |
| `intel.py:177-214` | 37 | Intel analysis prompt + format template |
| `main.py:64-76` | 12 | Research prompt |
| `telegram_bot.py:171-178` | 7 | `/start` welcome message |

### DRY violations

1. **Path constants** defined in 6 files: `main.py`, `session.py`, `tools.py`, `utils.py`, `telegram_bot.py`, `config.py`
2. **JSON state load/save** pattern duplicated 3x: `digest.py:31-41`, `intel.py:25-33`, `tools.py:118-126`
3. **Research prompt** duplicated: `main.py:64-76` vs `researcher.py:30-43`
4. **Telegram message chunking** duplicated: `telegram_bot.py:203-208` vs `telegram_bot.py:216-221`
5. **`_print` / `_safe_encode`** duplicated: `transcripts.py:92-94`, `diagnose_transcript.py:24-25`, `utils.py:39-41`
6. **Skip keywords** list duplicated: `transcripts.py:332-339` vs `diagnose_transcript.py:137-143`

### Modes are mostly just prompts

| Mode | Has real Python logic? | What Python does | SDK session work |
|------|----------------------|------------------|-----------------|
| monitor | **No** | Nothing | Send prompt → agent queries WorkIQ → writes report |
| digest | **Yes** | Scan folders, extract file content | Send content → agent queries WorkIQ → writes digest |
| intel | **Yes** | Fetch RSS feeds | Send articles → agent analyzes → writes brief |
| research | **No** | Nothing | Send task → agent works autonomously |
| transcripts | **Yes** (all of it) | Playwright browser automation | No LLM involved |

`monitor.py` is 37 lines of which 7 are the prompt and the rest is boilerplate. `researcher.py` is dead code.

### Oversized files

| File | Lines | Responsibilities (should be 1) |
|------|-------|-------------------------------|
| `session.py` | 529 | Agent defs + prompt builders + session config |
| `transcripts.py` | 608 | JS constants + calendar nav + meeting discovery + tab clicking + DOM extraction + text parsing |
| `main.py` | 484 | CLI + daemon lifecycle + job worker + OneDrive sync + utility functions |
| `digest.py` | 385 | File extractors + folder scanning + digest orchestration + prompt building |
| `diagnose_transcript.py` | 468 | Debug script duplicating transcripts.py logic |

---

## Target State

### Folder structure

```
src/
├── main.py                              # CLI entry point + daemon boot (~100 lines)
│
├── core/                                # Shared infrastructure
│   ├── __init__.py                      # Re-exports: constants, config, state, log
│   ├── constants.py                     # All path constants — ONE source of truth
│   ├── config.py                        # load_config, validate_config, load_pending_tasks
│   ├── state.py                         # Generic JSON state load/save
│   ├── logging.py                       # setup_logging, log_event, safe_encode, new_run_id
│   └── browser.py                       # BrowserManager singleton (shared Edge instance)
│
├── sdk/                                 # GHCP SDK integration
│   ├── __init__.py                      # Re-exports: run_job, get_tools, agent_session
│   ├── runner.py                        # run_job() — unified config-driven job runner
│   ├── session.py                       # build_session_config from modes.yaml
│   ├── agents.py                        # Load agent definitions from config/prompts/agents/*.md
│   ├── prompts.py                       # load_prompt() with {{variable}} interpolation
│   └── tools.py                         # Custom tools: log_action, write_output, etc.
│
├── daemon/                              # Always-on daemon machinery
│   ├── __init__.py
│   ├── worker.py                        # Job queue worker + stage dispatch
│   ├── heartbeat.py                     # Heartbeat loop, office hours, missed digest check
│   └── sync.py                          # OneDrive sync in/out
│
├── collectors/                          # Data collection (Python, no LLM)
│   ├── __init__.py
│   ├── extractors.py                    # File content extraction registry (txt, docx, pptx, pdf, xlsx)
│   ├── content.py                       # collect_content() — scan folders, extract text
│   ├── feeds.py                         # collect_feeds() — RSS feed fetcher
│   └── transcripts/                     # Playwright browser automation
│       ├── __init__.py                  # Exports run_transcript_collection
│       ├── collector.py                 # Main loop: launch browser, iterate meetings, save
│       ├── navigation.py               # Calendar nav, meeting buttons, return_to_calendar
│       ├── extraction.py               # Scroll+collect, clean transcript, find frame
│       └── js_snippets.py              # All JavaScript constants for DOM interaction
│
└── telegram/                            # User interface
    ├── __init__.py                      # Exports start_telegram_bot, stop_telegram_bot, notify
    ├── bot.py                           # TelegramBot class — no module-level globals
    └── confirmations.py                 # ask_user confirmation flow + Future management

config/
├── modes.yaml                           # Mode definitions: agents, prompts, tools, hooks
├── standing-instructions.yaml           # User preferences, priorities, feeds, models
├── prompts/
│   ├── agents/                          # Agent personality definitions
│   │   ├── pulse-reader.md
│   │   ├── m365-query.md
│   │   ├── digest-writer.md
│   │   └── signal-drafter.md
│   ├── system/                          # Self-contained system prompts per mode (instructions merged in)
│   │   ├── base.md                      # Shared base (identity, rules, log_action)
│   │   ├── monitor.md                   # Full triage workflow (merged from instructions/triage.md)
│   │   ├── digest.md                    # Full digest instructions (merged from instructions/digest.md)
│   │   ├── intel.md                     # Full intel instructions (merged from instructions/intel.md)
│   │   ├── research.md                  # Full research instructions (merged from instructions/research.md)
│   │   ├── chat.md                      # Full chat persona/rules (merged from instructions/chat.md)
│   │   └── transcripts.md              # Transcript collection workflow + embedded JS
│   ├── triggers/                        # Per-job trigger prompts — {{variables}} for runtime data only
│   │   ├── monitor.md                   # Plain text, no variables
│   │   ├── digest.md                    # Output rules inlined (merged from instructions/digest-output-rules.md)
│   │   ├── intel.md                     # {{articles}}, {{topics}}, {{competitors}}
│   │   └── research.md                 # {{task}}, {{description}}, {{output_path}}
│   └── telegram-welcome.md             # /start welcome message
└── skills/
    ├── pulse-signal-drafter/SKILL.md    # EXISTS — keep
    └── teams-sender/SKILL.md            # EXISTS — keep

tests/
├── conftest.py                          # Shared fixtures (mock config, temp dirs, etc.)
├── core/
│   ├── test_config.py                   # Config loading, validation, env var expansion
│   ├── test_state.py                    # JSON state load/save/default/mkdir
│   └── test_logging.py                  # Logger setup, safe_encode, JSON formatter
├── collectors/
│   ├── test_extractors.py              # Each file type extractor + registry dispatch
│   ├── test_content.py                 # Folder scanning, incremental state, char limits
│   ├── test_feeds.py                   # RSS parsing, deduplication, state pruning
│   └── transcripts/
│       └── test_extraction.py          # _clean_transcript (pure text parsing)
├── sdk/
│   ├── test_prompts.py                 # Template loading, variable interpolation
│   ├── test_agents.py                  # Agent config loading from .md files
│   └── test_tools.py                   # Tool handlers (log_action, write_output, etc.)
├── daemon/
│   ├── test_heartbeat.py              # _is_office_hours, _parse_interval, _check_missed_digest
│   ├── test_sync.py                   # OneDrive sync logic
│   └── test_worker.py                 # Job dispatch routing
└── telegram/
    ├── test_bot.py                     # Quick action matching, authorization
    └── test_confirmations.py          # Confirmation flow, timeout handling
```

### Files to DELETE

| File | Reason |
|------|--------|
| `src/diagnose_transcript.py` | Debug script, duplicates transcripts.py, gitignored anyway |
| `src/researcher.py` | Dead code — daemon handles research via worker |
| `src/monitor.py` | 37 lines → becomes a prompt file + 1 line in runner |
| `src/browser.py` | Moves to `core/browser.py` |
| `src/session.py` | Split into `sdk/session.py`, `sdk/agents.py`, `sdk/prompts.py` |
| `src/utils.py` | Split into `core/logging.py`, `core/state.py` |
| `src/digest.py` | Split into `collectors/extractors.py`, `collectors/content.py`, `sdk/runner.py` handles orchestration |
| `src/intel.py` | Split into `collectors/feeds.py`, prompt moves to config, orchestration in runner |
| `src/transcripts.py` | Split into `collectors/transcripts/` sub-package |
| `src/telegram_bot.py` | Rewritten as `telegram/bot.py` class |
| `src/main.py` | Rewritten (daemon logic moves to `daemon/`) |
| `src/tools.py` | Moves to `sdk/tools.py` |
| `src/config.py` | Moves to `core/config.py` |
| `config/instructions/` (entire folder) | All content merged into `config/prompts/system/` and `config/prompts/triggers/`. `task.md` moves to `config/prompts/task.md` |

---

## Config: modes.yaml

This replaces the hardcoded if/elif chain in `session.py:build_session_config()`.

```yaml
# Each mode defines how to build an SDK session.
# Python code loads this and assembles SessionConfig — no business logic in Python.

monitor:
  model_key: triage                        # Key into standing-instructions.yaml → models
  working_dir: output                      # "output" = OUTPUT_DIR, "root" = PROJECT_ROOT
  mcp_servers: [workiq]
  agents: []
  system_prompt: config/prompts/system/monitor.md
  system_prompt_mode: append               # "append" to CLI default, or "replace" it entirely
  trigger_prompt: config/prompts/triggers/monitor.md
  pre_process: null                        # No Python pre-processing needed

digest:
  model_key: digest
  working_dir: root
  mcp_servers: [workiq]
  agents: [m365-query, digest-writer, signal-drafter]
  system_prompt: config/prompts/system/digest.md
  system_prompt_mode: append
  trigger_prompt: config/prompts/triggers/digest.md
  pre_process: collect_content_and_feeds   # Python: scan folders + fetch RSS before agent call

intel:
  model_key: intel
  working_dir: root
  mcp_servers: [workiq]
  agents: []
  system_prompt: config/prompts/system/intel.md
  system_prompt_mode: append
  trigger_prompt: config/prompts/triggers/intel.md
  pre_process: collect_feeds               # Python: fetch RSS before agent call

research:
  model_key: research
  working_dir: root
  mcp_servers: [workiq]
  agents: []
  system_prompt: config/prompts/system/research.md
  system_prompt_mode: append
  trigger_prompt: config/prompts/triggers/research.md
  pre_process: null

chat:
  model_key: chat
  working_dir: root
  mcp_servers: [playwright]                # No workiq — delegates to m365-query agent
  agents: [pulse-reader, m365-query]
  system_prompt: config/prompts/system/chat.md
  system_prompt_mode: replace              # Full replacement — not "GitHub Copilot CLI"
  trigger_prompt: null                     # Chat mode uses user's message as trigger
  excluded_tools: [fetch_copilot_cli_documentation]
  user_input_handler: telegram             # Enable ask_user → Telegram relay

transcripts:
  standalone: true                         # No SDK session — pure Python
  handler: collectors.transcripts          # Python module to call directly
```

---

## Prompt template system

Simple `{{variable}}` interpolation. No Jinja2 dependency.

```python
# sdk/prompts.py
def load_prompt(path: str, variables: dict = {}) -> str:
    """Load a prompt from a config file and interpolate {{variables}}."""
    text = Path(path).read_text(encoding="utf-8")
    for key, value in variables.items():
        text = text.replace("{{" + key + "}}", str(value))
    return text
```

Template example (`config/prompts/triggers/digest.md`):
```markdown
Generate a SHORT daily digest for {{date}}. MAX 50 lines — only things I haven't dealt with yet.

WorkIQ query window: **{{workiq_window}}** (only query for NEW activity in this period).

## Your Priorities
{{priorities}}
{{dismissed_block}}
{{notes_block}}
{{carry_forward}}

## Part A — Local Content (already collected)
{{content_sections}}
{{articles_block}}

## Output Rules
(inlined — formerly loaded from config/instructions/digest-output-rules.md)
...
```

The Python code builds the variable dict:
```python
variables = {
    "date": date_str,
    "workiq_window": workiq_window,
    "priorities": priorities_str,
    "content_sections": content_block,
    ...
}
prompt = load_prompt("config/prompts/triggers/digest.md", variables)
```

---

## Agent definition format

Each agent is a `.md` file with YAML front matter:

```markdown
---
name: m365-query
display_name: M365 Query
description: >
  Queries Microsoft 365 data via WorkIQ — emails, calendar, Teams messages,
  people, and documents. Delegate when you need LIVE data from Outlook, Teams,
  or calendar that isn't in local reports.
mcp_servers: [workiq]
infer: true
---

You are the M365 Query agent — a specialist in retrieving Microsoft 365 data via WorkIQ.

## What You Can Query
- Emails (inbox, sent, threads)
- Calendar (meetings, attendees, agendas)
...
```

`sdk/agents.py` loads these and builds `CustomAgentConfig` dicts:
```python
def load_agent(name: str, config: dict) -> CustomAgentConfig:
    """Load an agent definition from config/prompts/agents/{name}.md."""
    path = CONFIG_DIR / "prompts" / "agents" / f"{name}.md"
    front_matter, prompt = parse_front_matter(path)
    return {
        "name": front_matter["name"],
        "display_name": front_matter["display_name"],
        "description": front_matter["description"],
        "prompt": prompt,
        "mcp_servers": {s: _mcp_config(s) for s in front_matter.get("mcp_servers", [])},
        "infer": front_matter.get("infer", True),
    }
```

---

## Detailed move map

### Phase 1: `core/`

#### `core/constants.py` (NEW — ~20 lines)

Source: extracted from `main.py:22-24`, `session.py:14-17`, `tools.py:10-13`, `utils.py:20-21`, `telegram_bot.py:23`, `config.py:8-9`

```python
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent  # src/core/ → project root
SRC_DIR = PROJECT_ROOT / "src"
CONFIG_DIR = PROJECT_ROOT / "config"
INPUT_DIR = PROJECT_ROOT / "input"
OUTPUT_DIR = PROJECT_ROOT / "output"
LOGS_DIR = PROJECT_ROOT / "logs"
TASKS_DIR = PROJECT_ROOT / "tasks"
PROMPTS_DIR = CONFIG_DIR / "prompts"
INSTRUCTIONS_DIR = CONFIG_DIR / "instructions"
```

#### `core/state.py` (NEW — ~25 lines)

Source: pattern from `digest.py:31-41`, `intel.py:25-33`, `tools.py:118-126`

```python
def load_json_state(path: Path, default: dict) -> dict:
    """Load JSON state file, returning default if missing/corrupt."""
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return default.copy()

def save_json_state(path: Path, data: dict):
    """Save JSON state file, creating parent dirs as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
```

#### `core/logging.py` (MOVED from `utils.py:1-93`)

Contains: `_JsonFormatter`, `safe_encode`, `setup_logging`, `new_run_id`, `log`, `log_event`

Changes:
- Rename `_safe_encode` → `safe_encode` (public — used by transcripts, tools)
- Import constants from `core.constants`

#### `core/config.py` (MOVED from `config.py`)

Changes:
- Import `CONFIG_DIR`, `TASKS_DIR` from `core.constants` instead of computing them
- No other changes needed — already clean

#### `core/browser.py` (MOVED from `browser.py`)

Changes:
- Import `log` from `core.logging` instead of `logging.getLogger`

### Phase 2: Externalize prompts (DONE)

**Key decision:** All `.md` files are self-contained text. `{{variables}}` are used ONLY for
runtime config values (dates, priorities, content). No file-loading variables — no `.md` file
that just loads another `.md` file. The entire `config/instructions/` folder is deleted; its
content is merged directly into the corresponding `config/prompts/` files.

#### Agent prompts (4 files — DONE)

| Source | Destination |
|--------|------------|
| `session.py:85-114` (pulse-reader) | `config/prompts/agents/pulse-reader.md` |
| `session.py:117-147` (m365-query) | `config/prompts/agents/m365-query.md` |
| `session.py:150-170` (digest-writer) | `config/prompts/agents/digest-writer.md` |
| `session.py:193-232` (signal-drafter) | `config/prompts/agents/signal-drafter.md` |

#### System prompts (7 files — DONE, self-contained with merged instructions)

| Source | Destination | Notes |
|--------|------------|-------|
| `session.py:335-340` | `config/prompts/system/base.md` | Identity + rules |
| `session.py:342-360` + `instructions/triage.md` | `config/prompts/system/monitor.md` | Full triage workflow merged in |
| `session.py:361-369` + `instructions/digest.md` | `config/prompts/system/digest.md` | Full digest instructions merged in |
| `session.py:370-372` + `instructions/intel.md` | `config/prompts/system/intel.md` | Full intel instructions merged in |
| `session.py:373-375` + `instructions/research.md` | `config/prompts/system/research.md` | Full research instructions merged in |
| `instructions/chat.md` | `config/prompts/system/chat.md` | Full chat persona merged in |
| `session.py:384-510` | `config/prompts/system/transcripts.md` | Full DOM scraping workflow |

#### Trigger prompts (4 files — DONE)

| Source | Destination | Notes |
|--------|------------|-------|
| `monitor.py:21-28` | `config/prompts/triggers/monitor.md` | Plain text, no variables |
| `digest.py:370-383` + `instructions/digest-output-rules.md` | `config/prompts/triggers/digest.md` | Output rules merged in (not loaded via variable) |
| `intel.py:177-214` | `config/prompts/triggers/intel.md` | |
| `main.py:64-76` | `config/prompts/triggers/research.md` | |

#### Other (DONE)

| Source | Destination |
|--------|------------|
| `telegram_bot.py:171-178` | `config/prompts/telegram-welcome.md` |
| `instructions/task.md` | `config/prompts/task.md` (reference doc for OneDrive, not an LLM prompt) |

#### Deleted (merged into above)

| File | Merged into |
|------|-------------|
| `config/instructions/triage.md` | `config/prompts/system/monitor.md` |
| `config/instructions/digest.md` | `config/prompts/system/digest.md` |
| `config/instructions/digest-output-rules.md` | `config/prompts/triggers/digest.md` |
| `config/instructions/intel.md` | `config/prompts/system/intel.md` |
| `config/instructions/research.md` | `config/prompts/system/research.md` |
| `config/instructions/chat.md` | `config/prompts/system/chat.md` |
| `config/instructions/task.md` | `config/prompts/task.md` |

### Phase 3: `sdk/`

#### `sdk/prompts.py` (NEW — ~20 lines)

- `load_prompt(path, variables)` — load `.md` file, interpolate `{{variables}}`
- No `load_instruction` needed — all instructions merged directly into prompt files

#### `sdk/agents.py` (NEW — ~80 lines)

- `parse_front_matter(path)` — split YAML front matter from markdown body
- `load_agent(name, config)` → `CustomAgentConfig` dict
- `load_agents(names, config)` → list of agent configs
- MCP config helpers: `workiq_mcp_config()`, `playwright_mcp_config(config)`

Source: agent definition loading replaces `session.py:74-232`

#### `sdk/session.py` (REWRITTEN — ~80 lines)

- `build_session_config(config, mode_cfg, ...)` — reads from `modes.yaml`, no if/elif
- `auto_approve_handler()` — from `session.py:40-42`
- `make_user_input_handler()` — from `session.py:45-67`

Source: replaces `session.py:239-318` (the big if/elif chain becomes a config lookup)

#### `sdk/runner.py` (NEW — ~60 lines)

- `run_job(client, config, mode, context, telegram_app, chat_id)` — unified entry point
- Loads mode config from `modes.yaml`
- Runs pre-processor if defined
- Builds prompt from template + variables
- Creates session via `agent_session` context manager
- Returns response

Source: replaces `run_stage()` from `main.py:35-48`, `run_digest()` orchestration from `digest.py:222-258`, `run_intel()` orchestration from `intel.py:123-151`, `run_monitoring_cycle()` from `monitor.py:9-37`, `run_single_research()` from `main.py:51-79`

#### `sdk/tools.py` (MOVED from `tools.py`)

Changes:
- Import constants from `core.constants`
- Import `load_json_state`/`save_json_state` from `core.state` (replaces `_load_actions`/`_save_actions`)
- Remove `_load_actions`/`_save_actions` (but keep ACTIONS_FILE path + export `load_actions` for digest)

### Phase 4: `collectors/`

#### `collectors/extractors.py` (EXTRACTED from `digest.py:48-136`)

Contains:
- `_extract_plaintext`, `_extract_docx`, `_extract_pptx`, `_extract_pdf`, `_extract_xlsx`
- `EXTRACTORS` registry dict
- `extract_text(filepath)` — public entry point (renamed from `_extract_text`)

Changes:
- Fix `callable` → `Callable` type hint
- Import `log` from `core.logging`

#### `collectors/content.py` (EXTRACTED from `digest.py:139-217`)

Contains:
- `collect_content(config)` — scan folders, extract text, track incremental state

Changes:
- Import `extract_text` from `collectors.extractors`
- Import `load_json_state`/`save_json_state` from `core.state`
- Import constants from `core.constants`

#### `collectors/feeds.py` (EXTRACTED from `intel.py:25-118`)

Contains:
- `_article_id(title, link)` — hash for dedup
- `collect_feeds(config)` — fetch RSS, deduplicate, filter by recency

Changes:
- Move `import re` to module level (currently inside a for loop at `intel.py:87`)
- Import `load_json_state`/`save_json_state` from `core.state`
- Import constants from `core.constants`

#### `collectors/transcripts/js_snippets.py` (EXTRACTED from `transcripts.py:22-89`)

Contains: `COLLECT_VISIBLE_JS`, `FIND_SCROLL_CONTAINER_JS`, `SCROLL_TO_JS`, `GET_TOTAL_ITEMS_JS`

#### `collectors/transcripts/navigation.py` (EXTRACTED from `transcripts.py:256-358`)

Contains:
- `return_to_calendar(page, iframe, force)`
- `get_iframe_text(page)`
- `find_meeting_buttons(page, iframe)` — with SKIP_KEYWORDS as a module constant

#### `collectors/transcripts/extraction.py` (EXTRACTED from `transcripts.py:361-593`)

Contains:
- `extract_meeting_transcript(page, iframe, meeting_name)` → `(str | None, bool)`
- `scroll_and_extract(frame)` — virtualized list scroll+collect
- `clean_transcript(raw_entries)` — text parsing
- `find_transcript_frame(page)` — frame locator

#### `collectors/transcripts/collector.py` (EXTRACTED from `transcripts.py:105-253`)

Contains:
- `run_transcript_collection(client, config)` — main orchestrator loop
- `_slugify(text)` helper

Imports from: `navigation`, `extraction`, `js_snippets`, `core.logging`, `core.constants`

### Phase 5: `daemon/`

#### `daemon/worker.py` (EXTRACTED from `main.py:35-157`)

Contains:
- `job_worker(client, config, job_queue, telegram_app)` — queue consumer
- `_get_latest_monitoring_report()` — read most recent report

Changes:
- `run_stage` and `run_single_research` replaced by `run_job` from `sdk/runner.py`
- `run_chat_query` stays here (it's worker-specific: creates session, returns text)
- Import `mark_task_completed` from `core.config`

#### `daemon/heartbeat.py` (EXTRACTED from `main.py:266-351, 467-480, 295-313`)

Contains:
- `heartbeat(config, job_queue, shutdown_event)` — periodic triage enqueue
- `is_office_hours(config)` — renamed from `_is_office_hours`, now public (for tests)
- `parse_interval(interval_str)` — renamed from `_parse_interval`, now public
- `check_missed_digest(job_queue)` — renamed from `_check_missed_digest`

#### `daemon/sync.py` (EXTRACTED from `main.py:175-263`)

Contains:
- `sync_jobs_from_onedrive(config, job_queue)` — pull jobs
- `sync_to_onedrive(config)` — push output files

Changes:
- Import constants from `core.constants`

### Phase 6: `telegram/`

#### `telegram/bot.py` (REWRITTEN from `telegram_bot.py`)

**Key change:** Replace module-level globals with a `TelegramBot` class.

```python
class TelegramBot:
    def __init__(self, config: dict, job_queue: asyncio.Queue):
        self.config = config
        self.job_queue = job_queue
        self.state_file = self._resolve_state_file()
        self.pending_confirmations: dict[int, asyncio.Future] = {}
        self.app: Application | None = None

    async def start(self) -> Application | None: ...
    async def stop(self): ...
    async def notify(self, chat_id: int, text: str): ...
    # ... handlers as methods
```

Contains:
- `TelegramBot` class — all state as instance attributes
- `_QUICK_ACTIONS` dict — module constant (stateless)
- Message chunking as a private method (deduplicated)

Source: full rewrite of `telegram_bot.py` to eliminate globals

#### `telegram/confirmations.py` (EXTRACTED from `telegram_bot.py:32-56`)

Contains:
- `has_pending_confirmation(pending, chat_id)`
- `resolve_confirmation(pending, chat_id, answer)`
- `wait_for_confirmation(pending, chat_id, timeout)`

Takes `pending` dict as parameter instead of module global.

### Phase 7: `main.py` (REWRITTEN — ~100 lines)

Becomes a thin entry point:
1. Parse CLI args
2. Load config
3. Start GHCP SDK client
4. If `--once --mode X`: call `run_job()` and exit
5. If daemon mode: start Telegram bot, start heartbeat, start worker, wait for shutdown

All logic moved to `daemon/`, `sdk/`, `telegram/`.

---

## Implementation order

Each phase is independently testable. Commit after each phase.

1. **Create folder structure** — empty `__init__.py` files
2. **Phase 1: `core/`** — constants, state, logging, config, browser
3. **Phase 2: Externalize prompts** — create all config/prompts/ files
4. **Phase 3: `sdk/`** — prompts.py, agents.py, session.py, tools.py, runner.py
5. **Phase 4: `collectors/`** — extractors, content, feeds, transcripts/
6. **Phase 5: `daemon/`** — worker, heartbeat, sync
7. **Phase 6: `telegram/`** — bot class, confirmations
8. **Phase 7: `main.py`** — rewrite entry point
9. **Phase 8: Cleanup** — delete old files, update CLAUDE.md architecture section, fix AGENTS.md
10. **Phase 9: Tests** — unit tests for all pure functions
11. **Phase 10: Verify** — run daemon, test Telegram, check imports

---

## Security fixes (do in Phase 1)

1. **Bot token** — change `standing-instructions.yaml:93` from hardcoded token to `$TELEGRAM_BOT_TOKEN`
2. **Hardcoded path** — delete `USER_DATA_DIR` constant from `transcripts.py:19` (config handles it)
3. **AGENTS.md** — remove `send_email` and `create_task` (not implemented)
4. **`allowed_users: []`** — add warning in `validate_config` when empty

---

## Test strategy

### What's testable (pure functions)

| Module | Functions | Test approach |
|--------|----------|--------------|
| `core/config.py` | `load_config`, `validate_config`, `_expand_env_vars` | Temp YAML files, env var mocking |
| `core/state.py` | `load_json_state`, `save_json_state` | Temp files, corrupt file handling |
| `core/logging.py` | `safe_encode`, `_JsonFormatter.format` | Direct string/record testing |
| `sdk/prompts.py` | `load_prompt` | Temp .md files with {{variables}} |
| `sdk/agents.py` | `parse_front_matter`, `load_agent` | Temp .md files with YAML front matter |
| `sdk/tools.py` | `log_action`, `write_output`, `queue_task`, `dismiss_item`, `add_note` | Temp dirs, verify file output |
| `collectors/extractors.py` | Each extractor + `extract_text` dispatch | Test files in `tests/fixtures/` |
| `collectors/content.py` | `collect_content` | Temp dirs with test files |
| `collectors/feeds.py` | `_article_id`, dedup logic, state pruning | Mock feedparser responses |
| `collectors/transcripts/extraction.py` | `clean_transcript` | Raw entry lists → expected output |
| `daemon/heartbeat.py` | `is_office_hours`, `parse_interval` | Various times + interval strings |
| `daemon/sync.py` | `sync_to_onedrive`, `sync_jobs_from_onedrive` | Temp dirs |
| `telegram/bot.py` | `_match_quick_action`, `_is_authorized` | Direct value testing |
| `telegram/confirmations.py` | `wait_for_confirmation`, `resolve_confirmation` | asyncio Future testing |

### Fixtures needed

```
tests/fixtures/
├── sample.txt              # Plain text
├── sample.docx             # Word document
├── sample.pptx             # PowerPoint
├── sample.pdf              # PDF
├── sample.xlsx             # Excel
├── sample.eml              # Email
├── config.yaml             # Minimal valid config
├── transcript_entries.json # Raw DOM entries for clean_transcript tests
└── rss_response.xml        # Sample RSS feed
```

### What needs mocking (integration tests, lower priority)

- `CopilotClient` / `Session` — for SDK runner tests
- `feedparser.parse` — for feeds.py tests
- `telegram.ext.Application` — for bot tests
- Playwright `Page`/`Frame` — for transcript navigation tests

---

## Metrics (before → after)

| Metric | Before | After |
|--------|--------|-------|
| Largest file | 608 lines (`transcripts.py`) | ~150 lines (`collectors/transcripts/collector.py`) |
| Files > 300 lines | 5 | 0 |
| Prompt text in Python | ~340 lines | 0 lines |
| Path constant definitions | 12 across 6 files | 1 in `constants.py` |
| JSON state helpers | 3 duplicated pairs | 1 in `state.py` |
| Module-level globals | 3 in `telegram_bot.py` | 0 (class instance) |
| Dead code files | 2 (`researcher.py`, `diagnose_transcript.py`) | 0 |
| Unit tests | 0 | ~30 test functions |
