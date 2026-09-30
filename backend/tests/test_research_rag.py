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


def test_project_rag_requires_explicit_binding_and_allows_shared_retrieval(monkeypatch, tmp_path: Path) -> None:
    _prepare_state(monkeypatch, tmp_path)
    from app.main import create_app
    from app.api.schemas import Citation
    from app.api.routes.research import research_rag_service

    observed: dict[str, object] = {}

    def fake_search_multiple(db, knowledge_base_ids, query, top_k=6, *, strategy="hybrid_rrf_no_rerank", use_rerank=None):
        observed.update({"knowledge_base_ids": knowledge_base_ids, "query": query, "top_k": top_k, "strategy": strategy})
        return [
            Citation(
                chunk_id=7,
                document_id=3,
                file_name="mndwi-method.pdf",
                file_path="protected://kb/mndwi-method.pdf",
                excerpt="MNDWI uses green and SWIR bands.",
                knowledge_base_id=knowledge_base_ids[0],
                knowledge_base_name="水体方法论文",
                score=0.91,
            )
        ]

    monkeypatch.setattr(research_rag_service.retrieval_service, "search_multiple", fake_search_multiple)

    with TestClient(create_app()) as client:
        owner = _register(client, "ragteacher")
        collaborator = _register(client, "ragstudent")
        outsider = _register(client, "ragoutsider")
        owner_headers = _headers(owner)
        collaborator_headers = _headers(collaborator)
        project = client.post(
            "/api/research/projects", json={"name": "项目 RAG", "entry_mode": "open"}, headers=owner_headers
        ).json()
        project_id = project["id"]
        knowledge_base = client.post(
            "/api/knowledge-bases",
            json={"name": "水体方法论文", "description": "MNDWI 与验证文献"},
            headers=owner_headers,
        )
        assert knowledge_base.status_code == 201, knowledge_base.text
        kb_id = knowledge_base.json()["id"]

        unbound = client.get(
            f"/api/research/projects/{project_id}/rag-search?query=MNDWI", headers=owner_headers
        )
        assert unbound.status_code == 200
        assert unbound.json()["citations"] == []
        assert unbound.json()["searched_knowledge_base_ids"] == []

        unbound_evidence_map = client.post(
            f"/api/research/projects/{project_id}/protocol-evidence-map-draft",
            json={"research_question": "比较 MNDWI 与融合算法在不同季节的水体制图表现。"},
            headers=owner_headers,
        )
        assert unbound_evidence_map.status_code == 200, unbound_evidence_map.text
        assert unbound_evidence_map.json()["evidence_map"] == []
        assert unbound_evidence_map.json()["protocol"]["method_plan"]["rag_evidence_map"] == []
        assert client.get(f"/api/research/projects/{project_id}", headers=owner_headers).json()["protocol"] == {}

        bound = client.post(
            f"/api/research/projects/{project_id}/rag-sources",
            json={"knowledge_base_id": kb_id, "category": "method"},
            headers=owner_headers,
        )
        assert bound.status_code == 201, bound.text
        assert bound.json()["category"] == "method"
        assert bound.json()["knowledge_base_name"] == "水体方法论文"

        other_kb = client.post(
            "/api/knowledge-bases",
            json={"name": "外部 IDL", "description": "不应被教师直接绑定"},
            headers=_headers(outsider),
        ).json()
        forbidden_binding = client.post(
            f"/api/research/projects/{project_id}/rag-sources",
            json={"knowledge_base_id": other_kb["id"], "category": "idl_code"},
            headers=owner_headers,
        )
        assert forbidden_binding.status_code == 400

        invited = client.post(
            f"/api/research/projects/{project_id}/members",
            json={"username": "ragstudent"},
            headers=owner_headers,
        )
        assert invited.status_code == 201
        visible_sources = client.get(f"/api/research/projects/{project_id}/rag-sources", headers=collaborator_headers)
        assert visible_sources.status_code == 200
        assert [source["knowledge_base_id"] for source in visible_sources.json()] == [kb_id]

        search = client.get(
            f"/api/research/projects/{project_id}/rag-search?query=MNDWI&category=method&top_k=4",
            headers=collaborator_headers,
        )
        assert search.status_code == 200, search.text
        assert observed == {
            "knowledge_base_ids": [kb_id],
            "query": "MNDWI",
            "top_k": 4,
            "strategy": "hybrid_rrf_no_rerank",
        }
        assert search.json()["citations"][0]["knowledge_base_id"] == kb_id
        assert "Data Catalog" in search.json()["notice"]

        evidence_map = client.post(
            f"/api/research/projects/{project_id}/protocol-evidence-map-draft",
            json={
                "research_question": "比较 MNDWI 与融合算法在不同季节的水体制图表现。",
                "category": "method",
                "top_k": 5,
            },
            headers=collaborator_headers,
        )
        assert evidence_map.status_code == 200, evidence_map.text
        evidence_map_payload = evidence_map.json()
        assert observed["top_k"] == 5
        assert evidence_map_payload["searched_knowledge_base_ids"] == [kb_id]
        assert evidence_map_payload["citations"][0]["chunk_id"] == 7
        assert evidence_map_payload["evidence_map"][0]["review_status"] == "unverified"
        assert evidence_map_payload["evidence_map"][0]["citation"]["knowledge_base_id"] == kb_id
        assert evidence_map_payload["protocol"]["method_plan"]["rag_evidence_map_status"] == "unverified_researcher_review_required"
        assert evidence_map_payload["protocol"]["method_plan"]["evidence_card_ids"] == []
        assert client.get(f"/api/research/projects/{project_id}", headers=collaborator_headers).json()["protocol"] == {}

        outsider_sources = client.get(f"/api/research/projects/{project_id}/rag-sources", headers=_headers(outsider))
        assert outsider_sources.status_code == 404
        outsider_evidence_map = client.post(
            f"/api/research/projects/{project_id}/protocol-evidence-map-draft",
            json={"research_question": "比较 MNDWI 与融合算法在不同季节的水体制图表现。"},
            headers=_headers(outsider),
        )
        assert outsider_evidence_map.status_code == 404

        deleted = client.delete(f"/api/knowledge-bases/{kb_id}", headers=owner_headers)
        assert deleted.status_code == 204, deleted.text
        sources_after_delete = client.get(f"/api/research/projects/{project_id}/rag-sources", headers=collaborator_headers)
        assert sources_after_delete.status_code == 200
        assert sources_after_delete.json() == []
