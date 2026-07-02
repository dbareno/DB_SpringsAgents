"""
app/schemas/design.py
─────────────────────────────────────────────────────────────────────────────
Pydantic models for the Spring Design API request/response contracts.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


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

    session_id: str = Field(
        ..., description="Session ID returned by the initial POST."
    )
    answers: list[str] = Field(
        ...,
        min_length=1,
        description=(
            "Array of answers, one per clarification question, in the same order "
            "as the questions were presented. The system will pair each answer "
            "with its corresponding question."
        ),
    )


class StepProgress(BaseModel):
    """Current step progress for the polling endpoint."""

    session_id: str
    status: str = Field(
        description=(
            "'processing' while the graph runs, "
            "'completed' when the final result is ready, "
            "'error' if execution failed."
        )
    )
    current_step: str | None = Field(
        None,
        description=(
            "Current graph node: 'requirements_analyst', 'materials_engineer', "
            "'design_engineer', 'normative_inspector', 'commercial_optimiser', "
            "'awaiting_clarification', or 'iteration_limit_reached'."
        ),
    )
    progress_pct: int = Field(
        0,
        ge=0,
        le=100,
        description="Approximate progress percentage for the frontend pipeline.",
    )
    error: str | None = Field(
        None,
        description="Error message if status is 'error'.",
    )


class DesignResponse(BaseModel):
    """Response from the design endpoint."""

    session_id: str
    status: str = Field(
        description=(
            "One of: 'processing', 'approved', 'needs_clarification', "
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
