# Pulse Agent — Video Production Guide

**Target**: ~1:20 (tight, punchy, nobody watches the second minute)
**Tools**: Veo 3.1 (V1-V6 recorded), Clipchamp (editing + Azure TTS), OBS (screen capture)

---

## The 3 Pillars (every scene serves one of these)

1. **Information Hub** — transcripts, Teams, Outlook, calendar, auto-reply — all in one place
2. **Developer Workflow** — runs while you code, audio pings, research on demand
3. **Multi-Agent Collaboration** — agents talk to each other, share context, discover overlapping work

Pillar 3 is the biggest differentiator. It goes last = the thing judges remember.

---

## Pre-Production

- [ ] TUI running with real data, pulse-dark theme
- [ ] 1920x1080, font 16-18pt, notifications off, PII blurred
- [ ] For SR3 (chat): stage a collaboration moment — e.g. "ask Esther what she knows about the Contoso timeline" or show an incoming agent_response notification

---

## Scene Structure

```
SCENE   TIME         SOURCE   PILLAR   WHAT
─────   ──────────   ──────   ──────   ──────────────────────────────
  1     0:00-0:08    V1       —        Hook — overload + stat overlays
  2     0:08-0:14    V2       —        Dawn — title card (trim to 6s)
  3     0:14-0:22    V3+V4    1        Data streams → single agent architecture
  4     0:22-0:24    V6       —        CRT bumper (trim to 2-3s)
  5     0:24-0:42    SR1      1        Inbox digest — all sources, one view
  6     0:42-0:56    SR2      1+2      Triage reply — draft → approve → sent
  7     0:56-1:04    V7       3        Two agents exchanging knowledge
  8     1:04-1:12    V8       3        Full agent network — 20 nodes, mesh
  9     1:12-1:20    V5       —        Closing — stats + tagline
```

**~80s total.** The arc: one agent (V3→V4→product) → two agents (V7) → twenty agents (V8) → close.

---

## Scene Details

### Scene 1 — Hook (V1, 0:00-0:08)

Music only, no voiceover. Stats fade in:
```
0:01  "8 meetings."
0:03  "50 email threads."
0:05  "20% retention."
```

### Scene 2 — Title (V2, 0:08-0:14)

Title card: **PULSE AGENT** / **Works when you don't.**

VO starts:
```
I had eight meetings yesterday and missed an escalation I didn't
even know about. This morning at seven, my agent told me what matters.
```

### Scene 3 — How One Agent Works (V3 → V4, 0:14-0:22)

V3 (data streams converging) cross-dissolves into V4 (single agent architecture). Shows one agent: many inputs in, structured output out, 14 tools orbiting.

Lower third on V3: `Transcripts • Inbox • Email • Calendar • RSS`
Callouts on V4: `GitHub Copilot SDK` → `WorkIQ` → `14 Tools` → `4 Hooks`

VO:
```
Overnight it collected transcripts, scanned my inbox, compressed
hours of content into structured notes. One agent — the GitHub
Copilot SDK, Work IQ, fourteen tools, fully audited.
```

### Scene 4 — Bumper (V6, 0:22-0:24)

Trim to 2-3s. No VO. Music + scan effect.

### Scene 5 — The Digest (SR1, 0:24-0:42) — PILLAR 1

Screen recording: Inbox tab, items grouped by project. Scroll slowly, expand detail panel.

Lower third: `All sources • One view • Only what's outstanding`

VO:
```
Everything in one place. Items grouped by project, cross-referenced
with Work IQ. Emails I replied to — gone. Stale items — dropped.
Thirty lines. Not four hundred.
```

### Scene 6 — One-Tap Reply (SR2, 0:42-0:56) — PILLAR 1+2

Screen recording: R → ReplyModal → draft → approve → toast. Audio ping plays.

Lower third: `Draft first • One keystroke to send`

VO:
```
Every item comes with a drafted reply. I press R, review it,
one keystroke to send. Draft first. Always.
```

### Scene 7 — Collaboration (V7, 0:56-1:04) — PILLAR 3

**The wow moment.** V7 shows two people at side-by-side desks, amber data packets flying between their screens. Two agents, one conversation.

Lower third: `Agents collaborate via OneDrive • Zero new infrastructure`

VO:
```
But here's what changes everything.
Every team member runs their own Pulse Agent.
They share context, answer each other's questions, and discover
overlapping work — automatically. No meetings required.
```

### Scene 8 — The Network (V8, 1:04-1:12) — PILLAR 3

V8 (20 agent nodes in a mesh). Scales up from V7's two agents to the full team.

Callouts: `"20+ Agents"` → `"OneDrive Sync"` → `"Zero Infrastructure"`

VO:
```
Twenty agents sharing knowledge. No servers. No setup. Just OneDrive.
```

### Scene 9 — Closing (V5, 1:12-1:20)

No VO. Music swell. Stats land one per beat:
```
"625 tests."
"7 modes."
"Local first."
"Copilot helps when you ask. Pulse Agent works when you don't."
```

---

## Full Voiceover Script (~48s)

```
[0:08] I had eight meetings yesterday and missed an escalation I didn't
even know about. This morning at seven, my agent told me what matters.

[0:14] Overnight it collected transcripts, scanned my inbox, compressed
hours of content into structured notes.

[0:20] Everything in one place. Items grouped by project, cross-referenced
with Work IQ. Emails I replied to — gone. Stale items — dropped.
Thirty lines. Not four hundred.

[0:38] Every item comes with a drafted reply. I press R, review it,
one keystroke to send. Draft first. Always.

[0:52] But here's what changes everything.
Every team member runs their own Pulse Agent.
They share context, answer each other's questions, and discover
overlapping work — automatically. No meetings required.

[1:00] Twenty agents sharing knowledge through OneDrive.
Fourteen tools. Four hooks. Fully audited.
```

Voice: **"Davis" (en-US)**, 1.0x. First-person, building to the pivot at 0:52.

**"But here's what changes everything"** — the first 50s is "useful for me", the climax is "transformative for the whole team."

---

## Screen Recording Notes

Only 2 recordings needed now (V7 replaced SR3):

**SR1 — Digest (18s)**: Scroll SLOWLY. Let viewer read items. Pause on one with OVERDUE tag.

**SR2 — Reply (14s)**: Hold 2-3s on the draft so viewer can read it. The toast is the payoff.

1920x1080, 60fps, OBS or Win+G. Multiple takes per scene.

---

## Assembly (Clipchamp)

```
Track 1:  Text overlays
Track 2:  V1 → V2 → V3 → V4 → V6 → SR1 → SR2 → V7 → V8 → V5
Track 3:  VO clips + background music
```

- Cross-dissolve 0.3s. Longer 0.6s on V1→V2.
- Trim V2 to 6s, V6 to 2-3s
- Font: **Cascadia Code** (monospace)
- Music: 30% during V1/V5, 10-15% under VO
- Auto-generate captions

---

## Export

| Setting | Value |
|---------|-------|
| Resolution | 1080p |
| Duration | ~1:18 |
| Filename | `PulseAgent-Demo.mp4` |
