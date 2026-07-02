"""
scripts/launcher.py
─────────────────────────────────────────────────────────────────────────────
Entry point for the standalone .exe version of Spring Design Agent.
Starts the FastAPI server, serves the built frontend, and opens the browser.
"""

from __future__ import annotations

import os
import sys

# ── Fix Windows console encoding for emoji/Unicode support ──────────────
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

import webbrowser
from threading import Timer

# ── Resolve project root ────────────────────────────────────────────────
if getattr(sys, "frozen", False):
    # Running as PyInstaller .exe — files are in sys._MEIPASS
    PROJECT_ROOT = sys._MEIPASS
else:
    PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

sys.path.insert(0, PROJECT_ROOT)

# ── Import app directly (NOT via string — fails in PyInstaller) ─────────
try:
    from app.main import app
except ImportError as e:
    print(f"\n[ERROR] Failed to import the application: {e}")
    print("Make sure all dependencies are installed.")
    input("\nPress Enter to exit...")
    sys.exit(1)

import uvicorn

HOST = "127.0.0.1"
PORT = 8000


def open_browser() -> None:
    """Open the browser to the app URL after a short delay."""
    webbrowser.open(f"http://{HOST}:{PORT}")


def main() -> None:
    print("=" * 55)
    print("  Spring Design Agent - Standalone")
    print("=" * 55)
    print(f"\n  -> Opening browser at http://{HOST}:{PORT}")
    print("  -> Press Ctrl+C to stop the server.\n")

    Timer(1.5, open_browser).start()

    uvicorn.run(
        app,  # Direct object, NOT string — avoids import issues
        host=HOST,
        port=PORT,
        log_level="info",
        reload=False,
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nServer stopped. Goodbye!")
        sys.exit(0)
    except Exception:
        print("\n[ERROR] Unexpected error during startup:")
        import traceback
        traceback.print_exc()
        input("\nPress Enter to close...")
        sys.exit(1)
