# Lessons from Octoclaw for gbb-pulse

Date: 2026-02-19 (updated with code-level analysis)

## Purpose
Capture what Octoclaw does better than `gbb-pulse` (both GHCP SDK agent systems), and define practical improvements we can apply in this repo. Combines strategic analysis with line-by-line code comparison.

---

## Executive Summary
Octoclaw is ahead in **platform maturity** (packaging/CI), **extensibility** (plugin lifecycle), **operational hardening** (security preflight and stronger runtime controls), and **runtime code quality** (message routing, session resilience, typing indicators, smart splitting).

`gbb-pulse` is already strong in:
- clear mode-driven orchestration (`config/modes.yaml` + unified runner),
- practical WorkIQ + Playwright integration,
- focused business use case (knowledge-worker digest/triage),
- local-first output flow for M365 Copilot discoverability,
- transcript collection (Teams DOM scraping — Octoclaw has nothing like this).

The biggest opportunity is to preserve Pulse's strong core flow while fixing runtime code quality gaps and adding Octoclaw-style capabilities.

---

## Part A: Code-Level Quality Gaps (from side-by-side read)

### 1) Message Routing — Hardcoded Keywords vs Slash Commands + LLM
**Pulse (BAD):** `src/tg/bot.py` uses `_KEYWORD_PATTERNS` with substring matching. "create new digest" falls through to chat mode because keyword matching is fragile. User explicitly flagged this as unacceptable.

**Octoclaw:** `commands.py` has a `CommandDispatcher` with registered slash commands (`/status`, `/memory`, `/plugins`). Everything else goes to the LLM — zero keyword matching. The LLM decides intent.

**Fix:** Delete `_KEYWORD_PATTERNS` and `_match_quick_action` entirely. Add slash commands for admin ops (`/digest`, `/triage`, `/intel`, `/transcripts`, `/status`, `/latest`). All natural language → `type: "chat"` job for LLM. Update chat system prompt so the agent knows to use `queue_task` when user wants a job.

**Files:** `src/tg/bot.py`

### 2) Typing Indicators While Agent Works
**Pulse (BAD):** User sends message, sees nothing until full response arrives (could be 30-60s). Looks broken.

**Octoclaw:** `message_processor.py` runs a `ChatAction.TYPING` loop in a background task while the agent works. User sees "typing..." immediately.

**Fix:** Add typing indicator loop in `_handle_message`. Start it when message received, cancel when response ready.

**Files:** `src/tg/bot.py`

### 3) Smart Message Splitting
**Pulse (BAD):** `_send_chunked` splits at exactly 4000 chars — can break mid-word, mid-sentence, mid-code-block.

**Octoclaw:** `split_message` in `message_processor.py` splits at newlines first, then spaces, then hard-cuts only as last resort. Preserves readability.

**Fix:** Port Octoclaw's newline-then-space splitting logic into `_send_chunked`.

**Files:** `src/tg/bot.py`

### 4) Agent Start Retry + Session Recovery
**Pulse (BAD):** `runner.py` creates one session. If CopilotClient.start() fails (CLI not running, network blip), the entire job fails with no retry. If session dies mid-conversation, no recovery.

**Octoclaw:** `agent.py` has `_start_with_retry(max_attempts=3)` with exponential backoff. Session recovery catches "Session not found" errors and recreates the session transparently.

**Fix:** Add retry wrapper around `CopilotClient.start()` and `create_session()`. Add session recovery in the send path.

**Files:** `src/sdk/runner.py`, `src/sdk/session.py`

### 5) Concurrency Safety — asyncio.Lock
**Pulse (BAD):** No lock on agent sends. If heartbeat triage and a Telegram chat message fire simultaneously, two `send_and_wait` calls can race on the same session.

**Octoclaw:** `agent.py` uses `asyncio.Lock()` around all agent interactions. One message at a time.

**Fix:** Add `self._lock = asyncio.Lock()` in worker/runner. Wrap `send_and_wait` calls with `async with self._lock`.

