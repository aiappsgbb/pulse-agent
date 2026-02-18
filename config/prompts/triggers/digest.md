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

## WorkIQ Queries

Make these WorkIQ queries IN ORDER. The query window is **{{workiq_window}}** — only look for NEW activity in this period.

### Step 1: Get NEW emails and messages
Ask WorkIQ: "Show me emails I received {{workiq_window}} that look like they need action or a reply. Include sender, subject, and what they need."

### Step 2: Check what's been handled
Ask WorkIQ: "Which of my recent emails have I already replied to? Have I responded to or dealt with any of the Known Outstanding Items listed above?"

### Step 3: Get NEW Teams messages (make 2 queries)
Ask WorkIQ: "What Teams 1:1 and group chat messages {{workiq_window}} am I yet to reply to? Show sender, preview, and time sent."
Then ask: "What Teams channel messages {{workiq_window}} mention me, ask me a question, or are in threads I replied to before? Include channel name."

### Step 4: MERGE with Known Outstanding Items
- For each **Known Outstanding Item** from the previous digest:
  - **KEEP** it if WorkIQ shows no reply/action was taken
  - **DROP** it if WorkIQ confirms it's been handled (reply sent, meeting attended, task done)
  - **UPDATE** it if there's new activity on the same thread
- For each **NEW** WorkIQ result (Steps 1 & 3):
  - **ADD** only if it's not already covered by a Known Outstanding Item
  - Skip FYI emails, newsletters, no-reply senders, and things clearly already handled
- The final digest = carried-forward items + genuinely new items = complete snapshot of what's outstanding

## Output Rules

**TARGET: 30-50 lines. Not 400. Be brutal about what makes the cut.**

The ONLY things that belong in the digest:
- Things I haven't responded to yet that need a response
- **Unreplied Teams messages** — 1:1 chats, group chats, and channel threads where someone is waiting for me
- Deadlines coming up that I haven't acted on
- Risks/escalations that are still unresolved
- Key decisions from meetings (1 line each, not paragraphs)
- Commitments I made that I haven't delivered on yet
- Significant competitor/industry moves from RSS articles (max 5 lines)

Things that do NOT belong:
- Emails I already replied to
- Meetings I already attended with no outstanding actions
- FYI emails, newsletters, community digests
- Anything that's clearly already handled
- Detailed per-meeting breakdowns (just the key takeaway + any open action items)
- Generic AI hype articles with no substance

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

## External Intel
- **[Company]** — what happened — why it matters

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
