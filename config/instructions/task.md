# Task Management

Pulse Agent runs as a daemon and picks up jobs from the `Pulse/Jobs/` folder in OneDrive.
To schedule work, create a `.yaml` file in that folder. The agent processes it on the next cycle (every 30 minutes) and removes the file when done.

## Job File Format

Each job is a YAML file with a `type` field. The filename doesn't matter but should be descriptive (e.g. `run-digest.yaml`).

### Run a digest
Scans local input folders, fetches RSS feeds, queries WorkIQ for inbox/Teams, and generates a filtered daily digest.

```yaml
type: digest
```

### Collect meeting transcripts
Opens Teams in Edge and scrapes transcript text from recent meetings.

```yaml
type: transcripts
```

### Run an intel brief
Fetches RSS feeds and generates a competitor/industry intel report.

```yaml
type: intel
```

### Research task
Executes an autonomous deep research mission using WorkIQ and local tools.

```yaml
type: research
task: "Short title of the research"
description: "Detailed description of what to investigate and what output you expect"
```

## Examples

**"Run my morning digest"** — Create `Pulse/Jobs/digest.yaml` with:
```yaml
type: digest
```

**"Research competitor pricing"** — Create `Pulse/Jobs/pricing-research.yaml` with:
```yaml
type: research
task: "Compare AWS Bedrock vs Azure OpenAI pricing"
description: "Pull latest public pricing for both services. Summarize key differences. Flag any changes in the last 30 days."
```

**"Grab this week's meeting transcripts"** — Create `Pulse/Jobs/transcripts.yaml` with:
```yaml
type: transcripts
```

## What happens after

- The agent picks up the job on the next 30-minute cycle
- Results appear in the corresponding `Pulse/` subfolder (digests/, intel/, pulse-signals/)
- The job file is automatically removed from `Pulse/Jobs/` once complete
- All actions are logged to the audit trail
