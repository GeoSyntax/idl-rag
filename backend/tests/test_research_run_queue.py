# ruff: noqa: E402
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event

import numpy as np
import rasterio
from fastapi.testclient import TestClient
from rasterio.transform import from_origin


def _prepare_state(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("IDLRAG_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("IDLRAG_IMPORT_ROOTS", str(tmp_path))
    monkeypatch.setenv("IDLRAG_INDEX_WORKER_ENABLED", "false")
    monkeypatch.setenv("IDLRAG_RESEARCH_RUN_TIMEOUT_MINUTES", "5")

    from app.core.config import get_app_settings
    from app.db.database import get_engine, get_index_engine, get_index_session_factory, get_session_factory

    get_app_settings.cache_clear()
    get_engine.cache_clear()
    get_index_engine.cache_clear()
    get_session_factory.cache_clear()
    get_index_session_factory.cache_clear()


def _write_raster(path: Path) -> None:
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=2,
        height=2,
        count=2,
        dtype="float32",
        crs="EPSG:4326",
        transform=from_origin(115, 30, 0.01, 0.01),
        nodata=-9999.0,
    ) as destination:
        destination.write(
            np.asarray(
                [
                    [[0.6, 0.2], [0.4, 0.1]],
                    [[0.2, 0.4], [0.2, 0.1]],
                ],
                dtype=np.float32,
            )
        )


def test_queue_mode_is_claimed_by_research_worker(monkeypatch, tmp_path: Path) -> None:
    _prepare_state(monkeypatch, tmp_path)

    from app.db.database import get_session_factory
    from app.api.routes import research as research_routes
    from app.core.config import get_app_settings
    from app.index_worker import run_index_worker
    from app.main import create_app

    raster_path = tmp_path / "queue-scene.tif"
    _write_raster(raster_path)
    with TestClient(create_app()) as client:
        auth = client.post(
            "/api/auth/register",
            json={"username": "queue-researcher", "password": "secret123"},
        ).json()
        headers = {"Authorization": f"Bearer {auth['access_token']}"}
        project = client.post(
            "/api/research/projects",
            json={
                "name": "后台运行验收",
                "entry_mode": "open",
                "protocol": {"research_question": "队列模式是否能生成可追溯的预览结果？"},
            },
            headers=headers,
        ).json()
        project_id = project["id"]
        with raster_path.open("rb") as source:
            asset = client.post(
                f"/api/research/projects/{project_id}/data-assets/upload",
                files={"file": (raster_path.name, source, "image/tiff")},
                data={"asset_kind": "raster", "name": "队列输入影像"},
                headers=headers,
            ).json()
        snapshot = client.post(
            f"/api/research/projects/{project_id}/data-snapshots",
            json={"name": "队列冻结快照", "asset_ids": [asset["id"]]},
            headers=headers,
        ).json()
        evidence = client.post(
            f"/api/research/projects/{project_id}/evidence-cards",
            json={
                "title": "队列测试方法证据",
                "status": "verified",
                "source_type": "official_document",
                "source_url": "https://example.invalid/queue-method",
            },
            headers=headers,
        ).json()
        formula = client.post(
            f"/api/research/projects/{project_id}/formula-specs",
            json={
                "name": "队列 MNDWI",
                "version": 1,
                "status": "frozen",
                "spec": {
                    "operation": "normalized_difference_threshold",
                    "input_asset_id": asset["id"],
                    "inputs": {"green": {"band": 1}, "swir1": {"band": 2}},
                    "parameters": {"threshold": 0.0},
                },
                "evidence_card_ids": [evidence["id"]],
            },
            headers=headers,
        ).json()
        experiment = client.post(
            f"/api/research/projects/{project_id}/experiments",
            json={
                "name": "队列预览实验",
                "formula_spec_id": formula["id"],
                "data_snapshot_id": snapshot["id"],
                "runner_type": "python",
                "execution_mode": "preview",
                "parameters": {"threshold": 0.0},
                "visualization_contract": ["input", "feature", "classification"],
            },
            headers=headers,
        ).json()

        queued = client.post(
            f"/api/research/projects/{project_id}/experiments/{experiment['id']}/runs?mode=queue",
            headers=headers,
        )
        assert queued.status_code == 201, queued.text
        queued_payload = queued.json()
        assert queued_payload["status"] == "queued"
        assert queued_payload["started_at"] is None

        stop_event = Event()
        run_index_worker(stop_event=stop_event, sleep=lambda _seconds: stop_event.set())
        heartbeat = json.loads(get_app_settings().index_worker_heartbeat_path.read_text(encoding="utf-8"))
        assert heartbeat["status"] == "stopped"
        assert heartbeat["processed_count"] >= 1

        runs = client.get(
            f"/api/research/projects/{project_id}/experiments/{experiment['id']}/runs",
            headers=headers,
        )
        assert runs.status_code == 200, runs.text
        completed = runs.json()[0]
        assert completed["id"] == queued_payload["id"]
        assert completed["status"] == "completed"
        assert completed["manifest"]["data_snapshot"]["id"] == snapshot["id"]
        assert any(output["kind"] == "feature_preview" for output in completed["outputs"])

        queued_for_cancel = client.post(
            f"/api/research/projects/{project_id}/experiments/{experiment['id']}/runs?mode=queue",
            headers=headers,
        )
        assert queued_for_cancel.status_code == 201, queued_for_cancel.text
        cancel_source_id = queued_for_cancel.json()["id"]
        cancelled = client.post(
            f"/api/research/projects/{project_id}/experiments/{experiment['id']}/runs/{cancel_source_id}/cancel",
            headers=headers,
        )
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["status"] == "cancelled"

        retried = client.post(
            f"/api/research/projects/{project_id}/experiments/{experiment['id']}/runs/{cancel_source_id}/retry?mode=queue",
            headers=headers,
        )
        assert retried.status_code == 201, retried.text
        retried_payload = retried.json()
        assert retried_payload["id"] != cancel_source_id
        assert retried_payload["status"] == "queued"
        assert retried_payload["manifest"]["retry_of_run_id"] == cancel_source_id

        retry_stop_event = Event()
        run_index_worker(stop_event=retry_stop_event, sleep=lambda _seconds: retry_stop_event.set())
        retried_runs = client.get(
            f"/api/research/projects/{project_id}/experiments/{experiment['id']}/runs",
            headers=headers,
        )
        assert retried_runs.status_code == 200, retried_runs.text
        retried_completed = next(item for item in retried_runs.json() if item["id"] == retried_payload["id"])
        assert retried_completed["status"] == "completed"
        assert retried_completed["manifest"]["run_control"]["retry_of_run_id"] == cancel_source_id
        completed_cancel = client.post(
            f"/api/research/projects/{project_id}/experiments/{experiment['id']}/runs/{retried_payload['id']}/cancel",
            headers=headers,
        )
        assert completed_cancel.status_code == 400, completed_cancel.text
        outsider = client.post(
            "/api/auth/register",
            json={"username": "queue-outsider", "password": "secret123"},
        ).json()
        outsider_headers = {"Authorization": f"Bearer {outsider['access_token']}"}
        outsider_cancel = client.post(
            f"/api/research/projects/{project_id}/experiments/{experiment['id']}/runs/{retried_payload['id']}/cancel",
            headers=outsider_headers,
        )
        assert outsider_cancel.status_code == 404, outsider_cancel.text
        outsider_retry = client.post(
            f"/api/research/projects/{project_id}/experiments/{experiment['id']}/runs/{retried_payload['id']}/retry?mode=queue",
            headers=outsider_headers,
        )
        assert outsider_retry.status_code == 404, outsider_retry.text

        cooperative = client.post(
            f"/api/research/projects/{project_id}/experiments/{experiment['id']}/runs?mode=queue",
            headers=headers,
        )
        assert cooperative.status_code == 201, cooperative.text
        cooperative_id = cooperative.json()["id"]
        
        stale_queued = client.post(
            f"/api/research/projects/{project_id}/experiments/{experiment['id']}/runs?mode=queue",
            headers=headers,
        )
        assert stale_queued.status_code == 201, stale_queued.text
        stale_run_id = stale_queued.json()["id"]
        db = get_session_factory()()
        try:
            from app.db.models import ResearchRun

            cooperative_run = db.get(ResearchRun, cooperative_id)
            assert cooperative_run is not None
            cooperative_run.status = "running"
            cooperative_run.started_at = datetime.now(UTC).replace(tzinfo=None)
            cooperative_run.experiment.status = "running"
            db.commit()
            cooperative_cancel = client.post(
                f"/api/research/projects/{project_id}/experiments/{experiment['id']}/runs/{cooperative_id}/cancel",
                headers=headers,
            )
            assert cooperative_cancel.status_code == 200, cooperative_cancel.text
            assert cooperative_cancel.json()["status"] == "running"
            db.refresh(cooperative_run)
            assert cooperative_run.manifest_json["cancel_requested"] is True
            cooperative_run.started_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=10)
            db.commit()
            assert research_routes.run_service.recover_stale_runs(db) == 1
            db.refresh(cooperative_run)
            assert cooperative_run.status == "cancelled"

            stale_run = db.get(ResearchRun, stale_run_id)
            assert stale_run is not None
            stale_run.status = "running"
            stale_run.started_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=10)
            stale_run.experiment.status = "running"
            db.commit()

            assert research_routes.run_service.recover_stale_runs(db) == 1
            db.refresh(stale_run)
            assert stale_run.status == "failed"
            assert "超时窗口" in (stale_run.error_message or "")
            assert stale_run.finished_at is not None
            assert stale_run.experiment.status == "failed"

            sync_run, _, _, _ = research_routes.run_service._create_queued_run(
                db,
                project_id,
                experiment["id"],
                project["owner_user_id"],
                execution_mode="sync",
            )
            sync_run.started_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=10)
            db.commit()
            assert sync_run.manifest_json["execution_mode"] == "sync"
            assert research_routes.run_service.recover_stale_runs(db) == 0
            db.refresh(sync_run)
            assert sync_run.status == "running"
        finally:
            db.close()
