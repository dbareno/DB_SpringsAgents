"""
app/core/paths.py
─────────────────────────────────────────────────────────────────────────────
Resolves the writable per-user data directory used by both server mode and
the packaged ``.exe`` (frozen) mode.

* Server mode:  project-local ``./data`` directory (created if missing).
* Frozen mode:  ``%LOCALAPPDATA%/SpringDesignAgent`` on Windows (created if
  missing), since the PyInstaller bundle directory itself is read-only /
  ephemeral and must not be used for persistent state.

Consumers (added in later phases): the LangGraph checkpointer (Phase 3),
the materials DB (Phase 1), and the standards vector store (Phase 2).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_APP_DIR_NAME = "SpringDesignAgent"


def get_data_dir() -> Path:
    """
    Return a writable, per-user data directory, creating it if necessary.

    - When running as a PyInstaller-frozen executable (``sys.frozen`` is
      True), returns ``%LOCALAPPDATA%/SpringDesignAgent`` on Windows.
    - Otherwise (normal server/dev mode), returns ``./data`` relative to the
      project root.
    """
    if getattr(sys, "frozen", False):
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            data_dir = Path(local_app_data) / _APP_DIR_NAME
        else:
            # Non-Windows or missing env var fallback: use the user's home dir.
            data_dir = Path.home() / f".{_APP_DIR_NAME.lower()}"
    else:
        project_root = Path(__file__).resolve().parent.parent.parent
        data_dir = project_root / "data"

    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir
