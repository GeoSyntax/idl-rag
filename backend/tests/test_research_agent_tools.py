from __future__ import annotations

from pathlib import Path

import pytest


def _new_db(tmp_path: Path):
    import os

    os.environ["IDLRAG_BASE_DIR"] = str(tmp_path)
    from app.core.config import get_app_settings
    from app.db.database import get_engine, get_session_factory, init_database

    get_app_settings.cache_clear()
    get_engine.cache_clear()
    get_session_factory.cache_clear()
    init_database()
    return get_session_factory()()


def _create_project(db):
    from app.db.models import ResearchProject, ResearchProjectMember, User

    owner = User(username="research-owner", password_hash="hash", role="user", is_active=True)
    member = User(username="research-member", password_hash="hash", role="user", is_active=True)
    stranger = User(username="research-stranger", password_hash="hash", role="user", is_active=True)
    db.add_all([owner, member, stranger])
    db.commit()
    db.refresh(owner)
    db.refresh(member)
    db.refresh(stranger)
    project = ResearchProject(
        owner_user_id=owner.id,
        name="Agent research project",
        entry_mode="open",
        protocol_json={"research_question": "How stable is water mapping across seasons?"},
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    db.add(ResearchProjectMember(project_id=project.id, user_id=member.id, added_by_user_id=owner.id))
    db.commit()
    return owner, member, stranger, project


def test_research_agent_read_only_tools_and_membership(tmp_path: Path) -> None:
    db = _new_db(tmp_path)
    try:
        owner, member, stranger, project = _create_project(db)
        from app.services.agent_tools import (
            get_openai_tools,
            tool_research_project_context,
            tool_research_protocol_draft,
        )

        exposed_tools = {
            item["function"]["name"]
            for item in get_openai_tools()
        }
        assert {
            "research_project_context",
            "research_rag_search",
            "research_protocol_draft",
            "research_protocol_readiness",
            "research_literature_search",
            "research_queue_preview",
            "research_fetch_gee_asset",
        }.issubset(exposed_tools)

        context = tool_research_project_context(db, project.id, member.id)
        assert context.metadata["private_raw_data_included"] is False
        assert '"project_id":' in context.output
        assert "research_question" in context.output

        draft = tool_research_protocol_draft(
            db,
            project.id,
            owner.id,
            "Compare optical and SAR water extraction under cloud cover",
        )
        assert draft.metadata["persisted"] is False
        assert db.get(type(project), project.id).protocol_json["research_question"].startswith("How stable")

        with pytest.raises(LookupError):
            tool_research_project_context(db, project.id, stranger.id)
    finally:
        db.close()


def test_research_agent_external_search_requires_explicit_consent(tmp_path: Path, monkeypatch) -> None:
    db = _new_db(tmp_path)
    try:
        owner, _member, _stranger, project = _create_project(db)
        from app.services.agent_service import AgentService
        from app.services.agent_tools import ToolResult

        service = AgentService()
        rejected = service._execute_tool(
            db,
            "research_literature_search",
            {"project_id": project.id, "query": "surface water mapping", "provider": "crossref", "rows": 3},
            knowledge_base_id=0,
            session_id=0,
            owner_user_id=owner.id,
            research_project_id=project.id,
            allow_external_research=False,
        )
        assert "没有显式允许" in rejected.output

        called: dict[str, object] = {}

        def fake_search(db, project_id, owner_user_id, query, provider, rows):
            called.update(project_id=project_id, owner_user_id=owner_user_id, query=query, provider=provider, rows=rows)
            return ToolResult(name="research_literature_search", output="candidate")

        monkeypatch.setattr("app.services.agent_service.tool_research_literature_search", fake_search)
        allowed = service._execute_tool(
            db,
            "research_literature_search",
            {"project_id": project.id, "query": "surface water mapping", "provider": "crossref", "rows": 3},
            knowledge_base_id=0,
            session_id=0,
            owner_user_id=owner.id,
            research_project_id=project.id,
            allow_external_research=True,
        )
        assert allowed.output == "candidate"
        assert called["project_id"] == project.id
    finally:
        db.close()


def test_research_agent_stream_uses_bound_project_and_rejects_stranger(tmp_path: Path, monkeypatch) -> None:
    db = _new_db(tmp_path)
    try:
        owner, _member, stranger, project = _create_project(db)
        from app.api.schemas import ChatRequest
        from app.services.agent_service import AgentService

        service = AgentService()
        monkeypatch.setattr(
            "app.services.agent_service.get_runtime_settings",
            lambda _db: type("Settings", (), {"api_key": "configured"})(),
        )
        responses = iter(
            [
                {"tool": "research_project_context", "args": {"project_id": project.id}},
                {"final_answer": "已读取项目摘要，并保留研究者审阅边界。"},
            ]
        )
        monkeypatch.setattr(service.llm_service, "agent_generate", lambda *_args, **_kwargs: next(responses))

        events = list(
            service.agent_answer_stream(
                db,
                ChatRequest(research_project_id=project.id, question="请先查看研究项目状态"),
                owner_user_id=owner.id,
            )
        )
        tool_events = [event for event in events if event.get("step") == "tool_result"]
        assert tool_events and tool_events[0]["tool"] == "research_project_context"
        assert any(event.get("type") == "done" for event in events)

        with pytest.raises(ValueError, match="不存在或当前用户无访问权限"):
            list(
                service.agent_answer_stream(
                    db,
                    ChatRequest(research_project_id=project.id, question="查看项目"),
                    owner_user_id=stranger.id,
                )
            )
    finally:
        db.close()


def test_research_agent_can_only_queue_existing_python_preview_with_consent(tmp_path: Path, monkeypatch) -> None:
    db = _new_db(tmp_path)
    try:
        owner, _member, _stranger, project = _create_project(db)
        from datetime import datetime

        from app.api.schemas import ResearchRunResponse
        from app.db.models import ResearchExperiment
        from app.services.agent_service import AgentService
        from app.services.agent_tools import tool_research_queue_preview

        preview = ResearchExperiment(
            project_id=project.id,
            formula_spec_id=1,
            data_snapshot_id=1,
            name="existing preview",
            runner_type="python",
            execution_mode="preview",
            status="planned",
        )
        formal = ResearchExperiment(
            project_id=project.id,
            formula_spec_id=1,
            data_snapshot_id=1,
            name="formal experiment",
            runner_type="python",
            execution_mode="formal",
            status="planned",
        )
        db.add_all([preview, formal])
        db.commit()
        db.refresh(preview)
        db.refresh(formal)

        queued_response = ResearchRunResponse(
            id=77,
            project_id=project.id,
            experiment_id=preview.id,
            runner_type="python",
            status="queued",
            run_token="a" * 32,
            manifest={"execution_mode": "queue"},
            outputs=[],
            error_message=None,
            started_at=None,
            finished_at=None,
            created_at=datetime.now(),
        )
        called: dict[str, int] = {}

        def fake_queue(self, db, project_id, experiment_id, owner_user_id, *, retry_of_run_id=None):
            called.update(project_id=project_id, experiment_id=experiment_id, owner_user_id=owner_user_id)
            return queued_response

        monkeypatch.setattr("app.services.research_run_service.ResearchRunService.queue_run", fake_queue)
        result = tool_research_queue_preview(db, project.id, preview.id, owner.id, confirm=True)
        assert '"status": "queued"' in result.output
        assert called["experiment_id"] == preview.id

        formal_result = tool_research_queue_preview(db, project.id, formal.id, owner.id, confirm=True)
        assert "只能排队 preview" in formal_result.output

        service = AgentService()
        rejected = service._execute_tool(
            db,
            "research_queue_preview",
            {"project_id": project.id, "experiment_id": preview.id, "confirm": True},
            knowledge_base_id=0,
            session_id=0,
            owner_user_id=owner.id,
            research_project_id=project.id,
            allow_research_execution=False,
        )
        assert "没有显式允许" in rejected.output
    finally:
        db.close()


def test_research_agent_gee_fetch_requires_consent_and_redacts_private_uri(tmp_path: Path, monkeypatch) -> None:
    db = _new_db(tmp_path)
    try:
        owner, _member, _stranger, project = _create_project(db)
        from app.api.schemas import ResearchDataAssetResponse, ResearchGeeFetchResponse
        from app.services.agent_service import AgentService
        from app.services.agent_tools import tool_research_fetch_gee_asset

        asset = ResearchDataAssetResponse(
            id=91,
            project_id=project.id,
            name="sentinel-preview",
            asset_kind="raster",
            source_type="gee",
            source_uri="research://assets/private/secret.tif",
            sha256="a" * 64,
            metadata={"gee_query": {"dataset_id": "COPERNICUS/S2_SR_HARMONIZED"}, "raw_project_data_sent": False},
            access_policy="private-local",
            created_at="2026-09-29T00:00:00",
        )

        def fake_fetch(self, db, project_id, payload, owner_user_id):
            assert project_id == project.id
            assert payload.dataset_id == "COPERNICUS/S2_SR_HARMONIZED"
            assert payload.bbox == [115.8, 28.9, 116.0, 29.1]
            return ResearchGeeFetchResponse(asset=asset, notice="登记成功")

        monkeypatch.setattr("app.services.gee_service.GeeService.fetch_research_asset", fake_fetch)
        rejected = tool_research_fetch_gee_asset(
            db,
            project.id,
            owner.id,
            "COPERNICUS/S2_SR_HARMONIZED",
            [115.8, 28.9, 116.0, 29.1],
            bands=["B03"],
            confirm=False,
        )
        assert "confirm=true" in rejected.output

        service = AgentService()
        args = {
            "project_id": project.id,
            "dataset_id": "COPERNICUS/S2_SR_HARMONIZED",
            "bbox": [115.8, 28.9, 116.0, 29.1],
            "bands": ["B03"],
            "scale": 10,
            "crs": "EPSG:4326",
            "composite": "median",
            "confirm": True,
        }
        denied = service._execute_tool(
            db,
            "research_fetch_gee_asset",
            args,
            knowledge_base_id=0,
            session_id=0,
            owner_user_id=owner.id,
            research_project_id=project.id,
            allow_gee_fetch=False,
        )
        assert "没有显式允许" in denied.output

        allowed = service._execute_tool(
            db,
            "research_fetch_gee_asset",
            args,
            knowledge_base_id=0,
            session_id=0,
            owner_user_id=owner.id,
            research_project_id=project.id,
            allow_gee_fetch=True,
        )
        assert '"asset_id": 91' in allowed.output
        assert "research://assets/private" not in allowed.output
        assert allowed.metadata["private_uri_exposed"] is False
    finally:
        db.close()


def test_research_agent_catalog_and_preview_creation_are_bounded(tmp_path: Path) -> None:
    db = _new_db(tmp_path)
    try:
        owner, _member, _stranger, project = _create_project(db)
        from app.db.models import FormulaSpec, ResearchDataAsset, ResearchDataSnapshot
        from app.services.agent_service import AgentService
        from app.services.agent_tools import tool_research_data_catalog

        asset = ResearchDataAsset(
            project_id=project.id,
            name="local bands",
            asset_kind="raster",
            source_type="local",
            source_uri="research://assets/private/secret.tif",
            sha256="b" * 64,
            metadata_json={"bands": ["green", "nir"], "path": "C:/private/secret.tif"},
        )
        db.add(asset)
        db.commit()
        db.refresh(asset)
        snapshot = ResearchDataSnapshot(
            project_id=project.id,
            name="frozen bands",
            description="bounded test snapshot",
            asset_ids_json=[asset.id],
            snapshot_hash="c" * 64,
        )
        formula = FormulaSpec(
            project_id=project.id,
            name="MNDWI candidate",
            version=1,
            status="draft",
            spec_json={"operation": "safe_band_math_threshold", "expression": "(green - swir) / (green + swir)"},
            evidence_card_ids_json=[],
        )
        db.add_all([snapshot, formula])
        db.commit()
        db.refresh(snapshot)
        db.refresh(formula)

        catalog = tool_research_data_catalog(db, project.id, owner.id)
        assert "secret.tif" not in catalog.output
        assert '"id": %s' % asset.id in catalog.output

        service = AgentService()
        denied = service._execute_tool(
            db,
            "research_create_preview_experiment",
            {
                "project_id": project.id,
                "name": "agent preview",
                "formula_spec_id": formula.id,
                "data_snapshot_id": snapshot.id,
                "confirm": True,
            },
            knowledge_base_id=0,
            session_id=0,
            owner_user_id=owner.id,
            research_project_id=project.id,
            allow_research_execution=False,
        )
        assert "没有显式允许" in denied.output

        allowed = service._execute_tool(
            db,
            "research_create_preview_experiment",
            {
                "project_id": project.id,
                "name": "agent preview",
                "formula_spec_id": formula.id,
                "data_snapshot_id": snapshot.id,
                "parameters": {"operation": "safe_band_math_threshold", "threshold": 0.1},
                "visualization_contract": ["input_preview", "classification_preview"],
                "confirm": True,
            },
            knowledge_base_id=0,
            session_id=0,
            owner_user_id=owner.id,
            research_project_id=project.id,
            allow_research_execution=True,
        )
        assert '"execution_mode": "preview"' in allowed.output
        assert '"runner_type": "python"' in allowed.output
        assert allowed.metadata["mutating"] is True
    finally:
        db.close()
