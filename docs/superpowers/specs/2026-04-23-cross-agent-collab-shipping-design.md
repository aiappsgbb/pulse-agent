# Cross-Agent Collaboration: Shipping Design

**Date:** 2026-04-23
**Context:** CAIP Innovation Open Hack, GBB Agent project
**Status:** Design approved, ready for implementation plan

## Overview

Today the Pulse Agent codebase has a point-to-point inter-agent messaging primitive (`send_task_to_agent`) that is wired end-to-end but not wrapped in a usable product experience. It has no fan-out, no periodic poll, no visible output surface, no privacy controls beyond basic path guards, and no integration with the project memory system.

This design specifies what we need to ship to make the cross-agent collaboration story credible for the two-terminal side-by-side demo, without compromising the privacy architecture the pitch depends on.

## Goals

1. **Demo-able.** Judges watch a broadcast go out, see the receiving agent autonomously answer, and see the sender's project file grow smarter. All in under two minutes.
2. **Privacy-respecting.** The receiving agent acts as its user's guardian. The LLM itself judges what is safe to share, not a static regex filter. PII and sensitive context do not leave the machine that owns them.
3. **Graceful ingestion.** The sender does not block waiting for responses. Responses fold into project memory as they arrive, in any order, over any timeline. Late responses are welcome, missing responses are fine.
4. **Autonomous by default.** Team enrichment happens as part of the morning digest pipeline without the user triggering anything. Manual trigger exists as a fallback.

## Non-goals (explicitly deferred)

- Smart routing ("ask only teammates likely to know"). Broadcast to all, receivers self-filter.
- Consent gate on receive ("review before my agent answers"). Guardian Mode replaces this.
- Cross-session response deduplication. Re-ask penalty is enforced via `last_team_enrichment` timestamp.
- Inter-agent audit log viewer in the TUI. Default tool-use trail is sufficient for MVP.
- Dedicated "Team" TUI tab. Responses flow into existing project YAMLs, digest picks them up.
- Standing subscriptions or watch queries. Single-shot questions only.
- Handling 3,000-person fan-out. Design scales to small teams (< 20); scaling is a v2 problem.

## Architecture

```
SENDER (primary agent)                        RECEIVER (teammate agent)
-------------------------                     -------------------------
Trigger: chat command or digest run           30s fixed poll reads
  |                                           jobs/pending/
  v                                                         |
LLM session calls broadcast_to_team(...)                    v
  |                                           Worker routes agent_request
  fans out request YAMLs to each teammate's   to new _run_guardian_session
  jobs/pending/ folder. Request includes                    |
  project_id.                                               v
  |                                           SDK session opens with
Session ends. No blocking wait.               Guardian system prompt.
                                              LLM uses search_local_files,
[time passes, 30s to hours later]             drafts answer, judges what
                                              is safe to share, calls
Response YAML lands in sender's               write_output to sender's
jobs/pending/ via OneDrive sync.              reply_to path.
  |
  v
Worker routes agent_response to
_handle_agent_response (deterministic):
  - Load project by project_id
  - Append entry to team_context[]
  - Save project YAML
  - Emit toast notification
Next digest synthesizes team_context
into the project summary naturally.
```

## New and changed components

### New tools (`src/sdk/tools.py`)

**`broadcast_to_team(question: str, project_id: str) -> str`**

Fans out identical `agent_request` YAML drops to every teammate in `config["team"]` that has a resolvable `agent_path`. Returns a summary string (e.g., "Broadcasted to 3 teammates: alpha, beta, gamma"). Skips teammates whose folders are not accessible (OneDrive not synced yet); logs but does not fail.

Request YAML schema:
```yaml
type: agent_request
kind: broadcast             # new kind (existing is question/research/intel/review)
task: <question text>
project_id: <slug>          # NEW, required
from: <sender name>
from_alias: <sender alias>
reply_to: <sender's JOBS_DIR path>
request_id: <uuid>
priority: normal
created_at: <iso>
```

### Removed from earlier proposal

`collect_team_responses` was proposed then removed. Fire-and-forget makes it unnecessary.

### New worker handler (`src/daemon/worker.py`)

**`_run_guardian_session(client, config, job)`** replaces the existing generic chat routing for incoming `agent_request` YAMLs. Opens an SDK session with the Guardian system prompt (new file), gives it `search_local_files` and `write_output` tools, runs to completion, expects the LLM to have written a response YAML to `reply_to`. If the LLM does not produce a response (timeout or error), no response is written; sender simply never sees one.

