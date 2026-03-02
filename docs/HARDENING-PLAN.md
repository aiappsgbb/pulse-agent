# Pulse Agent Hardening Plan

Production readiness fixes before shipping to 30+ users. Ordered by priority.

## Day 1 — Data Correctness (Must Fix)

### 1. Scanner exception returns: `[]` → `None`
**Files:** `collectors/teams_inbox.py`, `collectors/outlook_inbox.py`, `collectors/calendar.py`
**Bug:** All three scanners return `[]` on exception instead of `None`. Contract: `None` = unavailable, `[]` = scanned, nothing found. Browser crash/timeout masquerades as "no unread messages" — silent data loss.
**Fix:** Change `except Exception` return from `[]` to `None` in all three `_do_scan()` wrappers.
**Test:** Add test for each scanner that mocks browser crash and verifies `None` return.

### 2. Atomic state persistence
**File:** `core/state.py`
**Bug:** `save_json_state` uses `write_text()` which truncates-then-writes. Crash mid-write = corrupt/empty file → all state lost. Affects `.digest-state.json`, `.intel-state.json`, `.transcript-state.json`, `.scheduler.json`.
**Fix:** Write to `.tmp` file, then `os.replace()` (atomic on NTFS).
**Test:** Verify temp-file-then-rename pattern. Verify corrupt `.tmp` file doesn't affect main file.

### 3. Chat name exact match for reply_to_chat
**File:** `collectors/teams_sender.py` (FIND_CHAT_IN_SIDEBAR_JS)
**Bug:** `text.includes(lower)` substring match — "John" matches "Johnson", "Team with John and Amy". Reply goes to wrong person.
**Fix:** Match on first line of innerText only, require full word boundary or exact-name match.
**Test:** Add test with multiple chats containing similar names.

### 4. Action file UUID to prevent collisions
**Files:** `sdk/tools.py:516` (teams-send), `sdk/tools.py:539` (email-reply), `sdk/tools.py:127` (queue_task), `tui/ipc.py:583` (write_reply_job), `tui/ipc.py:293` (queue_job)
**Bug:** Second-level timestamp in filenames. Two actions in same second → overwrite → lost message/job.
**Fix:** Add `uuid.uuid4().hex[:8]` suffix to all job/action filenames.
**Test:** Verify two rapid calls produce distinct files.

### 5. parse_front_matter crash protection
**File:** `sdk/agents.py:21`
**Bug:** `str.index("---", 3)` raises uncaught `ValueError` if no closing `---`. Crashes all session creation.
**Fix:** Wrap in try/except, return `({}, text)` on malformed front matter.
**Test:** Test with malformed agent file (opening `---` but no closing).

## Day 2 — User-Facing Reliability

### 6. Send confirmation in both senders
**Files:** `collectors/teams_sender.py:440`, `collectors/outlook_sender.py`
**Bug:** Press Ctrl+Enter, wait 2s, declare success. No verification message was sent.
**Fix:** After send, check compose box is empty (message cleared = sent). If still has content, report failure.
**Test:** Mock page.evaluate for compose-box-empty check.

### 7. Outlook reply: preserve quoted content
**File:** `collectors/outlook_sender.py:243`
**Bug:** `Ctrl+A + Backspace` clears entire compose area including quoted email thread.
**Fix:** Click at beginning of compose area, type message, then send. Don't clear existing content. Or use Home key to position cursor before quoted text.
**Test:** Verify quoted content preserved after message insertion.

### 8. QuestionModal: delete pending file after showing
**File:** `tui/app.py:272`
**Bug:** `read_pending_question()` doesn't delete file. After user dismisses modal, file persists → modal reappears on next 2s tick.
**Fix:** Delete `.pending-question.json` immediately after pushing QuestionModal. Or track shown session_ids.
**Test:** Verify file deleted after modal push.

### 9. browser.close() must be awaited
**File:** `core/browser.py:165`
**Bug:** `Browser.close()` is async, called without `await`. Resource leak.
**Fix:** Add `await`.
**Test:** Verify no RuntimeWarning about unawaited coroutine.

