"""Transcript collection — uses Playwright MCP to download meeting transcripts from Teams web."""

from pathlib import Path

from copilot import CopilotClient
from copilot.generated.session_events import SessionEventType

from session import build_session_config, INPUT_DIR
from tools import get_tools


async def run_transcript_collection(client: CopilotClient, config: dict):
    """Run a transcript collection cycle.

    1. Create session with WorkIQ + Playwright MCP + custom tools
    2. System prompt instructs agent to:
       - Query WorkIQ for recent meetings
       - Open Teams web via Playwright (authenticated Edge)
       - Navigate to each meeting's Recap/Transcript
       - Download and save transcript text locally
    3. Agent works autonomously — we stream events to terminal
    """
    print("\n=== Transcript collection start ===")

    # Ensure output directory exists
    transcripts_dir = Path(
        config.get("transcripts", {}).get("output_dir", str(INPUT_DIR / "transcripts"))
    )
    transcripts_dir.mkdir(parents=True, exist_ok=True)

    session_config = build_session_config(config, mode="transcripts", tools=get_tools())
    session = await client.create_session(session_config)

    session.on(lambda event: _log_event(event))

    try:
        lookback = config.get("transcripts", {}).get("lookback_days", 7)
        max_meetings = config.get("transcripts", {}).get("max_per_run", 10)

        prompt = (
            f"Collect meeting transcripts from the last {lookback} days. "
            f"Process up to {max_meetings} meetings. "
            "Follow your step-by-step workflow: "
            "1) Query WorkIQ for recent meetings. "
            "2) Check which transcripts already exist locally. "
            "3) Open Teams web in the browser. "
            "4) For each new meeting, navigate to its transcript and save it. "
            "5) Write a collection summary report. "
            "Take your time — browser navigation can be slow."
        )

        print("Agent working...\n")
        response = await session.send_and_wait({"prompt": prompt}, timeout=1200)

        if not response:
            print("\nNo response from agent (timed out).")

    finally:
        await session.destroy()

    print("\n=== Transcript collection end ===")


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
        print(f"\n>> [TOOL] {tool_name}{mcp}", flush=True)

    elif event_type == SessionEventType.TOOL_EXECUTION_COMPLETE:
        data = getattr(event, "data", None)
        if data and data.result:
            preview = str(data.result)[:300]
            print(f"<< [RESULT] {preview}", flush=True)
