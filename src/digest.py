"""Internal digest — reads local content and generates a structured daily digest.

Two-phase approach:
1. Collection: Scan input folders for new/modified files, extract text content
2. Analysis: Send content to GHCP SDK agent for structured summarization

The agent generates: TLDRs, decisions, action items, risk flags, and a
"Needs Your Attention" section — all based on standing instructions.
"""

import json
from datetime import datetime
from pathlib import Path

from copilot import CopilotClient
from copilot.generated.session_events import SessionEventType

from config import load_config
from session import build_session_config, PROJECT_ROOT, OUTPUT_DIR
from tools import get_tools


# Max characters to send per file (avoid blowing up the context window)
MAX_CHARS_PER_FILE = 50_000
# Max total characters for all content in a single digest run
MAX_TOTAL_CHARS = 300_000


def _print(text: str):
    """Print with ASCII-safe encoding to avoid charmap errors on Windows."""
    print(text.encode("ascii", "replace").decode("ascii"))


# --- Phase 1: Content Collection ---

def _load_digest_state(state_file: Path) -> dict:
    """Load the incremental processing state (which files have been digested)."""
    if state_file.exists():
        return json.loads(state_file.read_text(encoding="utf-8"))
    return {"processed": {}}


def _save_digest_state(state_file: Path, state: dict):
    """Save the incremental processing state."""
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _extract_text(filepath: Path) -> str | None:
    """Extract text content from a file based on its extension.

    Returns text content or None if the file type isn't supported yet.
    """
    ext = filepath.suffix.lower()

    # Plain text files — read directly
    if ext in (".txt", ".md", ".vtt", ".csv", ".eml"):
        try:
            return filepath.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                return filepath.read_text(encoding="latin-1")
            except Exception:
                return None

    # Word documents
    if ext == ".docx":
        try:
            import docx
            doc = docx.Document(str(filepath))
            return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
        except ImportError:
            _print(f"    WARNING: python-docx not installed, skipping .docx")
            return None
        except Exception as e:
            _print(f"    WARNING: Failed to read .docx: {e}")
            return None

    # PowerPoint
    if ext == ".pptx":
        try:
            from pptx import Presentation
            prs = Presentation(str(filepath))
            text_parts = []
            for slide_num, slide in enumerate(prs.slides, 1):
                slide_text = []
                for shape in slide.shapes:
                    if hasattr(shape, "text") and shape.text.strip():
                        slide_text.append(shape.text)
                if slide_text:
                    text_parts.append(f"[Slide {slide_num}]\n" + "\n".join(slide_text))
            return "\n\n".join(text_parts)
        except ImportError:
            _print(f"    WARNING: python-pptx not installed, skipping .pptx")
            return None
        except Exception as e:
            _print(f"    WARNING: Failed to read .pptx: {e}")
            return None

    # PDF
    if ext == ".pdf":
        try:
            import PyPDF2
            with open(filepath, "rb") as f:
                reader = PyPDF2.PdfReader(f)
                text_parts = []
                for page in reader.pages:
                    text = page.extract_text()
                    if text and text.strip():
                        text_parts.append(text)
                return "\n\n".join(text_parts)
        except ImportError:
            _print(f"    WARNING: PyPDF2 not installed, skipping .pdf")
            return None
        except Exception as e:
            _print(f"    WARNING: Failed to read .pdf: {e}")
            return None

    # Excel — just extract cell values as CSV-like text
    if ext == ".xlsx":
        try:
            import openpyxl
            wb = openpyxl.load_workbook(str(filepath), read_only=True, data_only=True)
            text_parts = []
            for sheet_name in wb.sheetnames:
                ws = wb[sheet_name]
                rows = []
                for row in ws.iter_rows(values_only=True):
                    cells = [str(c) if c is not None else "" for c in row]
                    if any(cells):
                        rows.append(" | ".join(cells))
                if rows:
                    text_parts.append(f"[Sheet: {sheet_name}]\n" + "\n".join(rows[:200]))
            wb.close()
            return "\n\n".join(text_parts)
        except ImportError:
            _print(f"    WARNING: openpyxl not installed, skipping .xlsx")
            return None
        except Exception as e:
            _print(f"    WARNING: Failed to read .xlsx: {e}")
            return None

    return None


