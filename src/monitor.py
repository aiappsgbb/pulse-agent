"""Always-on monitoring — reads M365 state via WorkIQ and acts on standing instructions."""

from copilot import CopilotClient
from copilot.generated.session_events import SessionEventType

from session import build_session_config
from tools import get_tools


async def run_monitoring_cycle(client: CopilotClient, config: dict):
    """Run a single monitoring cycle.

    1. Create session with triage model + WorkIQ + custom tools
    2. System prompt carries standing instructions
    3. User prompt triggers the cycle
    4. Agent queries WorkIQ, evaluates, acts, logs
    """
    print("\n=== Monitoring cycle start ===")

    session_config = build_session_config(config, mode="triage", tools=get_tools())
    session = await client.create_session(session_config)

    # Stream events to terminal in real-time
    session.on(lambda event: _log_event(event))

    try:
        prompt = (
            "Run your full monitoring cycle now. Follow ALL 5 steps in your instructions. "
            "You MUST make multiple separate WorkIQ queries — do NOT try to get everything in one question. "
            "Step 1: Query emails, then drill into important ones individually. "
            "Step 2: Query calendar, then pull context for each meeting. "
            "Step 3: Query Teams threads, then read important ones in full. "
            "Step 4: Check for overdue action items and follow-ups. "
            "Step 5: Write a detailed report with specifics — names, subjects, dates, action items. "
            "Take your time. I expect at least 5 separate WorkIQ queries this cycle."
        )

        print("Agent working...\n")
        response = await session.send_and_wait({"prompt": prompt}, timeout=600)

        if not response:
            print("\nNo response from agent (timed out).")

    finally:
        await session.destroy()

    print("\n=== Monitoring cycle end ===")


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