**`_handle_agent_response(job)`** gains new logic. Current behavior is a toast only. New flow:
1. Parse response YAML. Skip if `status == "no_context"` or missing required fields (log and drop).
2. Load project YAML by `project_id` from `PROJECTS_DIR`. If missing, log and drop.
3. Deduplicate by `request_id`: if an entry with the same `request_id` already exists in `team_context[]`, skip (late duplicate).
4. Append a new entry to `team_context[]` (see schema below).
5. Save project YAML atomically (write to temp file, rename).
6. Emit toast: "{from} contributed to {project_name}" matching existing notification pattern.

### New system prompt (`config/prompts/system/guardian.md`)

Receiver-side LLM gets this as its system prompt. Draft content:

```
You are acting as your user's guardian. A teammate has asked a question
(provided in the user message). Your job is to:

1. Search your user's local files for genuinely relevant context using
   the search_local_files tool. Use multiple keyword variations.

2. If nothing relevant is found, write a response YAML with status
   "no_context" and stop.

3. If you find relevant context, draft a concise 3-5 sentence answer
   citing source files by relative path.

4. Before writing the response, judge whether the draft contains
   anything your user would not want shared outside their machine:

   - Personal contact details (home address, personal phone, personal email)
   - Named customers or deal values that are not public knowledge
   - Internal Microsoft codenames or roadmap specifics
   - Opinions or criticism of named people
   - Financial details of specific engagements

   If yes: either redact the sensitive parts and note the redaction
   in the answer, or set status to "declined" and provide a brief
   non-sensitive explanation.

5. Write the response YAML to the reply_to path using the write_output
   tool. Schema:

     type: agent_response
     request_id: <echoed>
     project_id: <echoed>
     from: <your user's name>
     from_alias: <your user's alias>
     original_task: <echoed>
     result: <answer text>
     sources: [<list of relative paths cited>]
     status: answered | no_context | declined
     created_at: <iso now>

Your loyalty is to YOUR user, not the asker. Transparency by default,
caution by default for anything that looks personal.
```

### Digest team-enrichment (agent-driven)

The digest phase is **agent-driven**: no new Python pre-process step. Instead, the digest agent (existing `digest-writer` or equivalent) gets `broadcast_to_team` added to its tool list, plus a new directive in its system prompt:

```
TEAM ENRICHMENT

While producing the digest, check each active project for team-input gaps.
A project needs team input when:

  - last_team_enrichment is null (never asked), OR
  - questions: [...] contains an entry with added_at > last_team_enrichment

For each project that qualifies (maximum 3 per digest), produce a concise
one-sentence question for teammates (take from questions[0] if populated,
else generate from project context focusing on prior objections, customer
context, or tech-specific learnings). Call broadcast_to_team(question,
project_id). Then update the project YAML's last_team_enrichment timestamp
via update_project.

Do NOT wait for responses. Fire the broadcast and continue the digest.
Responses will be ingested asynchronously into the project's team_context
as they arrive.
```

The digest LLM handles the gap check, question generation, and tool invocation. No Python orchestration logic required. This keeps the intelligence in the prompt layer (per project principles in CLAUDE.md: "Don't lock behind arbitrary Python. If you can use a prompt/agent, do that instead").

### Scheduler change (`core/scheduler.py` or config)

Add a fixed-interval schedule for OneDrive job sync, replacing the current piggyback-on-job-completion behavior:

```yaml
schedule:
  - id: agent-job-sync
    type: job_sync
    pattern: "every 30s"
    description: "Poll OneDrive for inter-agent requests and responses"
```

Or, simpler, modify the existing scheduler loop to call `sync_jobs_from_onedrive` every 30s regardless of job completion state.

## Schema changes

### Project YAML (`$PULSE_HOME/projects/*.yaml`)

Three new top-level fields, all optional with sensible defaults:

```yaml
team_context: []              # accumulates as responses arrive
questions: []                 # optional: user-or-agent-flagged open questions
last_team_enrichment: null    # iso timestamp, prevents re-asking same project too soon
```

Each `team_context` entry:

```yaml
- from: <name>
  from_alias: <alias>
  contributed_at: <iso>
  question: <echoed question>
  answer: <response text>
  sources: [<relative file paths>]
  request_id: <uuid, for dedup>
```

