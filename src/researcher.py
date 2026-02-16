"""Deep research mission runner — picks up queued tasks and executes autonomously."""

from copilot import CopilotClient

from config import load_pending_tasks, mark_task_completed
from session import build_session_config
from tools import get_tools


async def run_pending_tasks(client: CopilotClient, config: dict):
    """Process all pending research tasks.

    For each task:
    1. Create session with powerful research model
    2. Send task description as prompt
    3. Agent works autonomously with WorkIQ + local tools
    4. Move task to completed when done
    """
    tasks = load_pending_tasks()
    if not tasks:
        print("No pending research tasks.")
        return

    for task in tasks:
        task_name = task.get("task", "unnamed")
        print(f"\n=== Research mission: {task_name} ===")

        # Allow task to override model
        session_config = build_session_config(config, mode="research", tools=get_tools())
        if "model" in task:
            session_config["model"] = task["model"]

        session = await client.create_session(session_config)

        try:
            description = task.get("description", task_name)
            output_config = task.get("output", {})
            local_path = output_config.get("local", "./output/")

            prompt = f"""Execute this research mission:

## Task
{task_name}

## Description
{description}

## Output
Write all findings and deliverables to: {local_path}
Use markdown format. Create one file per logical section if the output is large.
When complete, provide a summary of your research and key findings.
"""

            print("Sending research task to agent...")
            response = await session.send_and_wait({"prompt": prompt}, timeout=3600)

            if response:
                print(f"\nAgent response:\n{_extract_text(response)}")
            else:
                print("No response from agent (may have timed out).")

        finally:
            await session.destroy()

        mark_task_completed(task)
        print(f"=== Research mission complete: {task_name} ===")


def _extract_text(event) -> str:
    """Extract text content from a session event."""
    if hasattr(event, "data") and hasattr(event.data, "content"):
        return event.data.content
    return str(event)
