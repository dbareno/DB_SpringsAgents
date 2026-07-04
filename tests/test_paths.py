"""
tests/test_paths.py
─────────────────────────────────────────────────────────────────────────────
Unit tests for the writable data-directory resolver (``app.core.paths``).

Covers both server mode (project-local ``./data``) and frozen ``.exe`` mode
(``%LOCALAPPDATA%/SpringDesignAgent``), mocking ``sys.frozen`` so no real
executable packaging is required.
"""

from __future__ import annotations

import sys
from pathlib import Path

from app.core.paths import get_data_dir


class TestGetDataDirServerMode:
    def test_returns_project_local_data_dir(self):
        # sys.frozen is absent/False in normal test runs.
        assert not getattr(sys, "frozen", False)

        result = get_data_dir()

        assert result.name == "data"
        assert result.is_dir()

    def test_creates_directory_if_missing(self):
        result = get_data_dir()
        assert result.exists()
        assert result.is_dir()


class TestGetDataDirFrozenMode:
    def test_returns_localappdata_subdir_on_windows(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

        result = get_data_dir()

        assert result == tmp_path / "SpringDesignAgent"
        assert result.is_dir()

        # Cleanup handled automatically by monkeypatch (unsets sys.frozen) and
        # tmp_path (removes the temporary directory tree).

    def test_falls_back_to_home_when_localappdata_missing(self, monkeypatch):
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.delenv("LOCALAPPDATA", raising=False)

        result = get_data_dir()

        assert result == Path.home() / ".springdesignagent"
        assert result.is_dir()

        # Best-effort cleanup of the fallback directory created under $HOME
        # (monkeypatch handles unsetting sys.frozen automatically).
        try:
            result.rmdir()
        except OSError:
            pass
