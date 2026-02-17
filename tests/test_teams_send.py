"""Quick test: send a Teams message directly via Playwright MCP at session level.

Bypasses custom agent delegation — just gives the model Playwright tools
and instructions to message someone on Teams.

Usage:
    python tests/test_teams_send.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from copilot import CopilotClient, MCPLocalServerConfig, PermissionRequest, PermissionRequestResult
from copilot.generated.session_events import SessionEventType

PROJECT_ROOT = Path(__file__).parent.parent


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
        print(f"\n== [SUBAGENT] {t} — {agent}", flush=True)


def auto_approve(request: PermissionRequest, context: dict) -> PermissionRequestResult:
    return PermissionRequestResult(kind="approved", rules=[])


TEAMS_PROMPT = """You are a Teams messaging assistant. You have Playwright browser automation tools.

## Your Task
Send a message to the specified person on Microsoft Teams.

## Steps
1. Use browser_navigate to go to https://teams.microsoft.com
2. Use browser_wait_for — wait 5 seconds for the page to load
3. Use browser_snapshot to see the current state of the page
4. Look for the Chat section or search box
5. Use browser_click or browser_type to search for the recipient
6. Once you find the right chat, type the message and send it

## Important
- Use browser_snapshot frequently to see what's on the page
- Wait after each action for the page to update
- If Teams shows a login page, STOP and report that auth is expired
"""


async def main():
    print("=== Teams Send Test (direct Playwright) ===")

    # Minimal session: just Playwright MCP + Teams instructions
    playwright_cfg = MCPLocalServerConfig(
        type="local",
        command="npx",
        args=[
            "@playwright/mcp@latest",
            "--browser", "msedge",
            "--headless",
            "--user-data-dir",
            str(Path.home() / "AppData/Local/ms-playwright/mcp-msedge-profile"),
        ],
        tools=["*"],
        timeout=120000,
    )

    session_config = {
        "model": "gpt-4.1",
        "system_message": {"mode": "replace", "content": TEAMS_PROMPT},
        "mcp_servers": {"playwright": playwright_cfg},
        "working_directory": str(PROJECT_ROOT),
        "streaming": True,
        "on_permission_request": auto_approve,
        "excluded_tools": ["fetch_copilot_cli_documentation"],
    }

    client = CopilotClient({"cwd": str(PROJECT_ROOT)})
    print("Starting SDK client...")
    await client.start()
    print(f"Connected: {client.get_state()}")

    print("Creating session with Playwright MCP...")
    session = await client.create_session(session_config)
    session.on(lambda event: _log_event(event))

    prompt = (
        'Send a message to "Artur Zielinski" on Microsoft Teams saying: '
        '"Testing Pulse Agent - please ignore this message."'
    )

    print(f"\nPrompt: {prompt}")
    print("=" * 60)

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
