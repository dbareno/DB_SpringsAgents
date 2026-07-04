"""
scripts/launcher.py
─────────────────────────────────────────────────────────────────────────────
Entry point for the standalone .exe version of Spring Design Agent.
Starts the FastAPI server, serves the built frontend, and opens the browser.
"""

from __future__ import annotations

import os
import socket
import sys

# ── Fix Windows console encoding ───────────────────────────────────────
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except AttributeError:
        pass  # Some frozen builds may not support reconfigure

import webbrowser
from threading import Timer

# ── Resolve project root ────────────────────────────────────────────────
if getattr(sys, "frozen", False):
    PROJECT_ROOT = sys._MEIPASS
else:
    PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

sys.path.insert(0, PROJECT_ROOT)

HOST = "127.0.0.1"
PORT = 8000


# ── Port conflict resolution ───────────────────────────────────────────
def is_port_in_use(host: str, port: int) -> bool:
    """Return True if *host:port* is already bound."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex((host, port)) == 0


def free_port(host: str, port: int) -> None:
    """Try to kill the process holding *host:port*."""
    import subprocess

    print(f"  [!] Port {port} is already in use — attempting to free it...")
    try:
        result = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        for line in result.stdout.splitlines():
            if f"{host}:{port}" in line and "LISTENING" in line:
                parts = line.strip().split()
                pid = parts[-1]
                print(f"  [>] Killing process PID {pid}...")
                subprocess.run(
                    ["taskkill", "/F", "/PID", pid],
                    capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW,
                )
                print(f"  [✓] Freed port {port}.")
                return
    except Exception as exc:
        print(f"  [!] Could not free port automatically: {exc}")


# ── Browser launcher ───────────────────────────────────────────────────
def open_browser() -> None:
    """Open the browser to the app URL after a short delay."""
    try:
        webbrowser.open(f"http://{HOST}:{PORT}")
    except Exception as exc:
        print(f"  [!] Could not open browser: {exc}")


# ── Import app (direct object — NOT string) ────────────────────────────
try:
    from app.main import app  # noqa: E402
except ImportError as e:
    print(f"\n[ERROR] Failed to import the application: {e}")
    print("Make sure all dependencies are installed.")
    input("\nPress Enter to exit...")
    sys.exit(1)

import uvicorn


def main() -> None:
    print("=" * 55)
    print("  Spring Design Agent - Standalone")
    print("=" * 55)
    print(f"\n  -> Server  : http://{HOST}:{PORT}")
    print("  -> Browser : opening in 1.5 seconds...")
    print("  -> Stop    : Ctrl+C in this window\n")

    # ── Port check ─────────────────────────────────────────────────────
    if is_port_in_use(HOST, PORT):
        free_port(HOST, PORT)
        if is_port_in_use(HOST, PORT):
            print(f"\n[ERROR] Port {PORT} is still in use after cleanup.")
            print("Close the other application manually and try again.")
            input("Press Enter to close...")
            sys.exit(1)

    Timer(1.5, open_browser).start()

    try:
        uvicorn.run(
            app,
            host=HOST,
            port=PORT,
            log_level="info",
            reload=False,
        )
    except KeyboardInterrupt:
        raise  # handled by outer block
    except Exception:
        print("\n[ERROR] The server encountered an error:")
        import traceback

        traceback.print_exc()
        print("\nPress Ctrl+C to close this window...")
        # Don't exit immediately — let the user read the error
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            pass
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nServer stopped. Goodbye!")
        sys.exit(0)
    except Exception:
        # Last-resort catch-all (shouldn't normally reach here)
        import traceback

        traceback.print_exc()
        try:
            input("\nPress Enter to close...")
        except (EOFError, KeyboardInterrupt):
            pass
        sys.exit(1)
