# ruff: noqa: E402
from __future__ import annotations

import json
from pathlib import Path
from zipfile import ZipFile

from app.services.geospatial_runtime import configure_bundled_rasterio_data

configure_bundled_rasterio_data()

import numpy as np
import pytest
import rasterio
from fastapi.testclient import TestClient
from rasterio.transform import from_origin


def _prepare_state(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("IDLRAG_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("IDLRAG_IMPORT_ROOTS", str(tmp_path))

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


def _register(client: TestClient) -> dict:
    response = client.post(
        "/api/auth/register",
        json={"username": "researcher", "password": "secret123"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _headers(auth: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {auth['access_token']}"}


def _create_two_band_raster(path: Path) -> None:
    values = np.array(
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
        transform=from_origin(115.0, 30.0, 10, 10),
        nodata=-9999.0,
    ) as destination:
        destination.write(values)


def _create_sar_raster(path: Path) -> None:
    values = np.array([[[-20.0, -10.0], [-25.0, -5.0]]], dtype=np.float32)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=2,
        height=2,
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=from_origin(115.0, 30.0, 10, 10),
        nodata=-9999.0,
    ) as destination:
        destination.write(values)


def _create_reference_raster(path: Path) -> None:
    values = np.array([[[1, 0], [1, 0]]], dtype=np.uint8)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=2,
        height=2,
        count=1,
        dtype="uint8",
        crs="EPSG:4326",
        transform=from_origin(115.0, 30.0, 10, 10),
        nodata=255,
    ) as destination:
        destination.write(values)


def _create_idl_classification_raster(path: Path, *, crs: str = "EPSG:4326") -> None:
    values = np.array([[[1, 0], [1, 1]]], dtype=np.uint8)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=2,
        height=2,
        count=1,
        dtype="uint8",
        crs=crs,
        transform=from_origin(115.0, 30.0, 10, 10),
        nodata=255,
    ) as destination:
        destination.write(values)


def _create_idl_continuous_raster(path: Path) -> None:
    values = np.array([[[0.5, -1 / 3], [1 / 3, 0.02]]], dtype=np.float32)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=2,
        height=2,
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=from_origin(115.0, 30.0, 10, 10),
        nodata=-9999.0,
    ) as destination:
        destination.write(values)


def test_python_runner_generates_rasters_previews_and_reproducible_manifest(monkeypatch, tmp_path: Path) -> None:
    _prepare_state(monkeypatch, tmp_path)
    input_path = tmp_path / "poyang_scene.tif"
    sar_path = tmp_path / "poyang_sar.tif"
    reference_path = tmp_path / "poyang_reference.tif"
    idl_classification_path = tmp_path / "poyang_idl_water_mask.tif"
    idl_continuous_path = tmp_path / "poyang_idl_index.tif"
    idl_misaligned_path = tmp_path / "poyang_idl_misaligned.tif"
    _create_two_band_raster(input_path)
    _create_sar_raster(sar_path)
    _create_reference_raster(reference_path)
    _create_idl_classification_raster(idl_classification_path)
    _create_idl_continuous_raster(idl_continuous_path)
    _create_idl_classification_raster(idl_misaligned_path, crs="EPSG:3857")

    from app.main import create_app

    with TestClient(create_app()) as client:
        auth = _register(client)
        headers = _headers(auth)
        project_response = client.post(
            "/api/research/projects",
            json={
                "name": "PythonRunner 验证",
                "entry_mode": "template",
                "protocol": {"research_question": "MNDWI 在鄱阳湖本地冻结影像中的水体制图精度是多少？"},
            },
            headers=headers,
        )
        assert project_response.status_code == 201
        project_id = project_response.json()["id"]

        with input_path.open("rb") as source:
            asset_response = client.post(
                f"/api/research/projects/{project_id}/data-assets/upload",
                files={"file": ("poyang_scene.tif", source, "image/tiff")},
                data={"asset_kind": "raster", "name": "鄱阳湖合成影像"},
                headers=headers,
            )
        assert asset_response.status_code == 201, asset_response.text
        asset = asset_response.json()
        assert asset["source_uri"].startswith("research://assets/project-")
        assert asset["sha256"]

        with reference_path.open("rb") as source:
            reference_asset_response = client.post(
                f"/api/research/projects/{project_id}/data-assets/upload",
                files={"file": ("poyang_reference.tif", source, "image/tiff")},
                data={"asset_kind": "reference", "name": "独立验证参考"},
                headers=headers,
            )
        assert reference_asset_response.status_code == 201, reference_asset_response.text
        reference_asset = reference_asset_response.json()

        def upload_idl_output(path: Path, name: str) -> dict:
            with path.open("rb") as source:
                response = client.post(
                    f"/api/research/projects/{project_id}/data-assets/upload",
                    files={"file": (path.name, source, "image/tiff")},
                    data={"asset_kind": "derived", "name": name},
                    headers=headers,
                )
            assert response.status_code == 201, response.text
            return response.json()

        idl_classification_asset = upload_idl_output(idl_classification_path, "本地 IDL 水体结果")
        idl_continuous_asset = upload_idl_output(idl_continuous_path, "本地 IDL 指数结果")
        idl_misaligned_asset = upload_idl_output(idl_misaligned_path, "本地 IDL 错位结果")

        snapshot_response = client.post(
            f"/api/research/projects/{project_id}/data-snapshots",
            json={"name": "两波段与参考冻结快照", "asset_ids": [asset["id"], reference_asset["id"]]},
            headers=headers,
        )
        assert snapshot_response.status_code == 201, snapshot_response.text
        snapshot = snapshot_response.json()

        evidence_response = client.post(
            f"/api/research/projects/{project_id}/evidence-cards",
            json={
                "title": "归一化差异指数的公开方法说明",
                "status": "verified",
                "source_type": "official_document",
                "source_url": "https://example.invalid/mndwi-method",
            },
            headers=headers,
        )
        assert evidence_response.status_code == 201
        evidence = evidence_response.json()

        formula_response = client.post(
            f"/api/research/projects/{project_id}/formula-specs",
            json={
                "name": "MNDWI 基线",
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
        )
        assert formula_response.status_code == 201, formula_response.text
        formula = formula_response.json()

        experiment_response = client.post(
            f"/api/research/projects/{project_id}/experiments",
            json={
                "name": "MNDWI 正式本地运行",
                "formula_spec_id": formula["id"],
                "data_snapshot_id": snapshot["id"],
                "runner_type": "python",
                "execution_mode": "formal",
                "parameters": {
                    "threshold": 0.0,
                    "idl_comparison": {
                        "idl_output_asset_id": idl_classification_asset["id"],
                        "comparison_mode": "classification",
                        "python_output_file": "water_mask.tif",
                        "absolute_tolerance": 0.0,
                    },
                },
                "validation_plan": {
                    "split": "spatiotemporal-holdout",
                    "metrics": ["f1", "iou"],
                    "reference_asset_id": reference_asset["id"],
                },
                "visualization_contract": ["input", "index", "water_mask"],
            },
            headers=headers,
        )
        assert experiment_response.status_code == 201, experiment_response.text
        experiment = experiment_response.json()
        assert experiment["project_protocol_revision_id"] is not None
        assert len(experiment["project_protocol_hash"]) == 64

        # 正式实验创建后即固定项目协议；后续项目编辑不得改写证据包中的历史协议。
        protocol_update = client.patch(
            f"/api/research/projects/{project_id}",
            json={"protocol": {"research_question": "这是一份运行后编辑过的项目协议，不应进入既有证据包。"}},
            headers=headers,
        )
        assert protocol_update.status_code == 200, protocol_update.text

        run_response = client.post(
            f"/api/research/projects/{project_id}/experiments/{experiment['id']}/runs",
            headers=headers,
        )
        assert run_response.status_code == 201, run_response.text
        run = run_response.json()
        assert run["status"] == "completed", run
        output_kinds = {output["kind"] for output in run["outputs"]}
        assert output_kinds == {
            "input_preview",
            "feature_raster",
            "feature_preview",
            "classification_raster",
            "classification_preview",
            "validation_metrics",
            "validation_error_raster",
            "validation_error_preview",
            "run_manifest",
            "idl_comparison_difference_raster",
            "idl_comparison_difference_preview",
            "idl_comparison_report",
            "research_report",
            "research_evidence_package",
        }
        assert run["manifest"]["data_snapshot"]["snapshot_hash"] == snapshot["snapshot_hash"]
        assert run["manifest"]["raster_metadata"]["water_pixel_count"] == 3
        assert run["manifest"]["raster_metadata"]["valid_pixel_count"] == 4
        assert run["manifest"]["validation"]["metrics"]["overall_accuracy"] == 0.75
        reference_intervals = run["manifest"]["validation"]["confidence_intervals"]
        assert reference_intervals["method"] == "wilson_95_unweighted"
        assert reference_intervals["overall_accuracy"]["trials"] == 4
        idl_comparison = run["manifest"]["idl_comparison"]
        assert idl_comparison["comparison_mode"] == "classification"
        assert idl_comparison["idl_output_asset"]["id"] == idl_classification_asset["id"]
        assert idl_comparison["agreement_rate"] == 1.0
        assert idl_comparison["water_f1"] == 1.0
        assert idl_comparison["nodata_mismatch_pixel_count"] == 0

        output_dir = tmp_path / "data" / "research" / "runs" / f"project-{project_id}" / run["run_token"]
        assert (output_dir / "input_preview.png").is_file()
        assert (output_dir / "normalized_difference_preview.png").is_file()
        assert (output_dir / "water_mask_preview.png").is_file()
        assert (output_dir / f"idl_python_classification_{idl_classification_asset['id']}_water_mask_difference.tif").is_file()
        assert (output_dir / f"idl_python_classification_{idl_classification_asset['id']}_water_mask_difference_preview.png").is_file()
        with rasterio.open(output_dir / "normalized_difference.tif") as index_dataset:
            index = index_dataset.read(1)
            np.testing.assert_allclose(index, [[0.5, -1 / 3], [1 / 3, 0.0]], rtol=1e-6)
            assert index_dataset.crs.to_string() == "EPSG:4326"
        with rasterio.open(output_dir / "water_mask.tif") as mask_dataset:
            np.testing.assert_array_equal(mask_dataset.read(1), [[1, 0], [1, 1]])
            assert mask_dataset.nodata == 255

        manifest = json.loads((output_dir / "run_manifest.json").read_text(encoding="utf-8"))
        assert manifest["formula_spec"]["id"] == formula["id"]
        assert manifest["input_asset"]["sha256"] == asset["sha256"]
        assert manifest["experiment"]["execution_mode"] == "formal"

        package_output = next(output for output in run["outputs"] if output["kind"] == "research_evidence_package")
        assert package_output["metadata"]["raw_data_included"] is False
        package_path = output_dir / package_output["file_name"]
        assert package_path.is_file()
        with ZipFile(package_path) as archive:
            names = set(archive.namelist())
            assert {
                "README.md",
                "checksums.sha256",
                "data_access.md",
                "data_snapshot.json",
                "evidence_cards.json",
                "experiment.json",
                "formula_spec.json",
                "package_manifest.json",
                "project_protocol.json",
                "run_manifest.json",
                "outputs/input_preview.png",
                "outputs/normalized_difference.tif",
                "outputs/water_mask.tif",
                f"outputs/idl_python_classification_{idl_classification_asset['id']}_water_mask_difference.tif",
                f"outputs/idl_python_classification_{idl_classification_asset['id']}_water_mask_difference_preview.png",
                f"outputs/idl_python_classification_{idl_classification_asset['id']}_water_mask_report.json",
            }.issubset(names)
            assert "poyang_scene.tif" not in names
            package_manifest = json.loads(archive.read("package_manifest.json"))
            assert package_manifest["privacy"]["raw_data_included"] is False
            assert package_manifest["project_protocol"]["revision_id"] == experiment["project_protocol_revision_id"]
            assert package_manifest["project_protocol"]["sha256"] == experiment["project_protocol_hash"]
            assert json.loads(archive.read("project_protocol.json")) == {
                "research_question": "MNDWI 在鄱阳湖本地冻结影像中的水体制图精度是多少？"
            }
            assert package_manifest["data_snapshot"]["asset_references"][0]["source_uri"] == (
                f"protected://project-asset/{asset['id']}"
            )
            assert "research://assets/" not in archive.read("package_manifest.json").decode("utf-8")

        band_math_formula_response = client.post(
            f"/api/research/projects/{project_id}/formula-specs",
            json={
                "name": "带偏置项的安全反射率指数候选",
                "version": 1,
                "status": "frozen",
                "spec": {
                    "operation": "safe_band_math_threshold",
                    "input_asset_id": asset["id"],
                    "inputs": {
                        "green": {"band": 1, "scale": 1.0, "offset": 0.0},
                        "swir1": {"band": 2, "scale": 1.0, "offset": 0.0},
                    },
                    "expression": "clip((green - swir1) / (green + swir1) + bias, -1, 1)",
                    "parameters": {"bias": 0.0, "threshold": 0.0},
                    "water_condition": ">=",
                },
                "evidence_card_ids": [evidence["id"]],
            },
            headers=headers,
        )
        assert band_math_formula_response.status_code == 201, band_math_formula_response.text
        band_math_experiment_response = client.post(
            f"/api/research/projects/{project_id}/experiments",
            json={
                "name": "带偏置项的反射率指数正式验证",
                "formula_spec_id": band_math_formula_response.json()["id"],
                "data_snapshot_id": snapshot["id"],
                "runner_type": "python",
                "execution_mode": "formal",
                "parameters": {"bias": 0.2, "threshold": 0.3},
                "validation_plan": {
                    "split": "spatiotemporal-holdout",
                    "reference_asset_id": reference_asset["id"],
                },
                "visualization_contract": ["input", "feature", "classification", "validation_error"],
            },
            headers=headers,
        )
        assert band_math_experiment_response.status_code == 201, band_math_experiment_response.text
        band_math_run_response = client.post(
            f"/api/research/projects/{project_id}/experiments/{band_math_experiment_response.json()['id']}/runs",
            headers=headers,
        )
        assert band_math_run_response.status_code == 201, band_math_run_response.text
        band_math_run = band_math_run_response.json()
        assert band_math_run["status"] == "completed", band_math_run
        assert band_math_run["manifest"]["formula_spec"]["operation"] == "safe_band_math_threshold"
        assert band_math_run["manifest"]["raster_metadata"]["parameter_values"] == {
            "bias": 0.2,
            "threshold": 0.3,
        }
        band_math_output_dir = tmp_path / "data" / "research" / "runs" / f"project-{project_id}" / band_math_run["run_token"]
        with rasterio.open(band_math_output_dir / "band_math_feature.tif") as dataset:
            np.testing.assert_allclose(dataset.read(1), [[0.7, -2 / 15], [8 / 15, 0.2]], rtol=1e-6)
        with rasterio.open(band_math_output_dir / "water_mask.tif") as dataset:
            np.testing.assert_array_equal(dataset.read(1), [[1, 0], [1, 0]])
        assert (band_math_output_dir / "band_math_feature_preview.png").is_file()
        assert any(output["kind"] == "research_evidence_package" for output in band_math_run["outputs"])

        unsafe_formula_response = client.post(
            f"/api/research/projects/{project_id}/formula-specs",
            json={
                "name": "必须拒绝任意代码的公式候选",
                "version": 1,
                "status": "candidate",
                "spec": {
                    "operation": "safe_band_math_threshold",
                    "input_asset_id": asset["id"],
                    "inputs": {"green": {"band": 1}},
                    "expression": "__import__('os')",
                    "parameters": {"threshold": 0.0},
                },
                "evidence_card_ids": [evidence["id"]],
            },
            headers=headers,
        )
        assert unsafe_formula_response.status_code == 201, unsafe_formula_response.text
        unsafe_experiment_response = client.post(
            f"/api/research/projects/{project_id}/experiments",
            json={
                "name": "拒绝任意代码的安全公式预览",
                "formula_spec_id": unsafe_formula_response.json()["id"],
                "data_snapshot_id": snapshot["id"],
                "runner_type": "python",
                "execution_mode": "preview",
                "visualization_contract": ["input", "feature", "classification"],
            },
            headers=headers,
        )
        assert unsafe_experiment_response.status_code == 201, unsafe_experiment_response.text
        unsafe_run_response = client.post(
            f"/api/research/projects/{project_id}/experiments/{unsafe_experiment_response.json()['id']}/runs",
            headers=headers,
        )
        assert unsafe_run_response.status_code == 201, unsafe_run_response.text
        unsafe_run = unsafe_run_response.json()
        assert unsafe_run["status"] == "failed"
        assert "不允许函数调用" in unsafe_run["error_message"]

        preview_download = client.get(
            f"/api/research/projects/{project_id}/experiments/{experiment['id']}/runs/{run['id']}/outputs/input_preview.png",
            headers=headers,
        )
        assert preview_download.status_code == 200
        assert preview_download.headers["content-type"].startswith("image/png")
        assert len(preview_download.content) > 100
        package_download = client.get(
            f"/api/research/projects/{project_id}/experiments/{experiment['id']}/runs/{run['id']}/outputs/{package_output['file_name']}",
            headers=headers,
        )
        assert package_download.status_code == 200
        assert package_download.headers["content-type"].split(";", 1)[0] in {
            "application/zip",
            "application/x-zip-compressed",
        }

        continuous_comparison_experiment_response = client.post(
            f"/api/research/projects/{project_id}/experiments",
            json={
                "name": "MNDWI 与本地 IDL 连续指数对照",
                "formula_spec_id": formula["id"],
                "data_snapshot_id": snapshot["id"],
                "runner_type": "python",
                "execution_mode": "preview",
                "parameters": {
                    "threshold": 0.0,
                    "idl_comparison": {
                        "idl_output_asset_id": idl_continuous_asset["id"],
                        "comparison_mode": "continuous",
                        "python_output_file": "normalized_difference.tif",
                        "absolute_tolerance": 0.01,
                    },
                },
                "visualization_contract": ["input", "index", "water_mask"],
            },
            headers=headers,
        )
        assert continuous_comparison_experiment_response.status_code == 201, continuous_comparison_experiment_response.text
        continuous_comparison_run_response = client.post(
            f"/api/research/projects/{project_id}/experiments/"
            f"{continuous_comparison_experiment_response.json()['id']}/runs",
            headers=headers,
        )
        assert continuous_comparison_run_response.status_code == 201, continuous_comparison_run_response.text
        continuous_comparison_run = continuous_comparison_run_response.json()
        assert continuous_comparison_run["status"] == "completed"
        continuous_summary = continuous_comparison_run["manifest"]["idl_comparison"]
        assert continuous_summary["comparison_mode"] == "continuous"
        assert continuous_summary["within_tolerance_rate"] == 0.75
        assert np.isclose(continuous_summary["mean_absolute_difference"], 0.005)
        assert np.isclose(continuous_summary["max_absolute_difference"], 0.02)

        mismatched_comparison_experiment_response = client.post(
            f"/api/research/projects/{project_id}/experiments",
            json={
                "name": "错位本地 IDL 栅格必须拒绝",
                "formula_spec_id": formula["id"],
                "data_snapshot_id": snapshot["id"],
                "runner_type": "python",
                "execution_mode": "preview",
                "parameters": {
                    "threshold": 0.0,
                    "idl_comparison": {
                        "idl_output_asset_id": idl_misaligned_asset["id"],
                        "comparison_mode": "classification",
                        "python_output_file": "water_mask.tif",
                        "absolute_tolerance": 0.0,
                    },
                },
                "visualization_contract": ["input", "index", "water_mask"],
            },
            headers=headers,
        )
        assert mismatched_comparison_experiment_response.status_code == 201, mismatched_comparison_experiment_response.text
        mismatched_comparison_run_response = client.post(
            f"/api/research/projects/{project_id}/experiments/"
            f"{mismatched_comparison_experiment_response.json()['id']}/runs",
            headers=headers,
        )
        assert mismatched_comparison_run_response.status_code == 201, mismatched_comparison_run_response.text
        mismatched_comparison_run = mismatched_comparison_run_response.json()
        assert mismatched_comparison_run["status"] == "failed"
        assert "CRS 不一致" in mismatched_comparison_run["error_message"]

        with sar_path.open("rb") as source:
            sar_asset_response = client.post(
                f"/api/research/projects/{project_id}/data-assets/upload",
                files={"file": ("poyang_sar.tif", source, "image/tiff")},
                data={"asset_kind": "raster", "name": "SAR 合成影像"},
                headers=headers,
            )
        assert sar_asset_response.status_code == 201, sar_asset_response.text
        sar_asset = sar_asset_response.json()
        fusion_snapshot_response = client.post(
            f"/api/research/projects/{project_id}/data-snapshots",
            json={
                "name": "光学、SAR 与参考冻结快照",
                "asset_ids": [asset["id"], sar_asset["id"], reference_asset["id"]],
            },
            headers=headers,
        )
        assert fusion_snapshot_response.status_code == 201, fusion_snapshot_response.text
        fusion_snapshot = fusion_snapshot_response.json()

        for longitude, latitude, label, conflict_status, sampling_weight in [
            (116.0, 29.0, 1, "none", 2.0),
            (126.0, 29.0, 0, "none", 1.0),
            (116.0, 19.0, 1, "none", 3.0),
            (126.0, 19.0, 0, "none", 4.0),
            (126.0, 29.0, 1, "flagged", 5.0),
        ]:
            sample_response = client.post(
                f"/api/research/projects/{project_id}/validation-samples",
                json={
                    "data_snapshot_id": fusion_snapshot["id"],
                    "source_asset_id": reference_asset["id"],
                    "longitude": longitude,
                    "latitude": latitude,
                    "label": label,
                    "observed_at": "2024-06-15T10:30:00Z",
                    "annotator": "teacher-a",
                    "confidence": 0.9,
                    "split": "independent_test",
                    "spatial_block": "block-east-03" if longitude < 120 else "block-west-01",
                    "temporal_stratum": "2024-wet-season",
                    "conflict_status": conflict_status,
                    "source_note": "合成参考样本，仅用于自动化验证。",
                    "metadata": {
                        "sampling_weight": sampling_weight,
                        "sampling_stratum": "open_water" if longitude < 120 else "wetland_edge",
                        "stratum_area": 60.0 if longitude < 120 else 40.0,
                    },
                },
                headers=headers,
            )
            assert sample_response.status_code == 201, sample_response.text

        sar_formula_response = client.post(
            f"/api/research/projects/{project_id}/formula-specs",
            json={
                "name": "SAR 阈值基线",
                "version": 1,
                "status": "candidate",
                "spec": {
                    "operation": "sar_backscatter_threshold",
                    "input_asset_id": sar_asset["id"],
                    "inputs": {"vv": {"band": 1}},
                    "parameters": {"threshold": -17.0},
                },
                "evidence_card_ids": [evidence["id"]],
            },
            headers=headers,
        )
        assert sar_formula_response.status_code == 201, sar_formula_response.text
        sar_experiment_response = client.post(
            f"/api/research/projects/{project_id}/experiments",
            json={
                "name": "SAR 预览运行",
                "formula_spec_id": sar_formula_response.json()["id"],
                "data_snapshot_id": fusion_snapshot["id"],
                "runner_type": "python",
                "execution_mode": "preview",
                "visualization_contract": ["input", "feature", "water_mask"],
            },
            headers=headers,
        )
        assert sar_experiment_response.status_code == 201, sar_experiment_response.text
        sar_run_response = client.post(
            f"/api/research/projects/{project_id}/experiments/{sar_experiment_response.json()['id']}/runs",
            headers=headers,
        )
        assert sar_run_response.status_code == 201, sar_run_response.text
        sar_run = sar_run_response.json()
        assert sar_run["status"] == "completed"
        assert sar_run["manifest"]["formula_spec"]["operation"] == "sar_backscatter_threshold"
        assert sar_run["manifest"]["raster_metadata"]["water_pixel_count"] == 2

        adaptive_formula_response = client.post(
            f"/api/research/projects/{project_id}/formula-specs",
            json={
                "name": "MNDWI Otsu 自适应候选",
                "version": 1,
                "status": "candidate",
                "spec": {
                    "operation": "adaptive_normalized_difference_otsu",
                    "input_asset_id": asset["id"],
                    "inputs": {"green": {"band": 1}, "swir1": {"band": 2}},
                    "parameters": {"histogram_bins": 64, "threshold_min": -1.0, "threshold_max": 1.0},
                },
                "evidence_card_ids": [evidence["id"]],
            },
            headers=headers,
        )
        assert adaptive_formula_response.status_code == 201, adaptive_formula_response.text
        adaptive_experiment_response = client.post(
            f"/api/research/projects/{project_id}/experiments",
            json={
                "name": "MNDWI Otsu 预览运行",
                "formula_spec_id": adaptive_formula_response.json()["id"],
                "data_snapshot_id": fusion_snapshot["id"],
                "runner_type": "python",
                "execution_mode": "preview",
                "visualization_contract": ["input", "feature", "water_mask"],
            },
            headers=headers,
        )
        assert adaptive_experiment_response.status_code == 201, adaptive_experiment_response.text
        adaptive_run_response = client.post(
            f"/api/research/projects/{project_id}/experiments/{adaptive_experiment_response.json()['id']}/runs",
            headers=headers,
        )
        assert adaptive_run_response.status_code == 201, adaptive_run_response.text
        adaptive_run = adaptive_run_response.json()
        assert adaptive_run["status"] == "completed"
        assert adaptive_run["manifest"]["formula_spec"]["operation"] == "adaptive_normalized_difference_otsu"
        assert adaptive_run["manifest"]["raster_metadata"]["threshold_method"] == "otsu_global_histogram"
        assert -1.0 <= adaptive_run["manifest"]["raster_metadata"]["threshold"] <= 1.0

        fusion_formula_response = client.post(
            f"/api/research/projects/{project_id}/formula-specs",
            json={
                "name": "透明光学-SAR 融合",
                "version": 1,
                "status": "frozen",
                "spec": {
                    "operation": "transparent_water_fusion",
                    "optical_asset_id": asset["id"],
                    "sar_asset_id": sar_asset["id"],
                    "inputs": {"green": {"band": 1}, "swir1": {"band": 2}, "vv": {"band": 1}},
                    "parameters": {"optical_threshold": 0.0, "sar_threshold": -17.0, "fusion_rule": "or"},
                },
                "evidence_card_ids": [evidence["id"]],
            },
            headers=headers,
        )
        assert fusion_formula_response.status_code == 201, fusion_formula_response.text
        fusion_experiment_response = client.post(
            f"/api/research/projects/{project_id}/experiments",
            json={
                "name": "透明融合正式运行",
                "formula_spec_id": fusion_formula_response.json()["id"],
                "data_snapshot_id": fusion_snapshot["id"],
                "runner_type": "python",
                "execution_mode": "formal",
                "parameters": {"fusion_rule": "or"},
                "validation_plan": {
                    "split": "spatiotemporal-holdout",
                    "metrics": ["f1", "iou"],
                    "reference_asset_id": reference_asset["id"],
                    "sample_validation": {
                        "split": "independent_test",
                        "min_confidence": 0.8,
                        "require_unconflicted": True,
                        "min_sample_count": 4,
                        "min_spatial_blocks": 2,
                        "min_temporal_strata": 1,
                        "min_samples_per_spatial_block": 2,
                        "min_samples_per_temporal_stratum": 4,
                    },
                },
                "visualization_contract": ["input", "feature", "classification", "uncertainty"],
            },
            headers=headers,
        )
        assert fusion_experiment_response.status_code == 201, fusion_experiment_response.text
        fusion_run_response = client.post(
            f"/api/research/projects/{project_id}/experiments/{fusion_experiment_response.json()['id']}/runs",
            headers=headers,
        )
        assert fusion_run_response.status_code == 201, fusion_run_response.text
        fusion_run = fusion_run_response.json()
        assert fusion_run["status"] == "completed", fusion_run
        assert fusion_run["manifest"]["formula_spec"]["operation"] == "transparent_water_fusion"
        assert fusion_run["manifest"]["raster_metadata"]["disagreement_pixel_count"] == 1
        assert fusion_run["manifest"]["validation"]["status"] == "completed"
        fusion_report_output = next(output for output in fusion_run["outputs"] if output["kind"] == "research_report")
        assert fusion_report_output["metadata"]["scientific_conclusion"] == "researcher_review_required"
        assert fusion_run["manifest"]["validation"]["metrics"]["overall_accuracy"] == 0.75
        assert fusion_run["manifest"]["validation"]["metrics"]["f1"] == 0.8
        point_validation = fusion_run["manifest"]["validation"]["point_samples"]
        assert point_validation["status"] == "completed"
        assert point_validation["selection"]["snapshot_bound_candidate_count"] == 5
        assert point_validation["selection"]["eligible_sample_count"] == 4
        assert point_validation["metrics"]["sample_count"] == 4
        assert point_validation["metrics"]["metrics"]["overall_accuracy"] == 0.75
        assert point_validation["metrics"]["confidence_intervals"]["method"] == "wilson_95_unweighted"
        assert point_validation["metrics"]["confidence_intervals"]["overall_accuracy"]["trials"] == 4
        assert point_validation["strata"]["temporal_stratum"]["2024-wet-season"]["sample_count"] == 4
        fusion_kinds = {output["kind"] for output in fusion_run["outputs"]}
        assert {
            "uncertainty_raster",
            "uncertainty_preview",
            "research_evidence_package",
            "validation_metrics",
            "validation_sample_metrics",
            "validation_error_raster",
            "validation_error_preview",
        }.issubset(fusion_kinds)

        fusion_package_output = next(
            output for output in fusion_run["outputs"] if output["kind"] == "research_evidence_package"
        )
        with ZipFile(output_dir.parent / fusion_run["run_token"] / fusion_package_output["file_name"]) as archive:
            assert "outputs/validation_sample_metrics.json" in archive.namelist()
            assert "outputs/research_report.md" in archive.namelist()
            assert "not an automatic scientific conclusion" in archive.read("outputs/research_report.md").decode("utf-8")
        package_verification_response = client.get(
            f"/api/research/projects/{project_id}/experiments/"
            f"{fusion_experiment_response.json()['id']}/runs/{fusion_run['id']}/verification",
            headers=headers,
        )
        assert package_verification_response.status_code == 200, package_verification_response.text
        package_verification = package_verification_response.json()
        assert package_verification["status"] == "verified"
        assert package_verification["verified"] is True
        assert package_verification["output_count"] >= 1
        fusion_rerun_response = client.post(
            f"/api/research/projects/{project_id}/experiments/{fusion_experiment_response.json()['id']}/runs",
            headers=headers,
        )
        assert fusion_rerun_response.status_code == 201, fusion_rerun_response.text
        fusion_rerun = fusion_rerun_response.json()
        assert fusion_rerun["status"] == "completed", fusion_rerun
        reproducibility_response = client.post(
            f"/api/research/projects/{project_id}/experiments/"
            f"{fusion_experiment_response.json()['id']}/runs/{fusion_rerun['id']}/reproducibility",
            json={"reference_run_id": fusion_run["id"], "absolute_tolerance": 1e-6, "relative_tolerance": 1e-6},
            headers=headers,
        )
        assert reproducibility_response.status_code == 200, reproducibility_response.text
        reproducibility = reproducibility_response.json()
        assert reproducibility["status"] == "matched", reproducibility
        assert reproducibility["matched"] is True
        assert reproducibility["compared_output_count"] >= 1
        comparison_response = client.post(
            f"/api/research/projects/{project_id}/experiments/"
            f"{fusion_experiment_response.json()['id']}/runs/{fusion_rerun['id']}/comparison",
            json={"reference_run_id": fusion_run["id"]},
            headers=headers,
        )
        assert comparison_response.status_code == 200, comparison_response.text
        comparison = comparison_response.json()
        assert comparison["status"] == "compared", comparison
        assert comparison["comparable"] is True
        assert comparison["compared_metric_count"] >= 5

        weighted_experiment_response = client.post(
            f"/api/research/projects/{project_id}/experiments",
            json={
                "name": "透明融合加权验证运行",
                "formula_spec_id": fusion_formula_response.json()["id"],
                "data_snapshot_id": fusion_snapshot["id"],
                "runner_type": "python",
                "execution_mode": "formal",
                "parameters": {"fusion_rule": "or"},
                "validation_plan": {
                    "split": "spatiotemporal-holdout",
                    "metrics": ["f1", "iou"],
                    "reference_asset_id": reference_asset["id"],
                    "sample_validation": {
                        "split": "independent_test",
                        "min_confidence": 0.8,
                        "require_unconflicted": True,
                        "min_sample_count": 4,
                        "min_spatial_blocks": 2,
                        "min_temporal_strata": 1,
                        "min_samples_per_spatial_block": 2,
                        "min_samples_per_temporal_stratum": 4,
                        "weighting": {
                            "enabled": True,
                            "metadata_key": "sampling_weight",
                            "minimum_total_weight": 4.0,
                            "minimum_effective_sample_size": 2.0,
                        },
                    },
                },
                "visualization_contract": ["input", "feature", "classification", "uncertainty"],
            },
            headers=headers,
        )
        assert weighted_experiment_response.status_code == 201, weighted_experiment_response.text
        weighted_run_response = client.post(
            f"/api/research/projects/{project_id}/experiments/{weighted_experiment_response.json()['id']}/runs",
            headers=headers,
        )
        assert weighted_run_response.status_code == 201, weighted_run_response.text
        weighted_run = weighted_run_response.json()
        assert weighted_run["status"] == "completed", weighted_run
        weighted_point_validation = weighted_run["manifest"]["validation"]["point_samples"]
        assert weighted_point_validation["selection"]["weighting"]["enabled"] is True
        assert weighted_point_validation["selection"]["weighting"]["total_weight"] == 10.0
        assert weighted_point_validation["metrics"]["weight_sum"] == 10.0
        assert weighted_point_validation["metrics"]["confidence_intervals"]["method"] == (
            "not_reported_for_weighted_samples"
        )

        area_adjusted_experiment_response = client.post(
            f"/api/research/projects/{project_id}/experiments",
            json={
                "name": "透明融合分层面积调整验证运行",
                "formula_spec_id": fusion_formula_response.json()["id"],
                "data_snapshot_id": fusion_snapshot["id"],
                "runner_type": "python",
                "execution_mode": "formal",
                "parameters": {"fusion_rule": "or"},
                "validation_plan": {
                    "split": "spatiotemporal-holdout",
                    "metrics": ["f1", "iou"],
                    "reference_asset_id": reference_asset["id"],
                    "sample_validation": {
                        "split": "independent_test",
                        "min_confidence": 0.8,
                        "require_unconflicted": True,
                        "min_sample_count": 4,
                        "min_spatial_blocks": 2,
                        "min_temporal_strata": 1,
                        "min_samples_per_spatial_block": 2,
                        "min_samples_per_temporal_stratum": 4,
                        "area_adjustment": {
                            "enabled": True,
                            "stratum_metadata_key": "sampling_stratum",
                            "area_metadata_key": "stratum_area",
                            "area_unit": "ha",
                            "expected_strata": ["open_water", "wetland_edge"],
                            "minimum_strata": 2,
                        },
                    },
                },
                "visualization_contract": ["input", "feature", "classification", "uncertainty"],
            },
            headers=headers,
        )
        assert area_adjusted_experiment_response.status_code == 201, area_adjusted_experiment_response.text
        area_adjusted_run_response = client.post(
            f"/api/research/projects/{project_id}/experiments/"
            f"{area_adjusted_experiment_response.json()['id']}/runs",
            headers=headers,
        )
        assert area_adjusted_run_response.status_code == 201, area_adjusted_run_response.text
        area_adjusted_run = area_adjusted_run_response.json()
        assert area_adjusted_run["status"] == "completed", area_adjusted_run
        area_point_validation = area_adjusted_run["manifest"]["validation"]["point_samples"]
        assert area_point_validation["selection"]["area_adjustment"]["enabled"] is True
        assert area_point_validation["selection"]["area_adjustment"]["strata_count"] == 2
        area_metrics = area_point_validation["metrics"]["area_adjusted"]
        assert area_metrics["method"] == "stratified_area_adjusted"
        assert area_metrics["area_unit"] == "ha"
        assert area_metrics["total_area"] == 100.0
        assert set(area_metrics["strata"]) == {"open_water", "wetland_edge"}
        assert area_metrics["confidence_intervals"]["method"] == (
            "not_reported_for_design_based_area_adjustment"
        )

        missing_sample_experiment_response = client.post(
            f"/api/research/projects/{project_id}/experiments",
            json={
                "name": "独立样本总数低于协议下限时应拒绝点验证",
                "formula_spec_id": fusion_formula_response.json()["id"],
                "data_snapshot_id": fusion_snapshot["id"],
                "runner_type": "python",
                "execution_mode": "formal",
                "validation_plan": {
                    "split": "spatiotemporal-holdout",
                    "sample_validation": {
                        "split": "independent_test",
                        "min_confidence": 0.8,
                        "require_unconflicted": True,
                        "min_sample_count": 5,
                        "min_spatial_blocks": 2,
                        "min_temporal_strata": 1,
                        "min_samples_per_spatial_block": 2,
                        "min_samples_per_temporal_stratum": 4,
                    }
                },
                "visualization_contract": ["input", "feature", "classification"],
            },
            headers=headers,
        )
        assert missing_sample_experiment_response.status_code == 201, missing_sample_experiment_response.text
        missing_sample_run_response = client.post(
            f"/api/research/projects/{project_id}/experiments/{missing_sample_experiment_response.json()['id']}/runs",
            headers=headers,
        )
        assert missing_sample_run_response.status_code == 201, missing_sample_run_response.text
        missing_sample_run = missing_sample_run_response.json()
        assert missing_sample_run["status"] == "failed"
        assert "min_sample_count=5" in missing_sample_run["error_message"]

        sparse_spatial_block_experiment_response = client.post(
            f"/api/research/projects/{project_id}/experiments",
            json={
                "name": "任一空间块样本覆盖不足时应拒绝点验证",
                "formula_spec_id": fusion_formula_response.json()["id"],
                "data_snapshot_id": fusion_snapshot["id"],
                "runner_type": "python",
                "execution_mode": "formal",
                "validation_plan": {
                    "split": "spatiotemporal-holdout",
                    "sample_validation": {
                        "split": "independent_test",
                        "min_confidence": 0.8,
                        "require_unconflicted": True,
                        "min_sample_count": 4,
                        "min_spatial_blocks": 2,
                        "min_temporal_strata": 1,
                        "min_samples_per_spatial_block": 3,
                        "min_samples_per_temporal_stratum": 4,
                    }
                },
                "visualization_contract": ["input", "feature", "classification"],
            },
            headers=headers,
        )
        assert sparse_spatial_block_experiment_response.status_code == 201, sparse_spatial_block_experiment_response.text
        sparse_spatial_block_run_response = client.post(
            f"/api/research/projects/{project_id}/experiments/"
            f"{sparse_spatial_block_experiment_response.json()['id']}/runs",
            headers=headers,
        )
        assert sparse_spatial_block_run_response.status_code == 201, sparse_spatial_block_run_response.text
        sparse_spatial_block_run = sparse_spatial_block_run_response.json()
        assert sparse_spatial_block_run["status"] == "failed"
        assert "min_samples_per_spatial_block=3" in sparse_spatial_block_run["error_message"]

        sparse_temporal_stratum_experiment_response = client.post(
            f"/api/research/projects/{project_id}/experiments",
            json={
                "name": "任一时间分层样本覆盖不足时应拒绝点验证",
                "formula_spec_id": fusion_formula_response.json()["id"],
                "data_snapshot_id": fusion_snapshot["id"],
                "runner_type": "python",
                "execution_mode": "formal",
                "validation_plan": {
                    "split": "spatiotemporal-holdout",
                    "sample_validation": {
                        "split": "independent_test",
                        "min_confidence": 0.8,
                        "require_unconflicted": True,
                        "min_sample_count": 4,
                        "min_spatial_blocks": 2,
                        "min_temporal_strata": 1,
                        "min_samples_per_spatial_block": 2,
                        "min_samples_per_temporal_stratum": 5,
                    }
                },
                "visualization_contract": ["input", "feature", "classification"],
            },
            headers=headers,
        )
        assert sparse_temporal_stratum_experiment_response.status_code == 201, sparse_temporal_stratum_experiment_response.text
        sparse_temporal_stratum_run_response = client.post(
            f"/api/research/projects/{project_id}/experiments/"
            f"{sparse_temporal_stratum_experiment_response.json()['id']}/runs",
            headers=headers,
        )
        assert sparse_temporal_stratum_run_response.status_code == 201, sparse_temporal_stratum_run_response.text
        sparse_temporal_stratum_run = sparse_temporal_stratum_run_response.json()
        assert sparse_temporal_stratum_run["status"] == "failed"
        assert "min_samples_per_temporal_stratum=5" in sparse_temporal_stratum_run["error_message"]

        unlisted_download = client.get(
            f"/api/research/projects/{project_id}/experiments/{experiment['id']}/runs/{run['id']}/outputs/not-an-output.png",
            headers=headers,
        )
        assert unlisted_download.status_code == 404

        idl_experiment_response = client.post(
            f"/api/research/projects/{project_id}/experiments",
            json={
                "name": "IDL 对照待配置",
                "formula_spec_id": formula["id"],
                "data_snapshot_id": snapshot["id"],
                "runner_type": "idl",
                "execution_mode": "preview",
                "visualization_contract": ["input", "water_mask"],
            },
            headers=headers,
        )
        assert idl_experiment_response.status_code == 201, idl_experiment_response.text
        idl_run_response = client.post(
            f"/api/research/projects/{project_id}/experiments/{idl_experiment_response.json()['id']}/runs",
            headers=headers,
        )
        assert idl_run_response.status_code == 201
        assert idl_run_response.json()["status"] == "unavailable"
        assert "不会影响 PythonRunner" in idl_run_response.json()["error_message"]


@pytest.mark.parametrize("expression", ["green.__class__", "green[0]", "green + unknown", "open('x')"])
def test_safe_band_math_ast_rejects_non_declarative_syntax(expression: str) -> None:
    from app.services.python_runner import PythonRunner

    parsed = PythonRunner._parse_band_math_expression(expression)
    with pytest.raises(ValueError):
        PythonRunner._evaluate_band_math_expression(parsed, {"green": np.ones((1, 1), dtype=np.float32)})
