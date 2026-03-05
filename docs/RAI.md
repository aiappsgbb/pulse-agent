# Responsible AI Notes — Pulse Agent

## Core Principles

Pulse Agent is designed as an **autonomous information processor** that handles sensitive enterprise data (meeting transcripts, emails, Teams messages, calendar events). Every design decision prioritizes user control, transparency, and data protection.

## Data Protection

### Where Data Goes
Pulse Agent processes content through multiple paths. It is important to understand what goes where:

- **Local only (never leaves your machine):** File scanning, text extraction from .docx/.pdf/.pptx, RSS feed fetching, Playwright browser automation (transcript scraping, inbox scanning, message sending)
- **GitHub Copilot SDK (Microsoft cloud):** Meeting transcripts, email content, Teams messages, and calendar data are sent to LLMs (Claude, GPT-4.1) via the GitHub Copilot CLI for analysis, summarization, and triage. This is the core of how the agent works — it cannot function without cloud LLM processing.
- **WorkIQ (Microsoft 365 Copilot):** Queries about your emails, calendar, Teams messages, and people are routed through the M365 Copilot data layer.
- **OneDrive sync:** All output files (digests, intel, project memory) are stored in `$PULSE_HOME` which syncs via OneDrive for Business.

**There is no third-party cloud backend** — all cloud processing goes through Microsoft/GitHub infrastructure. But content absolutely leaves your local machine. If your organization has data residency or classification requirements, evaluate whether sending meeting transcripts and email content through GitHub Copilot SDK is acceptable.

### Scoped Access
- **WorkIQ** only accesses data within the user's own M365 permission scope — meetings they attended, emails they received, Teams channels they're in
- **Playwright browser automation** uses the user's own authenticated Edge session — no service accounts, no elevated privileges
- **Local file scanning** only processes folders explicitly configured in `standing-instructions.yaml`
- **Inter-agent communication** uses explicit OneDrive file exchange — no hidden data sharing. Only configured team members can receive tasks, and all inter-agent requests/responses are logged to the audit trail

## Human-in-the-Loop Controls

### Draft-First Outbound Actions
Pulse Agent **never auto-sends** messages or emails. All outbound actions follow a draft-review-approve flow:
1. Agent produces a draft reply with suggested action
2. User reviews the draft in the TUI or notification
3. User explicitly approves before any message is sent
4. Approved actions execute via deterministic Playwright scripts (no LLM in the send path)

This separation ensures the LLM's suggestions are always reviewed before reaching recipients.

### Configurable Autonomy
Users control what the agent watches, what it flags, and what it can act on — all via `standing-instructions.yaml`:
- **Monitoring priorities** — which topics and contacts trigger alerts
- **VIP contacts** — who gets elevated attention
- **Schedule patterns** — when and how often each mode runs
- **Office hours gating** — triage only runs during configured work hours

## Transparency & Auditability

### Automatic Audit Trail (100% Coverage)
Every tool call is automatically logged via the `on_post_tool_use` session hook to `$PULSE_HOME/logs/YYYY-MM-DD.jsonl`. This is not agent-optional — the hook fires on every tool execution regardless of the agent's behavior. Each entry includes:
- Timestamp
- Tool name
- Arguments passed
- Result returned
- Session mode

### Session Metrics
The `on_session_end` hook logs session-level metrics: mode, duration, and end reason (complete/error/abort/timeout). This provides operational visibility into agent behavior patterns.

### Structured Error Logging
The `on_error_occurred` hook captures all session errors with full context, enabling post-incident analysis without requiring real-time monitoring.

## Security Guardrails

### Defense-in-Depth Path Validation
File write operations are protected at two layers:
1. **Hook layer** (`on_pre_tool_use`) — blocks `..` path traversal in `write_output` and validates project ID format in `update_project` before the tool handler executes
2. **Handler layer** — each tool's handler independently validates paths as a second defense

### Minimal Destructive Actions
The agent has no tools to delete user files, cancel meetings, or remove content. Most operations are additive:
- `write_output` creates new files
- `update_project` creates or updates (never deletes) project memory
- `dismiss_item` marks items as handled (soft state, auto-expires after 30 days)
- `sweep_inbox` marks messages as read (does not delete or archive them)

**Exceptions:**
- Transcript compression deletes the original raw `.txt` file after successfully producing a compressed `.md` version.
- Inbox sweep marks items as read (changes unread state in Teams/Outlook), but never deletes messages.

### Minimum Interval Guards
Recurring schedules enforce a minimum 5-minute interval to prevent runaway loops.

## Model Output Controls

### Structured Output Validation
Critical outputs (digest JSON, triage JSON) use defined schemas. Malformed LLM output is caught and logged rather than silently propagated.

### Multi-Model Routing
Different modes use different models optimized for their task:
- Fast models (GPT-4.1) for real-time triage and chat
- Balanced models (Claude Sonnet) for digest generation
- Powerful models (Claude Opus) for deep research

This routing ensures appropriate capability-to-task matching while managing cost and latency.

## Content Filtering & Accuracy

### Carry-Forward Staleness
Digest items older than 5 days are automatically dropped to prevent stale information from persisting indefinitely.

### Dismissed Item Expiry
User-dismissed items auto-expire after 30 days (TTL cleanup), preventing unbounded state growth.

### Cross-Reference Verification
Before surfacing items, the digest pipeline cross-references against:
- WorkIQ (has the user already replied/acted?)
- Browser inbox scans (is the item still showing as unread?)
- Previous digest (was this already surfaced and dismissed?)

### Failure Transparency
When data sources are unavailable:
- WorkIQ failures produce explicit fallback to browser scans (not silent degradation)
- Article filter failures are flagged with `UNFILTERED` warnings
- Browser unavailability returns `None` (distinct from `[]` — scanned but empty)

## Limitations & Honest Boundaries

- The agent cannot access content beyond the user's M365 permissions
- Transcript collection requires a headful browser (cannot run fully headless)
- Local file search is keyword-based, not semantic — relevance depends on keyword selection
- Browser automation is fragile — Microsoft UI updates can break selectors
- WorkIQ availability is intermittent — the system is designed to degrade gracefully, not crash