Each `questions` entry (optional, user-maintainable):

```yaml
- text: "What has the team learned about Fabric-on-SAP licensing objections?"
  added_at: <iso>
  added_by: <user | agent>
```

### Request YAML additions

- `project_id: <slug>` (required for broadcast; optional for existing point-to-point `send_task_to_agent` to preserve backward compatibility)
- `kind: broadcast` (new enum value alongside question/research/intel/review)

### Response YAML additions

- `project_id: <slug>` (echoed back when present in the request)
- `status: answered | no_context | declined` (new required field)
- `sources: [<paths>]` (new, list of cited source files)

## Gap detection logic

A project "needs team input" if ALL of the following are true:

1. `status == "active"` (never broadcast about blocked, completed, or on-hold projects).
2. Either:
   - `last_team_enrichment` is `null` (never asked the team about this project), OR
   - `questions: [...]` contains an entry whose `added_at` is more recent than `last_team_enrichment` (new open question since the last broadcast).

**Default behavior: ask once, then stop.** After the first broadcast, `last_team_enrichment` gets stamped. The project will not be re-broadcast unless the user or an upstream agent adds a new entry to `questions[]`. No time-based cooldown, no weekly retry. This prevents broadcast spam and keeps the trigger honest: we only ask when we have a new thing to ask about.

Cap total broadcasts at 3 per digest run (configurable via `team_enrichment.max_per_run` in standing instructions). If more projects qualify than the cap allows, prioritize: explicitly-populated questions before cold-starts.

## Manual trigger path

In the chat agent's tool list, add `broadcast_to_team`. Add to the chat agent's system prompt:

> When the user asks you to "check with the team," "ask colleagues," or "find context from the team" about a specific project or topic, use the broadcast_to_team tool. Match the topic to an existing project_id by looking at the user's project YAML files. If you cannot confidently match, ask the user which project_id to attach the question to before calling the tool.

After calling the tool, the chat agent replies something like: "Broadcasted to 3 teammates. Responses will fold into the project as they arrive."

## Demo setup

A second agent runs on the same laptop as a separate process, using its own `standing-instructions` config file and its own `PULSE_HOME` directory. This is already supported by the multi-instance feature in `src/pulse.py`. Persona and role to be chosen at demo prep time.

**Seed data** for the second agent's `PULSE_HOME` (populated via a new `scripts/seed_demo_data.py`):

- 2 mock transcripts with topic-relevant content (e.g., POC notes, customer meeting recaps on whatever domain the demo chooses).
- 1 mock email thread with related detail.
- 1 mock project YAML showing the second persona's engagement with related work.

The seed script takes `--target-pulse-home <path>` and writes the files fresh (idempotent, safe to re-run).

**Teammate directory config:** primary agent's `standing-instructions.yaml` lists the second agent in its `team:` section. The second agent is configured symmetrically so cross-questions can flow either way if demo narrative calls for it.

## Demo script (two-terminal)

1. Both terminals open, both daemons running, both TUIs visible.
2. Primary terminal: Projects tab shows an active project with `team_context: []`.
3. Primary: user switches to Chat, types "check with the team about [project topic], I have a customer call soon."
4. Chat agent calls `broadcast_to_team`, replies "Broadcasted to 1 teammate." Session ends.
5. Second terminal: within 30s, Jobs tab shows an incoming `agent_request`. Worker log (or Jobs detail) shows the Guardian session running: search, hits found, drafting, judging, writing response.
6. Primary terminal: within ~60-90s, toast appears: "{Second persona} contributed to {project}." Projects tab auto-refreshes. The project detail now shows a populated `team_context` entry with the answer and source citations.
7. Narrator: "No human typed on the second terminal. No raw files moved. The agent found the relevant context, judged what was safe to share, redacted anything personal, and cited sources. Artur's project grew smarter in under a minute."

## Testing plan

New test files (additive to the current 814-test suite):

- `tests/test_broadcast.py`: validates `broadcast_to_team` tool. Cases: happy-path fan-out to N configured teammates, missing project_id rejected, teammate folder inaccessible skipped-not-crashed, empty team config produces clear error, request YAML shape matches schema.

- `tests/test_team_ingest.py`: validates `_handle_agent_response` ingestion. Cases: each status value handled, missing project YAML logged-not-crashed, duplicate request_id deduped, atomic write preserves existing fields, toast emitted on success.