### 10. Auth-redirect detection in all scanners
**Files:** `collectors/teams_inbox.py:151`, `collectors/outlook_inbox.py`, `collectors/calendar.py`
**Bug:** Scanners don't detect login redirects. Session expires → scans return empty results as if inbox is empty.
**Fix:** Check URL for `login`/`oauth`/`microsoftonline` after navigation (same as `teams_sender._navigate_to_teams()`). Return `None` if auth expired.
**Test:** Mock page.url to return login URL, verify `None` return.

## Day 3 — Operational Resilience

### 11. Replace WMIC with PowerShell
**Files:** `core/browser.py:54`, `collectors/transcripts/collector.py:330`
**Bug:** WMIC deprecated on Windows 11, being removed. Orphan cleanup fails → locked profile → can't launch browser.
**Fix:** Use `Get-Process` or `Get-CimInstance` via PowerShell.
**Test:** Verify PowerShell command works.

### 12. Browser crash detection
**File:** `core/browser.py`
**Bug:** No mechanism to detect browser crash. Edge OOM → all scans silently return empty forever.
**Fix:** Before each scan, verify browser context alive. If dead, return `None` and log recovery needed.
**Test:** Mock dead context, verify None return.

### 13. Add logging to except blocks
**Files:** Throughout `tui/ipc.py`, `tui/app.py`
**Bug:** Blanket `except Exception: pass` with zero logging. OneDrive locks file → TUI silently stops. Zero diagnostic info.
**Fix:** Add `log.debug("...", exc_info=True)` to every bare `except` in IPC and app.
**Test:** Not unit-testable, but verify log calls present via code inspection.

### 14. File locking for .digest-actions.json
**File:** `tui/ipc.py` and `sdk/tools.py`
**Bug:** Read-modify-write race. TUI + daemon both write without locking.
**Fix:** Use `msvcrt.locking()` on Windows or atomic write pattern (read → modify → write to temp → rename).
**Test:** Concurrent write test.

### 15. Fix agent prompt template variables
**File:** `config/prompts/agents/knowledge-miner.md`
**Bug:** `{{lookback_window}}` never interpolated — `load_agent()` doesn't call `load_prompt()`. LLM sees literal template variable.
**Fix:** Either interpolate in `load_agent()` or replace `{{lookback_window}}` with a hardcoded sensible default in the prompt text.
**Test:** Verify no `{{` remains in loaded agent prompts.

## Day 4 — Test Coverage Gaps

### 16. Integration test: job_worker routing
Replace `inspect.getsource()` string-matching tests with actual mock-execution tests that verify each job type calls the right functions with correct arguments.

### 17. Scanner _do_scan() tests
Add tests for the actual scan flow (navigate, wait, extract, parse) with mocked Playwright pages.

### 18. run_job() integration test
Test the main SDK orchestration: session config → session create → prompt send → completion wait → result.

### 19. collect_content() and collect_feeds() tests
Test folder scanning, incremental state, size limits, feed dedup.

### 20. Onboarding tests
Test is_first_run(), build_config_from_answers(), write_config().

## Additional Issues (Fix When Convenient)

- Job notification overwrite: two jobs finish within 1s → first lost. Switch to append-only JSONL.
- `feedparser.parse()` has no timeout. Add `socket.setdefaulttimeout()` wrapper.
- PDF `extract_text()` called 3x per page in `extractors.py:42`. Cache result.
- Session destroy logged at DEBUG level (`session.py:225`). Raise to WARNING.
- `_build_onboarding_prompt` duplicated in `worker.py` and `tasks.py`. Consolidate.
- `knowledge-pipeline` writes `last_run` before work completes (`runner.py:839`). Move to after.
- Outlook sender `CLICK_SEARCH_RESULT_JS` matches `[role="listitem"]` too broadly.
- Transcript `entries` dict uses `ariaLabel` as key — duplicate speakers at same timestamp silently dropped.
- `query_one(Static)` in InboxPane is ambiguous — add explicit `#id` selectors.
