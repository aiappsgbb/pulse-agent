Generate a SHORT daily digest for {{date}}. This should be MAX 50 lines — only things I haven't dealt with yet.

WorkIQ query window: **{{workiq_window}}** (only query for NEW activity in this window).

## Your Priorities
{{priorities}}
{{dismissed_block}}
{{carry_forward}}

{{collection_warnings}}
## Part A — Local Content (already collected)
{{content_sections}}
{{articles_block}}

## Part B — Inbox & Calendar Scans (live Playwright scans)

These come from real-time browser scans. They show what is ACTUALLY happening right now.
**If any scan says "SCAN UNAVAILABLE", treat that data as UNKNOWN — not zero.**

### Teams Inbox
{{teams_inbox_block}}

### Outlook Inbox
{{outlook_inbox_block}}

### This Week's Calendar
{{calendar_block}}

{{commitments_summary}}
{{projects_block}}
{{msx_block}}

{{msx_instructions}}
### MANDATORY: Persist Project Memory Before Writing Digest

**You MUST call `update_project` for every active project before writing the digest.** This creates persistent YAML files that carry context across digest cycles. If you skip this, tomorrow's digest starts from zero.

**How to do it:**
1. Scan all sources (transcripts, emails, Teams, calendar) for customer engagements, deals, initiatives
2. For each project found: call `update_project(project_id="slug", yaml_content="...")` with full YAML
3. If Part D above has existing project files, merge new info into them (don't overwrite — add new stakeholders, update commitments, change status)
4. If Part D is empty, this is a bootstrap run — create a file for every project you discover
5. Link each digest item to its project using the `project` field in the JSON output

**What counts as a project:** Any customer engagement, deal, initiative, or workstream that has meetings, emails, or action items associated with it. If the same customer/topic appears in 2+ sources, it's a project.

**Project YAML schema** (use this exact structure):
```yaml
project: "Human-readable name"
involvement: lead  # lead | contributor | observer — YOUR role in this project
status: active  # active | blocked | on-hold | completed
risk_level: medium  # low | medium | high | critical
summary: "1-2 sentence context"
stakeholders:
  - name: "Full Name"
    role: "PM"
    org: "Their Company"
commitments:
  - what: "Send pricing proposal"
    who: "You"
    to: "Customer Name"
    due: "2026-02-28"  # ONLY if explicitly stated — leave empty if inferred
    due_confidence: explicit  # explicit | inferred — was the date stated verbatim?
    status: open  # open | done | overdue | cancelled
    source: "Feb 20 standup transcript"
next_meeting: "2026-02-25 14:00"
key_dates:
  - date: "2026-03-01"
    event: "Contract renewal deadline"
tags: [deal, enterprise]
```

**`involvement` rules:** `lead` = you own it (schedule meetings, drive action items). `contributor` = you participate but don't drive. `observer` = you're CC'd or mentioned in passing. **Default to `observer` if unsure.**

**`due_confidence` rules:** Only set `due` with `due_confidence: explicit` if the date was stated verbatim in the source material. Vague phrases ("follow up next week", "circle back") get `due_confidence: inferred` at most — and inferred dates do NOT trigger overdue alerts.

**Example `update_project` call:**
```
update_project(
  project_id="first-central-moonshot",
  yaml_content="project: First Central Moonshot\nstatus: active\nrisk_level: high\nsummary: Technical deep-dive series for moonshot project...\nstakeholders:\n  - name: Jay Crofton\n    role: Technical Lead\n..."
)
```

## WorkIQ Queries

Try these WorkIQ queries. The query window is **{{workiq_window}}**.

### Step 1: Get upcoming meetings and calendar
Ask WorkIQ: "What meetings do I have coming up this week? Include the meeting title, time, attendees, and any agenda or prep materials."

### Step 2: Get emails addressed TO ME
Ask WorkIQ: "Show me emails {{workiq_window}} where I am in the TO field (not just CC) and someone is directly asking ME to do something or reply. For each one, tell me: sender, subject, and exactly what they're asking ME to do."

### Step 3: Get Teams messages addressed TO ME
Ask WorkIQ: "What Teams channel messages {{workiq_window}} directly @mention me or ask me a specific question by name? Include channel name and the exact question."

### Step 4: Verify EACH carry-forward item individually
For EACH item in "Known Outstanding Items" above, run the **Verify** query listed with it.
Do NOT use a single generic query — verify each item INDIVIDUALLY.
If WorkIQ confirms you replied or acted on an item, DROP it immediately.

### IF WORKIQ FAILS:
If ANY WorkIQ query returns an error (e.g. "Failed to create conversation"), you MUST:
1. **Use the Inbox Scans (Part B above) as your primary source of truth** — Teams scan for chats, Outlook scan for emails, Calendar scan for meetings
2. **DO NOT blindly carry forward items from the previous digest** — if a person does NOT appear as unread in the Teams or Outlook inbox scans, assume you've already replied and DROP that item
3. State clearly in the digest: "WorkIQ unavailable — verified via browser inbox scans only."
4. Only keep carry-forward items that are CORROBORATED by the inbox scans (the person still shows as unread in Teams or Outlook)
5. For email-sourced items: check the Outlook scan first. If the sender appears as unread, keep. If not in the Outlook scan and >3 days old, DROP with note.

### IF BOTH WORKIQ AND BROWSER SCANS ARE UNAVAILABLE:
If WorkIQ fails AND any inbox/calendar scan says "SCAN UNAVAILABLE", you MUST:
1. **State clearly at the top**: "Data limited — WorkIQ unavailable and browser scans failed. Inbox/calendar status unknown."
2. **DO NOT claim 0 unread** — you simply don't know. Say "Unable to verify inbox status."
3. **KEEP carry-forward action items** that have deadlines approaching — you can't verify them, so err on the side of keeping important items
4. **DROP only** carry-forward items that are >5 days old (staleness cutoff still applies)
5. **Focus the digest on**: upcoming deadlines from carry-forward, action items from transcripts, and anything with a concrete due date
6. **DO NOT add an "Inbox Status" summary line** — since you have no data, omit it entirely rather than showing misleading zeros

### Step 5: MERGE with Known Outstanding Items
- For each **Known Outstanding Item** from the previous digest:
  - **DROP** if the person does NOT appear as unread in the Teams inbox scan (they've been replied to)
  - **DROP** if WorkIQ confirms it's been handled (reply sent, meeting attended, task done)
  - **KEEP** only if the person STILL shows as unread in the Teams inbox scan, OR if WorkIQ confirms no reply was sent
  - **UPDATE** if there's new activity on the same thread
- For each **NEW** item from WorkIQ or Teams inbox scan:
  - **ADD** only if it's not already covered by a Known Outstanding Item
  - Skip FYI emails, newsletters, no-reply senders, and things clearly already handled
- The final digest = verified carry-forward items + genuinely new items = accurate snapshot

## CRITICAL: No Hallucinated Items

**Every digest item MUST trace back to a concrete source.** You may ONLY add items that come from:
1. **WorkIQ query results** — emails or Teams messages returned by WorkIQ in THIS session
2. **Inbox scans above** (Part B) — Teams, Outlook, or Calendar entries listed in the scan data
3. **Carry-forward items** from the previous digest (Part A) that pass the merge/verification rules
4. **Local content** (Part A) — transcripts, documents, or emails with explicit action items

If a person is familiar from project context, transcripts, or prior digests but does NOT appear in any of the 4 sources above for THIS run, **DO NOT create a new item for them.** Knowing someone exists is not the same as having an unread message from them.

**Test before adding:** For every new item, ask: "Which specific WorkIQ result, inbox scan line, or local file contains this?" If you cannot point to a concrete source, DROP the item.

## CRITICAL FILTER: Is this actually MY responsibility?

Before adding ANY item to the digest, ask yourself:
- Is someone DIRECTLY asking ME (by name or as the TO recipient) to do something?
- Or am I just CC'd / in a group thread where someone ELSE needs to act?
- If the action is on someone else, DO NOT include it. I don't care about other people's tasks.
- If I'm just "looped in" or "FYI'd", it does NOT belong in the digest.
- Community Hub draft reviews, surveys, newsletters = SKIP unless I specifically committed to them.
- If in doubt, leave it OUT. False positives waste my time.

## Output Rules

**TARGET: 30-50 lines. Not 400. Be brutal about what makes the cut.**

**PRIORITIZE FORWARD-LOOKING CONTENT. The digest should answer: "What do I need to do next?" not "What happened last week?"**

**AGING / URGENCY CUES**: For every outstanding item, calculate how long it's been waiting and include it. Use relative time like "(2 days ago — no reply yet)" or "(sent 18h ago)". This creates urgency. Don't just state the date — make the reader feel the clock ticking.

**NEW vs CARRIED FORWARD**: Start the digest with a 1-line summary: "X new items, Y carried forward, Z resolved since last digest." If there's no previous digest, just say "X items found." This lets the reader immediately know what changed.

**CALENDAR FILTERING**: Strip personal recurring events (commutes, childcare, breaks, lunch, personal blocks) from the calendar section. Only surface meetings that need prep, have conflicts, or are new/unusual. The user already knows their own routine — don't recite it back.

**UNVERIFIED ITEMS**: When you can't verify whether an action was completed (e.g., WorkIQ unavailable and item isn't in inbox scans), tag it as "(unverified — may already be handled)" instead of presenting it as definitely outstanding. Don't drop it, but be honest about your confidence level.

**EMPTY SECTIONS**: If a section has no items (Pulse Signals, External Intel, Key Takeaways, Risks), omit the section heading entirely. Do NOT write "None" or "Nothing drafted." Sections that consistently say "none" train the reader to skip the digest.

The ONLY things that belong in the digest (in priority order):
1. **Upcoming meetings this week** that need prep or have important context — what's on my calendar, who's attending, what should I prepare
2. **Unreplied messages** — Teams chats, emails where someone is waiting for MY response
3. **Deadlines coming up** that I haven't acted on
4. **Action items I committed to** that I haven't delivered yet
5. **Risks/escalations** that are still unresolved
6. Key decisions from meetings (1 line each, not paragraphs) — ONLY if they change what I need to do next
7. RSS articles ONLY if they directly name one of your active customers, a competitor in a live deal, or a product you're actively selling — max 3 lines. Generic industry news belongs in the separate intel mode, not here.

Things that do NOT belong:
- Emails I already replied to
- Meetings I already attended with no outstanding actions
- FYI emails, newsletters, community digests, surveys, bookmark reminders
- Emails where I'm only CC'd and the action is on someone else
- Teams threads where someone else (not me) is being asked to act
- Items where I'm "looped in" but have no specific ask
- Microsoft Community Hub draft reviews (unless I specifically committed)
- Anything that's clearly already handled
- Detailed per-meeting breakdowns (just the key takeaway + any open action items)
- Generic AI hype articles with no substance — these go in the separate intel mode
- Competitor news that doesn't affect your active deals or customers

## Output Formats

You MUST produce TWO outputs using `write_output`:

### Output 1: Structured JSON — `digests/{{date}}.json`

```json
{
  "date": "{{date}}",
  "items": [
    {
      "id": "<type>-<slug>",
      "type": "<reply_needed|action_item|review_needed|input_needed|decision_needed|escalation|intel|fyi>",
      "priority": "<urgent|high|medium|low>",
      "source": "Email from <name> | Teams: <channel/person> | Meeting: <title> | RSS: <source>",
      "title": "<short title — max 80 chars>",
      "summary": "<1-2 sentence description of what needs attention>",
      "project": "<project-id or null if not linked to a project>",
      "date": "<YYYY-MM-DD when this originated>",
      "age": "<human-readable age, e.g. '2 days', '18 hours', 'today'>",
      "verified": "<true ONLY if confirmed via WorkIQ result or inbox scan in THIS session; false otherwise>",
      "status": "outstanding",
      "evidence": "<which source: 'WorkIQ email query', 'Outlook inbox scan', 'Teams inbox scan', 'carry-forward', 'transcript: filename.md', etc. — REQUIRED>",
      "suggested_actions": [
        {
          "label": "<short button label, max 30 chars>",
          "action_type": "draft_teams_reply|send_email_reply|schedule_meeting|dismiss",
          "draft": "<the actual draft message text if this is a reply action, or empty string>",
          "target": "<person name or channel>",
          "metadata": "<optional: for schedule_meeting, include attendees + duration + subject>"
        }
      ]
    }
  ],
  "signals": [
    {
      "id": "sig-<slug>",
      "type": "<customer_win|customer_loss|customer_escalation|compete|product|ip_initiative>",
      "title": "<signal title>",
      "file": "pulse-signals/{{date}}-<slug>.md"
    }
  ],
  "stats": {
    "sources_processed": "<number>",
    "items_outstanding": "<number>",
    "items_new": "<number of items not in previous digest>",
    "items_carried_forward": "<number of items kept from previous digest>",
    "items_resolved": "<number of previous items dropped as resolved>"
  }
}
```

Rules for item IDs:
- Format: lowercase, hyphens only, derived from type + key entity. E.g. `reply-sender-subject-slug`, `action-project-task-slug`.
- **ID STABILITY IS CRITICAL.** The user dismisses items by ID. If you change the ID between runs, dismissed items reappear — this is a bug.
- If an item appeared in the previous digest (carry-forward) or in the dismissed list, you MUST reuse its exact ID.
- If an item about the same topic/person appeared in triage with a different ID (e.g. `email-frank-press-review` vs `reply-frank-press-release`), reuse the EXISTING ID from the dismissed list or previous digest.
- Only generate a new ID for genuinely new items that have no match in dismissed or carry-forward.

Rules for `age` and `verified`:
- `age`: human-readable relative time from the item's origin date to today. E.g. "2 days", "18 hours", "today", "5 days".
- `verified`: `true` if you confirmed the item's status via WorkIQ or inbox scans. `false` if you're carrying it forward without confirmation (e.g., WorkIQ unavailable and item not found in scans).

Rules for `suggested_actions`:
- Every `reply_needed` item MUST have at least one `suggested_actions` entry with a drafted reply
- `action_item` items SHOULD have a suggested action if a concrete next step is obvious (e.g., reply, schedule meeting)
- The `draft` field should be a complete, ready-to-send message (not a placeholder)
- Use context from local files and WorkIQ to make drafts specific and informed
- Keep drafts concise and professional — match the sender's tone
- `fyi` and `intel` items don't need `suggested_actions` — leave the array empty or omit it
- **PREFER Teams over email for all internal/Microsoft contacts.** Use `draft_teams_reply` as the default action type. Only use `send_email_reply` for external contacts who are NOT on Teams.
- Action types: `draft_teams_reply` (Teams reply — DEFAULT for internal), `send_email_reply` (Outlook reply — external contacts only), `schedule_meeting` (M365 Copilot scheduling — put attendees, duration, subject in `metadata`)

### Output 2: Human-readable Markdown — `digests/{{date}}.md`

```markdown
# Digest — {{date}}

{X new items, Y carried forward, Z resolved since last digest.}
{If WorkIQ unavailable or scans failed, state data source caveat here.}

## Overdue Commitments
(OMIT if none. Surface overdue commitments from project files FIRST — these are the most time-sensitive.)
- **[OVERDUE {N}d]** {project}: {what} — committed to {person} by {date}

## Coming Up
- **{day, time}**: {meeting title} — {what to prep if known}
- **{deadline}**: {what's due} — {current status}
(Only non-routine meetings. No personal blocks, recurring commutes, childcare, etc.)

## By Project
(Group items by project. Each project header shows status + risk.)

### {Project Name} ({status}, {risk})
- **[REPLY]** {sender} — {subject} — {what they need} *({aging})*
- **[ACTION]** {what} — {deadline} *({aging})*
- **[DECISION]** {what needs deciding} — {by when}

### {Another Project}
- ...

## Other Items
(Items not linked to any project. Still grouped by priority.)
- **[URGENT REPLY]** {sender} — {subject} — {what they need} *({N days/hours ago — no reply yet})*
- **[ACTION]** {what} — {deadline} — {context} *({aging})* {if unverified: "(unverified — may already be handled)"}

## Key Takeaways
(OMIT this section if empty. Only include if a meeting decision changes what you do next.)
- {1-line insight}

## External Intel
(OMIT this section if nothing directly relevant to active deals/customers.)
- **[Company]** — what happened — why it matters to YOUR work

## Risks
(OMIT this section if empty.)
- {unresolved risk with specific customer/deal name}

## Pulse Signals
(OMIT this section if nothing qualifies. Do NOT write "None drafted.")
- **[Type]** {customer/topic} — {1-line summary} → `pulse-signals/YYYY-MM-DD-slug.md`
```

IMPORTANT: Write the JSON file FIRST, then the markdown file. Both are required.

## GBB Pulse Signal Drafting

After generating the digest, review ALL sources (transcripts, emails, Teams messages, RSS articles) for items that should be drafted as **GBB Pulse signals**. These are field insights fed back to product groups and go-to-market teams.

Draft a signal if you find ANY of these:
- **Customer Win** — deal closed, deployment succeeded, competitive displacement
- **Customer Loss** — lost to competitor, blocked by technical issue, deal fell through
- **Customer Escalation** — SLT-level issue, $$$ at risk, deadline pressure
- **Compete Signal** — competitor pricing change, feature gap, strategy shift, customer feedback
- **Product Signal** — feature request, bug, performance issue, integration gap
- **IP/Initiative** — reusable asset, best practice, program update

For each signal, use `write_output` to save a SEPARATE file as `pulse-signals/{{date}}-{slug}.md` using this template:

```markdown
# [Signal Type]: [Title]

- **Customer/Topic**: name
- **Type**: Win / Loss / Escalation / Compete / Product / IP
- **Impact**: quantify in $$ or strategic terms
- **Description**: 3-4 sentences — situation, approach, outcome
- **Compete**: competitor name if applicable
- **Action/Ask**: what should happen next
```

Rules for signal drafting:
- Only draft signals where you have SPECIFIC facts (customer names, deal sizes, product names)
- Do NOT fabricate — if the source material is vague, skip it
- One file per signal
- List all drafted signals in the digest under "## Pulse Signals" with their filenames
- If nothing qualifies, omit the section entirely — don't force it

CRITICAL:
- Be SPECIFIC (names, dates, amounts). No vague summaries.
- FILTER OUT everything already dealt with. This is the whole point.
- If everything is handled, say "Nothing outstanding" — don't pad it.
