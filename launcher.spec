# launcher.spec — PyInstaller spec for Spring Design Agent standalone .exe

import os
import sys
from pathlib import Path

# SPEC is the spec file path, provided by PyInstaller in the exec namespace
PROJECT_ROOT = Path(SPEC).parent

# ── Required DLLs from Anaconda's Library/bin ──────────────────────────
# _ssl.pyd, _hashlib.pyd, _lzma.pyd, _bz2.pyd, _ctypes.pyd, pyexpat.pyd,
# _sqlite3.pyd all depend on these — they MUST be bundled INSIDE the exe
# so they are extractable alongside the .pyd files in sys._MEIPASS.
_ANACONDA_BIN = Path(r"C:\Users\Diego\anaconda3\Library\bin")
_REQUIRED_DLLS = [
    "libcrypto-3-x64.dll",
    "libssl-3-x64.dll",
    "liblzma.dll",
    "libbz2.dll",
    "ffi.dll",
    "libexpat.dll",
    "sqlite3.dll",
]
_dll_binaries = []
for _dll in _REQUIRED_DLLS:
    _src = _ANACONDA_BIN / _dll
    if _src.is_file():
        _dll_binaries.append((str(_src), "."))

a = Analysis(
    [str(PROJECT_ROOT / "scripts" / "launcher.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=_dll_binaries,
    datas=[
        # Include the frontend static build
        (str(PROJECT_ROOT / "frontend" / "out"), "frontend/out"),
        # Include the app package
        (str(PROJECT_ROOT / "app"), "app"),
    ],
    hiddenimports=[
        "uvicorn.logging",
        "uvicorn.loops.auto",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.middleware.asgi2",
        "uvicorn.middleware.wsgi",
        "alembic",
        "alembic.config",
        "sqlalchemy",
        "sqlalchemy.ext.asyncio",
        "asyncpg",
        "langgraph",
        "langchain_core",
        "scipy",
        "scipy.optimize",
        "numpy",
        "pandas",
        "pydantic",
        "pydantic_settings",
        "aiosqlite",
        "chromadb",
        "httpx",
        "dotenv",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(PROJECT_ROOT / "_rt_hook.py")],
    excludes=[
        "tkinter",
        "PyQt5",
        "PyQt6",
        "PySide2",
        "PySide6",
        "matplotlib",
        "notebook",
        "jupyter",
        "jupyter_client",
        "ipython",
        "distributed",
        "bokeh",
        "tornado",
        "zmq",
        "cv2",
        "gevent",
        "dask",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="SpringDesignAgent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,         # Show console window for server logs
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,            # Optional: add an .ico file
)
