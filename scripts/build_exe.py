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

import shutil
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
    out_dir = frontend_dir / "out"

    # Check if frontend is already built
    if out_dir.is_dir() and (out_dir / "index.html").is_file():
        print(f"[+] Frontend already built at {out_dir}")
        print("    (skip build; delete frontend/out/ to force rebuild)\n")
        return

    # Use npm.cmd on Windows, npm on Unix
    npm_cmd = "npm.cmd" if sys.platform == "win32" else "npm"
    result = subprocess.run(
        [npm_cmd, "run", "build"],
        cwd=str(frontend_dir),
        capture_output=False,
    )
    if result.returncode != 0:
        print("\n[!] Frontend build failed!")
        sys.exit(1)

    if not out_dir.is_dir():
        print(f"\n[!] Frontend build output not found at {out_dir}")
        sys.exit(1)

    print(f"\n[+] Frontend built successfully -> {out_dir}\n")


def build_exe() -> None:
    """Run PyInstaller to create the standalone .exe."""
    print("=" * 60)
    print("  Step 2: Packaging with PyInstaller...")
    print("=" * 60)

    spec_path = PROJECT_ROOT / "launcher.spec"
    # Use sys.executable to ensure we run PyInstaller from the current Python environment
    result = subprocess.run(
        [sys.executable, "-m", "PyInstaller", str(spec_path), "--clean", "--noconfirm"],
        capture_output=False,
    )
    if result.returncode != 0:
        print("\n[!] PyInstaller build failed!")
        sys.exit(1)

    exe_path = PROJECT_ROOT / "dist" / "SpringDesignAgent.exe"
    if exe_path.is_file():
        print(f"\n[+] Standalone .exe created -> {exe_path}")
    else:
        print(f"\n[!] .exe not found at expected path {exe_path}")
        print("Check the dist/ directory for the output.")

    # ── Copy missing Anaconda DLLs ──────────────────────────────────────
    python_dll_dir = Path(sys.prefix) / "Library" / "bin"
    required_dlls = [
        "libcrypto-3-x64.dll",
        "libssl-3-x64.dll",
        "liblzma.dll",
        "libbz2.dll",
        "ffi.dll",
        "libexpat.dll",
        "sqlite3.dll",
    ]
    copied = 0
    for dll in required_dlls:
        src = python_dll_dir / dll
        dst = PROJECT_ROOT / "dist" / dll
        if src.is_file() and not dst.is_file():
            shutil.copy2(src, dst)
            copied += 1
    if copied:
        print(f"\n[+] Copied {copied} missing DLL(s) to dist/")
    elif python_dll_dir.is_dir():
        print(f"\n[+] All DLLs already present in dist/")


if __name__ == "__main__":
    build_frontend()
    build_exe()
    print("\n" + "=" * 60)
    print("  [+] Build complete! .exe ready in dist/")
    print("=" * 60)
