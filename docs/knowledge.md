# Pulse Agent Knowledge System — Vision & Gap Analysis

## Date: 2026-02-24

## The Vision

A OneDrive-based knowledge accumulation system where every transcript, email, Teams message, and document enriches a persistent, searchable, shareable knowledge base — organized by project, linked by people and topics, and accessible across team members.

**"I had 8 meetings yesterday. I was distracted in half of them. At 7 AM, Pulse Agent told me the 3 things that actually need my attention — including an escalation I completely missed. And when I asked 'what's the latest on HSBC?' it pulled together insights from 3 transcripts, 2 email threads, and a Teams channel I forgot I was in."**

---

## Current State: What Works

### Data Collection (Strong)
- **Transcript collection**: Outlook Calendar + SharePoint Stream pipeline — 34/60 transcripts from 5 weeks, 0 errors
- **Transcript compression**: Raw ~25k chars → structured ~2k chars via GHCP SDK (TLDR, decisions, action items, quotes)
- **Content scanning**: Local files (.docx, .pptx, .pdf, .xlsx, .csv, .eml) with incremental state tracking
- **RSS feeds**: Configurable sources with SDK-based relevance filtering and dedup
- **Teams inbox**: Real-time unread message scanning via Playwright
- **Outlook inbox**: Real-time unread email scanning via Playwright
- **Calendar**: Work-week event scanning with overflow expansion
- **WorkIQ**: M365 data enrichment (calendar, emails, Teams, people, documents)

### Digest Pipeline (Strong)
- 6-phase pre-processing: transcripts → content → feeds → Teams → Outlook → calendar → projects
- 4 sub-agents: m365-query, project-researcher, digest-writer, signal-drafter
- Carry-forward with 5-day window and verification against inbox scans
- Dismiss/notes persistence with 30-day TTL
- Structured JSON output for action buttons + human-readable markdown

### Project Memory (Foundation Built)
- `$PULSE_HOME/projects/*.yaml` — one file per engagement
- Schema: status, risk, stakeholders, commitments, next meeting, key dates
- Auto-discovered by project-researcher agent during digest
- Commitment tracking with overdue/due-soon alerts
- OneDrive-synced, survives across digest cycles

### Inter-Agent Communication (Basic)
- File-based async messaging via OneDrive (~60s latency)
- Team directory in standing-instructions.yaml
- Request/response YAML protocol
- Telegram notifications for incoming/completed requests

---

## Gap Analysis: Why It's Not "Million Dollar" Yet

### Gap 1: Insights Evaporate After 5 Days

The system processes 50+ transcripts, hundreds of emails, dozens of Teams threads — but most learnings **vanish after the 5-day carry-forward window** unless the agent happens to call `update_project()`.

```
Day 1: "HSBC frustrated with implementation delays" (from transcript)
Day 6: Agent has ZERO memory of this. Repeats analysis from scratch.
```

Project memory is the ONLY long-term store, and it's **agent-optional** — the LLM decides whether to persist something.

### Gap 2: No Email/Teams Message Persistence

Emails and Teams messages are collected as **ephemeral scan results** (sender, subject, preview). We don't:
- Extract full email bodies into searchable text
- Save Teams message content as knowledge artifacts
- Link messages to projects
- Track conversation threads over time

Transcripts get saved as files. Emails and Teams messages don't.

### Gap 3: No Cross-Source Correlation

Each data source is processed independently. No system says: *"Alice emailed about HSBC, Alice was in the HSBC standup yesterday, and Alice's calendar shows an HSBC escalation call Friday — this is all the same thread."*

### Gap 4: Shallow Project Memory

Current project YAML captures status snapshot but NOT:
- Timeline of key decisions (who decided what, when)
- Historical context ("3 months ago they wanted X, now they want Y")
- Meeting-by-meeting evolution
- Linked artifacts (which transcript, which email, which document)
- Relationship evolution ("Bob replaced Alice as PM in January")

### Gap 5: No Proactive Knowledge Mining

Knowledge is only updated during digest cycles. No mechanism to:
- Continuously monitor WorkIQ for project-relevant activity
- Detect when a new email arrives about a tracked project
- Cross-reference FoundryIQ/MSX/Fabric IQ for customer intel
- Proactively ask "what's new with HSBC?" across all data sources

