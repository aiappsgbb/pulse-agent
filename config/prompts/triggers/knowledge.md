# Knowledge Mining Run — {{date}}

## Phase Context

{{lookback_note}}

## Part A — Active Projects (current state)

{{projects_block}}

{{commitments_summary}}

## Part B — Recent Artifacts (since last knowledge run)

{{recent_artifacts}}

## Part C — Inbox Snapshots (for cross-reference)

### Teams Inbox (unread)
{{teams_inbox_block}}

### Outlook Inbox (unread)
{{outlook_inbox_block}}

## Your Tasks

Delegate to the **knowledge-miner** agent to execute all phases:

### 1. Archive Communications (via WorkIQ)
- Fetch emails from the last {{lookback_window}} where I am in the TO field
- Fetch Teams messages from the last {{lookback_window}}
- Save each as a `.md` file using `write_output` (check for duplicates first)

### 2. Mine, Compare & Enrich Projects
For each project in Part A above:
- Search ALL local files for project name, stakeholder names, company names
- **Compare every finding against the existing project state**
- If new info **contradicts** existing fields (status, risk, commitments, stakeholder roles): UPDATE the field and add an `[UPDATED]` timeline entry explaining the change
- If new info **confirms** existing fields: update `last_verified` to today
- Update project files with:
  - New timeline entries (date + event + source path)
  - New related artifacts (transcript/email/message paths + summaries)
  - Updated stakeholder info (role, last interaction date)
  - Watch queries for proactive monitoring
  - Commitment status updates (overdue if past due, done if fulfilled)

### 3. Staleness Check & Reconciliation
- Commitments past due with `status: open` → mark `overdue`
- Commitments with no mention in 7+ days → flag `[STALE]`
- Projects with no activity in 7+ days → verify via WorkIQ, flag if unconfirmed
- Stakeholders with no interaction in 14+ days → flag `[INFO]`

### 4. Run Watch Queries
For each active project with `watch_queries`:
- Ask WorkIQ about each query term
- If new activity found, update the project timeline
- If new info **contradicts** current state, update fields with `[UPDATED]` entries
- If risk change detected, update risk_level

### 5. Discover New Projects
Look for recurring names, companies, or initiatives across:
- Recent transcripts
- Archived emails
- Archived Teams messages
- Calendar events (via WorkIQ)
If you find a customer/initiative mentioned 2+ times across different sources with no project file, create one.

## Output

When done, summarize what you did:
- How many emails archived
- How many Teams messages archived
- Which projects were updated (and what changed)
- Any **contradictions detected** and how they were resolved
- Any stale items flagged
- Any new projects discovered
- Any risk changes or overdue commitments detected

Write this summary to `knowledge-run-{{date}}.md` using `write_output`.
