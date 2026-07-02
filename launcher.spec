# launcher.spec — PyInstaller spec for Spring Design Agent standalone .exe

import sys
from pathlib import Path

import PyInstaller.__main__

PROJECT_ROOT = Path(__file__).parent

a = Analysis(
    [str(PROJECT_ROOT / "scripts" / "launcher.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
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
        "chromadb",
        "httpx",
        "dotenv",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
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
        "PIL",
        "Pillow",
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
