"""
scripts/build_exe.py
─────────────────────────────────────────────────────────────────────────────
Build script for the standalone .exe.
1. Build the Next.js frontend as static export
2. Run PyInstaller to create the .exe

Usage:
    python scripts/build_exe.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent


def build_frontend() -> None:
    """Build the Next.js frontend as static export (frontend/out/)."""
    print("=" * 60)
    print("  Step 1: Building Next.js frontend...")
    print("=" * 60)

    frontend_dir = PROJECT_ROOT / "frontend"
    result = subprocess.run(
        ["npm", "run", "build"],
        cwd=str(frontend_dir),
        capture_output=False,
    )
    if result.returncode != 0:
        print("\n❌ Frontend build failed!")
        sys.exit(1)

    out_dir = frontend_dir / "out"
    if not out_dir.is_dir():
        print(f"\n❌ Frontend build output not found at {out_dir}")
        sys.exit(1)

    print(f"\n✅ Frontend built successfully → {out_dir}\n")


def build_exe() -> None:
    """Run PyInstaller to create the standalone .exe."""
    print("=" * 60)
    print("  Step 2: Packaging with PyInstaller...")
    print("=" * 60)

    spec_path = PROJECT_ROOT / "launcher.spec"
    result = subprocess.run(
        ["pyinstaller", str(spec_path), "--clean", "--noconfirm"],
        capture_output=False,
    )
    if result.returncode != 0:
        print("\n❌ PyInstaller build failed!")
        sys.exit(1)

    exe_path = PROJECT_ROOT / "dist" / "SpringDesignAgent.exe"
    if exe_path.is_file():
        print(f"\n✅ Standalone .exe created → {exe_path}")
    else:
        print(f"\n⚠️  .exe not found at expected path {exe_path}")
        print("Check the dist/ directory for the output.")


if __name__ == "__main__":
    build_frontend()
    build_exe()
    print("\n" + "=" * 60)
    print("  ✅ Build complete! .exe ready in dist/")
    print("=" * 60)
