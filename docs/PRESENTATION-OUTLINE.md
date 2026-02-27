# Pulse Agent — Presentation Deck Outline

Contest requires 1-2 slides. Build in `presentations/PulseAgent.pptx`.

---

## Slide 1: Business Value & Solution Overview

**Title:** Pulse Agent — Your Autonomous Information Processing Engine

**Left Column: The Problem**
- 8 meetings/day, 20% retention
- 50 email threads, never read
- Competitor moves at 2 AM, missed
- Copilot helps when you ask. Nobody asks at 2 AM.

**Right Column: The Solution**
- Local-first autonomous daemon — works when you don't
- 7 modes: transcript collection, digest, triage, research, intel, chat, knowledge mining
- Cross-references what needs attention vs. what you've handled
- 30-line digest, not 400

**Bottom Banner: Key Numbers**
- 391 tests | 14 custom tools | 7 modes | 6 sub-agents | 4 session hooks

---

## Slide 2: Architecture & Microsoft Integration

**Title:** Architecture — GitHub Copilot SDK + M365 Deep Integration

**Architecture Diagram (simplified version of README diagram):**

```
Data Collection (Playwright)           GitHub Copilot SDK              Output (OneDrive)
─────────────────────────            ──────────────────────          ─────────────────────
Teams Transcripts ─────┐             CopilotClient (JSON-RPC)       digests/*.json + .md
Teams Inbox Scan ──────┤                    │                       intel/*.md
Outlook Inbox Scan ────┼──→  Pulse Agent ───┤                       projects/*.yaml
Calendar Scan ─────────┤     (Python daemon) │                      monitoring-*.json
Local Content Scan ────┤                    │                       transcripts/*.md
RSS Feeds ─────────────┘                    │                       logs/*.jsonl
                                            │
                              ┌─────────────┼─────────────┐
                              │             │             │
                         WorkIQ MCP    13 Custom     4 Session
                         (M365 data)    Tools        Hooks
                                                  (audit, guardrails,
                                                   recovery, metrics)
```

**Microsoft Integration Points (highlight these):**
- **WorkIQ MCP** — calendar, email, Teams, people, documents
- **Teams transcript collection** — Playwright browser automation of Teams web UI
- **Inbox scanning** — real-time Teams, Outlook, Calendar via Playwright
- **Browser actions** — send Teams messages, reply to emails via Playwright
- **OneDrive sync** — all output stored locally, synced via OneDrive
- **Inter-agent communication** — OneDrive-based task queue between team members
- **Multi-model routing** — GPT-4.1, Claude Sonnet, Claude Opus via SDK

**Security & RAI Callouts:**
- Draft-first (never auto-sends)
- Full automatic audit trail (session hooks)
- Local-first (no data leaves tenant)
- PII filtering on all output

---

## Design Tips

- Use dark background with high-contrast text
- Minimize text, maximize the architecture diagram
- Include a screenshot of the TUI dashboard on slide 1 if space allows
- Keep font size readable at 1080p (no smaller than 18pt for body text)
- Use Microsoft + GitHub branding where appropriate
