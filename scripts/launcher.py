"""
scripts/launcher.py
─────────────────────────────────────────────────────────────────────────────
Entry point for the standalone .exe version of Spring Design Agent.
Starts the FastAPI server, serves the built frontend, and opens the browser.
"""

from __future__ import annotations

import os
import sys
import webbrowser
from threading import Timer

import uvicorn

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HOST = "127.0.0.1"
PORT = 8000


def open_browser() -> None:
    """Open the browser to the app URL after a short delay."""
    webbrowser.open(f"http://{HOST}:{PORT}")


def main() -> None:
    print("╔══════════════════════════════════════════════════════╗")
    print("║       Spring Design Agent — Standalone              ║")
    print("╚══════════════════════════════════════════════════════╝")
    print(f"\n🌐 Opening browser at http://{HOST}:{PORT}")
    print("Press Ctrl+C to stop the server.\n")

    Timer(1.5, open_browser).start()

    uvicorn.run(
        "app.main:app",
        host=HOST,
        port=PORT,
        log_level="info",
        reload=False,
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n👋 Server stopped. Goodbye!")
        sys.exit(0)
