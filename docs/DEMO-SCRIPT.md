# Pulse Agent — 3-Minute Demo Video Script

Target: 3 minutes max. Show the problem, the solution, and the "wow" moment.

## Opening Hook (0:00 – 0:20)

**Narration:**
> "I had 8 meetings yesterday. I was in 6 of them, distracted in 3, and completely missed an escalation in a meeting I skipped. This morning at 7 AM, my digest told me exactly what matters — 30 lines, not 400."

**Visual:** Show a real morning digest (markdown or TUI Digest tab). Scroll briefly to show it's concise.

## The Problem (0:20 – 0:40)

**Narration:**
> "Knowledge workers are drowning. 8 meetings a day, 50 email threads, competitor moves at 2 AM. Copilot helps when you ask — but nobody asks at 2 AM. Pulse Agent works when you don't."

**Visual:** Brief flash of a packed Teams calendar + overflowing Outlook inbox. Then: Pulse Agent TUI dashboard — clean, structured, filtered.

## Live Demo: The Daemon (0:40 – 1:10)

**Action:** Show `python src/main.py` starting up.

**Narration:**
> "Pulse Agent is an always-on local daemon. It runs three concurrent tasks: a config-driven scheduler, a job worker backed by the GitHub Copilot SDK, and a TUI backend for real-time interaction."

**Visual:** Terminal showing daemon startup logs — scheduler loaded, browser connected, jobs syncing.

**Narration:**
> "Schedules are config-driven — daily digest at 7 AM, triage every 30 minutes during office hours, intel brief at 9 AM. All defined in YAML, no code changes needed."

## Live Demo: Transcript Collection (1:10 – 1:40)

**Action:** Trigger `--mode transcripts --once` or show the daemon running it automatically.

**Narration:**
> "The first thing it does overnight is collect meeting transcripts. Playwright opens Teams, navigates the calendar, and scrapes transcripts from the virtualized list — then compresses each one via the SDK into structured notes: TLDR, decisions, action items, key quotes."

**Visual:** Edge browser opening, Teams Calendar loading, clicking into a meeting recap, transcript scrolling. Then show the compressed `.md` output — short, structured.

## Live Demo: Morning Digest (1:40 – 2:15)

**Action:** Show a real digest in the TUI Digest tab (or the `.md` file).

**Narration:**
> "At 7 AM, the digest pipeline runs. It scans transcripts, inbox, emails, RSS feeds, and project memory. It queries WorkIQ for what you've already handled. Cross-references everything. And delivers only what's genuinely outstanding."

**Visual:** TUI Digest tab showing project-grouped items. Highlight:
- A meeting decision that needs follow-up
- An email requiring a reply (with draft action button)
- A commitment approaching deadline from project memory

**Narration:**
> "Items you've already dealt with? Filtered out. Meetings with no open actions? Gone. Stale items older than 5 days? Auto-dropped. You see what matters."

## Live Demo: Triage + Action (2:15 – 2:40)

**Action:** Show the TUI Triage tab with a few items.

**Narration:**
> "Every 30 minutes, triage scans your Teams and Outlook inbox. Each item comes with a suggested action and a drafted reply. Press D to dismiss, R to reply, N to add a note."

**Visual:** Show an item with a draft reply → approve → message sent via Playwright (deterministic, no LLM in the send path).

**Narration:**
> "Draft-first, always. The agent suggests — you approve. One keystroke."

## Architecture Highlight (2:40 – 2:50)

**Visual:** Flash the architecture diagram from the README (or a slide version).

**Narration:**
> "Built on the GitHub Copilot SDK with WorkIQ for M365 data, Playwright for browser automation, 13 custom tools, 4 session hooks for automatic audit trails and guardrails, and multi-model routing — GPT-4.1 for fast triage, Claude Sonnet for digest, Claude Opus for deep research."

## Closing (2:50 – 3:00)

**Narration:**
> "342 tests. 7 modes. 13 custom tools. Local-first, no data leaves your tenant. Copilot helps when you ask — Pulse Agent works when you don't."

**Visual:** TUI dashboard, all tabs briefly. End on the Chat tab with a natural language query.

---

## Recording Tips

1. **Screen resolution:** 1920x1080, dark terminal theme, large font (16-18pt)
2. **Pre-stage data:** Have a real digest, triage report, and compressed transcript ready in `$PULSE_HOME`
3. **Browser session:** Ensure Edge is authenticated with Teams/Outlook before recording
4. **TUI:** Run `python src/watch.py` in a maximized terminal
5. **Daemon:** Start with `python src/main.py` in a split terminal
6. **Narration:** Record audio separately for cleaner results, then overlay
7. **No sensitive data:** Use test/demo data or blur PII before publishing
8. **Timing:** Each section has a time budget — rehearse to stay under 3 min
