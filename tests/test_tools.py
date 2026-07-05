"""
tests/test_tools.py
─────────────────────────────────────────────────────────────────────────────
Unit tests for the spring design tool functions.
Run with:  pytest tests/ -v
"""

from __future__ import annotations

import json

import pytest

from app.tools.commercial import commercial_scoring_tool
from app.tools.compliance import compliance_verification_tool
from app.tools.geometry import calculate_spring_geometry_tool
from app.tools.materials import query_material_properties_tool


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


@pytest.mark.usefixtures("patch_materials_db_session")
class TestMaterialsTool:
    """
    Tests para query_material_properties_tool contra una DB SQLite en
    memoria (fixture ``patch_materials_db_session``), no contra el stub
    hardcodeado — Fase 1 mueve la fuente de verdad a la tabla
    ``spring_materials``.
    """

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
        assert result["closest"] == []

    def test_excludes_inactive_materials(self):
        """Materiales con active=False nunca deben aparecer en candidates."""
        result = json.loads(
            query_material_properties_tool.invoke({
                "operating_temperature_c": 25.0,
                "corrosion_resistant": False,
            })
        )
        assert result["status"] == "ok"
        names = [c["name"] for c in result["candidates"]]
        assert "Retired Test Alloy" not in names


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
            "yield_strength_mpa": 1580.0,
            "cyclic_load": False,
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

    # ──────────────────────────────────────────────────────────────────────
    # Phase 5: Lot Amortization, Margin, and Price Tiers
    # ──────────────────────────────────────────────────────────────────────

    def test_default_parameters_backward_compatibility(self):
        """Regression: default settings (0% margin, $0 setup) reproduce pre-Phase-5 cost."""
        proposals = json.dumps([self._make_proposal()])
        # Call with explicit defaults: 0% margin, $0 setup
        result = json.loads(commercial_scoring_tool.invoke({
            "proposals": proposals,
            "margin_percent": 0.0,
            "setup_cost_usd": 0.0,
        }))
        assert result["status"] == "ok"
        ranked = result["ranked_proposals"]
        assert len(ranked) == 1
        r = ranked[0]

        # With 0% margin and $0 setup, total_unit_cost should equal material + manufacturing
        # For the test proposal: material ~0.159, manufacturing ~0.2406
        actual_unit_cost = r["total_unit_cost_usd"]
        # Should be approximately 0.3996
        assert actual_unit_cost == pytest.approx(0.3996, rel=0.01)

    def test_pricing_tiers_structure(self):
        """Verify pricing tiers are computed and returned in ranked proposals."""
        proposals = json.dumps([self._make_proposal()])
        result = json.loads(commercial_scoring_tool.invoke({
            "proposals": proposals,
            "margin_percent": 25.0,
            "setup_cost_usd": 250.0,
        }))
        assert result["status"] == "ok"
        ranked = result["ranked_proposals"]
        assert len(ranked) == 1
        r = ranked[0]

        # Check tiers exist and have required fields
        assert "price_tiers" in r
        assert len(r["price_tiers"]) >= 3  # At least 3 tiers
        for tier in r["price_tiers"]:
            assert "qty_min" in tier
            assert "qty_max" in tier
            assert "unit_price_usd" in tier
            assert "tier_name" in tier
            assert tier["unit_price_usd"] > 0

    def test_lot_amortization_decreases_price(self):
        """Verify larger qty tiers have lower unit prices due to setup amortization."""
        proposals = json.dumps([self._make_proposal()])
        result = json.loads(commercial_scoring_tool.invoke({
            "proposals": proposals,
            "margin_percent": 25.0,
            "setup_cost_usd": 250.0,
        }))
        assert result["status"] == "ok"
        ranked = result["ranked_proposals"]
        r = ranked[0]
        tiers = r["price_tiers"]

        # Tiers should be in ascending order by qty_min
        assert tiers[0]["qty_min"] < tiers[1]["qty_min"]
        if len(tiers) > 2:
            assert tiers[1]["qty_min"] < tiers[2]["qty_min"]

        # Price should decrease as quantity increases (setup amortized)
        assert tiers[0]["unit_price_usd"] > tiers[1]["unit_price_usd"]
        if len(tiers) > 2:
            assert tiers[1]["unit_price_usd"] > tiers[2]["unit_price_usd"]

    def test_margin_applies_to_unit_price(self):
        """Verify margin % is correctly applied to unit price."""
        proposals = json.dumps([self._make_proposal()])

        # Get result with 0% margin
        result_0 = json.loads(commercial_scoring_tool.invoke({
            "proposals": proposals,
            "margin_percent": 0.0,
            "setup_cost_usd": 100.0,
        }))
        price_0pct = result_0["ranked_proposals"][0]["price_tiers"][0]["unit_price_usd"]

        # Get result with 25% margin
        result_25 = json.loads(commercial_scoring_tool.invoke({
            "proposals": proposals,
            "margin_percent": 25.0,
            "setup_cost_usd": 100.0,
        }))
        price_25pct = result_25["ranked_proposals"][0]["price_tiers"][0]["unit_price_usd"]

        # With 25% margin: price = cost * (1 + 0.25) = cost * 1.25
        expected_ratio = 1.25
        actual_ratio = price_25pct / price_0pct
        assert actual_ratio == pytest.approx(expected_ratio, rel=0.01)

    def test_cost_parameters_in_result(self):
        """Verify cost_parameters are returned in the result."""
        proposals = json.dumps([self._make_proposal()])
        margin = 20.0
        setup = 300.0
        result = json.loads(commercial_scoring_tool.invoke({
            "proposals": proposals,
            "margin_percent": margin,
            "setup_cost_usd": setup,
        }))
        assert result["status"] == "ok"
        assert "cost_parameters" in result
        params = result["cost_parameters"]
        assert params["setup_cost_usd"] == setup
        assert params["margin_percent"] == margin
        assert "tier_definitions" in params

    def test_multiple_proposals_have_consistent_tiers(self):
        """Verify all proposals use the same tier structure."""
        p1 = self._make_proposal("P001")
        p2 = {**self._make_proposal("P002"), "cost_usd_per_kg": 1.0}
        proposals = json.dumps([p1, p2])
        result = json.loads(commercial_scoring_tool.invoke({
            "proposals": proposals,
            "margin_percent": 25.0,
            "setup_cost_usd": 250.0,
        }))
        assert result["status"] == "ok"
        ranked = result["ranked_proposals"]
        assert len(ranked) == 2

        # Both proposals should have the same tier structure (qty boundaries)
        tiers_1 = ranked[0]["price_tiers"]
        tiers_2 = ranked[1]["price_tiers"]
        assert len(tiers_1) == len(tiers_2)
        for t1, t2 in zip(tiers_1, tiers_2):
            assert t1["qty_min"] == t2["qty_min"]
            assert t1["qty_max"] == t2["qty_max"]
            # But prices differ due to different material costs
            assert t1["unit_price_usd"] != t2["unit_price_usd"]
