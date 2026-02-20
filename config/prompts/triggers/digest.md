Generate a SHORT daily digest for {{date}}. This should be MAX 50 lines — only things I haven't dealt with yet.

WorkIQ query window: **{{workiq_window}}** (only query for NEW activity in this window).

## Your Priorities
{{priorities}}
{{dismissed_block}}
{{notes_block}}
{{carry_forward}}

## Part A — Local Content (already collected)
{{content_sections}}
{{articles_block}}

## Part B — Inbox Scans (GROUND TRUTH — live Playwright scans)

These come from real-time browser scans. They show what is ACTUALLY unread right now.

### Teams Inbox
{{teams_inbox_block}}

### Outlook Inbox
{{outlook_inbox_block}}

### Today's Calendar
{{calendar_block}}

## WorkIQ Queries

Try these WorkIQ queries. The query window is **{{workiq_window}}**.

### Step 1: Get emails addressed TO ME
Ask WorkIQ: "Show me emails {{workiq_window}} where I am in the TO field (not just CC) and someone is directly asking ME to do something or reply. For each one, tell me: sender, subject, and exactly what they're asking ME to do."

### Step 2: Get Teams messages addressed TO ME
Ask WorkIQ: "What Teams channel messages {{workiq_window}} directly @mention me or ask me a specific question by name? Include channel name and the exact question."

### Step 3: Check what I've already handled
Ask WorkIQ: "Which of my recent emails and Teams messages have I already replied to or acted on?"

### IF WORKIQ FAILS:
If ANY WorkIQ query returns an error (e.g. "Failed to create conversation"), you MUST:
1. **Use the Inbox Scans (Part B above) as your primary source of truth** — Teams scan for chats, Outlook scan for emails, Calendar scan for meetings
2. **DO NOT blindly carry forward items from the previous digest** — if a person does NOT appear as unread in the Teams or Outlook inbox scans, assume you've already replied and DROP that item
3. State clearly in the digest: "WorkIQ unavailable — verified via browser inbox scans only."
4. Only keep carry-forward items that are CORROBORATED by the inbox scans (the person still shows as unread in Teams or Outlook)
5. For email-sourced items: check the Outlook scan first. If the sender appears as unread, keep. If not in the Outlook scan and >3 days old, DROP with note.

### Step 4: MERGE with Known Outstanding Items
- For each **Known Outstanding Item** from the previous digest:
  - **DROP** if the person does NOT appear as unread in the Teams inbox scan (they've been replied to)
  - **DROP** if WorkIQ confirms it's been handled (reply sent, meeting attended, task done)
  - **KEEP** only if the person STILL shows as unread in the Teams inbox scan, OR if WorkIQ confirms no reply was sent
  - **UPDATE** if there's new activity on the same thread
- For each **NEW** item from WorkIQ or Teams inbox scan:
  - **ADD** only if it's not already covered by a Known Outstanding Item
  - Skip FYI emails, newsletters, no-reply senders, and things clearly already handled
- The final digest = verified carry-forward items + genuinely new items = accurate snapshot

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

The ONLY things that belong in the digest:
- Things I haven't responded to yet that need a response
- **Unreplied Teams messages** — 1:1 chats, group chats, and channel threads where someone is waiting for me
- Deadlines coming up that I haven't acted on
- Risks/escalations that are still unresolved
- Key decisions from meetings (1 line each, not paragraphs)
- Commitments I made that I haven't delivered on yet
- RSS articles ONLY if they directly name one of your active customers, a competitor in a live deal, or a product you're actively selling — max 3 lines. Generic industry news belongs in the separate intel mode, not here.

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
      "date": "<YYYY-MM-DD when this originated>",
      "status": "outstanding"
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
    "items_outstanding": "<number>"
  }
}
```

Rules for item IDs: lowercase, hyphens only, derived from type + key entity. E.g. `reply-esther-enact-user-base`, `action-vodafone-voice-quality`, `intel-github-copilot-sdk-cli`.

### Output 2: Human-readable Markdown — `digests/{{date}}.md`

```markdown
# Digest — {{date}}

## Still Outstanding
- **[REPLY]** {sender} — {subject} — {what they need} ({date})
- **[ACTION]** {what} — {deadline} — {context}
- **[DECISION]** {what needs deciding} — {by when}

## Key Takeaways This Week
- {1-line meeting insight or decision that matters}

## External Intel (only if directly relevant to your deals/customers — omit section if nothing qualifies)
- **[Company]** — what happened — why it matters to YOUR work specifically

## Risks
- {unresolved risk with specific customer/deal name}

## Pulse Signals
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
- Use `log_action` to log your work.