**Files:** `src/daemon/worker.py` or `src/sdk/runner.py`

### 6) Startup Diagnostics
**Pulse (BAD):** If config is missing keys, WorkIQ isn't installed, or Copilot CLI isn't on PATH, you find out when the first job crashes. No pre-flight check.

**Octoclaw:** Has explicit setup/preflight checks for auth, endpoint exposure, and integration readiness. Warns before anything runs.

**Fix:** Add `src/core/diagnostics.py` — check config completeness, Copilot CLI availability (`copilot --version`), WorkIQ availability, browser profile existence. Run at daemon startup, log warnings.

**Files:** `src/core/diagnostics.py` (new), `src/main.py`

### 7) Event Handler — Dispatch Table vs Lambda Chain
**Pulse (OK but fragile):** `runner.py` registers event handlers with inline lambdas. Adding new event types means editing the registration block.

**Octoclaw:** `event_handler.py` uses a clean dispatch table pattern:
```python
_HANDLERS = {
    SessionEventType.ASSISTANT_MESSAGE_DELTA: _handle_delta,
    SessionEventType.TOOL_EXECUTION_START: _handle_tool_start,
    SessionEventType.TOOL_EXECUTION_COMPLETE: _handle_tool_complete,
}
```
Easy to extend, easy to test individual handlers.

**Fix:** Extract event handlers into `src/sdk/event_handler.py` with dispatch table. Each handler is a standalone function, testable in isolation.

**Files:** `src/sdk/event_handler.py` (new), `src/sdk/runner.py`

### 8) Stream Deltas to Telegram
**Pulse (BAD):** Waits for full `send_and_wait` completion, then sends entire response at once. For long responses (research, digest), user waits minutes with no feedback.

**Octoclaw:** Accumulates `ASSISTANT_MESSAGE_DELTA` events and periodically edits the Telegram message to show progressive output.

**Fix:** For Telegram-sourced chat jobs, use streaming mode: send initial "thinking..." message, then edit it as deltas arrive (throttled to ~1 edit/sec to avoid rate limits).

**Files:** `src/tg/bot.py`, `src/sdk/runner.py`

---

## Part B: Strategic / Platform Gaps (from architectural comparison)

### 9) CI Workflow
**Pulse:** No CI. Tests only run locally via Claude Code hook.
**Octoclaw:** GitHub Actions for backend/frontend/docker validation on every PR.

**Fix:** Add `.github/workflows/ci.yml` — run `pytest` on push/PR. Optional lint step.

### 10) Scheduler as First-Class Capability
**Pulse:** Heartbeat is a fixed 30-min interval. No user-configurable scheduling.
**Octoclaw:** Persistent `Scheduler` with cron + one-shot tasks. Agent tools to create/list/cancel. Guardrails (min interval). State survives restarts.

**Fix:** Add `src/core/scheduler.py` with persistent JSON state. Add `schedule_task`, `list_scheduled_tasks`, `cancel_task` tools. Integrate into worker.

### 11) Plugin Lifecycle (Post-Submission)
**Pulse:** All capabilities are core code. Adding a new mode means editing multiple files.
**Octoclaw:** Plugin metadata, dependency declarations, persistent state, declarative activation.

**Fix:** Phase 2 — add plugin-style extension layer. Move optional features behind plugin activation.

### 12) State Management Discipline (Post-Submission)
**Pulse:** State is scattered — `.chat-state.json` in bot, `.processed-files.json` in collectors, `.digest-actions.json` in tools. No unified pattern.
**Octoclaw:** Runtime data and plugin state separated, consistently persisted, isolated namespaces.

**Fix:** Phase 2 — unify state management pattern. Not urgent but prevents technical debt.

### 13) Wider Test Coverage (Ongoing)
**Pulse:** 102 tests, good coverage of core modules. Missing: scheduler, diagnostics, event handler, session recovery.
**Octoclaw:** Broader subsystem tests including scheduler, proactive loop, plugin registry, MCP server behavior.