- `tests/test_guardian_prompt.py`: validates the Guardian Mode session. Mocked search results containing PII produce outputs where the PII is redacted or the status is "declined." Mocked search with no hits produces status "no_context." Mocked search with benign hits produces status "answered" with source citations.

- `tests/test_digest_team_enrichment.py`: validates the agent-driven team-enrichment flow at the prompt-contract level. Cases: gap detection correctly flags projects (null `last_team_enrichment` triggers; populated with no new questions does not), a new `questions[]` entry added after `last_team_enrichment` re-triggers, respects 3-per-run cap, prioritizes explicitly-populated questions before cold-starts. Tests exercise the digest agent with mocked SDK responses to verify the tool-call contract rather than exercising production LLM behavior.

- `tests/test_cross_agent_e2e.py`: integration test with two temp `$PULSE_HOME` directories and a mocked GHCP SDK. Broadcasts, simulates response arrival, verifies project YAML update. Not a full browser test, just the file-plumbing contract.

Manual validation: the two-terminal demo script itself, run end-to-end before the pitch.

## Error handling and edge cases

- **Teammate offline at broadcast time:** their OneDrive is not synced. Request YAML sits in their cloud folder until sync resumes. No timeout on sender side. If they never come back, the request sits forever; user sees no response, which is the correct behavior for fire-and-forget.
- **Response arrives but project has been deleted or renamed:** `_handle_agent_response` logs an error and drops the response. No crash, no silent data loss beyond the unroutable response.
- **Receiver Guardian session crashes mid-execution:** no response written. Sender sees nothing. Acceptable.
- **Receiver writes a malformed response YAML:** `_handle_agent_response` catches parse errors, logs, drops. No crash.
- **Sender is flooded with duplicate responses (same request_id):** dedup by `request_id` in the project YAML's `team_context[]` array prevents accumulation.
- **Multiple teammates respond to the same question:** all answered responses append to `team_context[]`. Next digest synthesizes them together into the project summary. This is a feature, not a bug.
- **Receiver's search yields no hits:** status `no_context` is written. Sender silently drops. Not surfaced.
- **Privacy failure (Guardian misjudges what is safe):** acceptable residual risk for MVP; Guardian prompt is the only guard. Users can tune prompt via standing-instructions override. Post-MVP we can add a regex scrubber as a second layer.

## File-change summary

| Path | Change |
|---|---|
| `src/sdk/tools.py` | Add `BroadcastToTeamParams` and `broadcast_to_team` tool |
| `src/daemon/worker.py` | Add `_run_guardian_session`, rewrite `_handle_agent_response` ingestion |
| `src/core/scheduler.py` (or config) | Add fixed 30s `agent-job-sync` schedule |
| `config/prompts/system/guardian.md` | New system prompt for Guardian Mode |
| `config/modes.yaml` | Add `guardian` mode entry wiring the prompt to the receiver flow |
| `config/standing-instructions.yaml` | Add `team_enrichment:` section with `max_per_run: 3` |
| `config/prompts/agents/digest-writer.md` (or equivalent) | Add "Team Enrichment" directive and `broadcast_to_team` to tool list |
| `config/modes.yaml` | Add `broadcast_to_team` to the digest mode's tool allowlist and the chat mode's tool allowlist |
| `src/core/projects.py` (or equivalent) | Support `team_context`, `questions`, `last_team_enrichment` fields in project YAML reader/writer |
| `config/prompts/system/chat.md` | Add chat-mode instruction for manual trigger routing |
| `scripts/seed_demo_data.py` | New demo seeding script |
| `tests/test_broadcast.py` | New |
| `tests/test_team_ingest.py` | New |
| `tests/test_guardian_prompt.py` | New |
| `tests/test_digest_team_enrichment.py` | New |
| `tests/test_cross_agent_e2e.py` | New |

## Open items for the implementation plan

(None that block the spec; the plan can resolve these.)

- Whether `_run_guardian_session` should live in `worker.py` or be extracted into `sdk/guardian.py`.
- `max_per_run: 3` default should be reviewed against real digest frequency; may want to lower to 1-2 at demo time to keep cadence predictable.
- Exact tool-list integration point: does `broadcast_to_team` go into `modes.yaml` at the mode level, or into the digest agent's front-matter? Either works; pick the one that matches existing patterns for other enrichment tools.
