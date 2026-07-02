"""
app/api/v1/design.py
─────────────────────────────────────────────────────────────────────────────
FastAPI router for the Spring Design endpoints.

Endpoints
─────────
  POST /api/v1/design              → Start a new design workflow run (async).
  POST /api/v1/design/clarify      → Resume a workflow after user provides answers.
  GET  /api/v1/design/{id}         → Retrieve a completed design report.
  GET  /api/v1/design/{id}/status  → Poll current step progress.
  GET  /api/v1/design/health       → LLM provider status check.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.llm_factory import get_factory
from app.db.session import get_db_session
from app.schemas.design import ClarifyRequest, DesignRequest, DesignResponse, StepProgress
from app.services.design_service import DesignService

router = APIRouter(prefix="/api/v1/design", tags=["Spring Design"])


# ── Dependencies ──────────────────────────────────────────────────────────


async def get_design_service(
    db: AsyncSession = Depends(get_db_session),
) -> DesignService:
    """FastAPI dependency que inyecta un DesignService con sesión activa."""
    return DesignService(db=db)


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────


@router.post(
    "/",
    response_model=DesignResponse,
    status_code=status.HTTP_200_OK,
    summary="Start a new spring design workflow",
)
async def start_design(
    request: DesignRequest,
    service: DesignService = Depends(get_design_service),
) -> DesignResponse:
    """
    Accepts natural-language spring requirements and runs the full agentic
    LangGraph workflow **asynchronously**.  Returns immediately with
    ``status='processing'``; poll ``GET /{session_id}/status`` for progress.
    """
    return await service.start_design(
        user_input=request.user_input,
        max_iterations=request.max_iterations,
        session_id=request.session_id,
    )


@router.post(
    "/clarify",
    response_model=DesignResponse,
    status_code=status.HTTP_200_OK,
    summary="Resume a workflow after answering clarification questions",
)
async def clarify_design(
    request: ClarifyRequest,
    service: DesignService = Depends(get_design_service),
) -> DesignResponse:
    """
    Continue a paused workflow by providing answers as an **array of strings**,
    one per clarification question in the same order they were presented.
    Returns immediately with ``status='processing'``.
    """
    return await service.clarify_design(
        session_id=request.session_id,
        answers=request.answers,
    )


@router.get(
    "/{session_id}",
    response_model=DesignResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve a stored design report",
)
async def get_design(
    session_id: str,
    service: DesignService = Depends(get_design_service),
) -> DesignResponse:
    """Return the stored result for a previous design run."""
    result = await service.get_design(session_id=session_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session '{session_id}' not found.",
        )
    return result


@router.get(
    "/{session_id}/status",
    response_model=StepProgress,
    status_code=status.HTTP_200_OK,
    summary="Poll the current step progress of a running design",
)
async def get_design_status(
    session_id: str,
    service: DesignService = Depends(get_design_service),
) -> StepProgress:
    """
    Poll the execution progress of a design workflow.

    Returns ``status='processing'`` + the current ``current_step`` while the
    graph is running.  When finished, returns ``status='completed'`` (or
    ``'approved'`` / ``'needs_clarification'`` / etc.) and the frontend
    should fetch the full result via ``GET /{session_id}``.
    """
    progress = await service.get_step_progress(session_id=session_id)
    if progress is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session '{session_id}' not found.",
        )
    return progress


@router.get(
    "/health/llm",
    summary="Check LLM provider status",
    tags=["Health"],
)
async def llm_health() -> dict[str, Any]:
    """Return the current active LLM provider and any failed providers."""
    factory = get_factory()
    return {
        "active_provider": factory.active_provider,
        "failed_providers": factory.failed_providers,
        "priority_order": factory._settings.llm_priority_order,
    }
