# Pulse Agent — Video Production Guide

**Target**: 3-minute contest demo video (hard cap from contest rules)
**Tools**: Sora 2 (b-roll + stylized shots), Clipchamp (editing + Azure TTS voiceover), OBS/Windows Game Bar (screen capture)
**Style**: Cinematic opening → real CLI footage → polished closing. Professional but human.

---

## Table of Contents

1. [Pre-Production Checklist](#pre-production-checklist)
2. [Timeline Overview](#timeline-overview)
3. [Scene-by-Scene Breakdown](#scene-by-scene-breakdown)
4. [Sora 2 Prompt Library](#sora-2-prompt-library)
5. [Screen Recording Shot List](#screen-recording-shot-list)
6. [Voiceover Script (Azure TTS)](#voiceover-script-azure-tts)
7. [Clipchamp Assembly Guide](#clipchamp-assembly-guide)
8. [Audio & Music](#audio--music)
9. [Final Export Settings](#final-export-settings)

---

## Pre-Production Checklist

### Before You Record Anything

- [ ] **PULSE_HOME has real data** — fresh digest, triage report, compressed transcripts, project files
- [ ] **Daemon runs cleanly** — `python src/pulse.py` starts without errors
- [ ] **TUI runs cleanly** — `python src/pulse.py` loads all tabs, shows real items
- [ ] **Edge is authenticated** — Teams and Outlook sessions are active
- [ ] **Screen resolution set to 1920x1080** on all recording displays
- [ ] **Terminal font: 16-18pt**, dark theme (e.g., One Dark Pro or Catppuccin)
- [ ] **Close all notifications** — no stray Teams/Outlook popups during recording
- [ ] **Blur/redact PII** — no real names, emails, or customer data visible (or use demo data)
- [ ] **Sora 2 access** — ChatGPT Plus/Pro account (need credits for ~8-10 generations)
- [ ] **Clipchamp open** — create a new project, 16:9 aspect ratio

### Demo Data Preparation

Stage realistic-looking data in PULSE_HOME. If real data has PII, create synthetic equivalents:

```
digests/2026-02-27.json     → A realistic digest with 5-8 items across 3 projects
digests/2026-02-27.md       → Matching markdown
monitoring-*.json           → 3-4 triage items with draft replies
transcripts/2026-02-*.md    → 2-3 compressed transcripts
projects/contoso-migration.yaml → Active project with commitments
projects/partner-enablement.yaml → Project with approaching deadline
```

---

## Timeline Overview

```
0:00 ─── 0:20   HOOK: The Problem (Sora 2 b-roll + voiceover)
0:20 ─── 0:35   SOLUTION INTRO (Sora 2 transition → TUI reveal)
0:35 ─── 1:00   DAEMON STARTUP (screen recording: pulse.py launch)
1:00 ─── 1:25   TRANSCRIPT COLLECTION (screen recording: Edge + Teams)
1:25 ─── 1:55   MORNING DIGEST (screen recording: TUI Inbox tab)
1:55 ─── 2:20   TRIAGE + 1-TAP ACTION (screen recording: TUI action flow)
2:20 ─── 2:35   CHAT MODE (screen recording: TUI Chat tab)
2:35 ─── 2:50   ARCHITECTURE + INTEGRATION (Sora 2 b-roll + diagram overlay)
2:50 ─── 3:00   CLOSING (stats on screen, tagline)
```

Total Sora 2 footage needed: ~35-40 seconds (4-5 clips)
Total screen recording needed: ~120-130 seconds (5-6 clips)
Total text/overlay screens: ~10 seconds

---

## Scene-by-Scene Breakdown

### Scene 1: The Hook (0:00 – 0:20)

**Purpose**: Emotionally land the problem. Every knowledge worker watching should think "that's me."

| Element | Details |
|---------|---------|
| **Visual** | Sora 2 clip: information overload metaphor (see Prompt A1) |
| **Overlay text** | `8 meetings. 50 email threads. 20% retention.` (fade in one stat at a time) |
| **Voiceover** | See V1 below |
| **Music** | Ambient/electronic, low volume, tension-building |
| **Duration** | 20 seconds |

### Scene 2: Solution Intro (0:20 – 0:35)

**Purpose**: Contrast. From chaos to calm. Introduce Pulse Agent.

| Element | Details |
|---------|---------|
| **Visual** | Sora 2 clip: dawn/calm visualization (see Prompt A2), cross-dissolve into real TUI screenshot |
| **Overlay text** | `Pulse Agent` title card (large, clean), then `"Works when you don't."` subtitle |
| **Voiceover** | See V2 below |
| **Music** | Shift from tension to hopeful/resolved |
| **Duration** | 15 seconds |

### Scene 3: Daemon Startup (0:35 – 1:00)

**Purpose**: Show it's real, it runs, it's an always-on system.

| Element | Details |
|---------|---------|
| **Visual** | Screen recording: split terminal — left=`python src/pulse.py` starting, right=`python src/pulse.py` TUI loading |
| **Key moments to capture** | Daemon logs (scheduler loaded, browser connected), TUI tabs populating |
| **Overlay text** | Lower third: `Always-on local daemon • Config-driven scheduling` |
| **Voiceover** | See V3 below |
| **Duration** | 25 seconds |

### Scene 4: Transcript Collection (1:00 – 1:25)

**Purpose**: "Wow" moment — the agent automates something nobody thought possible.

| Element | Details |
|---------|---------|
| **Visual** | Screen recording: Edge browser opening Teams → Calendar → clicking into meeting → transcript scrolling → compressed .md output |
| **Speed** | 2x-3x speed on the browser navigation, normal speed on the transcript scroll (it's visually impressive) |
| **Overlay text** | Lower third: `Playwright extracts transcripts from Teams • SDK compresses to structured notes` |
| **Voiceover** | See V4 below |
| **Duration** | 25 seconds |

### Scene 5: Morning Digest (1:25 – 1:55)

**Purpose**: The core value prop — concise, project-oriented, actionable.

| Element | Details |
|---------|---------|
| **Visual** | Screen recording: TUI Inbox tab showing digest items grouped by project. Scroll slowly through 5-8 items. |
| **Key moments** | Show a commitment deadline, an email needing reply (with action button visible), a carry-forward item |
| **Overlay text** | Lower third: `Cross-references WorkIQ • Filters handled items • 30 lines, not 400` |
| **Voiceover** | See V5 below |
| **Duration** | 30 seconds |

### Scene 6: Triage + 1-Tap Action (1:55 – 2:20)

**Purpose**: Show the action loop. Not just reading — acting.

| Element | Details |
|---------|---------|
| **Visual** | Screen recording: TUI showing a triage item → press R → ReplyModal with draft → approve → "Sent" confirmation |
| **Key moments** | The draft appearing pre-written, the single keystroke to send, the toast notification |
| **Overlay text** | Lower third: `Draft-first • You approve, agent sends • Deterministic Playwright delivery` |
| **Voiceover** | See V6 below |
| **Duration** | 25 seconds |

### Scene 7: Chat Mode (2:20 – 2:35)

**Purpose**: Quick flex — the agent isn't just batch processing, it's conversational too.

| Element | Details |
|---------|---------|
| **Visual** | Screen recording: TUI Chat tab → type a question like "What did Esther say about the Contoso timeline?" → streaming response appears |
| **Speed** | Real-time to show the streaming effect |
| **Overlay text** | Lower third: `Natural language queries • WorkIQ + local file search • Streaming responses` |
| **Voiceover** | See V7 below |
| **Duration** | 15 seconds |

### Scene 8: Architecture + Integration (2:35 – 2:50)

**Purpose**: Technical credibility. Show the judges this is a real system.

| Element | Details |
|---------|---------|
| **Visual** | Sora 2 abstract data flow clip (see Prompt A3) with architecture diagram overlaid as semi-transparent PNG. Or: static architecture diagram with animated highlights (Clipchamp text animation on key components). |
| **Overlay text** | Callout boxes: `GitHub Copilot SDK`, `WorkIQ MCP`, `13 Custom Tools`, `4 Session Hooks`, `Multi-Model Routing` |
| **Voiceover** | See V8 below |
| **Duration** | 15 seconds |

### Scene 9: Closing (2:50 – 3:00)

**Purpose**: Memorable ending. Stats. Tagline. Done.

| Element | Details |
|---------|---------|
| **Visual** | Sora 2 dawn clip (see Prompt A4) or solid dark background. Stats appear one by one. |
| **Overlay text** | `366 tests` → `7 modes` → `13 custom tools` → `Local-first. No data leaves your tenant.` → `Copilot helps when you ask. Pulse Agent works when you don't.` |
| **Voiceover** | See V9 below |
| **Music** | Swell to conclusion, fade out |
| **Duration** | 10 seconds |

---

## Sora 2 Prompt Library

Generate these clips in advance. Budget ~10 credits per clip, expect 1-2 iterations each.

### Prompt A1 — Information Overload (Scene 1, ~12s)

```
A cinematic overhead shot slowly pulling back from a person sitting at a desk in a modern
glass office. The desk is clean but the air above is filled with hundreds of translucent
holographic notification cards, email previews, calendar alerts, and chat bubbles cascading
downward like falling leaves — glowing cyan, amber, and white against a dimly lit background.
The person looks overwhelmed, head slightly bowed. Shallow depth of field, 35mm anamorphic
lens, cool blue-white ambient lighting with warm amber accents from the notifications.
Slow, contemplative camera movement. Soft electronic ambient soundtrack.
```

### Prompt A2 — Dawn / Clarity (Scene 2, ~10s)

```
Smooth cinematic time-lapse of a city skyline transitioning from the blue hour before dawn
to warm golden sunrise. In the foreground, slightly out of focus, a single laptop screen
glows with a clean, minimal interface — soft blue light. As the sun rises, the screen
brightens subtly. The shot conveys calm and clarity after a long night. 50mm lens, shallow
depth of field, rack focus from city skyline to the laptop screen in the final 3 seconds.
Warm amber and cool blue color palette. Ambient morning sounds, distant city hum.
```

### Prompt A3 — Data Flow / Architecture (Scene 8, ~10s)

```
A slow cinematic tracking shot through a dark abstract space filled with interconnected
luminous nodes and flowing particle streams. Streams of glowing blue-white particles converge
from multiple directions (left, right, above) into a central bright node that pulses with
amber light. Thin connecting lines trace paths between nodes like a network diagram.
The camera dollies smoothly through the space. 32mm spherical lens, deep depth of field,
anamorphic lens flare as the camera passes near bright nodes. Dark navy background,
blue-white and amber color palette. Subtle electronic ambient hum.
```

### Prompt A4 — Closing Dawn (Scene 9, ~8s)

```
Close-up of a laptop screen in a dark room, displaying a minimal dashboard with a few
glowing blue indicators. The camera slowly pulls back as warm golden light begins flooding
in from a window to the right — sunrise. The room transforms from dark and focused to warm
and bright. The laptop screen is still visible but now the room feels calm and resolved.
35mm lens, shallow depth of field transitioning to deep. Cool blue to warm amber color shift.
Soft, uplifting ambient tone.
```

### Prompt A5 — Stylized UI Fly-Through (Optional, for transitions)

```
A cinematic macro shot traveling across the surface of a dark glass screen showing abstract
data visualizations — flowing line charts, pulsing node graphs, scrolling text columns
rendered as soft bokeh light. The camera glides laterally at a slight angle, creating
parallax between foreground data elements and background grid patterns. Everything is
slightly abstracted and dreamy — not literal UI but evocative of a smart dashboard.
50mm macro lens, very shallow depth of field, smooth lateral dolly. Blue-white with
teal accents. Quiet electronic pulse.
```

> **IMPORTANT**: Never rely on Sora for readable text. All text, stats, labels, and titles
> go in as Clipchamp overlays in post-production.

---

## Screen Recording Shot List

Record these with OBS Studio or Windows Game Bar (Win+G). Settings: 1920x1080, 60fps, MP4/H.264.

### SR1 — Daemon Startup (Scene 3)

```
Setup: Split terminal (left: cmd for daemon, right: cmd for TUI)
Action:
1. Left terminal: `python src/pulse.py` — let it start, show scheduler loaded, browser connected
2. Right terminal: `python src/pulse.py` — TUI loads, tabs populate
3. Hold for 3-4 seconds on the loaded TUI
Duration: ~40 seconds raw (will speed up to 25s in edit)
```

### SR2 — Transcript Collection (Scene 4)

```
Setup: Daemon already running. Trigger transcript collection.
Action:
1. Show Edge opening to Teams
2. Navigate to Calendar → click into a meeting with "View recap"
3. Click Transcript tab → show the virtualized list scrolling
4. Cut to: the compressed .md file in VS Code or terminal (cat the file)
Duration: ~60 seconds raw (will speed up to 25s in edit, 2-3x)
```

### SR3 — Morning Digest in TUI (Scene 5)

```
Setup: TUI running with real/staged digest loaded in Inbox tab.
Action:
1. Show Inbox tab with items listed, project grouping visible
2. Scroll slowly through items — pause on an actionable one
3. Show the priority indicators and action buttons (D/R/N)
4. Click into an item to show detail view
Duration: ~45 seconds raw (trim to 30s)
```

### SR4 — Triage Reply Action (Scene 6)

```
Setup: TUI showing triage items with reply-needed items.
Action:
1. Highlight an item with `reply_needed` indicator
2. Press R → ReplyModal appears with pre-drafted reply
3. Review the draft (pause 2s so viewer can read)
4. Press Enter to approve → show "Sent" confirmation / toast
Duration: ~30 seconds raw (trim to 25s)
```

### SR5 — Chat Mode (Scene 7)

```
Setup: TUI Chat tab active.
Action:
1. Type a natural language question: "What context do I have on the Contoso migration?"
2. Show the streaming response appearing character by character
3. Response mentions meetings, emails, project memory — shows cross-source intelligence
Duration: ~25 seconds raw (trim to 15s, speed up the typing)
```

### SR6 — Architecture Diagram (Scene 8, optional)

```
Setup: README.md open in browser or VS Code, scrolled to architecture diagram.
Action: Static screenshot or slow scroll. This may be replaced by a Sora clip + overlay.
Duration: ~5 seconds
```

---

## Voiceover Script (Azure TTS)

Use Clipchamp's built-in Azure TTS. Voice recommendation: **"Guy" (en-US)** or **"Davis" (en-US)** — neutral, professional, clear. Speed: 1.0x. No emotion modifiers.

Generate each segment as a separate audio clip in Clipchamp for easier timeline placement.

### V1 — Hook (0:00 – 0:20)

```
I had eight meetings yesterday. I was in six of them, distracted in three,
and completely missed an escalation in a meeting I skipped.

This morning at seven AM, my agent told me exactly what matters.
Thirty lines. Not four hundred.
```

*~18 seconds at 1.0x speed*

### V2 — Solution Intro (0:20 – 0:35)

```
This is Pulse Agent — an autonomous information processing engine built on
the GitHub Copilot SDK. It runs overnight, processes everything you can't,
and tells you only what matters.

Copilot helps when you ask. Pulse Agent works when you don't.
```

*~14 seconds*

### V3 — Daemon Startup (0:35 – 1:00)

```
Pulse Agent is an always-on local daemon with three concurrent tasks: a
config-driven scheduler, a job worker backed by the Copilot SDK, and a
terminal dashboard for real-time interaction.

Schedules are defined in YAML — daily digest at seven AM, triage every
thirty minutes, intel brief at nine. No code changes needed.
```

*~22 seconds*

### V4 — Transcript Collection (1:00 – 1:25)

```
The first thing it does overnight is collect meeting transcripts. Playwright
opens Teams, navigates the calendar, and scrapes transcripts from the
virtualized list — handling the dynamic DOM that only renders fifty items
at a time.

Each transcript is then compressed via the SDK into structured notes:
a summary, decisions, action items, and key quotes. Twenty thousand
characters reduced to two thousand.
```

*~24 seconds*

### V5 — Morning Digest (1:25 – 1:55)

```
At seven AM, the digest pipeline kicks in. It scans transcripts, inbox,
emails, RSS feeds, and project memory. It queries Work IQ for what you've
already handled. Cross-references everything.

What you see here is the result: items grouped by project, filtered to only
what's genuinely outstanding. Meetings with no open actions — gone. Emails
you already replied to — filtered out. Stale items older than five days —
auto-dropped. You see only what needs your attention.
```

*~28 seconds*

### V6 — Triage + Action (1:55 – 2:20)

```
Every thirty minutes during office hours, triage scans your Teams and
Outlook inbox. Each item comes with a suggested action and a pre-drafted
reply.

Watch this: I press R, the draft appears — reviewed but not sent. I approve
with one keystroke, and the message goes out via a deterministic Playwright
script. No LLM in the send path. Draft first, always.
```

*~22 seconds*

### V7 — Chat (2:20 – 2:35)

```
And it's not just batch processing. In chat mode, I can ask natural language
questions grounded in my actual data — transcripts, emails, project memory,
and Work IQ. The response streams in real time.
```

*~12 seconds*

### V8 — Architecture (2:35 – 2:50)

```
Under the hood: the GitHub Copilot SDK with Work IQ for Microsoft 365 data,
Playwright for browser automation, thirteen custom tools, four session hooks
for automatic audit trails and guardrails, and multi-model routing — GPT four
point one for fast triage, Claude Sonnet for digest, Claude Opus for deep
research.
```

*~14 seconds*

### V9 — Closing (2:50 – 3:00)

```
Three hundred sixty-six tests. Seven modes. Thirteen tools.
Local first — no data leaves your tenant.

Copilot helps when you ask. Pulse Agent works when you don't.
```

*~9 seconds*

**Total voiceover: ~163 seconds (~2:43)** — leaves 17 seconds of breathing room for visual-only moments.

---

## Clipchamp Assembly Guide

### Project Setup

1. Open Clipchamp → New video → 16:9 widescreen
2. Import all assets into media library:
   - Sora 2 clips (A1-A5, MP4 files)
   - Screen recordings (SR1-SR6, MP4 files)
   - Architecture diagram (PNG, exported from README or slide)
   - Any screenshots for overlays

### Timeline Layout (3 tracks)

```
Track 1 (top):     Text overlays, lower thirds, stat callouts, title cards
Track 2 (middle):  Video clips (Sora b-roll + screen recordings)
Track 3 (bottom):  Audio — voiceover clips + background music
```

### Step-by-Step Assembly

**1. Generate voiceover clips first**
   - Record → Text to speech → paste each V1-V9 segment
   - Voice: "Guy" or "Davis" (en-US), speed 1.0x
   - Place each on Track 3, spaced according to timeline

**2. Lay down video clips to match voiceover timing**
   - Scene 1 (0:00-0:20): Sora A1 clip, trimmed to 20s
   - Scene 2 (0:20-0:35): Sora A2, cross-dissolve to TUI screenshot
   - Scene 3 (0:35-1:00): SR1 (daemon startup), speed up to fit 25s
   - Scene 4 (1:00-1:25): SR2 (transcripts), speed to 2-3x, fit 25s
   - Scene 5 (1:25-1:55): SR3 (digest), trim to 30s
   - Scene 6 (1:55-2:20): SR4 (triage reply), trim to 25s
   - Scene 7 (2:20-2:35): SR5 (chat), speed up typing, trim to 15s
   - Scene 8 (2:35-2:50): Sora A3 + architecture diagram PNG overlay
   - Scene 9 (2:50-3:00): Sora A4 clip with text overlays

**3. Add transitions**
   - Use **cross-dissolve (0.3-0.5s)** between every scene
   - Exception: Scene 1→2 gets a longer dissolve (0.8s) for the chaos→calm shift
   - NO flashy transitions (wipe, spin, zoom) — keep it professional

**4. Add text overlays (Track 1)**
   - Scene 1: Stats fade in one at a time (`8 meetings` → `50 threads` → `20% retention`)
   - Scene 2: Title card `PULSE AGENT` (large, center) → subtitle `Works when you don't.`
   - Scenes 3-7: Lower-third labels (see scene breakdown above)
   - Scene 8: Architecture callout boxes
   - Scene 9: Closing stats, one per beat
   - Font: Use one font throughout. "Segoe UI" (Microsoft brand) or "Inter" (clean sans-serif)
   - Color: White text on dark backgrounds, or white text with subtle dark drop shadow on screen recordings

**5. Add background music**
   - Clipchamp → Content library → Audio → search "ambient technology" or "corporate minimal"
   - Volume: **10-15%** under voiceover, **30-40%** during visual-only moments
   - Fade in at 0:00, slight swell at transitions, fade out at 2:58

**6. Generate captions**
   - Select all voiceover clips → Auto-generate captions
   - Style: White text, semi-transparent dark background bar, bottom-center
   - This makes the video watchable on mute (judges often skim videos)

**7. Color correction on screen recordings**
   - Select each screen recording clip → Adjust color
   - Slight boost: contrast +5, temperature -3 (cooler = more "tech")
   - Keeps screen recordings visually consistent with Sora b-roll

### Timeline Visualization

```
0:00        0:20        0:35        1:00        1:25        1:55        2:20   2:35   2:50  3:00
|           |           |           |           |           |           |      |      |     |
|  SORA A1  |  SORA A2  |   SR1     |   SR2     |   SR3     |   SR4    | SR5  |SOR A3|A4   |
| overload  |  dawn/TUI |  daemon   | transcr.  |  digest   |  triage  | chat |arch  |close|
|           |           |           |           |           |          |      |      |     |
|---V1------|---V2------|---V3------|---V4------|---V5------|---V6-----|--V7--|--V8--|--V9-|
|   stats   | title card| lower 3rd | lower 3rd | lower 3rd | lower 3d| lo3  |calls |stats|
|           |           |           |           |           |          |      |      |     |
|========= background music (ambient tech, 10-15% volume) ============================== |
```

---

## Audio & Music

### Voiceover
- **Tool**: Clipchamp built-in Azure TTS
- **Voice**: "Guy" or "Davis" (en-US) — test both, pick whichever sounds less robotic
- **Speed**: 1.0x (bump to 1.05x if running over time)
- **No emotion modifier** — default "neutral" tone

### Background Music
- **Source**: Clipchamp royalty-free library
- **Search terms**: "ambient technology", "corporate minimal", "lo-fi focus"
- **Volume levels**:
  - Under voiceover: 10-15%
  - Visual-only moments: 30-40%
  - Opening (0:00-0:03): 40% then duck to 15% when voice starts
  - Closing (2:55-3:00): swell to 30%, fade to 0% at 3:00
- **Avoid**: Anything with lyrics, heavy drums, or "upbeat corporate"
- **Length**: Find a track that's 3+ minutes so you don't need to loop

### Sound Design (Optional, Nice-to-Have)
- Subtle "notification ping" sound effect when showing the toast notification
- Soft keyboard click when showing the TUI keystroke actions
- These can come from Clipchamp's sound effects library or Sora-generated audio

---

## Final Export Settings

| Setting | Value |
|---------|-------|
| Resolution | 1080p (1920x1080) |
| Format | MP4 (only option in Clipchamp) |
| Aspect ratio | 16:9 |
| Duration | 2:55 – 3:00 (leave a beat, don't hit exactly 3:00) |
| Filename | `PulseAgent-Demo.mp4` |

### Pre-Export Checklist

- [ ] Total duration under 3:00
- [ ] No PII visible in any frame
- [ ] All text overlays are readable at 1080p
- [ ] Captions are generated and positioned correctly
- [ ] Audio levels are consistent (no jarring volume jumps)
- [ ] Transitions are smooth (no black frames between clips)
- [ ] Opening hook grabs attention in the first 5 seconds
- [ ] Closing has the tagline and project name visible

---

## Production Schedule (Estimated)

| Step | Time | Notes |
|------|------|-------|
| Stage demo data in PULSE_HOME | 30 min | Create/curate realistic items |
| Generate Sora 2 clips (A1-A5) | 45 min | ~10 min per clip including iteration |
| Record screen captures (SR1-SR6) | 45 min | Multiple takes per scene |
| Generate voiceover in Clipchamp | 20 min | Type script, generate, preview |
| Assemble timeline | 45 min | Place clips, add transitions |
| Add text overlays + lower thirds | 30 min | Match timing to voiceover |
| Add music + captions | 15 min | Select track, adjust levels |
| Review + final tweaks | 20 min | Watch 3x, fix timing issues |
| Export | 5 min | |
| **Total** | **~4 hours** | Can be done in one focused session |

---

## Tips for a Winning Video

1. **First 5 seconds matter most** — judges decide quickly. The information overload visual + "8 meetings yesterday" hook should be immediately compelling.

2. **Show, don't tell** — every claim in the voiceover should have a matching visual. "Cross-references what you've handled" → show the filtered digest. "One keystroke" → show the keystroke.

3. **Speed is your friend** — 2-3x on browser navigation looks impressive and saves time. Normal speed on the "wow" moments (transcript scrolling, draft appearing, streaming response).

4. **Consistent visual language** — same font, same lower-third style, same color palette throughout. The Sora clips provide cinematic polish; the screen recordings provide authenticity. Don't fight the contrast — embrace it.

5. **End strong** — the stats-on-screen closing is the last thing judges see. Make each number land with a beat of silence between them.

6. **The tagline is your brand** — "Copilot helps when you ask. Pulse Agent works when you don't." appears twice (intro + closing) for memorability.
