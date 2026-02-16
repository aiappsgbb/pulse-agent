"""Deep research mission runner — picks up queued tasks and executes autonomously."""

from copilot import CopilotClient

from config import load_pending_tasks, mark_task_completed
from tools import get_tools
from utils import agent_session, log


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
        log.info("No pending research tasks.")
        return

    for task in tasks:
        task_name = task.get("task", "unnamed")
        log.info(f"=== Research mission: {task_name} ===")

        async with agent_session(client, config, "research", tools=get_tools()) as session:
            # Allow task to override model
            if "model" in task:
                pass  # model override handled via session_config before creation

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

            log.info("Sending research task to agent...")
            response = await session.send_and_wait({"prompt": prompt}, timeout=3600)

            if not response:
                log.warning("No response from agent (may have timed out).")

        mark_task_completed(task)
        log.info(f"=== Research mission complete: {task_name} ===")
