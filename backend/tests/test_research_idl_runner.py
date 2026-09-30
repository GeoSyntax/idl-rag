from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import rasterio
from fastapi.testclient import TestClient
from rasterio.transform import from_origin


def _prepare_state(monkeypatch, tmp_path: Path, idl_executable: str = "idl") -> None:
    monkeypatch.setenv("IDLRAG_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("IDLRAG_IMPORT_ROOTS", str(tmp_path))
    monkeypatch.setenv("IDLRAG_IDL_EXECUTABLE", idl_executable)

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


def _raster_bytes(tmp_path: Path) -> bytes:
    path = tmp_path / "input.tif"
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=2,
        height=2,
        count=1,
        dtype="uint8",
        crs="EPSG:4326",
        transform=from_origin(115, 30, 0.01, 0.01),
    ) as destination:
        destination.write(np.ones((1, 2, 2), dtype=np.uint8))
    return path.read_bytes()


def test_project_idl_runner_uploads_private_script_and_collects_outputs(monkeypatch, tmp_path: Path) -> None:
    _prepare_state(monkeypatch, tmp_path)
    from app.services import research_idl_runner

    observed: dict[str, object] = {}

    def fake_run(args, *, cwd, stdout, stderr, timeout, shell, env):
        observed["args"] = args
        observed["timeout"] = timeout
        observed["shell"] = shell
        observed["env"] = env
        stdout.write("IDL OK\n")
        Path(cwd, "water_mask.png").write_bytes(b"fake-png")
        with rasterio.open(
            Path(cwd, "water_mask.tif"),
            "w",
            driver="GTiff",
            width=2,
            height=2,
            count=1,
            dtype="uint8",
            crs="EPSG:4326",
            transform=from_origin(115, 30, 0.01, 0.01),
        ) as destination:
            destination.write(np.ones((1, 2, 2), dtype=np.uint8))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(research_idl_runner.subprocess, "run", fake_run)

    from app.main import create_app

    with TestClient(create_app()) as client:
        owner = _register(client, "idlresearcher")
        headers = _headers(owner)
        project_response = client.post(
            "/api/research/projects",
            json={"name": "IDL 研究项目", "entry_mode": "open", "protocol": {"research_question": "IDL 对照"}},
            headers=headers,
        )
        assert project_response.status_code == 201, project_response.text
        project_id = project_response.json()["id"]

        script_response = client.post(
            f"/api/research/projects/{project_id}/idl-scripts/upload",
            files={"file": ("water.pro", b"pro water_run\n  print, 'ok'\nend\n", "text/plain")},
            headers=headers,
        )
        assert script_response.status_code == 201, script_response.text
        script = script_response.json()
        assert script["asset_kind"] == "derived"
        assert script["metadata"]["asset_role"] == "idl_script"
        assert script["source_uri"].startswith("research://assets/")

        script_snapshot = client.post(
            f"/api/research/projects/{project_id}/data-snapshots",
            json={"name": "非法脚本快照", "asset_ids": [script["id"]]},
            headers=headers,
        )
        assert script_snapshot.status_code == 400

        invalid_script = client.post(
            f"/api/research/projects/{project_id}/idl-scripts/upload",
            files={"file": ("not.txt", b"not idl", "text/plain")},
            headers=headers,
        )
        assert invalid_script.status_code == 400

        raster_response = client.post(
            f"/api/research/projects/{project_id}/data-assets/upload",
            files={"file": ("input.tif", _raster_bytes(tmp_path), "image/tiff")},
            data={"asset_kind": "raster", "name": "输入影像"},
            headers=headers,
        )
        assert raster_response.status_code == 201, raster_response.text
        raster_asset = raster_response.json()
        reference_response = client.post(
            f"/api/research/projects/{project_id}/data-assets/upload",
            files={"file": ("reference.tif", _raster_bytes(tmp_path), "image/tiff")},
            data={"asset_kind": "reference", "name": "参考栅格"},
            headers=headers,
        )
        assert reference_response.status_code == 201, reference_response.text
        reference_asset = reference_response.json()
        snapshot_response = client.post(
            f"/api/research/projects/{project_id}/data-snapshots",
            json={"name": "IDL 输入快照", "asset_ids": [raster_asset["id"], reference_asset["id"]]},
            headers=headers,
        )
        assert snapshot_response.status_code == 201, snapshot_response.text
        snapshot = snapshot_response.json()

        formula_response = client.post(
            f"/api/research/projects/{project_id}/formula-specs",
            json={"name": "IDL 自定义过程", "spec": {"operation": "idl_external"}},
            headers=headers,
        )
        assert formula_response.status_code == 201, formula_response.text

        other_project_response = client.post(
            "/api/research/projects",
            json={"name": "另一个项目", "entry_mode": "open"},
            headers=headers,
        )
        assert other_project_response.status_code == 201, other_project_response.text
        other_project_id = other_project_response.json()["id"]
        other_raster_response = client.post(
            f"/api/research/projects/{other_project_id}/data-assets/upload",
            files={"file": ("other.tif", _raster_bytes(tmp_path), "image/tiff")},
            data={"asset_kind": "raster", "name": "另一个输入"},
            headers=headers,
        )
        assert other_raster_response.status_code == 201, other_raster_response.text
        other_snapshot_response = client.post(
            f"/api/research/projects/{other_project_id}/data-snapshots",
            json={"name": "另一个快照", "asset_ids": [other_raster_response.json()["id"]]},
            headers=headers,
        )
        assert other_snapshot_response.status_code == 201, other_snapshot_response.text
        other_formula_response = client.post(
            f"/api/research/projects/{other_project_id}/formula-specs",
            json={"name": "另一个公式", "spec": {"operation": "idl_external"}},
            headers=headers,
        )
        assert other_formula_response.status_code == 201, other_formula_response.text
        cross_project_experiment = client.post(
            f"/api/research/projects/{other_project_id}/experiments",
            json={
                "name": "跨项目 IDL 脚本",
                "formula_spec_id": other_formula_response.json()["id"],
                "data_snapshot_id": other_snapshot_response.json()["id"],
                "runner_type": "idl",
                "execution_mode": "preview",
                "parameters": {"idl_script_asset_id": script["id"]},
            },
            headers=headers,
        )
        assert cross_project_experiment.status_code == 400

        experiment_response = client.post(
            f"/api/research/projects/{project_id}/experiments",
            json={
                "name": "IDL 预览",
                "formula_spec_id": formula_response.json()["id"],
                "data_snapshot_id": snapshot["id"],
                "runner_type": "idl",
                "execution_mode": "preview",
                "parameters": {"idl_script_asset_id": script["id"]},
                "validation_plan": {"reference_asset_id": reference_asset["id"]},
            },
            headers=headers,
        )
        assert experiment_response.status_code == 201, experiment_response.text
        experiment_id = experiment_response.json()["id"]

        invalid_entrypoint = client.post(
            f"/api/research/projects/{project_id}/experiments",
            json={
                "name": "非法 IDL 入口",
                "formula_spec_id": formula_response.json()["id"],
                "data_snapshot_id": snapshot["id"],
                "runner_type": "idl",
                "execution_mode": "preview",
                "parameters": {"idl_script_asset_id": script["id"], "idl_entrypoint": "../escape"},
            },
            headers=headers,
        )
        assert invalid_entrypoint.status_code == 400

        invalid_prediction_file = client.post(
            f"/api/research/projects/{project_id}/experiments",
            json={
                "name": "非法 IDL 预测输出",
                "formula_spec_id": formula_response.json()["id"],
                "data_snapshot_id": snapshot["id"],
                "runner_type": "idl",
                "execution_mode": "preview",
                "parameters": {
                    "idl_script_asset_id": script["id"],
                    "idl_prediction_output_file": "../escape.tif",
                },
            },
            headers=headers,
        )
        assert invalid_prediction_file.status_code == 400

        run_response = client.post(
            f"/api/research/projects/{project_id}/experiments/{experiment_id}/runs?mode=sync",
            headers=headers,
        )
        assert run_response.status_code == 201, run_response.text
        run = run_response.json()
        assert run["status"] == "completed"
        assert run["runner_type"] == "idl"
        assert run["manifest"]["runner"] == "idl"
        assert run["manifest"]["entrypoint"] == "water_run"
        assert run["manifest"]["prediction_output_file"] == "water_mask.tif"
        assert run["manifest"]["validation"]["status"] == "completed"
        assert run["manifest"]["validation"]["metrics"]["overall_accuracy"] == 1.0
        assert any(output["file_name"] == "water_mask.png" for output in run["outputs"])
        assert any(output["kind"] == "validation_metrics" for output in run["outputs"])
        assert any(output["kind"] == "validation_error_raster" for output in run["outputs"])
        assert any(output["kind"] == "validation_error_preview" for output in run["outputs"])
        assert observed["args"][0:2] == ["idl", "-batch"]
        assert observed["shell"] is False
        wrapper = Path(observed["args"][2]).read_text(encoding="utf-8")
        assert ".compile 'idl_source.pro'" in wrapper
        assert "water_run" in wrapper

        output = client.get(
            f"/api/research/projects/{project_id}/experiments/{experiment_id}/runs/{run['id']}/outputs/water_mask.png",
            headers=headers,
        )
        assert output.status_code == 200
        assert output.content == b"fake-png"

        # An absent licensed runtime is an explicit unavailable state, not a
        # Python failure and not a silently successful empty run.
        monkeypatch.setenv("IDLRAG_IDL_EXECUTABLE", "missing-idlde-for-test")
        from app.core.config import get_app_settings

        get_app_settings.cache_clear()

        def missing_runtime(*_args, **_kwargs):
            raise FileNotFoundError("idlde")

        monkeypatch.setattr(research_idl_runner.subprocess, "run", missing_runtime)
        unavailable_experiment = client.post(
            f"/api/research/projects/{project_id}/experiments",
            json={
                "name": "IDL 不可用探针",
                "formula_spec_id": formula_response.json()["id"],
                "data_snapshot_id": snapshot["id"],
                "runner_type": "idl",
                "execution_mode": "preview",
                "parameters": {"idl_script_asset_id": script["id"]},
            },
            headers=headers,
        )
        assert unavailable_experiment.status_code == 201, unavailable_experiment.text
        unavailable_run = client.post(
            f"/api/research/projects/{project_id}/experiments/{unavailable_experiment.json()['id']}/runs?mode=sync",
            headers=headers,
        )
        assert unavailable_run.status_code == 201, unavailable_run.text
        assert unavailable_run.json()["status"] == "unavailable"
        assert "不会影响 PythonRunner" in unavailable_run.json()["error_message"]

        # A GUI/Workbench launcher must be rejected before subprocess even
        # when the executable exists; a clean launcher exit is not a run.
        monkeypatch.setenv("IDLRAG_IDL_EXECUTABLE", "envi_idl")
        get_app_settings.cache_clear()
        launcher_experiment = client.post(
            f"/api/research/projects/{project_id}/experiments",
            json={
                "name": "IDL Workbench 入口拒绝",
                "formula_spec_id": formula_response.json()["id"],
                "data_snapshot_id": snapshot["id"],
                "runner_type": "idl",
                "execution_mode": "preview",
                "parameters": {"idl_script_asset_id": script["id"]},
            },
            headers=headers,
        )
        assert launcher_experiment.status_code == 201, launcher_experiment.text
        launcher_run = client.post(
            f"/api/research/projects/{project_id}/experiments/{launcher_experiment.json()['id']}/runs?mode=sync",
            headers=headers,
        )
        assert launcher_run.status_code == 201, launcher_run.text
        assert launcher_run.json()["status"] == "unavailable"
        assert "Workbench" in launcher_run.json()["error_message"]

        # A real command-line binary that cannot initialize its license is
        # also an unavailable runtime, not a scientific experiment failure.
        monkeypatch.setenv("IDLRAG_IDL_EXECUTABLE", "idl")
        get_app_settings.cache_clear()

        def initialization_failure(*_args, stdout, stderr, **_kwargs):
            stderr.write("Failed to initialize IDL instance: license unavailable\n")
            stderr.flush()
            return SimpleNamespace(returncode=1)

        monkeypatch.setattr(research_idl_runner.subprocess, "run", initialization_failure)
        license_experiment = client.post(
            f"/api/research/projects/{project_id}/experiments",
            json={
                "name": "IDL 许可初始化失败",
                "formula_spec_id": formula_response.json()["id"],
                "data_snapshot_id": snapshot["id"],
                "runner_type": "idl",
                "execution_mode": "preview",
                "parameters": {"idl_script_asset_id": script["id"]},
            },
            headers=headers,
        )
        assert license_experiment.status_code == 201, license_experiment.text
        license_run = client.post(
            f"/api/research/projects/{project_id}/experiments/{license_experiment.json()['id']}/runs?mode=sync",
            headers=headers,
        )
        assert license_run.status_code == 201, license_run.text
        assert license_run.json()["status"] == "unavailable"
        assert "未能初始化" in license_run.json()["error_message"]


