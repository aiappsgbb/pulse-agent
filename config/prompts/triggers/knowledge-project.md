# Knowledge Mining — Project: {{project_name}} — {{date}}

## Current Project State

```yaml
{{project_yaml}}
```

## Recent Artifacts Available

{{recent_artifacts}}

## Your Tasks

Delegate to the **knowledge-miner** agent to process **this single project**:

### 1. Mine, Compare & Enrich
- Search ALL local files for "{{project_name}}" and related stakeholder/company names using `search_local_files`
- **Compare every finding against the current project state above**
- If new info **contradicts** existing fields (status, risk, commitments, stakeholder roles): UPDATE the field and add an `[UPDATED]` timeline entry
- If new info **confirms** existing fields: update `last_verified` to today
- Update the project file with:
  - New timeline entries (date + event + source path)
  - New related artifacts (transcript/email/message paths + summaries)
  - Updated stakeholder info (role, last interaction date)
  - Watch queries for proactive monitoring
  - Commitment status updates (overdue if past due, done if fulfilled)

### 2. Staleness Check
- Commitments past due with `status: open` → mark `overdue`
- Commitments with no mention in 7+ days → flag `[STALE]`
- Stakeholders with no interaction in 14+ days → flag `[INFO]`

### 3. Run Watch Queries
For each `watch_queries` entry in the project:
- Ask WorkIQ about the query term
- If new activity found, update the project timeline
- If new info **contradicts** current state, update fields with `[UPDATED]` entries

### 4. Save Updated Project
Call `update_project` with the project ID `{{project_id}}` and the enriched YAML.

## Output

Briefly summarize what changed for this project (1-3 sentences). Do NOT write a separate file — just report back.
