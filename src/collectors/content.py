"""Content collection — scan folders, extract text, track incremental state."""

from datetime import datetime
from pathlib import Path

from core.constants import PROJECT_ROOT
from core.logging import log
from core.state import load_json_state, save_json_state
from collectors.extractors import extract_text

# Max characters to send per file (avoid blowing up the context window)
MAX_CHARS_PER_FILE = 50_000
# Max total characters for all content in a single digest run
MAX_TOTAL_CHARS = 400_000


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

    state = load_json_state(state_file, {"processed": {}}) if incremental else {"processed": {}}
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
            log.info(f"  Input path does not exist (creating): {folder}")
            folder.mkdir(parents=True, exist_ok=True)
            continue

        log.info(f"  Scanning {folder} (type: {content_type})...")

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
            text = extract_text(filepath)
            if not text or not text.strip():
                log.debug(f"    SKIP (empty/unreadable): {filepath.name}")
                continue

            # Truncate if too large
            if len(text) > MAX_CHARS_PER_FILE:
                text = text[:MAX_CHARS_PER_FILE] + f"\n\n[... truncated at {MAX_CHARS_PER_FILE} chars]"

            if total_chars + len(text) > MAX_TOTAL_CHARS:
                log.warning(f"    STOP: Total content limit reached ({MAX_TOTAL_CHARS} chars)")
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
    save_json_state(state_file, state)

    return collected
