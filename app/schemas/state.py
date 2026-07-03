"""
app/schemas/state.py
─────────────────────────────────────────────────────────────────────────────
Shared state definition for the LangGraph Spring-Design agentic workflow.

Every node reads from and writes back to this TypedDict, which LangGraph
keeps consistent across the entire graph execution.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field, field_validator


# ─────────────────────────────────────────────────────────────────────────────
# Sub-schemas (Pydantic models used as typed sub-documents inside the state)
# ─────────────────────────────────────────────────────────────────────────────


class SpringType(str, Enum):
    COMPRESSION = "compression"
    EXTENSION = "extension"
    TORSION = "torsion"
    SPIRAL = "spiral"
    WAVE = "wave"
    UNKNOWN = "unknown"


class UserRequirements(BaseModel):
    """Structured representation of what Agent 1 (Requirements Analyst) extracts."""

    raw_input: str = Field(..., description="Verbatim user input text")
    spring_type: SpringType = Field(
        SpringType.UNKNOWN, description="Detected or inferred spring type"
    )
    # Load / deflection
    load_force_n: float | None = Field(None, description="Required force in Newtons")
    deflection_mm: float | None = Field(None, description="Required deflection in mm")
    spring_rate_n_mm: float | None = Field(
        None, description="Spring rate k = F/x in N/mm"
    )
    # Physical envelope
    max_outer_diameter_mm: float | None = Field(None, description="OD constraint in mm")
    max_free_length_mm: float | None = Field(
        None, description="Maximum free length in mm"
    )
    solid_length_mm: float | None = Field(None, description="Solid (compressed) length")
    # Environmental / application
    operating_temperature_c: float | None = Field(
        None, description="Max operating temperature °C"
    )
    corrosion_resistant: bool = Field(False, description="Corrosion resistance required")
    cyclic_load: bool = Field(False, description="True → fatigue life matters")
    cycles_expected: int | None = Field(None, description="Expected fatigue cycles")
    # Clarification tracking
    clarification_questions: list[str] = Field(
        default_factory=list,
        description="Open questions generated when requirements are ambiguous",
    )
    is_complete: bool = Field(
        False, description="True when Agent 1 judges inputs sufficient for design"
    )

    @field_validator("corrosion_resistant", "cyclic_load", mode="before")
    @classmethod
    def coerce_none_to_false(cls, v: object) -> bool:
        """Convert null/missing to False so LLMs that omit these don't crash."""
        return bool(v) if v is not None else False

    class Config:
        use_enum_values = True


class SpringGeometry(BaseModel):
    """Output of Agent 2 / calculate_spring_geometry_tool."""

    wire_diameter_mm: float = Field(..., description="Wire diameter d (mm)")
    mean_coil_diameter_mm: float = Field(..., description="Mean coil diameter D (mm)")
    outer_diameter_mm: float = Field(..., description="Outer diameter OD = D + d (mm)")
    inner_diameter_mm: float = Field(..., description="Inner diameter ID = D - d (mm)")
    active_coils: float = Field(..., description="Number of active coils n_a")
    total_coils: float = Field(..., description="Total coils n_t (includes dead coils)")
    free_length_mm: float = Field(..., description="Free length L0 (mm)")
    pitch_mm: float = Field(..., description="Coil pitch p (mm)")
    spring_index: float = Field(..., description="Spring index C = D/d")
    spring_rate_n_mm: float = Field(
        ..., description="Computed spring constant k (N/mm)"
    )
    # Torsion-specific (optional)
    torsion_moment_n_mm: float | None = Field(None)
    angular_deflection_deg: float | None = Field(None)


class MaterialProperties(BaseModel):
    """Row retrieved from the SQL materials catalogue (Agent 3)."""

    material_id: int
    name: str = Field(..., description="e.g. 'ASTM A228 Music Wire'")
    shear_modulus_gpa: float = Field(..., description="G in GPa")
    elastic_modulus_gpa: float = Field(..., description="E in GPa")
    density_kg_m3: float = Field(..., description="Density ρ in kg/m³")
    yield_strength_mpa: float = Field(..., description="Tensile / torsional Sy in MPa")
    ultimate_strength_mpa: float = Field(..., description="Sut in MPa")
    max_temp_c: float = Field(..., description="Max operating temperature °C")
    corrosion_resistant: bool
    cost_usd_per_kg: float = Field(..., description="Base raw-material cost USD/kg")


