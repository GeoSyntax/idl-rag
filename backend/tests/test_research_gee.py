from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


def _prepare_state(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("IDLRAG_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("IDLRAG_IMPORT_ROOTS", str(tmp_path))
    monkeypatch.setenv("IDLRAG_GEE_ENABLED", "true")

    from app.core.config import get_app_settings
    from app.db.database import (
        get_engine,
        get_index_engine,
        get_index_session_factory,
        get_session_factory,
    )

    get_app_settings.cache_clear()
    get_engine.cache_clear()
    get_index_engine.cache_clear()
    get_session_factory.cache_clear()
    get_index_session_factory.cache_clear()

    from app.api.routes.auth import _clear_register_rate_limits

    _clear_register_rate_limits()


def _register(client: TestClient, username: str) -> dict:
    response = client.post("/api/auth/register", json={"username": username, "password": "secret123"})
    assert response.status_code == 201, response.text
    return response.json()


def _headers(auth: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {auth['access_token']}"}


def test_gee_fetch_creates_project_owned_private_data_asset(monkeypatch, tmp_path: Path) -> None:
    _prepare_state(monkeypatch, tmp_path)

    from app.api.routes.research import gee_service
    from app.main import create_app
    from app.services.research_asset_storage import ResearchAssetStorage

    monkeypatch.setattr(gee_service, "_download_image", lambda _: (b"fake-geotiff-bytes", None))
    with TestClient(create_app()) as client:
        owner = _register(client, "geeowner")
        other = _register(client, "geeother")
        owner_headers = _headers(owner)
        project_response = client.post(
            "/api/research/projects",
            json={"name": "GEE 研究资产", "entry_mode": "open"},
            headers=owner_headers,
        )
        assert project_response.status_code == 201, project_response.text
        project_id = project_response.json()["id"]
        payload = {
            "dataset_id": "COPERNICUS/S2_SR_HARMONIZED",
            "start_date": "2024-05-01",
            "end_date": "2024-05-15",
            "bbox": [115.7, 28.8, 115.8, 28.9],
            "bands": ["B3", "B11"],
            "scale": 10,
            "crs": "EPSG:4326",
            "composite": "median",
            "label": "poyang_s2_wet",
        }
        response = client.post(
            f"/api/research/projects/{project_id}/gee-fetch", json=payload, headers=owner_headers
        )
        assert response.status_code == 201, response.text
        asset = response.json()["asset"]
        assert asset["source_type"] == "gee"
        assert asset["asset_kind"] == "raster"
        assert asset["access_policy"] == "private-local"
        assert asset["source_uri"].startswith("research://assets/project-")
        assert asset["metadata"]["gee_query"] == {
            key: payload[key]
            for key in ("dataset_id", "bbox", "bands", "scale", "crs", "composite", "start_date", "end_date")
        }
        assert asset["metadata"]["raw_project_data_sent"] is False
        assert ResearchAssetStorage().resolve_asset_uri(asset["source_uri"]).read_bytes() == b"fake-geotiff-bytes"

        foreign = client.post(
            f"/api/research/projects/{project_id}/gee-fetch", json=payload, headers=_headers(other)
        )
        assert foreign.status_code == 404

        invalid_dataset = client.post(
            f"/api/research/projects/{project_id}/gee-fetch",
            json={**payload, "dataset_id": "PRIVATE/NOT_ALLOWED"},
            headers=owner_headers,
        )
        assert invalid_dataset.status_code == 400
