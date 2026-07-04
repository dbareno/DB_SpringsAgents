"""
tests/test_materials_api.py
─────────────────────────────────────────────────────────────────────────────
Tests for the materials admin API (`/api/v1/materials`), including the
hardcoded admin-token auth stub and the CSV bulk-import endpoint.

Uses a seeded in-memory SQLite DB (``seeded_materials_engine`` fixture from
conftest.py) wired in via FastAPI's ``dependency_overrides`` — no real
database connection is required.
"""

from __future__ import annotations

import io

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.v1.materials import _ADMIN_TOKEN
from app.db.session import get_db_session
from app.main import app


@pytest.fixture
async def client(seeded_materials_engine):
    """AsyncClient wired to the seeded in-memory SQLite session factory."""

    async def _override_get_db_session():
        async with seeded_materials_engine() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db_session] = _override_get_db_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.pop(get_db_session, None)


ADMIN_HEADERS = {"X-Admin-Token": _ADMIN_TOKEN}


class TestListMaterials:
    async def test_list_returns_active_only_by_default(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/materials/")
        assert response.status_code == 200
        data = response.json()
        names = [m["name"] for m in data["materials"]]
        assert "Retired Test Alloy" not in names
        assert data["count"] == len(data["materials"])

    async def test_list_includes_inactive_when_requested(
        self, client: AsyncClient
    ) -> None:
        response = await client.get("/api/v1/materials/", params={"active": "false"})
        assert response.status_code == 200
        data = response.json()
        names = [m["name"] for m in data["materials"]]
        assert "Retired Test Alloy" in names

    async def test_list_filters_by_corrosion_resistant(
        self, client: AsyncClient
    ) -> None:
        response = await client.get(
            "/api/v1/materials/", params={"corrosion_resistant": "true"}
        )
        assert response.status_code == 200
        data = response.json()
        for m in data["materials"]:
            assert m["corrosion_resistant"] is True


class TestGetMaterial:
    async def test_get_existing_material(self, client: AsyncClient) -> None:
        list_response = await client.get("/api/v1/materials/")
        material_id = list_response.json()["materials"][0]["id"]

        response = await client.get(f"/api/v1/materials/{material_id}")
        assert response.status_code == 200
        assert response.json()["id"] == material_id

    async def test_get_missing_material_returns_404(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/materials/999999")
        assert response.status_code == 404


class TestCreateMaterial:
    _PAYLOAD = {
        "name": "Test Created Alloy",
        "standard": "ASTM Z",
        "shear_modulus_gpa": 80.0,
        "elastic_modulus_gpa": 200.0,
        "density_kg_m3": 7800.0,
        "yield_strength_mpa": 1300.0,
        "ultimate_strength_mpa": 1600.0,
        "max_temp_c": 150.0,
        "corrosion_resistant": False,
        "cost_usd_per_kg": 4.2,
    }

    async def test_create_without_admin_token_returns_401(
        self, client: AsyncClient
    ) -> None:
        response = await client.post("/api/v1/materials/", json=self._PAYLOAD)
        assert response.status_code == 401

    async def test_create_with_admin_token_succeeds(self, client: AsyncClient) -> None:
        response = await client.post(
            "/api/v1/materials/", json=self._PAYLOAD, headers=ADMIN_HEADERS
        )
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "Test Created Alloy"
        assert data["active"] is True

    async def test_create_duplicate_name_returns_409(self, client: AsyncClient) -> None:
        await client.post("/api/v1/materials/", json=self._PAYLOAD, headers=ADMIN_HEADERS)
        response = await client.post(
            "/api/v1/materials/", json=self._PAYLOAD, headers=ADMIN_HEADERS
        )
        assert response.status_code == 409


class TestUpdateMaterial:
    async def test_update_without_admin_token_returns_401(
        self, client: AsyncClient
    ) -> None:
        list_response = await client.get("/api/v1/materials/")
        material_id = list_response.json()["materials"][0]["id"]

        response = await client.put(
            f"/api/v1/materials/{material_id}", json={"cost_usd_per_kg": 99.0}
        )
        assert response.status_code == 401

    async def test_update_price_with_admin_token(self, client: AsyncClient) -> None:
        list_response = await client.get("/api/v1/materials/")
        material_id = list_response.json()["materials"][0]["id"]

        response = await client.put(
            f"/api/v1/materials/{material_id}",
            json={"cost_usd_per_kg": 12.34},
            headers=ADMIN_HEADERS,
        )
        assert response.status_code == 200
        assert response.json()["cost_usd_per_kg"] == 12.34

    async def test_soft_delete_via_active_false(self, client: AsyncClient) -> None:
        list_response = await client.get("/api/v1/materials/")
        material_id = list_response.json()["materials"][0]["id"]

        response = await client.put(
            f"/api/v1/materials/{material_id}",
            json={"active": False},
            headers=ADMIN_HEADERS,
        )
        assert response.status_code == 200
        assert response.json()["active"] is False

        # Excluded from the default active-only listing afterward.
        list_after = await client.get("/api/v1/materials/")
        ids_after = [m["id"] for m in list_after.json()["materials"]]
        assert material_id not in ids_after

    async def test_update_missing_material_returns_404(self, client: AsyncClient) -> None:
        response = await client.put(
            "/api/v1/materials/999999",
            json={"cost_usd_per_kg": 1.0},
            headers=ADMIN_HEADERS,
        )
        assert response.status_code == 404


class TestImportMaterialsCsv:
    _CSV_HEADER = (
        "name,standard,shear_modulus_gpa,elastic_modulus_gpa,density_kg_m3,"
        "yield_strength_mpa,ultimate_strength_mpa,max_temp_c,"
        "corrosion_resistant,cost_usd_per_kg,active\n"
    )

    def _csv_file(self, body: str) -> dict:
        return {
            "file": (
                "materials.csv",
                io.BytesIO((self._CSV_HEADER + body).encode("utf-8")),
                "text/csv",
            )
        }

    async def test_import_without_admin_token_returns_401(
        self, client: AsyncClient
    ) -> None:
        files = self._csv_file(
            "New CSV Alloy,ASTM Y,80,200,7800,1300,1600,150,false,4.0,true\n"
        )
        response = await client.post("/api/v1/materials/import", files=files)
        assert response.status_code == 401

    async def test_import_creates_new_material(self, client: AsyncClient) -> None:
        files = self._csv_file(
            "New CSV Alloy,ASTM Y,80,200,7800,1300,1600,150,false,4.0,true\n"
        )
        response = await client.post(
            "/api/v1/materials/import", files=files, headers=ADMIN_HEADERS
        )
        assert response.status_code == 200
        data = response.json()
        assert data["created"] == 1
        assert data["updated"] == 0
        assert data["errors"] == 0

    async def test_import_updates_existing_material_by_name(
        self, client: AsyncClient
    ) -> None:
        files = self._csv_file(
            "ASTM A228 Music Wire,ASTM A228,81.5,207,7850,1580,1900,120,false,7.77,true\n"
        )
        response = await client.post(
            "/api/v1/materials/import", files=files, headers=ADMIN_HEADERS
        )
        assert response.status_code == 200
        data = response.json()
        assert data["updated"] == 1
        assert data["created"] == 0

        list_response = await client.get("/api/v1/materials/")
        updated = next(
            m for m in list_response.json()["materials"]
            if m["name"] == "ASTM A228 Music Wire"
        )
        assert updated["cost_usd_per_kg"] == 7.77

    async def test_import_reports_malformed_row_without_aborting(
        self, client: AsyncClient
    ) -> None:
        body = (
            "Bad Row Alloy,ASTM Q,not-a-number,200,7800,1300,1600,150,false,4.0,true\n"
            "Good Row Alloy,ASTM R,80,200,7800,1300,1600,150,false,4.0,true\n"
        )
        files = self._csv_file(body)
        response = await client.post(
            "/api/v1/materials/import", files=files, headers=ADMIN_HEADERS
        )
        assert response.status_code == 200
        data = response.json()
        assert data["errors"] == 1
        assert data["created"] == 1
        error_rows = [r for r in data["results"] if r["action"] == "error"]
        assert error_rows[0]["name"] == "Bad Row Alloy"

    async def test_import_missing_required_column_returns_400(
        self, client: AsyncClient
    ) -> None:
        files = {
            "file": (
                "materials.csv",
                io.BytesIO(b"name,standard\nIncomplete Alloy,ASTM Q\n"),
                "text/csv",
            )
        }
        response = await client.post(
            "/api/v1/materials/import", files=files, headers=ADMIN_HEADERS
        )
        assert response.status_code == 400
