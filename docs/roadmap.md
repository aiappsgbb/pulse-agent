# Pulse Agent — Future Phases

## Phase 1 (Shipped)
**Project Memory & Commitment Tracking**

- Persistent project files (`output/projects/*.yaml`) auto-discovered from content
- Commitment tracking with overdue detection
- Project-oriented digest output (grouped by engagement)
- `project-researcher` agent in digest pipeline
- `update_project` tool for agent-driven project maintenance
- OneDrive sync for project files

## Phase 2: Meeting Prep Auto-Injection

**Goal:** Before each meeting, automatically surface relevant project context, recent activity, and open commitments for attendees.

**How it could work:**
- Calendar scan identifies meetings in the next 24 hours
- For each meeting: look up attendees in project files (stakeholder matches)
- Pull recent transcript summaries involving those people
- Surface open commitments and overdue items related to the meeting topic
- Inject as a "Meeting Prep" section at the top of triage output
- Optionally push a Telegram notification 30 minutes before: "Your 2pm with Alice — 2 open commitments, 1 overdue"

**Key decisions needed:**
- Trigger: part of triage cycle or separate scheduled job?
- Output: inline in triage or separate prep document?
- How far back to look for relevant transcripts?

## Phase 3: Weekly Retrospective

**Goal:** End-of-week summary that tracks progress across projects, highlights what moved forward, what's stuck, and what's overdue.

**How it could work:**
- Scheduled for Friday afternoon (or configurable)
- Diff project files from Monday vs Friday: what changed?
- Count commitments fulfilled vs new ones added
- Identify projects with no activity (stale engagements)
- Produce a "Week in Review" markdown with velocity metrics
- Could feed into a team-level dashboard if multiple agents share project files

**Key decisions needed:**
- Separate mode or extension of digest?
- How to measure "velocity" meaningfully?
- Share across team (OneDrive) or personal only?

## Phase 4: Parallel Project Research

**Goal:** Delegate per-project deep research to concurrent sub-sessions for richer context.

**How it could work:**
- During digest, identify projects with significant new activity
- Spawn parallel GHCP SDK sessions, one per active project
- Each session gets: project file + relevant transcripts + WorkIQ queries scoped to that project
- Results merged back into the main digest
- Could use different models: fast model for simple updates, research model for complex projects

**Key decisions needed:**
- Concurrency limits (SDK session count)?
- Cost implications of multiple parallel sessions?
- How to merge parallel results without duplication?
- Model routing: same model or per-project model selection?
