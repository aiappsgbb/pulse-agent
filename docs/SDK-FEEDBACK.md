# GitHub Copilot SDK — Product Feedback

Feedback from building Pulse Agent, an enterprise-grade autonomous daemon using the Python GitHub Copilot SDK (`github-copilot-sdk`). This covers ~4 weeks of intensive development (Feb 2026).

## What Works Well

### 1. Tool Definition API (`@define_tool`)
The decorator-based tool definition with Pydantic parameter schemas is excellent. Clean, type-safe, and easy to test. We built 13 custom tools and the API never got in the way.

### 2. MCP Server Integration
Plugging in WorkIQ as an MCP server was straightforward. `MCPLocalServerConfig` with `tools=["*"]` just works once you know the pattern. The ability to attach multiple MCP servers to a single session is powerful.

### 3. Session Hooks
The 4-hook lifecycle (`on_pre_tool_use`, `on_post_tool_use`, `on_error_occurred`, `on_session_end`) is the right abstraction. We use them for:
- Automatic audit trail (100% tool call coverage — not agent-optional)
- Path traversal guardrails (defense-in-depth)
- Structured error logging with auto-retry
- Session metrics (duration, end reason)

Hooks being crash-proof (wrapped in try/except so failures don't disrupt sessions) is a critical design choice for production.

### 4. Multi-Model Routing
Being able to specify different models per session (`gpt-4.1` for fast triage, `claude-sonnet` for digest, `claude-opus` for deep research) is essential for production workloads. Cost/latency optimization per task is a real requirement.

### 5. Sub-Agent Composition
Loading agent definitions from markdown files with YAML front-matter lets us version-control agent behavior alongside code. Clean separation between orchestration (Python) and behavior (prompts).

## Issues & Friction Points

### 6. `PermissionHandler` Silent Failure (Critical)
**Issue:** If the `PermissionHandler` callback has the wrong signature (1 param instead of 2), ALL tool calls are silently denied. No error, no warning, no log entry.

**Impact:** This cost us ~4 hours of debugging. The agent appeared to work but never called any tools.

**Recommendation:** Validate the handler signature at registration time and raise a clear error. Or at minimum, log when a permission handler denies a request.

### 7. `MCPLocalServerConfig` — `tools` Parameter Not Documented
**Issue:** Without `tools=["*"]`, MCP server tools are registered but not exposed to the agent. This is not in the docs or examples.

**Impact:** WorkIQ was "connected" but the agent couldn't use any of its tools. Debugging this required reading SDK source code.

**Recommendation:** Add `tools=["*"]` to all MCP examples in the docs, or make it the default behavior.

### 8. CLI Process CWD vs Session `workingDirectory` Mismatch
**Issue:** The Copilot CLI process starts in the directory where `copilot-cli serve` is invoked. But sessions have a `workingDirectory` field. File-path tools sometimes resolve relative to the CLI CWD, not the session's `workingDirectory`.

**Impact:** Subtle bugs when the daemon starts from a different directory than expected. We now always use absolute paths.

**Recommendation:** Ensure all built-in tools respect `workingDirectory` from the session config.

### 9. Custom Agent MCP Servers Broken (CLI Issue #693)
**Issue:** When using sub-agents (the `agents` field in `SessionConfig`), MCP servers defined at the session level are not available to the sub-agent. The CLI returns tool-not-found errors.

**Impact:** We had to work around this by flattening agent behavior into system prompts instead of using the elegant agent composition model.

**Recommendation:** Fix the MCP server inheritance for sub-agents. This is a blocking issue for complex agent architectures.

### 10. `SessionConfig` is a `TypedDict`, Not a Class
**Issue:** The Python SDK defines `SessionConfig` as a `TypedDict` (plain dict), but the naming convention suggests a class. IDE autocompletion shows constructor-style usage that doesn't work.

**Impact:** Minor but confusing. Every new developer on the team made the same mistake.

**Recommendation:** Either make it an actual dataclass/Pydantic model, or add prominent documentation noting it's a dict.

### 11. `send_and_wait()` Timeout Behavior
**Issue:** When `send_and_wait()` times out, it's unclear what state the session is in. Can we still read partial results? Is the session recoverable?

**Impact:** We switched to event-driven completion (`EventHandler` + `asyncio.wait_for`) to have more control. This works well but required significant custom code.

**Recommendation:** Document timeout behavior explicitly. Consider adding a `partial_result` field to the timeout exception. The event-driven pattern should be a documented alternative in the SDK.

### 12. No Built-in Streaming Callback
**Issue:** Getting token-by-token streaming for progressive UI updates requires subscribing to `SESSION_EVENT` and filtering for `ASSISTANT_MESSAGE_DELTA`. There's no simple `on_delta` callback option.

**Impact:** We built a custom `StreamingReply` class for progressive Telegram/TUI updates. Works, but every SDK consumer will need something similar.

**Recommendation:** Add an optional `on_delta` callback to `send()` or `send_and_wait()`.

### 13. Unicode/Encoding Issues on Windows
**Issue:** SDK debug output and some tool results contain Unicode characters that crash on Windows terminals with default encoding (`charmap`).

**Impact:** Required wrapping all terminal output in `encode("ascii", "replace")`.

**Recommendation:** Ensure all SDK terminal output is encoding-safe, especially on Windows.

## Feature Requests

### 14. Session Pause/Resume
For long-running overnight jobs (knowledge mining, deep research), the ability to pause a session, persist state, and resume later would be valuable. Currently, if the process crashes mid-session, all context is lost.

### 15. Built-in Rate Limiting / Backpressure
When running multiple sessions or high-frequency triage cycles, there's no SDK-level rate limiting. We implemented our own job queue (one-at-a-time processing), but SDK-level concurrency controls would help.

### 16. Session Cost Tracking
Knowing the token count and estimated cost per session would help with operational budgeting. Currently we have no visibility into cost beyond model-level pricing tables.

### 17. Tool Result Streaming
Large tool results (e.g., `search_local_files` returning 50 matches) are sent as one block. Streaming tool results would improve perceived latency for the user.

## Summary

The GitHub Copilot SDK is a strong foundation for building enterprise agent systems. The tool definition API, MCP integration, and session hooks are production-ready. The main friction points are around documentation gaps (permission handlers, MCP config), sub-agent MCP inheritance (blocking bug), and Windows compatibility. With the fixes above, this SDK would be the clear best choice for enterprise agent development on the Microsoft stack.

**Rating: 8/10** — Production-capable today with workarounds. Would be 9.5/10 with the documentation and sub-agent MCP fixes.
