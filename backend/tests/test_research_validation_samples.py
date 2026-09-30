from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


def _prepare_state(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("IDLRAG_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("IDLRAG_IMPORT_ROOTS", str(tmp_path))
    from app.core.config import get_app_settings
    from app.db.database import get_engine, get_index_engine, get_index_session_factory, get_session_factory

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


def _asset_payload(name: str) -> dict:
    return {
        "name": name,
        "asset_kind": "reference",
        "source_type": "reference",
        "source_uri": f"research://assets/{name}.tif",
    }


def test_validation_samples_keep_provenance_strata_and_owner_isolation(monkeypatch, tmp_path: Path) -> None:
    _prepare_state(monkeypatch, tmp_path)
    from app.main import create_app

    with TestClient(create_app()) as client:
        owner = _register(client, "sampleowner")
        other = _register(client, "sampleother")
        owner_headers = _headers(owner)
        project = client.post(
            "/api/research/projects", json={"name": "样本溯源", "entry_mode": "open"}, headers=owner_headers
        ).json()
        project_id = project["id"]
        source_asset = client.post(
            f"/api/research/projects/{project_id}/data-assets",
            json=_asset_payload("official_reference"),
            headers=owner_headers,
        ).json()
        snapshot = client.post(
            f"/api/research/projects/{project_id}/data-snapshots",
            json={"name": "样本冻结快照", "asset_ids": [source_asset["id"]]},
            headers=owner_headers,
        ).json()
        sample_payload = {
            "data_snapshot_id": snapshot["id"],
            "source_asset_id": source_asset["id"],
            "longitude": 115.75,
            "latitude": 28.85,
            "label": 1,
            "observed_at": "2024-06-15T10:30:00Z",
            "annotator": "teacher-a",
            "confidence": 0.9,
            "split": "independent_test",
            "spatial_block": "block-east-03",
            "temporal_stratum": "2024-wet-season",
            "conflict_status": "none",
            "source_note": "基于高分影像的人工判读。",
        }
        created = client.post(
            f"/api/research/projects/{project_id}/validation-samples",
            json=sample_payload,
            headers=owner_headers,
        )
        assert created.status_code == 201, created.text
        sample = created.json()
        assert sample["split"] == "independent_test"
        assert sample["confidence"] == 0.9
        assert sample["spatial_block"] == "block-east-03"
        assert sample["observed_at"].startswith("2024-06-15T10:30:00")

        csv_content = "\n".join(
            [
                "longitude,latitude,label,observed_at,annotator,confidence,split,spatial_block,temporal_stratum,source_note",
                "115.80,28.80,1,2024-07-01T10:30:00Z,teacher-a,0.95,independent_test,block-east-03,2024-wet-season,人工判读",
                "115.65,28.70,0,2024-08-01T10:30:00Z,teacher-b,0.90,independent_test,block-west-01,2024-wet-season,人工判读",
            ]
        )
        imported = client.post(
            f"/api/research/projects/{project_id}/validation-samples/import",
            data={"data_snapshot_id": str(snapshot["id"]), "source_asset_id": str(source_asset["id"])},
            files={"file": ("independent-samples.csv", csv_content.encode("utf-8"), "text/csv")},
            headers=owner_headers,
        )
        assert imported.status_code == 201, imported.text
        imported_payload = imported.json()
        assert imported_payload["imported_count"] == 2
        assert {item["spatial_block"] for item in imported_payload["samples"]} == {"block-east-03", "block-west-01"}
        assert all(item["source_asset_id"] == source_asset["id"] for item in imported_payload["samples"])

        invalid_csv_content = "\n".join(
            [
                "longitude,latitude,label,observed_at,annotator,confidence,split,spatial_block,temporal_stratum,source_note",
                "115.80,28.80,1,2024-09-01T10:30:00Z,teacher-a,0.95,independent_test,block-east-03,2024-wet-season,人工判读",
                "115.65,28.70,not-a-label,2024-09-02T10:30:00Z,teacher-b,0.90,independent_test,block-west-01,2024-wet-season,人工判读",
            ]
        )
        invalid_import = client.post(
            f"/api/research/projects/{project_id}/validation-samples/import",
            data={"data_snapshot_id": str(snapshot["id"]), "source_asset_id": str(source_asset["id"])},
            files={"file": ("invalid-samples.csv", invalid_csv_content.encode("utf-8"), "text/csv")},
            headers=owner_headers,
        )
        assert invalid_import.status_code == 400
        assert "第 3 行" in invalid_import.json()["detail"]

        samples = client.get(f"/api/research/projects/{project_id}/validation-samples", headers=owner_headers)
        assert samples.status_code == 200
        assert [item["id"] for item in samples.json()] == [sample["id"], *[item["id"] for item in imported_payload["samples"]]]
        other_list = client.get(f"/api/research/projects/{project_id}/validation-samples", headers=_headers(other))
        assert other_list.status_code == 404

        foreign_project = client.post(
            "/api/research/projects", json={"name": "其他样本", "entry_mode": "open"}, headers=_headers(other)
        ).json()
        foreign_asset = client.post(
            f"/api/research/projects/{foreign_project['id']}/data-assets",
            json=_asset_payload("foreign_reference"),
            headers=_headers(other),
        ).json()
        invalid = client.post(
            f"/api/research/projects/{project_id}/validation-samples",
            json={**sample_payload, "source_asset_id": foreign_asset["id"]},
            headers=owner_headers,
        )
        assert invalid.status_code == 400
