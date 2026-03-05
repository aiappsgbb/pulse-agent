"""Test: Teams message with ask_user confirmation flow.

Simulates the full flow:
1. Agent searches for recipient on Teams
2. Agent calls ask_user with recipient details
3. We auto-confirm with "yes" (or "no" to test abort)

Usage:
    python tests/test_teams_send.py          # auto-confirms YES
    python tests/test_teams_send.py --deny   # auto-denies NO
    python tests/test_teams_send.py --manual # waits for manual input in terminal
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from copilot import CopilotClient, MCPLocalServerConfig, PermissionRequest, PermissionRequestResult
from copilot.generated.session_events import SessionEventType
from core.config import load_config
from core.constants import PROJECT_ROOT
from sdk.session import auto_approve_handler
from sdk.agents import playwright_mcp_config as _playwright_mcp

MANUAL = "--manual" in sys.argv
DENY = "--deny" in sys.argv


def _safe(text: str) -> str:
    return text.encode("ascii", "replace").decode("ascii")


def _log_event(event):
    t = getattr(event, "type", None)
    d = getattr(event, "data", None)

    if t == SessionEventType.ASSISTANT_MESSAGE_DELTA:
        if d and d.delta_content:
            print(_safe(d.delta_content), end="", flush=True)
    elif t == SessionEventType.ASSISTANT_MESSAGE:
        print(flush=True)
    elif t == SessionEventType.TOOL_EXECUTION_START:
        tool = d.tool_name if d and d.tool_name else "?"
        mcp = f" ({d.mcp_server_name})" if d and d.mcp_server_name else ""
        args = ""
        if d and hasattr(d, "arguments") and d.arguments:
            args = f" {_safe(str(d.arguments)[:300])}"
        elif d and hasattr(d, "input") and d.input:
            args = f" {_safe(str(d.input)[:300])}"
        print(_safe(f"\n>> [TOOL] {tool}{mcp}{args}"), flush=True)
    elif t == SessionEventType.TOOL_EXECUTION_COMPLETE:
        if d and d.result:
            print(_safe(f"<< [RESULT] {str(d.result)[:500]}"), flush=True)
    elif t and "subagent" in str(t).lower():
        agent = getattr(d, "agent_name", "") if d else ""
        print(f"\n== [SUBAGENT] {t} - {agent}", flush=True)


async def user_input_handler(request, context):
    """Simulates user confirmation — auto-yes, auto-no, or manual."""
    question = request.get("question", "")
    choices = request.get("choices", [])

    print(f"\n{'='*60}")
    print(f"ASK_USER: {question}")
    if choices:
        print(f"Choices: {choices}")
    print(f"{'='*60}")

    if MANUAL:
        answer = input("Your answer: ").strip()
    elif DENY:
        answer = "no"
        print(f">> Auto-denying: {answer}")
    else:
        answer = "yes"
        print(f">> Auto-confirming: {answer}")

    return {"answer": answer, "wasFreeform": True}


TEAMS_PROMPT = """You are a Teams messaging assistant with Playwright browser automation.

## Your Task
Send a message to the specified person on Microsoft Teams.

## Steps
1. Use browser_navigate to go to https://teams.microsoft.com
2. Wait 5 seconds for the page to load
3. Take a browser_snapshot to see the current state
4. Use the search box to search for the recipient by name
5. Wait for results, take a browser_snapshot
6. Read the search results — get the person's full name, email, and title
7. **MANDATORY**: Call ask_user to confirm before sending:
   "Confirm Teams message:
    To: [Full Name] ([email])
    Message: [exact message text]

    Reply YES to send, or NO to cancel."
8. If user says NO → abort immediately, do NOT send
9. If user says YES → click on the person, open the chat, type the message, click Send
10. Confirm the message was sent

## CRITICAL RULES
- NEVER send without ask_user confirmation
- If multiple people match, list ALL in ask_user and let user pick
- If user says anything other than YES → abort
"""


async def main():
    mode = "MANUAL" if MANUAL else ("DENY" if DENY else "AUTO-YES")
    print(f"=== Teams Send Test (confirmation flow, mode={mode}) ===")

    config = load_config()

    session_config = {
        "model": "gpt-4.1",
        "system_message": {"mode": "replace", "content": TEAMS_PROMPT},
        "mcp_servers": {"playwright": _playwright_mcp(config)},
        "working_directory": str(PROJECT_ROOT),
        "streaming": True,
        "on_permission_request": auto_approve_handler,
        "on_user_input_request": user_input_handler,
        "excluded_tools": ["fetch_copilot_cli_documentation"],
    }

    client = CopilotClient({"cwd": str(PROJECT_ROOT)})
    print("Starting SDK client...")
    await client.start()
    print(f"Connected: {client.get_state()}")

    session = await client.create_session(session_config)
    session.on(lambda event: _log_event(event))

    prompt = (
        'Send a message to "Artur Zielinski" on Microsoft Teams saying: '
        '"Testing confirmation flow - please ignore."'
    )

    print(f"\nPrompt: {prompt}\n")

    try:
        response = await session.send_and_wait({"prompt": prompt}, timeout=180)
        print("\n" + "=" * 60)
        if response and response.data and response.data.content:
            print(f"Final: {_safe(response.data.content[:500])}")
        else:
            print("No response / timed out")
    except Exception as e:
        print(f"\nError: {e}")
    finally:
        await session.destroy()
        await client.stop()
        print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