def collect_content(config: dict) -> list[dict]:
    """Scan configured input folders and collect text content from new files.

    Returns a list of dicts: {path, type, name, content, size}
    """
    digest_cfg = config.get("digest", {})
    input_paths = digest_cfg.get("input_paths", [])
    supported_ext = set(digest_cfg.get("supported_extensions",
                                        [".vtt", ".txt", ".md", ".docx", ".pptx",
                                         ".pdf", ".xlsx", ".csv", ".eml"]))
    incremental = digest_cfg.get("incremental", True)
    state_file = PROJECT_ROOT / digest_cfg.get("state_file", "output/.digest-state.json")

    state = _load_digest_state(state_file) if incremental else {"processed": {}}
    processed = state.get("processed", {})
    collected = []
    total_chars = 0

    for path_cfg in input_paths:
        folder = Path(path_cfg["path"])
        # Support both absolute and relative (to project root) paths
        if not folder.is_absolute():
            folder = PROJECT_ROOT / folder
        content_type = path_cfg.get("type", "unknown")

        if not folder.exists():
            _print(f"  Input path does not exist (creating): {folder}")
            folder.mkdir(parents=True, exist_ok=True)
            continue

        _print(f"  Scanning {folder} (type: {content_type})...")

        for filepath in sorted(folder.rglob("*")):
            if not filepath.is_file():
                continue
            if filepath.suffix.lower() not in supported_ext:
                continue
            if filepath.name.startswith("."):
                continue

            # Incremental: skip files already processed (same path + same mtime)
            file_key = str(filepath)
            file_mtime = filepath.stat().st_mtime
            if incremental and file_key in processed:
                if processed[file_key] >= file_mtime:
                    continue

            # Extract text
            text = _extract_text(filepath)
            if not text or not text.strip():
                _print(f"    SKIP (empty/unreadable): {filepath.name}")
                continue

            # Truncate if too large
            if len(text) > MAX_CHARS_PER_FILE:
                text = text[:MAX_CHARS_PER_FILE] + f"\n\n[... truncated at {MAX_CHARS_PER_FILE} chars]"

            if total_chars + len(text) > MAX_TOTAL_CHARS:
                _print(f"    STOP: Total content limit reached ({MAX_TOTAL_CHARS} chars)")
                break

            collected.append({
                "path": str(filepath),
                "type": content_type,
                "name": filepath.name,
                "content": text,
                "size": len(text),
            })
            total_chars += len(text)

            # Mark as processed
            processed[file_key] = file_mtime

    # Save updated state
    state["processed"] = processed
    state["last_run"] = datetime.now().isoformat()
    _save_digest_state(state_file, state)

    return collected


# --- Phase 2: LLM Analysis via GHCP SDK ---

async def run_digest(client: CopilotClient, config: dict):
    """Run a full digest cycle: collect content → analyze → write daily digest."""
    _print("\n=== Digest cycle start ===")

    # Phase 1: Collect content
    _print("Phase 1: Collecting content from input folders...")
    items = collect_content(config)

    if not items:
        _print("  No new content to process. Digest cycle complete.")
        _print("=== Digest cycle end ===")
        return

    _print(f"  Collected {len(items)} items:")
    for item in items:
        _print(f"    - [{item['type']}] {item['name']} ({item['size']} chars)")

    # Phase 2: Send to GHCP SDK agent for analysis
    _print("\nPhase 2: Sending content to agent for analysis...")

    session_config = build_session_config(config, mode="digest", tools=get_tools())
    session = await client.create_session(session_config)

    session.on(lambda event: _log_event(event))

    try:
        # Build the prompt with all collected content
        prompt = _build_digest_prompt(items, config)
        _print(f"  Prompt size: {len(prompt)} chars")
        _print("  Agent working...\n")

        response = await session.send_and_wait({"prompt": prompt}, timeout=600)

        if not response:
            _print("\nNo response from agent (timed out).")

    finally:
        await session.destroy()

    _print("\n=== Digest cycle end ===")


