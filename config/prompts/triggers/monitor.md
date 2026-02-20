Run a 30-minute triage cycle NOW. Focus on the LAST 60 MINUTES only — not the whole day.

## Teams Inbox Scan (from Playwright — real-time)

{{teams_inbox}}

## Your Task

Priority order: Teams messages first (from scan above + WorkIQ), then emails, then upcoming meetings.

### Step 0: Search Local Context
Before triaging, use `search_local_files` to look up any names or topics from the Teams inbox scan in your local transcripts. This gives you meeting context about what was recently discussed with each person.

### Step 1: Process Teams Inbox Results
For EACH unread Teams message from the scan above:
1. Search local files for the sender's name and any keywords from the message preview
2. Query WorkIQ for additional context about the sender (who are they, recent interactions, related threads)
3. Query WorkIQ for the full message content if the preview is truncated
4. Determine: is this person waiting for MY reply?
5. If yes: draft a specific suggested reply based on the context you found

### Step 2: Check Emails
Ask WorkIQ: "Show me emails in the last 60 minutes where I am in the TO field (not CC) and someone is asking ME to do something or reply."

For each result, query for sender context and suggest an action.

### Step 3: Upcoming Meetings
Ask WorkIQ: "What meetings do I have in the next 2 hours? Any prep needed?"

Make at least 5 separate WorkIQ queries. Write the report to monitoring-YYYY-MM-DDTHH-MM.md.

If nothing new happened in the last hour AND no unread Teams messages, write "All quiet — no new items" and finish quickly.

## Output Format

After writing the monitoring report, you MUST also write a structured JSON file using `write_output` to `monitoring-YYYY-MM-DDTHH-MM.json` with this exact schema:

```json
{
  "timestamp": "YYYY-MM-DDTHH:MM",
  "items": [
    {
      "id": "<type>-<slug>",
      "type": "reply_needed|action_needed|meeting_prep|fyi",
      "priority": "urgent|high|medium|low",
      "source": "Teams: <person/channel> | Email: <sender> | Calendar: <meeting>",
      "summary": "<1-2 sentences: what they need FROM ME>",
      "context": "<brief context from transcripts/WorkIQ — what I should know before acting>",
      "suggested_actions": [
        {
          "label": "<short button label, max 30 chars>",
          "action_type": "draft_teams_reply|draft_email_reply|dismiss|schedule_followup",
          "draft": "<the actual draft message text if this is a reply action, or empty string>",
          "target": "<person name or channel>"
        }
      ]
    }
  ],
  "stats": {
    "teams_unread": 0,
    "emails_actioned": 0,
    "meetings_upcoming": 0
  }
}
```

Rules for the JSON:
- Every `reply_needed` item MUST have at least one `suggested_actions` entry with a drafted reply
- The `draft` field should be a complete, ready-to-send message (not a placeholder)
- Use context from local files and WorkIQ to make drafts specific and informed
- Keep drafts concise and professional — match the sender's tone
- Write the JSON AFTER the markdown report
