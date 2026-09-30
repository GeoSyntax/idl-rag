# ruff: noqa: E402

from pathlib import Path

from fastapi.testclient import TestClient
import numpy as np
from app.services.geospatial_runtime import configure_bundled_rasterio_data

configure_bundled_rasterio_data()

import rasterio
from rasterio.io import MemoryFile
from rasterio.transform import from_origin


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


def _geotiff_bytes(*, width: int, height: int, pixel_size: float, values: np.ndarray) -> bytes:
    with MemoryFile() as memory:
        with memory.open(
            driver="GTiff",
            width=width,
            height=height,
            count=1,
            dtype="float32",
            crs="EPSG:4326",
            transform=from_origin(0, 4, pixel_size, pixel_size),
        ) as dataset:
            dataset.write(values.astype(np.float32)[0], 1)
        return memory.read()


def _upload(client: TestClient, project_id: int, headers: dict[str, str], content: bytes, name: str) -> dict:
    response = client.post(
        f"/api/research/projects/{project_id}/data-assets/upload",
        files={"file": (name, content, "image/tiff")},
        data={"asset_kind": "raster", "name": name},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_private_raster_stack_aligns_bands_and_records_grid(monkeypatch, tmp_path: Path) -> None:
    _prepare_state(monkeypatch, tmp_path)
    from app.main import create_app
    from app.services.research_asset_storage import ResearchAssetStorage

    with TestClient(create_app()) as client:
        auth = _register(client, "stack-owner")
        headers = _headers(auth)
        project = client.post(
            "/api/research/projects",
            json={"name": "MNDWI 对齐验证", "entry_mode": "open"},
            headers=headers,
        )
        assert project.status_code == 201, project.text
        project_id = project.json()["id"]
        reference = _upload(
            client,
            project_id,
            headers,
            _geotiff_bytes(width=4, height=4, pixel_size=1, values=np.arange(16).reshape(1, 4, 4)),
            "B03.tif",
        )
        coarse = _upload(
            client,
            project_id,
            headers,
            _geotiff_bytes(width=2, height=2, pixel_size=2, values=np.array([[[10, 20], [30, 40]]])),
            "B11.tif",
        )

        response = client.post(
            f"/api/research/projects/{project_id}/data-assets/stack",
            json={
                "name": "B03_B11_aligned",
                "asset_ids": [reference["id"], coarse["id"]],
                "reference_asset_id": reference["id"],
                "band_names": ["green", "swir1"],
                "resampling": "bilinear",
            },
            headers=headers,
        )
        assert response.status_code == 201, response.text
        stacked = response.json()
        assert stacked["asset_kind"] == "raster"
        assert stacked["source_type"] == "local"
        assert stacked["source_uri"].startswith("research://assets/")
        assert len(stacked["sha256"]) == 64
        assert stacked["metadata"]["derived_operation"] == "raster_stack"
        assert stacked["metadata"]["source_asset_ids"] == [reference["id"], coarse["id"]]
        assert stacked["metadata"]["band_names"] == ["green", "swir1"]
        assert stacked["metadata"]["grid"]["count"] == 2
        assert stacked["metadata"]["grid"]["width"] == 4
        assert stacked["metadata"]["grid"]["height"] == 4

        output_path = ResearchAssetStorage().resolve_asset_uri(stacked["source_uri"])
        with rasterio.open(output_path) as dataset:
            assert dataset.count == 2
            assert (dataset.width, dataset.height) == (4, 4)
            assert dataset.crs.to_string() == "EPSG:4326"
            assert dataset.dtypes == ("float32", "float32")
            assert np.isfinite(dataset.read(1)).all()
            assert np.isfinite(dataset.read(2)).all()

        snapshot = client.post(
            f"/api/research/projects/{project_id}/data-snapshots",
            json={"name": "aligned-mndwi-snapshot", "asset_ids": [stacked["id"]]},
            headers=headers,
        )
        assert snapshot.status_code == 201, snapshot.text
        evidence = client.post(
            f"/api/research/projects/{project_id}/evidence-cards",
            json={
                "title": "MNDWI aligned-band baseline",
                "status": "verified",
                "source_type": "paper",
                "doi": "10.1080/01431160600589179",
                "applicability": "Green/SWIR normalized difference baseline.",
            },
            headers=headers,
        )
        assert evidence.status_code == 201, evidence.text
        formula = client.post(
            f"/api/research/projects/{project_id}/formula-specs",
            json={
                "name": "MNDWI aligned-band preview",
                "version": 1,
                "status": "frozen",
                "spec": {
                    "operation": "normalized_difference_threshold",
                    "input_asset_id": stacked["id"],
                    "inputs": {"green": 1, "swir1": 2},
                    "parameters": {"threshold": 0.0},
                },
                "evidence_card_ids": [evidence.json()["id"]],
            },
            headers=headers,
        )
        assert formula.status_code == 201, formula.text
        experiment = client.post(
            f"/api/research/projects/{project_id}/experiments",
            json={
                "name": "MNDWI aligned-band Python preview",
                "formula_spec_id": formula.json()["id"],
                "data_snapshot_id": snapshot.json()["id"],
                "runner_type": "python",
                "execution_mode": "preview",
                "visualization_contract": ["input", "feature", "classification"],
            },
            headers=headers,
        )
        assert experiment.status_code == 201, experiment.text
        run = client.post(
            f"/api/research/projects/{project_id}/experiments/{experiment.json()['id']}/runs",
            headers=headers,
        )
        assert run.status_code == 201, run.text
        run_payload = run.json()
        assert run_payload["status"] == "completed"
        assert {output["kind"] for output in run_payload["outputs"]} >= {
            "input_preview",
            "feature_raster",
            "feature_preview",
            "classification_raster",
            "classification_preview",
            "run_manifest",
        }


def test_raster_stack_rejects_remote_and_cross_project_inputs(monkeypatch, tmp_path: Path) -> None:
    _prepare_state(monkeypatch, tmp_path)
    from app.main import create_app

    with TestClient(create_app()) as client:
        owner = _register(client, "stack-security-owner")
        other = _register(client, "stack-security-other")
        owner_headers = _headers(owner)
        other_headers = _headers(other)
        project = client.post(
            "/api/research/projects",
            json={"name": "项目 A", "entry_mode": "open"},
            headers=owner_headers,
        ).json()
        other_project = client.post(
            "/api/research/projects",
            json={"name": "项目 B", "entry_mode": "open"},
            headers=other_headers,
        ).json()
        private = _upload(
            client,
            project["id"],
            owner_headers,
            _geotiff_bytes(width=2, height=2, pixel_size=1, values=np.ones((1, 2, 2))),
            "private.tif",
        )
        remote = client.post(
            f"/api/research/projects/{project['id']}/data-assets",
            json={
                "name": "remote reference",
                "asset_kind": "raster",
                "source_type": "reference",
                "source_uri": "https://example.test/remote.tif",
            },
            headers=owner_headers,
        ).json()
        foreign = _upload(
            client,
            other_project["id"],
            other_headers,
            _geotiff_bytes(width=2, height=2, pixel_size=1, values=np.zeros((1, 2, 2))),
            "foreign.tif",
        )

        remote_response = client.post(
            f"/api/research/projects/{project['id']}/data-assets/stack",
            json={"name": "bad-remote", "asset_ids": [private["id"], remote["id"]]},
            headers=owner_headers,
        )
        assert remote_response.status_code == 400
        foreign_response = client.post(
            f"/api/research/projects/{project['id']}/data-assets/stack",
            json={"name": "bad-foreign", "asset_ids": [private["id"], foreign["id"]]},
            headers=owner_headers,
        )
        assert foreign_response.status_code == 400


def test_raster_stack_rejects_invalid_geotiff_without_registering_output(monkeypatch, tmp_path: Path) -> None:
    _prepare_state(monkeypatch, tmp_path)
    from app.main import create_app

    with TestClient(create_app()) as client:
        auth = _register(client, "stack-invalid-owner")
        headers = _headers(auth)
        project = client.post(
            "/api/research/projects",
            json={"name": "无效栅格", "entry_mode": "open"},
            headers=headers,
        ).json()
        first = _upload(
            client,
            project["id"],
            headers,
            _geotiff_bytes(width=2, height=2, pixel_size=1, values=np.ones((1, 2, 2))),
            "first.tif",
        )
        invalid = _upload(client, project["id"], headers, b"not-a-geotiff", "invalid.tif")
        response = client.post(
            f"/api/research/projects/{project['id']}/data-assets/stack",
            json={"name": "should-fail", "asset_ids": [first["id"], invalid["id"]]},
            headers=headers,
        )
        assert response.status_code == 400
        assets = client.get(f"/api/research/projects/{project['id']}/data-assets", headers=headers)
        assert assets.status_code == 200
        assert all(asset["metadata"].get("derived_operation") != "raster_stack" for asset in assets.json())
