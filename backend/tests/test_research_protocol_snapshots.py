from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine


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


def _register(client: TestClient, username: str = "protocol-researcher") -> dict:
    response = client.post("/api/auth/register", json={"username": username, "password": "secret123"})
    assert response.status_code == 201, response.text
    return response.json()


def _headers(auth: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {auth['access_token']}"}


def test_formal_experiment_requires_saved_question_but_preview_can_start_blank_protocol(
    monkeypatch, tmp_path: Path
) -> None:
    _prepare_state(monkeypatch, tmp_path)

    from app.main import create_app

    with TestClient(create_app()) as client:
        headers = _headers(_register(client))
        project_response = client.post(
            "/api/research/projects",
            json={"name": "尚未写协议的探索项目", "entry_mode": "open"},
            headers=headers,
        )
        assert project_response.status_code == 201, project_response.text
        project_id = project_response.json()["id"]

        asset_response = client.post(
            f"/api/research/projects/{project_id}/data-assets",
            json={
                "name": "探索用本地影像",
                "asset_kind": "raster",
                "source_type": "local",
                "source_uri": "research://private/protocol-preview.tif",
                "sha256": "b" * 64,
            },
            headers=headers,
        )
        assert asset_response.status_code == 201, asset_response.text
        snapshot_response = client.post(
            f"/api/research/projects/{project_id}/data-snapshots",
            json={"name": "探索快照", "asset_ids": [asset_response.json()["id"]]},
            headers=headers,
        )
        assert snapshot_response.status_code == 201, snapshot_response.text

        evidence_response = client.post(
            f"/api/research/projects/{project_id}/evidence-cards",
            json={
                "title": "探索方法说明",
                "status": "verified",
                "source_type": "official_document",
                "source_url": "https://example.invalid/protocol-preview",
            },
            headers=headers,
        )
        assert evidence_response.status_code == 201, evidence_response.text
        formula_response = client.post(
            f"/api/research/projects/{project_id}/formula-specs",
            json={
                "name": "探索公式",
                "status": "frozen",
                "spec": {"operation": "normalized_difference_threshold"},
                "evidence_card_ids": [evidence_response.json()["id"]],
            },
            headers=headers,
        )
        assert formula_response.status_code == 201, formula_response.text

        preview = client.post(
            f"/api/research/projects/{project_id}/experiments",
            json={
                "name": "允许未完成协议的预览",
                "formula_spec_id": formula_response.json()["id"],
                "data_snapshot_id": snapshot_response.json()["id"],
                "execution_mode": "preview",
                "visualization_contract": ["input", "water_mask"],
            },
            headers=headers,
        )
        assert preview.status_code == 201, preview.text
        assert len(preview.json()["project_protocol_hash"]) == 64

        formal = client.post(
            f"/api/research/projects/{project_id}/experiments",
            json={
                "name": "必须先写研究问题的正式实验",
                "formula_spec_id": formula_response.json()["id"],
                "data_snapshot_id": snapshot_response.json()["id"],
                "execution_mode": "formal",
                "validation_plan": {"split": "spatiotemporal-holdout"},
                "visualization_contract": ["input", "water_mask"],
            },
            headers=headers,
        )
        assert formal.status_code == 400
        assert "research_question 或 question" in formal.json()["detail"]


def test_protocol_revisions_are_deduplicated_versioned_and_project_scoped(monkeypatch, tmp_path: Path) -> None:
    _prepare_state(monkeypatch, tmp_path)

    from app.main import create_app

    with TestClient(create_app()) as client:
        owner_headers = _headers(_register(client, "protocol-owner"))
        collaborator_headers = _headers(_register(client, "protocol-collaborator"))
        outsider_headers = _headers(_register(client, "protocol-outsider"))
        initial_protocol = {"research_question": "鄱阳湖丰枯水期水体制图在不同传感器间是否稳定？"}
        project_response = client.post(
            "/api/research/projects",
            json={"name": "协议版本研究", "entry_mode": "open", "protocol": initial_protocol},
            headers=owner_headers,
        )
        assert project_response.status_code == 201, project_response.text
        project_id = project_response.json()["id"]

        initial_revisions = client.get(
            f"/api/research/projects/{project_id}/protocol-revisions", headers=owner_headers
        )
        assert initial_revisions.status_code == 200, initial_revisions.text
        assert [(item["version"], item["protocol"]) for item in initial_revisions.json()] == [(1, initial_protocol)]
        revision_one = initial_revisions.json()[0]
        assert len(revision_one["protocol_hash"]) == 64

        description_only = client.patch(
            f"/api/research/projects/{project_id}",
            json={"description": "只修改说明，不应生成协议版本。"},
            headers=owner_headers,
        )
        assert description_only.status_code == 200
        identical_protocol = client.patch(
            f"/api/research/projects/{project_id}",
            json={"protocol": initial_protocol},
            headers=owner_headers,
        )
        assert identical_protocol.status_code == 200
        still_one_revision = client.get(
            f"/api/research/projects/{project_id}/protocol-revisions", headers=owner_headers
        )
        assert [item["version"] for item in still_one_revision.json()] == [1]

        updated_protocol = {
            "research_question": "鄱阳湖丰枯水期水体制图在不同传感器间是否稳定？",
            "hypothesis": "融合模型在云遮挡情景下只作为待验证候选。",
        }
        update_protocol = client.patch(
            f"/api/research/projects/{project_id}",
            json={"protocol": updated_protocol},
            headers=owner_headers,
        )
        assert update_protocol.status_code == 200, update_protocol.text
        revisions = client.get(f"/api/research/projects/{project_id}/protocol-revisions", headers=owner_headers)
        assert revisions.status_code == 200
        assert [(item["version"], item["protocol"]) for item in revisions.json()] == [
            (2, updated_protocol),
            (1, initial_protocol),
        ]

        invitation = client.post(
            f"/api/research/projects/{project_id}/members",
            json={"username": "protocol-collaborator"},
            headers=owner_headers,
        )
        assert invitation.status_code == 201, invitation.text
        collaborator_revisions = client.get(
            f"/api/research/projects/{project_id}/protocol-revisions", headers=collaborator_headers
        )
        assert collaborator_revisions.status_code == 200
        assert [item["version"] for item in collaborator_revisions.json()] == [2, 1]
        outsider_revisions = client.get(
            f"/api/research/projects/{project_id}/protocol-revisions", headers=outsider_headers
        )
        assert outsider_revisions.status_code == 404


def test_database_migrates_legacy_research_experiment_columns_and_legacy_formal_run_is_refused(
    monkeypatch, tmp_path: Path
) -> None:
    _prepare_state(monkeypatch, tmp_path)
    database_path = tmp_path / "data" / "app.db"
    database_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_engine = create_engine(f"sqlite:///{database_path.as_posix()}", future=True)
    with legacy_engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE research_experiments (id INTEGER PRIMARY KEY)")
    legacy_engine.dispose()

    from app.db.database import get_engine, init_database

    init_database()
    with get_engine().connect() as connection:
        columns = {
            row["name"]
            for row in connection.exec_driver_sql("PRAGMA table_info(research_experiments)").mappings().all()
        }
    assert {
        "project_protocol_revision_id",
        "project_protocol_json",
        "project_protocol_hash",
    }.issubset(columns)

    from app.db.models import FormulaSpec, ResearchDataSnapshot, ResearchExperiment
    from app.services.research_run_service import ResearchRunService

    legacy_experiment = ResearchExperiment(
        project_id=1,
        formula_spec_id=1,
        data_snapshot_id=1,
        name="旧正式实验",
        runner_type="python",
        execution_mode="formal",
        parameters_json={},
        validation_plan_json={"split": "spatiotemporal-holdout"},
        visualization_contract_json=["input"],
        project_protocol_json={},
        project_protocol_hash="",
    )
    with pytest.raises(ValueError, match="research_question 或 question"):
        ResearchRunService._validate_execution_preconditions(
            None,
            legacy_experiment,
            FormulaSpec(status="frozen"),
            ResearchDataSnapshot(is_frozen=True),
        )


def test_protocol_readiness_reports_gaps_then_passes_complete_saved_protocol(monkeypatch, tmp_path: Path) -> None:
    _prepare_state(monkeypatch, tmp_path)
    from app.main import create_app

    with TestClient(create_app()) as client:
        owner = _register(client, "readiness-owner")
        outsider = _register(client, "readiness-outsider")
        owner_headers = _headers(owner)
        outsider_headers = _headers(outsider)
        project_response = client.post(
            "/api/research/projects",
            json={"name": "协议就绪检查", "entry_mode": "open"},
            headers=owner_headers,
        )
        assert project_response.status_code == 201, project_response.text
        project_id = project_response.json()["id"]

        initial_readiness = client.get(
            f"/api/research/projects/{project_id}/protocol-readiness", headers=owner_headers
        )
        assert initial_readiness.status_code == 200, initial_readiness.text
        assert initial_readiness.json()["ready"] is False
        assert {item["code"] for item in initial_readiness.json()["missing"]} >= {
            "protocol_revision_missing",
            "research_question_missing",
            "roi_missing",
            "snapshot_missing",
            "evidence_missing",
        }

        roi = client.post(
            f"/api/research/projects/{project_id}/data-assets",
            json={
                "name": "鄱阳湖研究区",
                "asset_kind": "roi",
                "source_type": "local",
                "source_uri": "research://private/readiness/poyang.geojson",
            },
            headers=owner_headers,
        )
        raster = client.post(
            f"/api/research/projects/{project_id}/data-assets",
            json={
                "name": "就绪检查输入栅格",
                "asset_kind": "raster",
                "source_type": "local",
                "source_uri": "research://private/readiness/input.tif",
                "sha256": "c" * 64,
            },
            headers=owner_headers,
        )
        assert roi.status_code == 201, roi.text
        assert raster.status_code == 201, raster.text
        snapshot = client.post(
            f"/api/research/projects/{project_id}/data-snapshots",
            json={"name": "就绪检查冻结快照", "asset_ids": [raster.json()["id"]]},
            headers=owner_headers,
        )
        assert snapshot.status_code == 201, snapshot.text
        evidence = client.post(
            f"/api/research/projects/{project_id}/evidence-cards",
            json={
                "title": "就绪检查方法证据",
                "status": "verified",
                "source_type": "paper",
                "doi": "10.0000/readiness.example",
            },
            headers=owner_headers,
        )
        assert evidence.status_code == 201, evidence.text

        complete_protocol = {
            "research_question": "鄱阳湖不同季节的多源水体制图是否具有稳定差异？",
            "hypothesis": "SAR 与光学融合在云遮挡时段将作为待验证候选提升边界稳定性。",
            "study_area": {"description": "鄱阳湖主体及湿地过渡区。", "roi_asset_id": roi.json()["id"]},
            "temporal_scope": {"start": "2023-01-01", "end": "2025-12-31", "seasonal_strata": ["wet", "dry"]},
            "data_plan": {"snapshot_id": snapshot.json()["id"], "private_data_egress": "private-local"},
            "method_plan": {"evidence_card_ids": [evidence.json()["id"]]},
            "validation_plan": {
                "split": "spatiotemporal-holdout",
                "independent_test_period": "2025",
                "reference_source": "独立人工判读点样本",
                "spatial_blocks": ["north", "south"],
            },
            "visualization_contract": ["input", "preprocessing", "feature", "classification", "validation_error"],
            "conclusion_boundary": "结论只适用于当前 ROI、时间范围、数据产品和独立测试设计。",
        }
        update = client.patch(
            f"/api/research/projects/{project_id}",
            json={"protocol": complete_protocol},
            headers=owner_headers,
        )
        assert update.status_code == 200, update.text

        ready = client.get(f"/api/research/projects/{project_id}/protocol-readiness", headers=owner_headers)
        assert ready.status_code == 200, ready.text
        ready_payload = ready.json()
        assert ready_payload["ready"] is True
        assert ready_payload["missing"] == []
        assert ready_payload["protocol_revision_id"] is not None
        assert len(ready_payload["protocol_hash"]) == 64

        forbidden = client.get(
            f"/api/research/projects/{project_id}/protocol-readiness", headers=outsider_headers
        )
        assert forbidden.status_code == 404
