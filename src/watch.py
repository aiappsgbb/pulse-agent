"""Pulse Agent terminal dashboard — entry point.

Usage:
    python src/watch.py

Launches the interactive Textual TUI. The daemon (python src/main.py) can
run independently — the TUI reads/writes PULSE_HOME files directly via
file-based IPC. No daemon required for browsing; daemon required for chat.
"""

import sys
from pathlib import Path

# Add src/ to sys.path so imports work when running from project root
_src = Path(__file__).parent
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from dotenv import load_dotenv
load_dotenv()

from tui.app import PulseApp


def main() -> None:
    app = PulseApp()
    app.run()


if __name__ == "__main__":
    main()
