"""Quick test: does Playwright MCP work with GHCP SDK?

Opens a simple webpage in Edge, takes a screenshot, confirms browser automation works.
"""

import asyncio
from copilot import CopilotClient, MCPLocalServerConfig, PermissionRequest, PermissionRequestResult
from copilot.generated.session_events import SessionEventType


def auto_approve(request: PermissionRequest, context: dict) -> PermissionRequestResult:
    return PermissionRequestResult(kind="approved", rules=[])


async def main():
    print("Starting GHCP SDK client...")
    client = CopilotClient()
    await client.start()
    print(f"Client state: {client.get_state()}")

    session = await client.create_session({
        "model": "claude-sonnet",
        "system_message": {
            "base": (
                "You have Playwright browser automation. "
                "Use it to complete the user's request. "
                "Be concise in your responses."
            ),
        },
        "mcp_servers": {
            "playwright": MCPLocalServerConfig(
                type="local",
                command="npx",
                args=[
                    "@playwright/mcp@latest",
                    "--browser", "msedge",
                    "--headless",
                    "--user-data-dir",
                    "C:/Users/arzielinski/AppData/Local/ms-playwright/mcp-msedge-profile",
                ],
                tools=["*"],
                timeout=120000,
            ),
        },
        "streaming": True,
        "on_permission_request": auto_approve,
    })

    session.on(lambda event: log_event(event))

    print("\nSending prompt...\n")
    response = await session.send_and_wait(
        {"prompt": (
            "Navigate to https://teams.microsoft.com. "
            "Wait for the page to fully load. "
            "Tell me: 1) Are we logged in or do you see a sign-in page? "
            "2) What do you see on the page (main UI elements)? "
            "3) Can you see the Calendar or Chat sections in the left nav?"
        )},
        timeout=120,
    )

    if not response:
        print("\nNo response (timed out).")

    await session.destroy()
    await client.stop()
    print("\nDone.")


def log_event(event):
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
            preview = str(data.result)[:200]
            print(f"<< [RESULT] {preview}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
