"""
tests/test_spring_engines.py
─────────────────────────────────────────────────────────────────────────────
Unit tests for the per-spring-type strategy engines (Phase 4 — Engineering
Depth). Covers compression (regression), extension (hook stress + initial
tension), and torsion (arm bending + angular deflection) engines, plus the
compliance-side routing that picks the correct stress checks per type.

Run with:  pytest tests/test_spring_engines.py -v
"""

from __future__ import annotations

import json

import pytest

from app.tools.compliance import compliance_verification_tool
from app.tools.geometry import (
    CompressionEngine,
    ExtensionEngine,
    TorsionEngine,
    calculate_spring_geometry_tool,
)

# ─────────────────────────────────────────────────────────────────────────────
# CompressionEngine — regression (must match pre-refactor behavior exactly)
# ─────────────────────────────────────────────────────────────────────────────


class TestCompressionEngineRegression:
    def test_compute_geometry_basic(self):
        """Known-good compression geometry — same as the pre-Phase-4 tool call."""
        result = json.loads(
            calculate_spring_geometry_tool.invoke({
                "spring_type": "compression",
                "load_force_n": 100.0,
                "deflection_mm": 20.0,
            })
        )
        assert result["status"] == "ok"
        g = result["geometry"]
        assert g["wire_diameter_mm"] > 0
        assert g["mean_coil_diameter_mm"] > g["wire_diameter_mm"]
        assert g["active_coils"] >= 2.0
        assert g["spring_rate_n_mm"] == pytest.approx(5.0, rel=0.05)
        # Compression-only fields must be absent/None
        assert g["torsion_moment_n_mm"] is None
        assert g["angular_deflection_deg"] is None
        assert g.get("hook_bending_stress_mpa") is None
        assert g.get("arm_bending_stress_mpa") is None

    def test_od_constraint_respected(self):
        result = json.loads(
            calculate_spring_geometry_tool.invoke({
                "spring_type": "compression",
                "load_force_n": 50.0,
                "deflection_mm": 10.0,
                "max_outer_diameter_mm": 20.0,
            })
        )
        assert result["status"] == "ok"
        g = result["geometry"]
        assert g["outer_diameter_mm"] <= 21.0

    def test_engine_class_direct_call(self):
        """CompressionEngine.compute_geometry() callable directly by class."""
        engine = CompressionEngine()
        geom = engine.compute_geometry(
            load_force_n=100.0,
            deflection_mm=20.0,
            shear_modulus_gpa=79.3,
            yield_strength_mpa=1500.0,
        )
        assert geom["wire_diameter_mm"] > 0
        assert geom["spring_rate_n_mm"] == pytest.approx(5.0, rel=0.05)


# ─────────────────────────────────────────────────────────────────────────────
# ExtensionEngine — hook stress + initial tension
# ─────────────────────────────────────────────────────────────────────────────


class TestExtensionEngine:
    def test_tool_produces_valid_extension_geometry(self):
        result = json.loads(
            calculate_spring_geometry_tool.invoke({
                "spring_type": "extension",
                "load_force_n": 80.0,
                "deflection_mm": 15.0,
                "initial_tension_n": 10.0,
            })
        )
        assert result["status"] == "ok"
        g = result["geometry"]
        assert g["wire_diameter_mm"] > 0
        assert g["mean_coil_diameter_mm"] > g["wire_diameter_mm"]
        assert g["active_coils"] >= 2.0
        # Extension-specific fields populated
        assert g["hook_bending_stress_mpa"] is not None
        assert g["hook_bending_stress_mpa"] > 0
        assert g["hook_torsion_stress_mpa"] is not None
        assert g["initial_tension_n"] == pytest.approx(10.0)
        # Torsion-only fields must stay None
        assert g["torsion_moment_n_mm"] is None
        assert g["angular_deflection_deg"] is None

    def test_initial_tension_reduces_effective_load(self):
        """Higher initial tension reduces the working spring rate target
        (k = (F - Fi)/deflection per Shigley eq. 10-31) — the coils don't
        start opening until the applied load exceeds the preload, so the
        SAME overall load/deflection target implies a softer working rate
        as initial tension increases. This is the invariant the engine must
        respect regardless of which (d, D) the volume optimizer picks."""
        low_tension = json.loads(
            calculate_spring_geometry_tool.invoke({
                "spring_type": "extension",
                "load_force_n": 80.0,
                "deflection_mm": 15.0,
                "initial_tension_n": 0.0,
            })
        )["geometry"]
        high_tension = json.loads(
            calculate_spring_geometry_tool.invoke({
                "spring_type": "extension",
                "load_force_n": 80.0,
                "deflection_mm": 15.0,
                "initial_tension_n": 40.0,
            })
        )["geometry"]
        assert high_tension["spring_rate_n_mm"] < low_tension["spring_rate_n_mm"]

    def test_active_coils_minimum_enforced(self):
        """A stiff, low-deflection target naturally drives active coils toward
        the physical minimum (2.0) — the engine must never go below it, even
        when the unconstrained spring-rate math would ask for fewer."""
        engine = ExtensionEngine()
        geom = engine.compute_geometry(
            load_force_n=100.0,
            deflection_mm=5.0,
            shear_modulus_gpa=79.3,
            yield_strength_mpa=1500.0,
            initial_tension_n=0.0,
        )
        assert geom["active_coils"] >= 2.0

    def test_engine_class_direct_call(self):
        engine = ExtensionEngine()
        geom = engine.compute_geometry(
            load_force_n=80.0,
            deflection_mm=15.0,
            shear_modulus_gpa=79.3,
            yield_strength_mpa=1500.0,
            initial_tension_n=10.0,
        )
        assert geom["hook_bending_stress_mpa"] > 0
        assert geom["hook_torsion_stress_mpa"] > 0


