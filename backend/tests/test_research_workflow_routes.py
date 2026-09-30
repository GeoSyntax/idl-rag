from __future__ import annotations

import io
from pathlib import Path

from fastapi.testclient import TestClient


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


def _register(client: TestClient, username: str) -> dict:
    response = client.post(
        "/api/auth/register",
        json={"username": username, "password": "secret123"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _headers(auth: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {auth['access_token']}"}


def test_research_workflow_requires_owned_assets_evidence_and_frozen_formal_protocol(
    monkeypatch, tmp_path: Path
) -> None:
    _prepare_state(monkeypatch, tmp_path)

    from app.main import create_app

    with TestClient(create_app()) as client:
        owner = _register(client, "teacher")
        other = _register(client, "student")
        owner_headers = _headers(owner)
        other_headers = _headers(other)

        project_response = client.post(
            "/api/research/projects",
            json={
                "name": "鄱阳湖水体研究",
                "description": "从开放研究模式开始。",
                "entry_mode": "open",
                "protocol": {"question": "云遮挡下多源水体制图是否更稳定？"},
            },
            headers=owner_headers,
        )
        assert project_response.status_code == 201, project_response.text
        project = project_response.json()
        project_id = project["id"]
        assert project["visibility"] == "my"
        assert project["egress_policy"] == "private-local"
        assert project["status"] == "exploratory"

        research_question = (
            "比较 Sentinel-1、Sentinel-2 与可解释融合方法在鄱阳湖不同季节的水体制图稳定性。"
        )
        protocol_draft = client.post(
            f"/api/research/projects/{project_id}/protocol-draft",
            json={"research_question": research_question},
            headers=owner_headers,
        )
        assert protocol_draft.status_code == 200, protocol_draft.text
        draft_payload = protocol_draft.json()
        assert draft_payload["protocol"]["research_question"] == research_question
        assert draft_payload["protocol"]["status"] == "draft"
        assert draft_payload["protocol"]["data_plan"]["private_data_egress"] == "private-local"
        assert draft_payload["protocol"]["validation_plan"]["split"] == "spatiotemporal-holdout"
        assert "未查询外部服务" in draft_payload["notice"]

        # 生成草案只是本地预填，不应覆盖研究者尚未保存的既有协议。
        project_after_draft = client.get(f"/api/research/projects/{project_id}", headers=owner_headers)
        assert project_after_draft.status_code == 200
        assert project_after_draft.json()["protocol"] == {"question": "云遮挡下多源水体制图是否更稳定？"}

        other_protocol_draft = client.post(
            f"/api/research/projects/{project_id}/protocol-draft",
            json={"research_question": research_question},
            headers=other_headers,
        )
        assert other_protocol_draft.status_code == 404

        owner_projects = client.get("/api/research/projects", headers=owner_headers)
        assert owner_projects.status_code == 200
        assert [item["id"] for item in owner_projects.json()] == [project_id]

        other_project = client.get(f"/api/research/projects/{project_id}", headers=other_headers)
        assert other_project.status_code == 404
        other_assets = client.get(
            f"/api/research/projects/{project_id}/data-assets", headers=other_headers
        )
        assert other_assets.status_code == 404

        local_asset_response = client.post(
            f"/api/research/projects/{project_id}/data-assets",
            json={
                "name": "Sentinel-2 2023 丰水期合成",
                "asset_kind": "raster",
                "source_type": "local",
                "source_uri": "research://private/poyang/s2-2023-wet.tif",
                "sha256": "a" * 64,
                "metadata": {
                    "sensor": "Sentinel-2 SR Harmonized",
                    "crs": "EPSG:32650",
                    "scale_m": 10,
                },
            },
            headers=owner_headers,
        )
        assert local_asset_response.status_code == 201, local_asset_response.text
        local_asset = local_asset_response.json()
        assert local_asset["access_policy"] == "private-local"

        gee_asset_response = client.post(
            f"/api/research/projects/{project_id}/data-assets",
            json={
                "name": "Sentinel-1 GEE 查询快照",
                "asset_kind": "raster",
                "source_type": "gee",
                "source_uri": "gee://COPERNICUS/S1_GRD?start=2023-06-01&end=2023-06-30",
                "metadata": {"polarizations": ["VV", "VH"], "scale_m": 10},
            },
            headers=owner_headers,
        )
        assert gee_asset_response.status_code == 201, gee_asset_response.text
        gee_asset = gee_asset_response.json()

        duplicate_snapshot = client.post(
            f"/api/research/projects/{project_id}/data-snapshots",
            json={"name": "重复资产快照", "asset_ids": [local_asset["id"], local_asset["id"]]},
            headers=owner_headers,
        )
        assert duplicate_snapshot.status_code == 400

        other_workspace = client.post(
            "/api/research/projects",
            json={"name": "学生私有研究", "entry_mode": "template"},
            headers=other_headers,
        )
        assert other_workspace.status_code == 201
        other_workspace_id = other_workspace.json()["id"]
        other_asset = client.post(
            f"/api/research/projects/{other_workspace_id}/data-assets",
            json={
                "name": "学生私有 ROI",
                "asset_kind": "roi",
                "source_type": "local",
                "source_uri": "research://private/student/roi.geojson",
            },
            headers=other_headers,
        )
        assert other_asset.status_code == 201

        foreign_asset_snapshot = client.post(
            f"/api/research/projects/{project_id}/data-snapshots",
            json={"name": "非法跨项目快照", "asset_ids": [other_asset.json()["id"]]},
            headers=owner_headers,
        )
        assert foreign_asset_snapshot.status_code == 400

        snapshot_response = client.post(
            f"/api/research/projects/{project_id}/data-snapshots",
            json={
                "name": "2023 丰水期多源冻结快照",
                "description": "用于模型选择验证。",
                "asset_ids": [local_asset["id"], gee_asset["id"]],
            },
            headers=owner_headers,
        )
        assert snapshot_response.status_code == 201, snapshot_response.text
        snapshot = snapshot_response.json()
        assert snapshot["asset_ids"] == [local_asset["id"], gee_asset["id"]]
        assert snapshot["is_frozen"] is True
        assert len(snapshot["snapshot_hash"]) == 64

        unverified_card = client.post(
            f"/api/research/projects/{project_id}/evidence-cards",
            json={
                "title": "缺失来源的已核验证据",
                "status": "verified",
                "source_type": "paper",
            },
            headers=owner_headers,
        )
        assert unverified_card.status_code == 400

        candidate_without_evidence = client.post(
            f"/api/research/projects/{project_id}/formula-specs",
            json={
                "name": "MNDWI 候选",
                "status": "candidate",
                "spec": {"formula": "(green - swir1) / (green + swir1)"},
            },
            headers=owner_headers,
        )
        assert candidate_without_evidence.status_code == 400

        evidence_response = client.post(
            f"/api/research/projects/{project_id}/evidence-cards",
            json={
                "title": "MNDWI 方法依据",
                "status": "verified",
                "source_type": "paper",
                "doi": "10.0000/example.mndwi",
                "applicability": "Sentinel-2 B3/B11 水体指数基线。",
            },
            headers=owner_headers,
        )
        assert evidence_response.status_code == 201, evidence_response.text
        evidence = evidence_response.json()

        formula_response = client.post(
            f"/api/research/projects/{project_id}/formula-specs",
            json={
                "name": "MNDWI 水体基线",
                "version": 1,
                "status": "frozen",
                "spec": {
                    "inputs": {"green": "B3", "swir1": "B11"},
                    "formula": "(green - swir1) / (green + swir1)",
                    "parameters": {"threshold": 0.0},
                },
                "evidence_card_ids": [evidence["id"]],
            },
            headers=owner_headers,
        )
        assert formula_response.status_code == 201, formula_response.text
        formula = formula_response.json()
        assert formula["status"] == "frozen"

        idl_comparison_with_nonderived_asset = client.post(
            f"/api/research/projects/{project_id}/experiments",
            json={
                "name": "IDL 对照不得引用普通输入栅格",
                "formula_spec_id": formula["id"],
                "data_snapshot_id": snapshot["id"],
                "execution_mode": "preview",
                "parameters": {
                    "idl_comparison": {
                        "idl_output_asset_id": local_asset["id"],
                        "comparison_mode": "classification",
                        "python_output_file": "water_mask.tif",
                        "absolute_tolerance": 0,
                    }
                },
                "visualization_contract": ["input", "water_mask"],
            },
            headers=owner_headers,
        )
        assert idl_comparison_with_nonderived_asset.status_code == 400
        assert "asset_kind=derived" in idl_comparison_with_nonderived_asset.json()["detail"]

        formal_without_validation = client.post(
            f"/api/research/projects/{project_id}/experiments",
            json={
                "name": "缺少验证的正式实验",
                "formula_spec_id": formula["id"],
                "data_snapshot_id": snapshot["id"],
                "execution_mode": "formal",
                "visualization_contract": ["input", "water_mask"],
            },
            headers=owner_headers,
        )
        assert formal_without_validation.status_code == 400

        formal_without_independent_evidence = client.post(
            f"/api/research/projects/{project_id}/experiments",
            json={
                "name": "只有泛化方案的正式实验",
                "formula_spec_id": formula["id"],
                "data_snapshot_id": snapshot["id"],
                "execution_mode": "formal",
                "validation_plan": {"split": "spatiotemporal-holdout"},
                "visualization_contract": ["input", "water_mask"],
            },
            headers=owner_headers,
        )
        assert formal_without_independent_evidence.status_code == 400

        formal_without_sample_minimums = client.post(
            f"/api/research/projects/{project_id}/experiments",
            json={
                "name": "未声明最小独立样本覆盖的正式实验",
                "formula_spec_id": formula["id"],
                "data_snapshot_id": snapshot["id"],
                "execution_mode": "formal",
                "validation_plan": {
                    "split": "spatiotemporal-holdout",
                    "sample_validation": {
                        "split": "independent_test",
                        "min_confidence": 0.8,
                        "require_unconflicted": True,
                    },
                },
                "visualization_contract": ["input", "water_mask"],
            },
            headers=owner_headers,
        )
        assert formal_without_sample_minimums.status_code == 400
        assert "min_sample_count" in formal_without_sample_minimums.json()["detail"]

        formal_without_visuals = client.post(
            f"/api/research/projects/{project_id}/experiments",
            json={
                "name": "缺少图件的正式实验",
                "formula_spec_id": formula["id"],
                "data_snapshot_id": snapshot["id"],
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
                        "min_samples_per_spatial_block": 1,
                        "min_samples_per_temporal_stratum": 1,
                    },
                },
            },
            headers=owner_headers,
        )
        assert formal_without_visuals.status_code == 400

        formal_response = client.post(
            f"/api/research/projects/{project_id}/experiments",
            json={
                "name": "MNDWI 2023 正式验证",
                "formula_spec_id": formula["id"],
                "data_snapshot_id": snapshot["id"],
                "runner_type": "python",
                "execution_mode": "formal",
                "parameters": {"threshold": 0.0},
                "validation_plan": {
                    "split": "spatiotemporal-holdout",
                    "metrics": ["f1", "iou", "area_difference"],
                    "sample_validation": {
                        "split": "independent_test",
                        "min_confidence": 0.8,
                        "require_unconflicted": True,
                        "min_sample_count": 4,
                        "min_spatial_blocks": 2,
                        "min_temporal_strata": 1,
                        "min_samples_per_spatial_block": 1,
                        "min_samples_per_temporal_stratum": 1,
                    },
                },
                "visualization_contract": ["input", "cloud_mask", "mndwi", "water_mask", "error_map"],
            },
            headers=owner_headers,
        )
        assert formal_response.status_code == 201, formal_response.text
        formal = formal_response.json()
        assert formal["runner_type"] == "python"
        assert formal["execution_mode"] == "formal"
        assert formal["status"] == "planned"

        experiment_list = client.get(
            f"/api/research/projects/{project_id}/experiments", headers=owner_headers
        )
        assert experiment_list.status_code == 200
        assert [item["id"] for item in experiment_list.json()] == [formal["id"]]

        invited = client.post(
            f"/api/research/projects/{project_id}/members",
            json={"username": "student"},
            headers=owner_headers,
        )
        assert invited.status_code == 201, invited.text
        member = invited.json()
        assert member["username"] == "student"
        assert member["added_by_user_id"] == project["owner_user_id"]

        member_list = client.get(f"/api/research/projects/{project_id}/members", headers=other_headers)
        assert member_list.status_code == 200
        assert [item["id"] for item in member_list.json()] == [member["id"]]
        shared_projects = client.get("/api/research/projects", headers=other_headers)
        assert shared_projects.status_code == 200
        assert project_id in {item["id"] for item in shared_projects.json()}

        shared_assets = client.get(f"/api/research/projects/{project_id}/data-assets", headers=other_headers)
        assert shared_assets.status_code == 200
        shared_runs = client.get(
            f"/api/research/projects/{project_id}/experiments/{formal['id']}/runs", headers=other_headers
        )
        assert shared_runs.status_code == 200
        member_asset = client.post(
            f"/api/research/projects/{project_id}/data-assets",
            json={
                "name": "学生补充的判读 ROI",
                "asset_kind": "roi",
                "source_type": "local",
                "source_uri": "research://private/poyang/student-roi.geojson",
            },
            headers=other_headers,
        )
        assert member_asset.status_code == 201, member_asset.text

        member_cannot_invite = client.post(
            f"/api/research/projects/{project_id}/members",
            json={"username": "teacher"},
            headers=other_headers,
        )
        assert member_cannot_invite.status_code == 404
        removed = client.delete(
            f"/api/research/projects/{project_id}/members/{member['id']}", headers=owner_headers
        )
        assert removed.status_code == 204
        revoked_assets = client.get(f"/api/research/projects/{project_id}/data-assets", headers=other_headers)
        assert revoked_assets.status_code == 404


def test_upload_data_asset_persists_structured_provenance_metadata(monkeypatch, tmp_path: Path) -> None:
    _prepare_state(monkeypatch, tmp_path)

    from app.main import create_app

    with TestClient(create_app()) as client:
        auth = _register(client, "provenance-user")
        headers = _headers(auth)
        project_response = client.post(
            "/api/research/projects",
            json={"name": "provenance metadata", "entry_mode": "open"},
            headers=headers,
        )
        assert project_response.status_code == 201, project_response.text
        project_id = project_response.json()["id"]

        response = client.post(
            f"/api/research/projects/{project_id}/data-assets/upload",
            files={"file": ("derived.tif", io.BytesIO(b"not-a-raster"), "image/tiff")},
            data={
                "asset_kind": "raster",
                "name": "derived dB input",
                "metadata_json": '{"derived_operation":"linear_power_to_db","output_units":"dB"}',
            },
            headers=headers,
        )
        assert response.status_code == 201, response.text
        assert response.json()["metadata"]["derived_operation"] == "linear_power_to_db"
        assert response.json()["metadata"]["output_units"] == "dB"

        invalid = client.post(
            f"/api/research/projects/{project_id}/data-assets/upload",
            files={"file": ("invalid.tif", io.BytesIO(b"not-a-raster"), "image/tiff")},
            data={"metadata_json": "[]"},
            headers=headers,
        )
        assert invalid.status_code == 400
        assert "JSON 对象" in invalid.json()["detail"]
