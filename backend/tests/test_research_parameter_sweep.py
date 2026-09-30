# ruff: noqa: E402
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from threading import Event

import numpy as np
import rasterio
from fastapi.testclient import TestClient
from rasterio.transform import from_origin

from app.services.geospatial_runtime import configure_bundled_rasterio_data

configure_bundled_rasterio_data()


def _prepare_state(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("IDLRAG_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("IDLRAG_IMPORT_ROOTS", str(tmp_path))
    monkeypatch.setenv("IDLRAG_INDEX_WORKER_ENABLED", "false")
    from app.core.config import get_app_settings
    from app.db.database import get_engine, get_index_engine, get_index_session_factory, get_session_factory

    get_app_settings.cache_clear()
    get_engine.cache_clear()
    get_index_engine.cache_clear()
    get_session_factory.cache_clear()
    get_index_session_factory.cache_clear()
    from app.api.routes.auth import _clear_register_rate_limits

    _clear_register_rate_limits()


def _write_raster(path: Path) -> None:
    values = np.asarray(
        [
            [[0.6, 0.2], [0.4, 0.1]],
            [[0.2, 0.4], [0.2, 0.1]],
        ],
        dtype=np.float32,
    )
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=2,
        height=2,
        count=2,
        dtype="float32",
        crs="EPSG:4326",
        transform=from_origin(115, 30, 10, 10),
        nodata=-9999.0,
    ) as destination:
        destination.write(values)


def test_parameter_sweep_ranks_development_candidates_and_isolates_test_split(monkeypatch, tmp_path: Path) -> None:
    _prepare_state(monkeypatch, tmp_path)
    raster_path = tmp_path / "sweep-input.tif"
    _write_raster(raster_path)
    from app.main import create_app

    with TestClient(create_app()) as client:
        auth = client.post("/api/auth/register", json={"username": "sweep-user", "password": "secret123"}).json()
        headers = {"Authorization": f"Bearer {auth['access_token']}"}
        project = client.post(
            "/api/research/projects",
            json={"name": "候选公式比较", "entry_mode": "open", "protocol": {"research_question": "比较开发样本上的阈值候选。"}},
            headers=headers,
        ).json()
        project_id = project["id"]
        with raster_path.open("rb") as source:
            asset = client.post(
                f"/api/research/projects/{project_id}/data-assets/upload",
                files={"file": (raster_path.name, source, "image/tiff")},
                data={"asset_kind": "raster", "name": "候选输入"},
                headers=headers,
            ).json()
        snapshot = client.post(
            f"/api/research/projects/{project_id}/data-snapshots",
            json={"name": "候选冻结快照", "asset_ids": [asset["id"]]},
            headers=headers,
        ).json()
        formula = client.post(
            f"/api/research/projects/{project_id}/formula-specs",
            json={
                "name": "阈值候选公式",
                "version": 1,
                "status": "draft",
                "spec": {
                    "operation": "normalized_difference_threshold",
                    "input_asset_id": asset["id"],
                    "inputs": {"green": {"band": 1}, "swir1": {"band": 2}},
                    "parameters": {"threshold": 0.0},
                },
            },
            headers=headers,
        ).json()
        experiment = client.post(
            f"/api/research/projects/{project_id}/experiments",
            json={
                "name": "开发样本阈值预览",
                "formula_spec_id": formula["id"],
                "data_snapshot_id": snapshot["id"],
                "runner_type": "python",
                "execution_mode": "preview",
                "parameters": {},
                "validation_plan": {
                    "split": "spatiotemporal-holdout",
                    "sample_validation": {"split": "independent_test", "min_confidence": 0.0, "require_unconflicted": True},
                },
                "visualization_contract": ["input", "feature", "classification"],
            },
            headers=headers,
        ).json()
        sample_points = [
            (116, 29, 1, "development", "north-west"),
            (126, 29, 0, "development", "north-east"),
            (116, 19, 1, "development", "south-west"),
            (126, 19, 0, "development", "south-east"),
            (116, 29, 1, "independent_test", "test-block"),
        ]
        for longitude, latitude, label, split, block in sample_points:
            response = client.post(
                f"/api/research/projects/{project_id}/validation-samples",
                json={
                    "data_snapshot_id": snapshot["id"],
                    "longitude": longitude,
                    "latitude": latitude,
                    "label": label,
                    "observed_at": datetime(2024, 1, 1, tzinfo=UTC).isoformat(),
                    "annotator": "teacher",
                    "confidence": 1.0,
                    "split": split,
                    "spatial_block": block,
                    "temporal_stratum": "dry-season",
                    "source_note": "controlled sweep fixture",
                },
                headers=headers,
            )
            assert response.status_code == 201, response.text

        sweep = client.post(
            f"/api/research/projects/{project_id}/experiments/{experiment['id']}/sweeps",
            json={
                "evaluation_split": "development",
                "ranking_metric": "f1",
                "candidates": [
                    {"name": "fixed-0", "parameters": {"threshold": 0.0}},
                    {"name": "strict-0.4", "parameters": {"threshold": 0.4}},
                ],
            },
            headers=headers,
        )
        assert sweep.status_code == 201, sweep.text
        payload = sweep.json()
        assert payload["status"] == "completed"
        assert payload["manifest"]["run_kind"] == "parameter_sweep"
        sweep_manifest = payload["manifest"]["sweep"]
        assert sweep_manifest["evaluation_split"] == "development"
        assert sweep_manifest["ranking"][0]["name"] == "fixed-0"
        assert all(item["selection"]["split"] == "development" for item in sweep_manifest["candidates"])
        assert all("independent_test" not in str(item) for item in sweep_manifest["candidates"])
        assert any(output["kind"] == "parameter_sweep_manifest" for output in payload["outputs"])
        manifest_name = next(item["file_name"] for item in payload["outputs"] if item["kind"] == "parameter_sweep_manifest")
        downloaded = client.get(
            f"/api/research/projects/{project_id}/experiments/{experiment['id']}/runs/{payload['id']}/outputs/{manifest_name}",
            headers=headers,
        )
        assert downloaded.status_code == 200, downloaded.text
        assert b'"selection_notice"' in downloaded.content

        queued_sweep = client.post(
            f"/api/research/projects/{project_id}/experiments/{experiment['id']}/sweeps?mode=queue",
            json={
                "evaluation_split": "development",
                "ranking_metric": "f1",
                "candidates": [
                    {"name": "queued-0", "parameters": {"threshold": 0.0}},
                    {"name": "queued-1", "parameters": {"threshold": 0.4}},
                ],
            },
            headers=headers,
        )
        assert queued_sweep.status_code == 201, queued_sweep.text
        queued_payload = queued_sweep.json()
        assert queued_payload["status"] == "queued"
        assert queued_payload["manifest"]["run_kind"] == "parameter_sweep"
        from app.index_worker import run_index_worker

        worker_stop = Event()
        run_index_worker(stop_event=worker_stop, sleep=lambda _seconds: worker_stop.set())
        queued_runs = client.get(
            f"/api/research/projects/{project_id}/experiments/{experiment['id']}/runs",
            headers=headers,
        ).json()
        queued_completed = next(item for item in queued_runs if item["id"] == queued_payload["id"])
        assert queued_completed["status"] == "completed"
        assert queued_completed["manifest"]["run_kind"] == "parameter_sweep"
        completed_retry = client.post(
            f"/api/research/projects/{project_id}/experiments/{experiment['id']}/runs/{queued_payload['id']}/retry?mode=queue",
            headers=headers,
        )
        assert completed_retry.status_code == 201, completed_retry.text
        completed_retry_id = completed_retry.json()["id"]
        completed_retry_worker_stop = Event()
        run_index_worker(stop_event=completed_retry_worker_stop, sleep=lambda _seconds: completed_retry_worker_stop.set())
        completed_retry_runs = client.get(
            f"/api/research/projects/{project_id}/experiments/{experiment['id']}/runs",
            headers=headers,
        ).json()
        completed_retry_payload = next(item for item in completed_retry_runs if item["id"] == completed_retry_id)
        assert completed_retry_payload["status"] == "completed"
        assert completed_retry_payload["manifest"]["run_control"]["retry_of_run_id"] == queued_payload["id"]

        running_cancel = client.post(
            f"/api/research/projects/{project_id}/experiments/{experiment['id']}/sweeps?mode=queue",
            json={
                "evaluation_split": "development",
                "ranking_metric": "f1",
                "candidates": [
                    {"name": "running-cancel-0", "parameters": {"threshold": 0.0}},
                    {"name": "running-cancel-1", "parameters": {"threshold": 0.4}},
                ],
            },
            headers=headers,
        )
        assert running_cancel.status_code == 201, running_cancel.text
        running_cancel_id = running_cancel.json()["id"]
        from app.api.routes import research as research_routes
        from app.db.database import get_session_factory

        execution_db = get_session_factory()()
        original_execute = research_routes.run_service.python_runner.execute
        cancel_triggered = False

        def execute_then_cancel(*args, **kwargs):
            nonlocal cancel_triggered
            result = original_execute(*args, **kwargs)
            if not cancel_triggered:
                cancel_triggered = True
                research_routes.run_service.cancel_run(
                    execution_db,
                    project_id,
                    experiment["id"],
                    running_cancel_id,
                    auth["user"]["id"],
                )
            return result

        monkeypatch.setattr(research_routes.run_service.python_runner, "execute", execute_then_cancel)
        try:
            assert research_routes.run_service.process_next_queued_run(execution_db) is True
        finally:
            execution_db.close()
        running_cancel_runs = client.get(
            f"/api/research/projects/{project_id}/experiments/{experiment['id']}/runs",
            headers=headers,
        ).json()
        running_cancel_payload = next(item for item in running_cancel_runs if item["id"] == running_cancel_id)
        assert running_cancel_payload["status"] == "cancelled"
        assert running_cancel_payload["outputs"]

        queued_for_cancel = client.post(
            f"/api/research/projects/{project_id}/experiments/{experiment['id']}/sweeps?mode=queue",
            json={
                "evaluation_split": "development",
                "candidates": [
                    {"name": "cancel-0", "parameters": {"threshold": 0.0}},
                    {"name": "cancel-1", "parameters": {"threshold": 0.4}},
                ],
            },
            headers=headers,
        )
        assert queued_for_cancel.status_code == 201, queued_for_cancel.text
        cancel_id = queued_for_cancel.json()["id"]
        cancelled = client.post(
            f"/api/research/projects/{project_id}/experiments/{experiment['id']}/runs/{cancel_id}/cancel",
            headers=headers,
        )
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["status"] == "cancelled"
        retried = client.post(
            f"/api/research/projects/{project_id}/experiments/{experiment['id']}/runs/{cancel_id}/retry?mode=queue",
            headers=headers,
        )
        assert retried.status_code == 201, retried.text
        retried_id = retried.json()["id"]
        retry_worker_stop = Event()
        run_index_worker(stop_event=retry_worker_stop, sleep=lambda _seconds: retry_worker_stop.set())
        retry_runs = client.get(
            f"/api/research/projects/{project_id}/experiments/{experiment['id']}/runs",
            headers=headers,
        ).json()
        retried_completed = next(item for item in retry_runs if item["id"] == retried_id)
        assert retried_completed["status"] == "completed"
        assert retried_completed["manifest"]["run_control"]["retry_of_run_id"] == cancel_id

        forbidden_split = client.post(
            f"/api/research/projects/{project_id}/experiments/{experiment['id']}/sweeps",
            json={
                "evaluation_split": "independent_test",
                "candidates": [
                    {"name": "a", "parameters": {"threshold": 0.0}},
                    {"name": "b", "parameters": {"threshold": 0.1}},
                ],
            },
            headers=headers,
        )
        assert forbidden_split.status_code == 422, forbidden_split.text

        too_many = client.post(
            f"/api/research/projects/{project_id}/experiments/{experiment['id']}/sweeps",
            json={
                "evaluation_split": "development",
                "candidates": [{"name": str(index), "parameters": {"threshold": 0.0}} for index in range(21)],
            },
            headers=headers,
        )
        assert too_many.status_code == 422, too_many.text

        from app.db.database import get_session_factory

        db = get_session_factory()()
        try:
            from app.db.models import ResearchExperiment

            stored_experiment = db.get(ResearchExperiment, experiment["id"])
            assert stored_experiment is not None
            stored_experiment.execution_mode = "formal"
            db.commit()
        finally:
            db.close()
        formal_rejected = client.post(
            f"/api/research/projects/{project_id}/experiments/{experiment['id']}/sweeps",
            json={
                "evaluation_split": "development",
                "candidates": [
                    {"name": "a", "parameters": {"threshold": 0.0}},
                    {"name": "b", "parameters": {"threshold": 0.1}},
                ],
            },
            headers=headers,
        )
        assert formal_rejected.status_code == 400, formal_rejected.text

        outsider = client.post("/api/auth/register", json={"username": "sweep-outsider", "password": "secret123"}).json()
        forbidden = client.post(
            f"/api/research/projects/{project_id}/experiments/{experiment['id']}/sweeps",
            json={
                "evaluation_split": "development",
                "candidates": [
                    {"name": "a", "parameters": {"threshold": 0.0}},
                    {"name": "b", "parameters": {"threshold": 0.1}},
                ],
            },
            headers={"Authorization": f"Bearer {outsider['access_token']}"},
        )
        assert forbidden.status_code == 404, forbidden.text


def test_parameter_sweep_preserves_candidate_failures(monkeypatch, tmp_path: Path) -> None:
    _prepare_state(monkeypatch, tmp_path)
    raster_path = tmp_path / "sweep-failure.tif"
    _write_raster(raster_path)
    from app.main import create_app

    with TestClient(create_app()) as client:
        auth = client.post("/api/auth/register", json={"username": "sweep-failure", "password": "secret123"}).json()
        headers = {"Authorization": f"Bearer {auth['access_token']}"}
        project = client.post(
            "/api/research/projects",
            json={"name": "候选失败保留", "entry_mode": "open", "protocol": {"research_question": "保留候选失败原因。"}},
            headers=headers,
        ).json()
        project_id = project["id"]
        with raster_path.open("rb") as source:
            asset = client.post(
                f"/api/research/projects/{project_id}/data-assets/upload",
                files={"file": (raster_path.name, source, "image/tiff")},
                data={"asset_kind": "raster", "name": "候选失败输入"},
                headers=headers,
            ).json()
        snapshot = client.post(
            f"/api/research/projects/{project_id}/data-snapshots",
            json={"name": "候选失败快照", "asset_ids": [asset["id"]]},
            headers=headers,
        ).json()
        formula = client.post(
            f"/api/research/projects/{project_id}/formula-specs",
            json={
                "name": "候选失败公式",
                "version": 1,
                "status": "draft",
                "spec": {
                    "operation": "normalized_difference_threshold",
                    "input_asset_id": asset["id"],
                    "inputs": {"green": {"band": 1}, "swir1": {"band": 2}},
                    "parameters": {"threshold": 0.0},
                },
            },
            headers=headers,
        ).json()
        experiment = client.post(
            f"/api/research/projects/{project_id}/experiments",
            json={
                "name": "候选失败预览",
                "formula_spec_id": formula["id"],
                "data_snapshot_id": snapshot["id"],
                "runner_type": "python",
                "execution_mode": "preview",
                "validation_plan": {
                    "sample_validation": {"split": "development", "min_confidence": 0.0, "require_unconflicted": True},
                },
            },
            headers=headers,
        ).json()
        for longitude, latitude, label in [(116, 29, 1), (126, 29, 0)]:
            response = client.post(
                f"/api/research/projects/{project_id}/validation-samples",
                json={
                    "data_snapshot_id": snapshot["id"],
                    "longitude": longitude,
                    "latitude": latitude,
                    "label": label,
                    "observed_at": datetime(2024, 1, 1, tzinfo=UTC).isoformat(),
                    "annotator": "teacher",
                    "confidence": 1.0,
                    "split": "development",
                    "spatial_block": "block",
                    "temporal_stratum": "season",
                    "source_note": "failure fixture",
                },
                headers=headers,
            )
            assert response.status_code == 201, response.text
        response = client.post(
            f"/api/research/projects/{project_id}/experiments/{experiment['id']}/sweeps",
            json={
                "evaluation_split": "development",
                "candidates": [
                    {"name": "valid", "parameters": {"threshold": 0.0}},
                    {"name": "invalid", "parameters": {"threshold": "not-a-number"}},
                ],
            },
            headers=headers,
        )
        assert response.status_code == 201, response.text
        payload = response.json()
        assert payload["status"] == "failed"
        assert payload["manifest"]["sweep"]["failed_candidate_count"] == 1
        failed = next(item for item in payload["manifest"]["sweep"]["candidates"] if item["name"] == "invalid")
        assert failed["status"] == "failed"
        assert failed["error"]