# ─────────────────────────────────────────────────────────────────────────────
# TorsionEngine — angular deflection + arm bending
# ─────────────────────────────────────────────────────────────────────────────


class TestTorsionEngine:
    def test_tool_produces_valid_torsion_geometry(self):
        result = json.loads(
            calculate_spring_geometry_tool.invoke({
                "spring_type": "torsion",
                "load_force_n": 30.0,
                "deflection_mm": 5.0,
                "arm_length_mm": 25.0,
            })
        )
        assert result["status"] == "ok"
        g = result["geometry"]
        assert g["torsion_moment_n_mm"] is not None
        assert g["angular_deflection_deg"] is not None
        assert g["arm_bending_stress_mpa"] is not None
        assert g["arm_bending_stress_mpa"] > 0
        assert g["coil_torsion_stress_mpa"] is not None
        # Extension/compression-only fields must stay None
        assert g.get("hook_bending_stress_mpa") is None

    def test_angular_deflection_positive_and_finite(self):
        result = json.loads(
            calculate_spring_geometry_tool.invoke({
                "spring_type": "torsion",
                "load_force_n": 30.0,
                "deflection_mm": 5.0,
            })
        )
        g = result["geometry"]["angular_deflection_deg"]
        assert g > 0
        assert g < 3600  # sanity upper bound, not multiple full turns

    def test_engine_class_direct_call(self):
        engine = TorsionEngine()
        geom = engine.compute_geometry(
            load_force_n=30.0,
            deflection_mm=5.0,
            shear_modulus_gpa=79.3,
            yield_strength_mpa=1500.0,
            arm_length_mm=25.0,
        )
        assert geom["arm_bending_stress_mpa"] > 0
        assert geom["angular_deflection_deg"] > 0


# ─────────────────────────────────────────────────────────────────────────────
# Compliance routing — correct stress fields checked per spring type
# ─────────────────────────────────────────────────────────────────────────────


