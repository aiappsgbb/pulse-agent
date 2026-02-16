"""Always-on monitoring — reads M365 state via WorkIQ and acts on standing instructions."""

from copilot import CopilotClient

from tools import get_tools
from utils import agent_session, log


async def run_monitoring_cycle(client: CopilotClient, config: dict):
    """Run a single monitoring cycle.

    1. Create session with triage model + WorkIQ + custom tools
    2. System prompt carries standing instructions
    3. User prompt triggers the cycle
    4. Agent queries WorkIQ, evaluates, acts, logs
    """
    log.info("=== Monitoring cycle start ===")

    async with agent_session(client, config, "triage", tools=get_tools()) as session:
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

        log.info("Agent working...")
        response = await session.send_and_wait({"prompt": prompt}, timeout=600)

        if not response:
            log.warning("No response from agent (timed out).")

    log.info("=== Monitoring cycle end ===")
