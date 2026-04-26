# Knowledge Mining — Archive & Discover — {{date}}

## Phase Context

{{lookback_note}}

## Recent Artifacts (since last knowledge run)

{{recent_artifacts}}

## Inbox Snapshots (for cross-reference)

### Teams Inbox (unread)
{{teams_inbox_block}}

### Outlook Inbox (unread)
{{outlook_inbox_block}}

## Your Tasks

Delegate to the **knowledge-miner** agent to execute:

### 1. Archive Communications (via WorkIQ)
- Fetch emails from the last {{lookback_window}} where I am in the TO field
- Fetch Teams messages from the last {{lookback_window}}
- Save each as a `.md` file using `write_output` (check for duplicates first)

### 2. Discover New Projects
Look for recurring names, companies, or initiatives across:
- Recent transcripts (listed above)
- Newly archived emails
- Newly archived Teams messages
- Calendar events (via WorkIQ)

If you find a customer/initiative mentioned 2+ times across different sources with no project file, create one using `update_project`.
{{msx_instructions}}

## Output

When done, summarize what you archived:
- How many emails archived (with subjects)
- How many Teams messages archived
- Any new projects discovered

Write this summary to `knowledge-run-{{date}}-archive.md` using `write_output`.
