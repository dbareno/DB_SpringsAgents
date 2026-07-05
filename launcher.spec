# launcher.spec — PyInstaller spec for Spring Design Agent standalone .exe

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

# SPEC is the spec file path, provided by PyInstaller in the exec namespace
PROJECT_ROOT = Path(SPEC).parent

# sqlite_vec ships its loadable SQLite extension (vec0.dll) as package data,
# loaded dynamically via sqlite_vec.load() rather than a normal Python
# import — PyInstaller's static import analysis cannot discover it on its
# own, so it must be collected explicitly or the frozen .exe will fail to
# load the extension at runtime (the exact class of bug that broke chromadb).
_sqlite_vec_datas = collect_data_files("sqlite_vec")

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
        # Include scripts package (seed_materials.py / ingest_standards.py run
        # on every startup, including inside the frozen .exe — first-launch
        # DB seed + standards corpus ingestion)
        (str(PROJECT_ROOT / "scripts"), "scripts"),
        # Bundle the starter standards corpus (Phase 2 / ADR-3) so the .exe
        # self-ingests a working dataset on first launch.
        (str(PROJECT_ROOT / "data" / "standards"), "data/standards"),
        # sqlite_vec's vec0.dll loadable extension (see note above).
        *_sqlite_vec_datas,
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
        # Multi-turn conversation checkpointer (Phase 3) — separate pip
        # package (langgraph-checkpoint-sqlite), PyInstaller's static
        # import graph analysis can miss it since it's resolved dynamically
        # via app.core.checkpointer's runtime import.
        "langgraph.checkpoint.sqlite",
        "langgraph.checkpoint.sqlite.aio",
        "scipy",
        "scipy.optimize",
        "numpy",
        "pandas",
        "pydantic",
        "pydantic_settings",
        "aiosqlite",
        # Standards RAG store (Phase 2 / ADR-3) — replaces chromadb, which
        # is no longer bundled (its onnxruntime/posthog runtime deps were
        # the historical freeze failure point).
        "sqlite_vec",
        "pypdf",
        "httpx",
        "dotenv",
        "multipart",
        "python_multipart",
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
