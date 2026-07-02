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
    # Future: warm up DB connections, ChromaDB client, etc.
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


# ─────────────────────────────────────────────────────────────────────────────
# Root & health endpoints
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/", tags=["Root"], summary="API root")
async def root() -> dict:
    return {
        "service": settings.app_name,
        "version": settings.app_version,
        "status": "running",
        "docs": "/docs",
    }


@app.get("/health", tags=["Health"], summary="System health check")
async def health() -> JSONResponse:
    return JSONResponse(
        content={"status": "healthy", "version": settings.app_version}
    )


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
