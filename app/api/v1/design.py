"""
app/api/v1/design.py
─────────────────────────────────────────────────────────────────────────────
FastAPI router for the Spring Design endpoints.

Endpoints
─────────
  POST /api/v1/design          → Start a new design workflow run.
  POST /api/v1/design/clarify  → Resume a workflow after user provides answers.
  GET  /api/v1/design/{id}     → Retrieve a completed design report.
  GET  /api/v1/design/health   → LLM provider status check.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from pydantic import BaseModel, Field

from app.core.llm_factory import get_factory
from app.graph.workflow import spring_design_graph
from app.schemas.state import initial_state

router = APIRouter(prefix="/api/v1/design", tags=["Spring Design"])

# In-memory store for demo purposes; swap for Redis / DB in production.
_RESULT_STORE: dict[str, dict[str, Any]] = {}


# ─────────────────────────────────────────────────────────────────────────────
# Request / Response schemas
# ─────────────────────────────────────────────────────────────────────────────


class DesignRequest(BaseModel):
    """Payload for starting a new spring design workflow."""

    user_input: str = Field(
        ...,
        min_length=5,
        description=(
            "Natural-language spring requirements. Can be vague or ultra-precise. "
            "Examples: 'I need a compression spring that supports 50N with 10mm deflection' "
            "or 'small spring for a ballpoint pen'."
        ),
        examples=[
            "Design a compression spring to support 120 N with 15 mm deflection. "
            "Max outer diameter 25 mm, must be corrosion resistant, stainless preferred."
        ],
    )
    max_iterations: int = Field(
        5,
        ge=1,
        le=10,
        description="Maximum number of redesign iterations before giving up.",
    )
    session_id: str | None = Field(
        None,
        description="Optional session identifier for resuming a clarification flow.",
    )


class ClarifyRequest(BaseModel):
    """Payload for resuming a paused (needs_clarification) workflow."""

    session_id: str = Field(..., description="Session ID returned by the initial POST.")
    answers: str = Field(
        ...,
        description=(
            "Free-text answers to the clarification questions. "
            "The system will merge them with the original input."
        ),
    )


class DesignResponse(BaseModel):
    """Response from the design endpoint."""

    session_id: str
    status: str = Field(
        description=(
            "One of: 'approved', 'needs_clarification', "
            "'iteration_limit_reached', 'error'."
        )
    )
    report: dict[str, Any] | None = Field(
        None, description="Full design report (only present when status='approved')."
    )
    clarification_questions: list[str] | None = Field(
        None,
        description="Questions to ask the user (only when status='needs_clarification').",
    )
    errors: list[dict[str, Any]] | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────


@router.post(
    "/",
    response_model=DesignResponse,
    status_code=status.HTTP_200_OK,
    summary="Start a new spring design workflow",
)
async def start_design(request: DesignRequest) -> DesignResponse:
    """
    Accepts natural-language spring requirements and runs the full agentic
    LangGraph workflow synchronously.

    For long-running jobs, consider wrapping the graph invocation in
    ``BackgroundTasks`` and returning a 202 Accepted with a polling URL.
    """
    session_id = request.session_id or str(uuid.uuid4())
    state = initial_state(request.user_input, max_iterations=request.max_iterations)

    try:
        final_state: dict[str, Any] = spring_design_graph.invoke(state)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Graph execution failed: {exc}",
        ) from exc

    return _build_response(session_id, final_state)


@router.post(
    "/clarify",
    response_model=DesignResponse,
    status_code=status.HTTP_200_OK,
    summary="Resume a workflow after answering clarification questions",
)
async def clarify_design(request: ClarifyRequest) -> DesignResponse:
    """
    Continue a paused workflow by providing answers to the clarification
    questions.  The answers are appended to the original user input and
    the full graph is re-invoked from the start.
    """
    # Retrieve the stored partial state (original input) from the store
    stored = _RESULT_STORE.get(request.session_id)
    if stored is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session '{request.session_id}' not found.",
        )

    original_input = stored.get("_raw_input", "")
    combined_input = f"{original_input}\n\nAdditional information: {request.answers}"

    state = initial_state(combined_input)
    try:
        final_state: dict[str, Any] = spring_design_graph.invoke(state)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Graph execution failed: {exc}",
        ) from exc

    return _build_response(request.session_id, final_state)


@router.get(
    "/{session_id}",
    response_model=DesignResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve a cached design report",
)
async def get_design(session_id: str) -> DesignResponse:
    """Return the stored result for a previous design run."""
    stored = _RESULT_STORE.get(session_id)
    if stored is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session '{session_id}' not found.",
        )
    return DesignResponse(**stored["response"])


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


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _build_response(session_id: str, final_state: dict[str, Any]) -> DesignResponse:
    """Map the final LangGraph state to a DesignResponse."""
    final_report: dict[str, Any] = final_state.get("final_report") or {}
    graph_status = final_report.get("status", "unknown")

    # Map graph status to API status string
    status_map = {
        "approved":               "approved",
        "needs_clarification":    "needs_clarification",
        "iteration_limit_reached":"iteration_limit_reached",
        "error":                  "error",
    }
    api_status = status_map.get(graph_status, graph_status)

    response = DesignResponse(
        session_id=session_id,
        status=api_status,
        report=final_report if api_status == "approved" else None,
        clarification_questions=(
            final_report.get("clarification_questions")
            if api_status == "needs_clarification"
            else None
        ),
        errors=final_state.get("errors") or None,
    )

    # Cache result and raw input for potential clarification follow-up
    _RESULT_STORE[session_id] = {
        "response": response.model_dump(),
        "_raw_input": final_state.get("_raw_input", ""),
    }

    return response
