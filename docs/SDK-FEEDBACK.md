SDK Feedback - PulseAgent (https://aka.ms/pulse-agent)

What worked well:
- @define_tool with Pydantic schemas — built 15 tools, clean and testable
- MCP server integration — WorkIQ + Dataverse, multiple per session
- Session hooks (pre/post tool use, error, session end) — automatic audit trail, guardrails, error recovery
- Multi-model routing per session — GPT-4.1 for triage, Claude Sonnet for digest, Claude Opus for research
- Sub-agent composition from markdown + YAML front-matter

What needs sorting:
- PermissionHandler with wrong signature (1 param vs 2) silently denies all tool calls — no error, no log, cost ~4 hours
- MCPLocalServerConfig needs tools=["*"] to expose tools — not documented anywhere
- Sub-agent MCP inheritance broken (CLI #693) — session-level MCP servers unavailable to sub-agents
- SessionConfig is a TypedDict not a class — misleading, every dev tried to instantiate it
- send_and_wait() timeout gives no partial results, unclear session state — we built EventHandler + asyncio.wait_for
- No on_delta callback for streaming — have to manually filter SESSION_EVENT for ASSISTANT_MESSAGE_DELTA
- Windows Unicode crashes in SDK debug output — had to wrap everything in encode("ascii", "replace")
