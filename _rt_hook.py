"""
PyInstaller runtime hook for Spring Design Agent.
This file is loaded at runtime to set up any necessary initialization.
"""

import sys
import os

# Fix Windows console encoding for frozen builds
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except AttributeError:
        pass