class ComplianceReport(BaseModel):
    """Output of Agent 4 / compliance_verification_tool."""

    approved: bool
    safety_factor_shear: float = Field(
        ..., description="Ks corrected shear safety factor (Wahl factor applied)"
    )
    safety_factor_buckling: float = Field(
        ..., description="Slenderness-based buckling safety"
    )
    safety_factor_fatigue: float | None = Field(
        None, description="Goodman fatigue safety (if cyclic)"
    )
    applicable_standard: str = Field(
        ..., description="e.g. 'DIN 2095', 'ASTM F1123'"
    )
    failure_modes: list[str] = Field(
        default_factory=list,
        description="List of active failure conditions that triggered rejection",
    )
    redesign_directives: list[str] = Field(
        default_factory=list,
        description="Actionable redesign hints for Agent 2 if rejected",
    )
    retrieved_standards: list[str] = Field(
        default_factory=list,
        description="Normative clause text retrieved from ChromaDB for this validation",
    )
    standards_referenced: list[str] = Field(
        default_factory=list,
        description="Standard IDs (e.g. 'BS-EN-13906-1', 'DIN EN 10270-1') that were consulted",
    )


class CommercialScore(BaseModel):
    """Output of Agent 5 / commercial_scoring_tool."""

    proposal_id: str
    wire_mass_kg: float
    material_cost_usd: float
    estimated_life_cycles: int | None
    composite_score: float = Field(
        ..., description="Weighted index (higher = more cost-effective + durable)"
    )
    rank: int


class LLMProviderStatus(BaseModel):
    """Tracks which provider is active and which have failed."""

    active_provider: str = Field("ollama", description="Currently active LLM provider")
    failed_providers: list[str] = Field(default_factory=list)
    retry_count: int = 0
    max_retries: int = 5


# ─────────────────────────────────────────────────────────────────────────────
# Root AgentState  (LangGraph TypedDict)
# ─────────────────────────────────────────────────────────────────────────────


class AgentState(dict):
    """
    LangGraph state container.

    Using a plain dict subclass (instead of TypedDict) so that we can annotate
    ``messages`` with the built-in ``add_messages`` reducer while still letting
    Pydantic models be stored as ordinary dict values.

    Key design rules:
    - Every agent reads only the fields it needs.
    - Every agent RETURNS a partial dict with only the keys it mutated.
    - LangGraph merges the partial update onto the running state automatically.
    """

    # ── Raw input (preserved across graph steps) ──────────────────────────────
    _raw_input: str
    """Original user input text. MUST be annotated so LangGraph keeps it as a channel — otherwise it is silently dropped and regex extraction never runs."""

    # ── Conversation / messaging ──────────────────────────────────────────────
    messages: Annotated[list[BaseMessage], add_messages]
    """Full conversation history with the add_messages reducer (append-only)."""

    # ── Workflow control ──────────────────────────────────────────────────────
    current_step: str
    """Name of the last completed node (for logging / routing decisions)."""

    iteration_count: int
    """How many full design-loop iterations have been attempted."""

    max_iterations: int
    """Hard cap to prevent infinite redesign loops (default 5)."""

    # ── Agent outputs ─────────────────────────────────────────────────────────
    requirements: UserRequirements | None
    """Structured requirements produced by Agent 1."""

    geometry: SpringGeometry | None
    """Geometry solution produced by Agent 2 via calculate_spring_geometry_tool."""

    material: MaterialProperties | None
    """Material record selected by Agent 3 via query_material_properties_tool."""

    compliance: ComplianceReport | None
    """Compliance report produced by Agent 4 via compliance_verification_tool."""

    redesign_directives: list[str]
    """Redesign directives from the PREVIOUS compliance iteration (persists
    across iterations — NOT cleared by increment_iteration_node). Used by
    Agent 2 to adjust geometry parameters on redesign attempts."""

    commercial_proposals: list[CommercialScore]
    """Ranked commercial scores produced by Agent 5."""

    # ── LLM orchestration ────────────────────────────────────────────────────
    llm_status: LLMProviderStatus
    """Tracks active provider and fallback history."""

    # ── Error / audit trail ──────────────────────────────────────────────────
    errors: list[dict[str, Any]]
    """Structured error log: [{step, error_type, message, timestamp}]."""

    final_report: dict[str, Any] | None
    """Assembled final JSON report ready for the API response and frontend."""


def initial_state(raw_user_input: str, max_iterations: int = 5) -> dict:
    """
    Factory that produces the initial AgentState dict for a new workflow run.

    Args:
        raw_user_input: The unprocessed text the user submitted.
        max_iterations: Safety cap on redesign loops.

    Returns:
        A dict conforming to AgentState shape with sensible defaults.
    """
    return {
        "messages": [],
        "current_step": "start",
        "iteration_count": 0,
        "max_iterations": max_iterations,
        "requirements": None,
        "geometry": None,
        "material": None,
        "compliance": None,
        "redesign_directives": [],
        "commercial_proposals": [],
        "llm_status": LLMProviderStatus(),
        "errors": [],
        "final_report": None,
        # Inject the raw input as the first human message so that Agent 1 can
        # pick it up from the message history without needing a separate field.
        "_raw_input": raw_user_input,
    }