def test_project_pro_executable_rejects_workbench_and_runtime_launchers() -> None:
    from app.services.idl_runtime import validate_project_pro_executable

    for executable in ("idlde", "envi_idl.exe", r"D:\\envi\\idlrt.exe"):
        reason = validate_project_pro_executable(executable)
        assert reason is not None
        assert ".pro" in reason
    assert validate_project_pro_executable("idl.exe") is None


def test_project_idl_runner_real_local_probe(monkeypatch, tmp_path: Path) -> None:
    """Opt-in field probe for a licensed local IDL/ENVI installation.

    The default suite remains deterministic and mocks subprocess. Setting
    ``IDLRAG_REAL_IDL_EXECUTABLE`` runs the same project runner against the
    configured executable and requires a real ``WRITE_TIFF`` output; a process
    that merely exits without producing the declared GeoTIFF must fail.
    """
    executable = os.getenv("IDLRAG_REAL_IDL_EXECUTABLE", "").strip()
    if not executable or not Path(executable).is_file():
        pytest.skip("set IDLRAG_REAL_IDL_EXECUTABLE to a licensed IDL/ENVI batch executable")

    _prepare_state(monkeypatch, tmp_path, executable)
    monkeypatch.setenv("IDLRAG_IDL_RUN_TIMEOUT_SECONDS", "30")

    from app.main import create_app

    with TestClient(create_app()) as client:
        owner = _register(client, "idlrealprobe")
        headers = _headers(owner)
        project_response = client.post(
            "/api/research/projects",
            json={"name": "IDL 真实运行探针", "entry_mode": "open", "protocol": {"research_question": "IDL batch probe"}},
            headers=headers,
        )
        assert project_response.status_code == 201, project_response.text
        project_id = project_response.json()["id"]

        script_path = Path(__file__).resolve().parent / "fixtures" / "idl_real_output_probe.pro"
        script_response = client.post(
            f"/api/research/projects/{project_id}/idl-scripts/upload",
            files={"file": (script_path.name, script_path.read_bytes(), "text/plain")},
            headers=headers,
        )
        assert script_response.status_code == 201, script_response.text
        script = script_response.json()

        raster_response = client.post(
            f"/api/research/projects/{project_id}/data-assets/upload",
            files={"file": ("input.tif", _raster_bytes(tmp_path), "image/tiff")},
            data={"asset_kind": "raster", "name": "IDL 探针输入"},
            headers=headers,
        )
        assert raster_response.status_code == 201, raster_response.text
        snapshot_response = client.post(
            f"/api/research/projects/{project_id}/data-snapshots",
            json={"name": "IDL 探针快照", "asset_ids": [raster_response.json()["id"]]},
            headers=headers,
        )
        assert snapshot_response.status_code == 201, snapshot_response.text
        formula_response = client.post(
            f"/api/research/projects/{project_id}/formula-specs",
            json={"name": "IDL 探针公式", "spec": {"operation": "idl_external"}},
            headers=headers,
        )
        assert formula_response.status_code == 201, formula_response.text
        experiment_response = client.post(
            f"/api/research/projects/{project_id}/experiments",
            json={
                "name": "IDL 真实 batch 预览",
                "formula_spec_id": formula_response.json()["id"],
                "data_snapshot_id": snapshot_response.json()["id"],
                "runner_type": "idl",
                "execution_mode": "preview",
                "parameters": {
                    "idl_script_asset_id": script["id"],
                    "idl_entrypoint": "idl_real_output_probe",
                    "idl_prediction_output_file": "idl_probe.tif",
                },
            },
            headers=headers,
        )
        assert experiment_response.status_code == 201, experiment_response.text
        run_response = client.post(
            f"/api/research/projects/{project_id}/experiments/{experiment_response.json()['id']}/runs?mode=sync",
            headers=headers,
        )
        assert run_response.status_code == 201, run_response.text
        run = run_response.json()
        assert run["status"] == "completed", run
        assert run["manifest"]["runner"] == "idl"
        assert run["manifest"]["exit_code"] == 0
        assert run["manifest"]["prediction_output_file"] == "idl_probe.tif"
        assert any(output["file_name"] == "idl_probe.tif" for output in run["outputs"])
        assert any(output["kind"] == "idl_log" for output in run["outputs"])
