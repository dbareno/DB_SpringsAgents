"""
app/main.py
─────────────────────────────────────────────────────────────────────────────
FastAPI application entry point.

Startup sequence
────────────────
1. Load settings from environment / .env.
2. Configure structured logging.
3. Wire all API routers.
4. Expose OpenAPI docs at /docs (disabled in production via DOCS_ENABLED=false).
"""

from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.design import router as design_router
from app.api.v1.materials import router as materials_router
from app.core.settings import get_settings

settings = get_settings()

# ─────────────────────────────────────────────────────────────────────────────
# Logging configuration
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Lifespan (startup / shutdown hooks)
# ─────────────────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """FastAPI lifespan context manager for startup and shutdown tasks."""
    logger.info("=== Spring Design Agent API starting up ===")
    logger.info(
        "LLM priority order: %s", settings.llm_priority_order
    )

    # Seed the materials catalogue idempotently (safe to run on every boot —
    # required so the .exe populates its own SQLite on first launch).
    try:
        from scripts.seed_materials import seed as seed_materials

        inserted = await seed_materials()
        logger.info("Materials catalogue seeded (%d new rows).", inserted)
    except Exception:
        logger.exception("Materials seed failed — continuing startup anyway.")

    # Ingest the bundled starter standards corpus idempotently (safe to run
    # on every boot — required so the .exe self-ingests on first launch).
    try:
        from scripts.ingest_standards import ingest as ingest_standards

        ingest_results = ingest_standards()
        total_chunks = sum(ingest_results.values())
        logger.info("Standards corpus ingested (%d new chunks).", total_chunks)
    except Exception:
        logger.exception("Standards ingestion failed — continuing startup anyway.")

    yield
    logger.info("=== Spring Design Agent API shutting down ===")


# ─────────────────────────────────────────────────────────────────────────────
# Application factory
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "Agentic multi-type spring design system powered by LangGraph and FastAPI. "
        "Accepts natural-language requirements and returns engineered spring designs "
        "validated against DIN/ASTM standards."
    ),
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

# ── CORS ───────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # Restrict to your frontend origin in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────
app.include_router(design_router)
app.include_router(materials_router)

# ── Frontend static build detection (standalone .exe mode) ──────────────
import os
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

if getattr(sys, "frozen", False):
    FRONTEND_DIR = os.path.join(sys._MEIPASS, "frontend", "out")
else:
    FRONTEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend", "out")

_HAS_FRONTEND = os.path.isdir(FRONTEND_DIR)

if _HAS_FRONTEND:
    logger.info("Frontend static build detected at %s — serving SPA.", FRONTEND_DIR)

    # Mount Next.js static chunks
    next_static = os.path.join(FRONTEND_DIR, "_next")
    if os.path.isdir(next_static):
        app.mount("/_next", StaticFiles(directory=next_static), name="frontend_next")


# ─────────────────────────────────────────────────────────────────────────────
# Root & health endpoints
# ─────────────────────────────────────────────────────────────────────────────

def _serve_index() -> FileResponse | JSONResponse:
    """Serve the SPA index.html if frontend is available."""
    if _HAS_FRONTEND:
        index_path = os.path.join(FRONTEND_DIR, "index.html")
        if os.path.isfile(index_path):
            return FileResponse(index_path, media_type="text/html")
    return JSONResponse(
        {"service": settings.app_name, "version": settings.app_version, "status": "running", "docs": "/docs"}
    )


@app.get("/", tags=["Root"], summary="API root", response_model=None)
async def root() -> FileResponse | JSONResponse:
    """Return the SPA when frontend is available, otherwise API metadata."""
    return _serve_index()


@app.get("/health", tags=["Health"], summary="System health check")
async def health() -> JSONResponse:
    return JSONResponse(
        content={"status": "healthy", "version": settings.app_version}
    )


# ─────────────────────────────────────────────────────────────────────────────
# SPA catch-all — MUST be the LAST route registered
# ─────────────────────────────────────────────────────────────────────────────

if _HAS_FRONTEND:
    @app.get("/{full_path:path}", response_model=None, include_in_schema=False)
    async def serve_frontend(full_path: str) -> FileResponse | JSONResponse:
        """Serve index.html for any unmatched path (SPA fallback)."""
        return _serve_index()


# ─────────────────────────────────────────────────────────────────────────────
# Development entrypoint
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.debug,
        log_level=settings.log_level.lower(),
    )