class TestComplianceRoutingPerType:
    def test_compression_uses_shear_and_buckling(self):
        result = json.loads(
            compliance_verification_tool.invoke({
                "wire_diameter_mm": 3.5,
                "mean_coil_diameter_mm": 28.0,
                "active_coils": 8.0,
                "free_length_mm": 60.0,
                "spring_rate_n_mm": 5.0,
                "load_force_n": 100.0,
                "yield_strength_mpa": 1500.0,
                "shear_modulus_gpa": 79.3,
                "spring_type": "compression",
            })
        )
        assert result["status"] == "ok"
        r = result["report"]
        assert r["safety_factor_shear"] > 0
        assert r["safety_factor_buckling"] > 0

    def test_extension_checks_hook_stress_no_buckling(self):
        geom = json.loads(
            calculate_spring_geometry_tool.invoke({
                "spring_type": "extension",
                "load_force_n": 80.0,
                "deflection_mm": 15.0,
                "initial_tension_n": 10.0,
            })
        )["geometry"]

        result = json.loads(
            compliance_verification_tool.invoke({
                "wire_diameter_mm": geom["wire_diameter_mm"],
                "mean_coil_diameter_mm": geom["mean_coil_diameter_mm"],
                "active_coils": geom["active_coils"],
                "free_length_mm": geom["free_length_mm"],
                "spring_rate_n_mm": geom["spring_rate_n_mm"],
                "load_force_n": 80.0,
                "yield_strength_mpa": 1500.0,
                "shear_modulus_gpa": 79.3,
                "spring_type": "extension",
                "hook_bending_stress_mpa": geom["hook_bending_stress_mpa"],
                "hook_torsion_stress_mpa": geom["hook_torsion_stress_mpa"],
            })
        )
        assert result["status"] == "ok"
        r = result["report"]
        # Extension springs are constrained at both ends effectively via the
        # hook geometry; the classic slenderness/buckling check does not apply.
        assert r["safety_factor_buckling"] is None or r["safety_factor_buckling"] == pytest.approx(1.0)
        assert r["safety_factor_shear"] > 0  # semantically = hook stress margin here

    def test_torsion_checks_arm_and_coil_no_buckling(self):
        geom = json.loads(
            calculate_spring_geometry_tool.invoke({
                "spring_type": "torsion",
                "load_force_n": 30.0,
                "deflection_mm": 5.0,
                "arm_length_mm": 25.0,
            })
        )["geometry"]

        result = json.loads(
            compliance_verification_tool.invoke({
                "wire_diameter_mm": geom["wire_diameter_mm"],
                "mean_coil_diameter_mm": geom["mean_coil_diameter_mm"],
                "active_coils": geom["active_coils"],
                "free_length_mm": geom["free_length_mm"],
                "spring_rate_n_mm": geom["spring_rate_n_mm"],
                "load_force_n": 30.0,
                "yield_strength_mpa": 1500.0,
                "shear_modulus_gpa": 79.3,
                "spring_type": "torsion",
                "arm_bending_stress_mpa": geom["arm_bending_stress_mpa"],
                "coil_torsion_stress_mpa": geom["coil_torsion_stress_mpa"],
            })
        )
        assert result["status"] == "ok"
        r = result["report"]
        assert r["safety_factor_buckling"] is None or r["safety_factor_buckling"] == pytest.approx(1.0)
        assert r["safety_factor_shear"] > 0  # semantically = min(arm bending, coil shear) Sf


# ─────────────────────────────────────────────────────────────────────────────
# Round-trip smoke: Agent 2 tool → Agent 4 tool, no crashes, for all 3 types
# ─────────────────────────────────────────────────────────────────────────────


class TestRoundTripAllTypes:
    @pytest.mark.parametrize(
        "spring_type,extra",
        [
            ("compression", {}),
            ("extension", {"initial_tension_n": 5.0}),
            ("torsion", {"arm_length_mm": 20.0}),
        ],
    )
    def test_geometry_then_compliance_no_crash(self, spring_type, extra):
        geom_result = json.loads(
            calculate_spring_geometry_tool.invoke({
                "spring_type": spring_type,
                "load_force_n": 60.0,
                "deflection_mm": 12.0,
                **extra,
            })
        )
        assert geom_result["status"] == "ok"
        g = geom_result["geometry"]

        compliance_input = {
            "wire_diameter_mm": g["wire_diameter_mm"],
            "mean_coil_diameter_mm": g["mean_coil_diameter_mm"],
            "active_coils": g["active_coils"],
            "free_length_mm": g["free_length_mm"],
            "spring_rate_n_mm": g["spring_rate_n_mm"],
            "load_force_n": 60.0,
            "yield_strength_mpa": 1500.0,
            "shear_modulus_gpa": 79.3,
            "spring_type": spring_type,
        }
        if spring_type == "extension":
            compliance_input["hook_bending_stress_mpa"] = g["hook_bending_stress_mpa"]
            compliance_input["hook_torsion_stress_mpa"] = g["hook_torsion_stress_mpa"]
        if spring_type == "torsion":
            compliance_input["arm_bending_stress_mpa"] = g["arm_bending_stress_mpa"]
            compliance_input["coil_torsion_stress_mpa"] = g["coil_torsion_stress_mpa"]

        compliance_result = json.loads(
            compliance_verification_tool.invoke(compliance_input)
        )
        assert compliance_result["status"] == "ok"
        r = compliance_result["report"]
        assert "approved" in r
        assert isinstance(r["failure_modes"], list)