### Gap 6: No Shared Knowledge Across Team

Each agent's knowledge is siloed:
- Agent A's `projects/` folder ≠ Agent B's
- No team-wide project registry
- No conflict detection
- No aggregated signals

---

## Architecture: Knowledge Accumulation System

### Target State

```
$PULSE_HOME/
├── knowledge/                    ← NEW: Persistent knowledge base
│   ├── projects/                 ← Moved from projects/ — richer schema
│   │   ├── hsbc-cloud-migration.yaml
│   │   └── contoso-renewal.yaml
│   ├── people/                   ← NEW: People knowledge
│   │   ├── alice-johnson.yaml
│   │   └── bob-smith.yaml
│   ├── threads/                  ← NEW: Conversation threads
│   │   ├── hsbc-quota-escalation.yaml
│   │   └── contoso-pricing-discussion.yaml
│   ├── decisions/                ← NEW: Decision log
│   │   └── 2026-02-24-hsbc-go-live-delayed.yaml
│   └── index.yaml                ← NEW: Cross-reference index
├── transcripts/                  ← Existing: meeting transcripts (.md compressed)
├── emails/                       ← ENHANCED: Full email content (not just .eml drops)
│   ├── 2026-02-24_hsbc_alice-escalation.md
│   └── 2026-02-24_contoso_bob-pricing.md
├── teams-messages/               ← NEW: Teams message content
│   ├── 2026-02-24_hsbc-channel_quota-discussion.md
│   └── 2026-02-24_alice-johnson_review-request.md
├── documents/                    ← Existing: user-dropped docs
├── digests/                      ← Existing: daily digests
├── intel/                        ← Existing: external intel
└── pulse-signals/                ← Existing: field signals
```

### Rich Project Schema (Target)

```yaml
project: "HSBC Cloud Migration"
status: active
risk_level: high
summary: "Enterprise Azure migration — Phase 2 blocked on quota"

stakeholders:
  - name: "Alice Johnson"
    role: "PM"
    org: "HSBC"
    sentiment: "frustrated with delays"
    last_interaction: "2026-02-24"
  - name: "Bob Smith"
    role: "Technical Lead"
    org: "Microsoft"

commitments:
  - what: "Resolve Azure quota issue"
    who: "You"
    to: "Alice Johnson"
    due: "2026-02-28"
    status: open
    source: "transcripts/2026-02-20_hsbc-standup.md"
    id: "commit-quota-fix"

timeline:
  - date: "2026-01-15"
    event: "Phase 1 completed successfully"
    source: "transcripts/2026-01-15_hsbc-review.md"
  - date: "2026-02-10"
    event: "Phase 2 blocked — Azure quota limits hit"
    source: "emails/2026-02-10_hsbc_alice-escalation.md"
  - date: "2026-02-20"
    event: "Escalation call — Alice frustrated with delays"
    source: "transcripts/2026-02-20_hsbc-standup.md"

related_artifacts:
  - type: transcript
    path: "transcripts/2026-02-20_hsbc-standup.md"
    summary: "Phase 2 review — quota blocking progress"
  - type: email
    path: "emails/2026-02-22_hsbc_alice-followup.md"
    summary: "Alice asking for timeline on quota resolution"
  - type: teams_message
    path: "teams-messages/2026-02-24_hsbc-channel_quota-update.md"
    summary: "Bob posted quota increase request status"

watch_queries:
  - "HSBC"
  - "Alice Johnson"
  - "Azure quota"
  - "cloud migration phase 2"

next_meeting: "2026-02-28 14:00"
key_dates:
  - date: "2026-03-15"
    event: "Phase 2 go-live deadline"
tags: [enterprise, migration, azure, escalation]
updated_at: "2026-02-24T14:30:00"
```

### Knowledge Mining Pipeline