def _build_digest_prompt(items: list[dict], config: dict) -> str:
    """Build the analysis prompt containing all collected content."""
    date_str = datetime.now().strftime("%Y-%m-%d")
    digest_cfg = config.get("digest", {})
    priorities = digest_cfg.get("priorities", [])
    priorities_str = "\n".join(f"- {p}" for p in priorities)

    # Group items by type
    by_type: dict[str, list[dict]] = {}
    for item in items:
        by_type.setdefault(item["type"], []).append(item)

    # Build content sections
    content_sections = []
    for content_type, type_items in by_type.items():
        section = f"### {content_type.title()} ({len(type_items)} files)\n"
        for item in type_items:
            section += f"\n---\n#### File: {item['name']}\n```\n{item['content']}\n```\n"
        content_sections.append(section)

    content_block = "\n".join(content_sections)

    return f"""Analyze the following content and generate a daily digest for {date_str}.

## Your Priorities
{priorities_str}

## Content to Process
{content_block}

## Instructions

Generate a structured daily digest. Use the `write_output` tool to save it as `digests/{date_str}.md`.

The digest MUST follow this exact structure:

```markdown
# Daily Digest — {date_str}
## {{count}} items processed

### Needs Your Attention (urgent items first)
- [URGENT] ... (escalations, risks, customer complaints)
- [ACTION] ... (action items assigned to me, with deadlines)
- [REVIEW] ... (documents needing my review)

### Meeting TLDRs
For each meeting transcript, provide:
#### {{Meeting Title}}
- **Duration**: ...
- **Attendees**: ... (list key participants)
- **TLDR**: 3-5 bullet points of what was discussed
- **Decisions Made**: ... (if any)
- **Action Items**: who → what → deadline
- **Relevant to Me**: anything specifically mentioning the owner or their responsibilities

### Documents & Emails Scanned
For each non-transcript item:
- **{{filename}}**: 2-3 sentence summary, flagging anything that needs attention

### Risk & Escalation Flags
- Any customer complaints, churn risks, escalations, deadline pressures
```

CRITICAL:
- Be SPECIFIC. Use names, dates, numbers from the content.
- Do NOT write vague summaries like "discussed various topics".
- If a transcript mentions action items, extract EVERY one with the assignee name.
- If nothing needs attention, say so explicitly — don't invent urgency.
- Use `log_action` to log each file you analyze.
- Use `write_output` to save the final digest. Filename: `digests/{date_str}.md`
"""


def _log_event(event):
    """Log streaming events from the agent to terminal."""
    event_type = getattr(event, "type", None)

    if event_type == SessionEventType.ASSISTANT_MESSAGE_DELTA:
        data = getattr(event, "data", None)
        if data and data.delta_content:
            text = data.delta_content.encode("ascii", "replace").decode("ascii")
            print(text, end="", flush=True)

    elif event_type == SessionEventType.ASSISTANT_MESSAGE:
        print(flush=True)

    elif event_type == SessionEventType.TOOL_EXECUTION_START:
        data = getattr(event, "data", None)
        tool_name = data.tool_name if data and data.tool_name else "unknown"
        mcp = f" ({data.mcp_server_name})" if data and data.mcp_server_name else ""
        _print(f"\n>> [TOOL] {tool_name}{mcp}")

    elif event_type == SessionEventType.TOOL_EXECUTION_COMPLETE:
        data = getattr(event, "data", None)
        if data and data.result:
            preview = str(data.result)[:300]
            _print(f"<< [RESULT] {preview}")
