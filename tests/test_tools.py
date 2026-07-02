"""
tests/test_tools.py
─────────────────────────────────────────────────────────────────────────────
Unit tests for the spring design tool functions.
Run with:  pytest tests/ -v
"""

from __future__ import annotations

import json

import pytest

from app.tools.spring_tools import (
    calculate_spring_geometry_tool,
    commercial_scoring_tool,
    compliance_verification_tool,
    query_material_properties_tool,
)


class TestGeometryTool:
    def test_compression_spring_basic(self):
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

    def test_spring_with_od_constraint(self):
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
        # OD must not exceed constraint
        assert g["outer_diameter_mm"] <= 21.0  # allow small tolerance

    def test_torsion_spring(self):
        result = json.loads(
            calculate_spring_geometry_tool.invoke({
                "spring_type": "torsion",
                "load_force_n": 30.0,
                "deflection_mm": 5.0,
            })
        )
        assert result["status"] == "ok"
        g = result["geometry"]
        assert g["torsion_moment_n_mm"] is not None
        assert g["angular_deflection_deg"] is not None


class TestMaterialsTool:
    def test_basic_query(self):
        result = json.loads(
            query_material_properties_tool.invoke({
                "operating_temperature_c": 25.0,
                "corrosion_resistant": False,
            })
        )
        assert result["status"] == "ok"
        assert len(result["candidates"]) > 0

    def test_corrosion_filter(self):
        result = json.loads(
            query_material_properties_tool.invoke({
                "operating_temperature_c": 25.0,
                "corrosion_resistant": True,
            })
        )
        assert result["status"] == "ok"
        for mat in result["candidates"]:
            assert mat["corrosion_resistant"] is True

    def test_high_temp_filter(self):
        result = json.loads(
            query_material_properties_tool.invoke({
                "operating_temperature_c": 400.0,
                "corrosion_resistant": False,
            })
        )
        assert result["status"] == "ok"
        for mat in result["candidates"]:
            assert mat["max_temp_c"] >= 400.0

    def test_no_match(self):
        result = json.loads(
            query_material_properties_tool.invoke({
                "operating_temperature_c": 700.0,
                "corrosion_resistant": True,
                "max_cost_usd_per_kg": 1.0,
            })
        )
        assert result["status"] == "no_match"


class TestComplianceTool:
    def test_approved_design(self):
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
            })
        )
        assert result["status"] == "ok"
        r = result["report"]
        assert r["safety_factor_shear"] > 0
        assert r["safety_factor_buckling"] > 0

    def test_rejected_high_slenderness(self):
        """A very long, thin spring should fail the buckling check."""
        result = json.loads(
            compliance_verification_tool.invoke({
                "wire_diameter_mm": 1.0,
                "mean_coil_diameter_mm": 10.0,
                "active_coils": 30.0,
                "free_length_mm": 200.0,  # λ = 200/10 = 20 >> 5.26
                "spring_rate_n_mm": 2.0,
                "load_force_n": 20.0,
                "yield_strength_mpa": 1500.0,
                "shear_modulus_gpa": 79.3,
            })
        )
        assert result["status"] == "ok"
        r = result["report"]
        assert r["approved"] is False
        assert any("buckling" in fm.lower() or "slenderness" in fm.lower()
                   for fm in r["failure_modes"])


class TestCommercialTool:
    def _make_proposal(self, pid: str = "P001") -> dict:
        return {
            "proposal_id": pid,
            "wire_diameter_mm": 3.0,
            "mean_coil_diameter_mm": 24.0,
            "outer_diameter_mm": 27.0,
            "active_coils": 8.0,
            "total_coils": 10.0,
            "free_length_mm": 55.0,
            "spring_rate_n_mm": 5.0,
            "density_kg_m3": 7850.0,
            "cost_usd_per_kg": 3.80,
            "safety_factor_shear": 2.1,
            "safety_factor_buckling": 1.8,
            "cycles_expected": 500_000,
        }

    def test_single_proposal(self):
        proposals = json.dumps([self._make_proposal()])
        result = json.loads(commercial_scoring_tool.invoke({"proposals": proposals}))
        assert result["status"] == "ok"
        ranked = result["ranked_proposals"]
        assert len(ranked) == 1
        assert ranked[0]["rank"] == 1
        assert ranked[0]["composite_score"] > 0
        assert "three_js_params" in ranked[0]

    def test_ranking_multiple_proposals(self):
        p1 = self._make_proposal("P001")
        p2 = {**self._make_proposal("P002"), "cost_usd_per_kg": 1.0}  # cheaper
        proposals = json.dumps([p1, p2])
        result = json.loads(commercial_scoring_tool.invoke({"proposals": proposals}))
        assert result["status"] == "ok"
        ranked = result["ranked_proposals"]
        # Cheaper proposal should rank higher (better cost score)
        assert ranked[0]["proposal_id"] == "P002"