```
                    ┌─────────────────────────────────┐
                    │   CONTINUOUS KNOWLEDGE MINING    │
                    │   (autonomous background mode)   │
                    └──────────┬──────────────────────┘
                               │
        ┌──────────────────────┼──────────────────────┐
        │                      │                      │
   ┌────▼─────┐         ┌─────▼─────┐         ┌─────▼──────┐
   │ Collect   │         │ Correlate │         │ Enrich     │
   │           │         │           │         │            │
   │ Transcripts│        │ Link same │         │ WorkIQ     │
   │ Emails    │         │ person    │         │ FoundryIQ  │
   │ Teams msgs│         │ across    │         │ MSX/CRM    │
   │ Documents │         │ sources   │         │ Fabric IQ  │
   │ Calendar  │         │           │         │            │
   └────┬──────┘         └─────┬─────┘         └─────┬──────┘
        │                      │                      │
        └──────────────────────┼──────────────────────┘
                               │
                    ┌──────────▼──────────────────────┐
                    │   KNOWLEDGE PERSISTENCE LAYER   │
                    │                                  │
                    │ • Update project timelines       │
                    │ • Update people context          │
                    │ • Track decision evolution        │
                    │ • Link artifacts to projects      │
                    │ • Detect new threads/topics       │
                    │ • Flag risk changes               │
                    └──────────┬──────────────────────┘
                               │
                    ┌──────────▼──────────────────────┐
                    │   PROACTIVE MONITORING           │
                    │                                  │
                    │ For each tracked project:        │
                    │ • "What's new with HSBC?"        │
                    │ • Check WorkIQ every cycle       │
                    │ • Surface changes immediately     │
                    │ • Update project file             │
                    └─────────────────────────────────┘
```

### Proactive Project Watchlist

Each project has `watch_queries` — keywords the system monitors across ALL data sources:

```
Every triage cycle (30 min):
  For each active project:
    → Search new transcripts for watch_queries
    → Search new emails for watch_queries
    → Search new Teams messages for watch_queries
    → Query WorkIQ: "Any activity related to {project}?"
    → Query FoundryIQ: "Customer health for {company}?"
    → If new activity found → update project timeline
    → If risk change detected → alert immediately
```

---

## Implementation Priorities

### Phase 1: Persistent Knowledge (Build First)
1. **Email archival** — Save full email content from WorkIQ/Outlook scans to `emails/`
2. **Teams message archival** — Save Teams message content to `teams-messages/`
3. **Rich project schema** — Add timeline, related_artifacts, watch_queries to project YAML
4. **Automatic knowledge extraction** — Post-digest sub-agent that mines and persists insights

### Phase 2: Cross-Correlation & Mining
5. **Cross-source entity linking** — People, companies, topics across all sources
6. **Autonomous knowledge mining mode** — Background agent that continuously updates projects
7. **Proactive project monitoring** — Watch queries checked every triage cycle

### Phase 3: Team Knowledge Layer
8. **Shared project registry** — Team-wide OneDrive folder with merged project files
9. **Cross-agent search** — Query teammates' knowledge bases
10. **Trend detection** — Weekly meta-analysis across all agents' findings

### Phase 4: External Intelligence
11. **FoundryIQ integration** — Customer health signals from Dynamics/CRM
12. **MSX/Fabric IQ** — Deal pipeline, revenue data, account health
13. **Competitive intelligence correlation** — Link RSS intel to specific project contexts

---

## Success Metrics

| Metric | Current | Target |
|--------|---------|--------|
| Knowledge persistence beyond 5 days | Project YAML only | All insights |
| Email content captured | Unread preview only | Full body + metadata |
| Teams messages captured | Unread preview only | Full content + context |
| Cross-source correlation | None (LLM ad-hoc) | Automatic entity linking |
| Project timeline depth | Current snapshot only | Full history with sources |
| Proactive monitoring | None | Watch queries per project |
| Team knowledge sharing | Task-based only | Shared registry + search |
| Data sources for "what's new with X?" | Manual search | WorkIQ + local + FoundryIQ |

---

## The Million-Dollar Insight

The gap isn't data collection — we already collect from 7+ sources. The gap is **knowledge accumulation**.

Every meeting, every email, every Teams thread should make the system smarter about your projects. Today, most of that intelligence evaporates. Tomorrow, it compounds.

**"Pulse Agent didn't just tell me about today's emails. It told me that the HSBC escalation Alice sent this morning is the third one in two weeks, that it's related to the quota issue Bob flagged in last Tuesday's standup, and that the go-live deadline is in 19 days. Here's the draft reply that references all of that context."**

That's the million-dollar system.