**Fix:** Add tests for each new module as it's built.

---

## Where gbb-pulse Is Stronger (Keep These)
- Mode definitions are clean and readable (`config/modes.yaml`).
- Unified runner architecture in `src/sdk/runner.py` is straightforward.
- Domain fit is sharper (digest/triage/research for knowledge workers).
- Transcript collection is deeply practical — Octoclaw has nothing like it.
- Teams inbox scanning via Playwright fills a real WorkIQ gap.
- Contest narrative and output design are focused and credible.
- Local-first architecture — Octoclaw is container-only, can't do browser automation.

**Recommendation:** Keep these strengths. Do not over-generalize into a broad "everything agent."

---

## Consolidated Priority Plan

### NOW — Before Next Demo (fixes 1-6)

| # | Fix | Files | Effort |
|---|-----|-------|--------|
| 1 | Kill keyword matching, add slash commands, route NL to LLM | `src/tg/bot.py` | 1hr |
| 2 | Typing indicators while agent works | `src/tg/bot.py` | 30min |
| 3 | Smart message splitting (newline/space-aware) | `src/tg/bot.py` | 30min |
| 4 | Agent start retry (3 attempts) + session recovery | `src/sdk/runner.py`, `src/sdk/session.py` | 1hr |
| 5 | asyncio.Lock on agent sends | `src/daemon/worker.py` or `src/sdk/runner.py` | 30min |
| 6 | Startup diagnostics | `src/core/diagnostics.py` (new), `src/main.py` | 1hr |

### THIS WEEK — Before Submission (fixes 7-10)

| # | Fix | Files | Effort |
|---|-----|-------|--------|
| 7 | Event handler dispatch table | `src/sdk/event_handler.py` (new), `src/sdk/runner.py` | 1hr |
| 8 | Stream deltas to Telegram | `src/tg/bot.py`, `src/sdk/runner.py` | 2hr |
| 9 | CI workflow (pytest on push/PR) | `.github/workflows/ci.yml` | 30min |
| 10 | Scheduler primitives | `src/core/scheduler.py` (new), `src/sdk/tools.py`, `src/daemon/worker.py` | 3hr |

### POST-SUBMISSION — If Time Allows (fixes 11-13)

| # | Fix | Files | Effort |
|---|-----|-------|--------|
| 11 | Plugin lifecycle | `src/core/plugin_state.py` (new), `src/sdk/session.py` | 1wk |
| 12 | Unified state management | Multiple | 2-3 days |
| 13 | Expanded test coverage | `tests/` | Ongoing |

---

## Design Constraints While Adopting
- Do not dilute Pulse's core mission (signal extraction for busy knowledge workers).
- Keep deterministic collection separate from LLM reasoning.
- Preserve local-first data handling and auditability.
- Prefer additive modules over deep rewrites.
- Don't turn Pulse into Octoclaw — borrow patterns, not architecture.

---

## OctoClaw Key File Reference (for porting code)

| Pattern | OctoClaw File | Purpose |
|---------|--------------|---------|
| Slash commands | `app/runtime/messaging/commands.py` | `CommandDispatcher` with registered handlers |
| Message processing | `app/runtime/messaging/message_processor.py` | Typing indicators, smart splitting |
| Agent retry/recovery | `app/runtime/agent/agent.py` | `_start_with_retry`, session recovery |
| Event dispatch | `app/runtime/agent/event_handler.py` | Dispatch table for session events |
| Scheduler | `app/runtime/scheduler.py` | Persistent cron + one-shot tasks |
| One-shot sessions | `app/runtime/agent/one_shot.py` | Ephemeral CopilotClient for background work |
| Plugin system | `app/runtime/plugins/plugins.py` | Plugin registry with state persistence |
| Typed settings | `app/runtime/config/settings.py` | Singleton Settings class |

OctoClaw repo location: `C:\dev\octoclaw`
