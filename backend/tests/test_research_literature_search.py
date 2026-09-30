from __future__ import annotations

from pathlib import Path

import httpx
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

    # Route services are module singletons. Recreate the two services that
    # resolve storage/index paths so this test remains isolated when another
    # test has already loaded the app with a different temporary base dir.
    from app.api.routes import research as research_routes
    from app.services.research_literature_search import ResearchLiteratureSearchService
    from app.services.research_protocol_evidence_service import ResearchProtocolEvidenceMapService
    from app.services.research_rag_service import ResearchRagService

    research_routes.literature_search_service = ResearchLiteratureSearchService()
    research_routes.research_rag_service = ResearchRagService()
    research_routes.protocol_evidence_map_service = ResearchProtocolEvidenceMapService(
        research_routes.research_rag_service
    )


def _register(client: TestClient, username: str) -> dict:
    response = client.post("/api/auth/register", json={"username": username, "password": "secret123"})
    assert response.status_code == 201, response.text
    return response.json()


def _headers(auth: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {auth['access_token']}"}


class _FakeResponse:
    def raise_for_status(self) -> None:
        return None

    @staticmethod
    def json() -> dict:
        return {
            "message": {
                "items": [
                    {
                        "DOI": "10.1234/example.water",
                        "URL": "https://doi.org/10.1234/example.water",
                        "title": ["<i>Transparent</i> water mapping under clouds"],
                        "author": [{"given": "Ada", "family": "Lovelace"}],
                        "container-title": ["Remote Sensing Journal"],
                        "published-online": {"date-parts": [[2024, 5, 1]]},
                        "type": "journal-article",
                        "abstract": "<jats:p>Candidate <b>metadata</b>, not a verified method.</jats:p>",
                    }
                ]
            }
        }


class _FakeClient:
    last_params: dict | None = None

    def __init__(self, **_: object) -> None:
        pass

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def get(self, _: str, *, params: dict) -> _FakeResponse:
        _FakeClient.last_params = params
        return _FakeResponse()


class _OpenAlexFakeResponse:
    def raise_for_status(self) -> None:
        return None

    @staticmethod
    def json() -> dict:
        return {
            "results": [
                {
                    "id": "https://openalex.org/W123456789",
                    "doi": "https://doi.org/10.5555/openalex.water",
                    "title": "OpenAlex water mapping candidate",
                    "authorships": [{"author": {"display_name": "Grace Hopper"}}],
                    "primary_location": {"source": {"display_name": "Remote Sensing Letters"}},
                    "publication_year": 2023,
                    "type": "article",
                    "abstract_inverted_index": {"Candidate": [0], "metadata": [1], "only": [2], "not": [3], "verified": [4]},
                }
            ]
        }


class _OpenAlexFakeClient(_FakeClient):
    def get(self, _: str, *, params: dict) -> _OpenAlexFakeResponse:
        _FakeClient.last_params = params
        return _OpenAlexFakeResponse()


class _SemanticScholarFakeResponse:
    def raise_for_status(self) -> None:
        return None

    @staticmethod
    def json() -> dict:
        return {
            "total": 1,
            "data": [
                {
                    "paperId": "s2-paper-123",
                    "externalIds": {"DOI": "10.9999/semantic.water"},
                    "title": "Semantic Scholar water mapping candidate",
                    "authors": [{"authorId": "a1", "name": "Katherine Johnson"}],
                    "venue": "Remote Sensing of Environment",
                    "year": 2022,
                    "publicationTypes": ["JournalArticle"],
                    "abstract": "Semantic Scholar candidate abstract only.",
                    "url": "https://www.semanticscholar.org/paper/s2-paper-123",
                }
            ],
        }


class _SemanticScholarFakeClient(_FakeClient):
    last_init_kwargs: dict[str, object] = {}

    def __init__(self, **kwargs: object) -> None:
        self.last_init_kwargs = kwargs
        type(self).last_init_kwargs = kwargs

    def get(self, _: str, *, params: dict) -> _SemanticScholarFakeResponse:
        _FakeClient.last_params = params
        return _SemanticScholarFakeResponse()


class _SemanticScholarNetworkResponse:
    def raise_for_status(self) -> None:
        return None

    @staticmethod
    def json() -> dict:
        return {
            "offset": 0,
            "next": 2,
            "data": [
                {
                    "citedPaper": {
                        "paperId": "s2-reference-456",
                        "externalIds": {"DOI": "10.9999/reference.456"},
                        "title": "Reference paper for water mapping",
                        "authors": [{"name": "Grace Hopper"}],
                        "venue": "Remote Sensing",
                        "year": 2021,
                        "publicationTypes": ["JournalArticle"],
                        "abstract": "Reference metadata abstract.",
                        "url": "https://www.semanticscholar.org/paper/s2-reference-456",
                    }
                }
            ],
        }


class _SemanticScholarNetworkClient(_SemanticScholarFakeClient):
    last_url: str | None = None

    def get(self, url: str, *, params: dict) -> _SemanticScholarNetworkResponse:
        type(self).last_url = url
        _FakeClient.last_params = params
        return _SemanticScholarNetworkResponse()


class _SemanticScholarRateLimitedResponse(_SemanticScholarFakeResponse):
    def raise_for_status(self) -> None:
        request = httpx.Request("GET", "https://api.semanticscholar.org/graph/v1/paper/search")
        response = httpx.Response(429, request=request)
        raise httpx.HTTPStatusError("rate limited", request=request, response=response)


class _SemanticScholarRateLimitedClient(_SemanticScholarFakeClient):
    def get(self, _: str, *, params: dict) -> _SemanticScholarRateLimitedResponse:
        _FakeClient.last_params = params
        return _SemanticScholarRateLimitedResponse()


def test_project_literature_search_logs_query_and_imports_candidate_evidence(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("IDLRAG_SEMANTIC_SCHOLAR_API_KEY", "s2-test-secret")
    _prepare_state(monkeypatch, tmp_path)

    from app.api.routes.research import literature_search_service
    from app.main import create_app

    monkeypatch.setattr(literature_search_service, "client_factory", _FakeClient)
    with TestClient(create_app()) as client:
        owner = _register(client, "researcher")
        other = _register(client, "observer")
        owner_headers = _headers(owner)
        project_response = client.post(
            "/api/research/projects",
            json={"name": "文献搜索审计", "entry_mode": "open"},
            headers=owner_headers,
        )
        assert project_response.status_code == 201, project_response.text
        project_id = project_response.json()["id"]

        search_response = client.get(
            f"/api/research/projects/{project_id}/literature-search",
            params={"query": "Sentinel-1 Sentinel-2 seasonal water mapping", "rows": 5},
            headers=owner_headers,
        )
        assert search_response.status_code == 200, search_response.text
        payload = search_response.json()
        assert payload["provider"] == "crossref"
        assert payload["audit_id"] > 0
        assert len(payload["candidates"]) == 1
        candidate = payload["candidates"][0]
        assert candidate["title"] == "Transparent water mapping under clouds"
        assert candidate["authors"] == ["Ada Lovelace"]
        assert candidate["published_year"] == 2024
        assert candidate["abstract"] == "Candidate metadata, not a verified method."
        assert _FakeClient.last_params == {
            "query.bibliographic": "Sentinel-1 Sentinel-2 seasonal water mapping",
            "rows": 5,
            "select": "DOI,title,author,container-title,published-print,published-online,published,type,abstract,URL",
        }
        assert "private" not in str(_FakeClient.last_params).lower()

        monkeypatch.setattr(literature_search_service, "client_factory", _OpenAlexFakeClient)
        openalex_response = client.get(
            f"/api/research/projects/{project_id}/literature-search",
            params={"query": "seasonal water mapping", "rows": 3, "provider": "openalex"},
            headers=owner_headers,
        )
        assert openalex_response.status_code == 200, openalex_response.text
        openalex_payload = openalex_response.json()
        assert openalex_payload["provider"] == "openalex"
        assert openalex_payload["candidates"][0]["provider"] == "openalex"
        assert openalex_payload["candidates"][0]["doi"] == "10.5555/openalex.water"
        assert openalex_payload["candidates"][0]["abstract"] == "Candidate metadata only not verified"
        assert _FakeClient.last_params == {
            "search": "seasonal water mapping",
            "per-page": 3,
            "select": "id,doi,title,authorships,primary_location,publication_year,type,abstract_inverted_index",
        }
        monkeypatch.setattr(literature_search_service, "client_factory", _SemanticScholarFakeClient)
        semantic_response = client.get(
            f"/api/research/projects/{project_id}/literature-search",
            params={"query": "seasonal water mapping", "rows": 4, "provider": "semantic_scholar"},
            headers=owner_headers,
        )
        assert semantic_response.status_code == 200, semantic_response.text
        semantic_payload = semantic_response.json()
        assert semantic_payload["provider"] == "semantic_scholar"
        assert semantic_payload["candidates"][0]["external_id"] == "s2-paper-123"
        assert semantic_payload["candidates"][0]["doi"] == "10.9999/semantic.water"
        assert semantic_payload["candidates"][0]["abstract"] == "Semantic Scholar candidate abstract only."
        assert _FakeClient.last_params == {
            "query": "seasonal water mapping",
            "limit": 4,
            "fields": "paperId,externalIds,title,authors,venue,year,publicationTypes,abstract,url,openAccessPdf",
        }
        assert _SemanticScholarFakeClient.last_init_kwargs["headers"] == {
            "User-Agent": "IDL-RAG-Research-Workflow/0.1 (metadata discovery)",
            "x-api-key": "s2-test-secret",
        }
        assert "s2-test-secret" not in str(semantic_payload)
        semantic_card = client.post(
            f"/api/research/projects/{project_id}/literature-search/evidence-cards",
            json={"audit_id": semantic_payload["audit_id"], "candidate": semantic_payload["candidates"][0]},
            headers=owner_headers,
        )
        assert semantic_card.status_code == 201, semantic_card.text
        assert semantic_card.json()["doi"] == "10.9999/semantic.water"

        monkeypatch.setattr(literature_search_service, "client_factory", _SemanticScholarNetworkClient)
        network_response = client.post(
            f"/api/research/projects/{project_id}/literature-search/semantic-scholar-network",
            json={
                "audit_id": semantic_payload["audit_id"],
                "candidate": semantic_payload["candidates"][0],
                "relation": "references",
                "limit": 2,
                "offset": 0,
            },
            headers=owner_headers,
        )
        assert network_response.status_code == 200, network_response.text
        network_payload = network_response.json()
        assert network_payload["source_audit_id"] == semantic_payload["audit_id"]
        assert network_payload["audit_id"] != semantic_payload["audit_id"]
        assert network_payload["relation"] == "references"
        assert network_payload["next_offset"] == 2
        assert network_payload["candidates"][0]["external_id"] == "s2-reference-456"
        assert network_payload["candidates"][0]["doi"] == "10.9999/reference.456"
        assert _SemanticScholarNetworkClient.last_url.endswith("/s2-paper-123/references")
        assert _FakeClient.last_params["offset"] == 0
        assert _FakeClient.last_params["limit"] == 2
        assert "citedPaper.title" in _FakeClient.last_params["fields"]
        assert _SemanticScholarNetworkClient.last_init_kwargs["headers"]["x-api-key"] == "s2-test-secret"
        assert "s2-test-secret" not in str(network_payload)
        network_card = client.post(
            f"/api/research/projects/{project_id}/literature-search/evidence-cards",
            json={"audit_id": network_payload["audit_id"], "candidate": network_payload["candidates"][0]},
            headers=owner_headers,
        )
        assert network_card.status_code == 201, network_card.text

        forged_network = dict(semantic_payload["candidates"][0])
        forged_network["external_id"] = "../../private"
        forged_response = client.post(
            f"/api/research/projects/{project_id}/literature-search/semantic-scholar-network",
            json={"audit_id": semantic_payload["audit_id"], "candidate": forged_network, "relation": "references"},
            headers=owner_headers,
        )
        assert forged_response.status_code == 400
        crossref_network = client.post(
            f"/api/research/projects/{project_id}/literature-search/semantic-scholar-network",
            json={"audit_id": payload["audit_id"], "candidate": candidate, "relation": "references"},
            headers=owner_headers,
        )
        assert crossref_network.status_code == 400

        monkeypatch.setattr(literature_search_service, "client_factory", _SemanticScholarRateLimitedClient)
        rate_limited_response = client.get(
            f"/api/research/projects/{project_id}/literature-search",
            params={"query": "rate limited water mapping", "rows": 2, "provider": "semantic_scholar"},
            headers=owner_headers,
        )
        assert rate_limited_response.status_code == 503
        assert "限流" in rate_limited_response.json()["detail"]
        from app.db.database import get_session_factory
        from app.db.models import ResearchExternalSearchLog

        audit_session = get_session_factory()()
        try:
            openalex_audit = (
                audit_session.query(ResearchExternalSearchLog)
                .filter(ResearchExternalSearchLog.id == openalex_payload["audit_id"])
                .one()
            )
            assert openalex_audit.provider == "openalex"
            assert openalex_audit.request_metadata_json["outbound_fields"] == ["query", "rows", "provider"]
            assert openalex_audit.request_metadata_json["raw_project_data_sent"] is False
            semantic_audit = (
                audit_session.query(ResearchExternalSearchLog)
                .filter(ResearchExternalSearchLog.id == semantic_payload["audit_id"])
                .one()
            )
            assert semantic_audit.provider == "semantic_scholar"
            assert semantic_audit.request_metadata_json["outbound_fields"] == ["query", "limit", "fields", "provider"]
            assert semantic_audit.request_metadata_json["raw_project_data_sent"] is False
            assert "s2-test-secret" not in str(semantic_audit.request_metadata_json)
            network_audit = (
                audit_session.query(ResearchExternalSearchLog)
                .filter(ResearchExternalSearchLog.id == network_payload["audit_id"])
                .one()
            )
            assert network_audit.status == "completed"
            assert network_audit.request_metadata_json["source_audit_id"] == semantic_payload["audit_id"]
            assert network_audit.request_metadata_json["relation"] == "references"
            assert network_audit.request_metadata_json["candidates"][0]["external_id"] == "s2-reference-456"
            assert "s2-test-secret" not in str(network_audit.request_metadata_json)
            failed_semantic_audit = (
                audit_session.query(ResearchExternalSearchLog)
                .filter(
                    ResearchExternalSearchLog.project_id == project_id,
                    ResearchExternalSearchLog.provider == "semantic_scholar",
                    ResearchExternalSearchLog.status == "failed",
                )
                .one()
            )
            assert "s2-test-secret" not in (failed_semantic_audit.error_message or "")
        finally:
            audit_session.close()

        invalid_provider = client.get(
            f"/api/research/projects/{project_id}/literature-search",
            params={"query": "water mapping", "provider": "unknown"},
            headers=owner_headers,
        )
        assert invalid_provider.status_code == 400

        forbidden_search = client.get(
            f"/api/research/projects/{project_id}/literature-search",
            params={"query": "water mapping"},
            headers=_headers(other),
        )
        assert forbidden_search.status_code == 404

        imported = client.post(
            f"/api/research/projects/{project_id}/literature-search/evidence-cards",
            json={"audit_id": payload["audit_id"], "candidate": candidate},
            headers=owner_headers,
        )
        assert imported.status_code == 201, imported.text
        evidence = imported.json()
        assert evidence["status"] == "candidate"
        assert evidence["doi"] == "10.1234/example.water"
        assert evidence["metadata"]["candidate_only"] is True
        assert evidence["metadata"]["external_search_audit_id"] == payload["audit_id"]

        knowledge_base_response = client.post(
            "/api/knowledge-bases",
            json={"name": "论文方法库"},
            headers=owner_headers,
        )
        assert knowledge_base_response.status_code == 201, knowledge_base_response.text
        knowledge_base_id = knowledge_base_response.json()["id"]
        binding_response = client.post(
            f"/api/research/projects/{project_id}/rag-sources",
            json={"knowledge_base_id": knowledge_base_id, "category": "method"},
            headers=owner_headers,
        )
        assert binding_response.status_code == 201, binding_response.text

        rag_import = client.post(
            f"/api/research/projects/{project_id}/literature-search/rag-import",
            json={
                "audit_id": payload["audit_id"],
                "candidate": candidate,
                "knowledge_base_id": knowledge_base_id,
                "category": "method",
            },
            headers=owner_headers,
        )
        assert rag_import.status_code == 201, rag_import.text
        rag_document = rag_import.json()["document"]
        assert rag_document["status"] == "queued"
        assert rag_document["file_name"].startswith("literature-crossref-")
        document_content = Path(rag_document["file_path"]).read_text(encoding="utf-8")
        assert "import_scope: abstract_only" in document_content
        assert "external_search_audit_id" in document_content
        assert candidate["abstract"] in document_content

        from app.db.database import get_session_factory
        from app.services.ingest_service import IngestService

        indexing_session = get_session_factory()()
        try:
            assert IngestService().process_next_job(indexing_session) is True
        finally:
            indexing_session.close()
        rag_search = client.get(
            f"/api/research/projects/{project_id}/rag-search",
            params={"query": "10.1234/example.water", "category": "method"},
            headers=owner_headers,
        )
        assert rag_search.status_code == 200, rag_search.text
        assert rag_search.json()["searched_knowledge_base_ids"] == [knowledge_base_id]
        assert rag_search.json()["citations"]
        assert any(
            citation["file_name"] == rag_document["file_name"]
            for citation in rag_search.json()["citations"]
        )

        duplicate_rag_import = client.post(
            f"/api/research/projects/{project_id}/literature-search/rag-import",
            json={
                "audit_id": payload["audit_id"],
                "candidate": candidate,
                "knowledge_base_id": knowledge_base_id,
                "category": "method",
            },
            headers=owner_headers,
        )
        assert duplicate_rag_import.status_code == 400

        forbidden_rag_import = client.post(
            f"/api/research/projects/{project_id}/literature-search/rag-import",
            json={
                "audit_id": payload["audit_id"],
                "candidate": candidate,
                "knowledge_base_id": knowledge_base_id,
                "category": "method",
            },
            headers=_headers(other),
        )
        assert forbidden_rag_import.status_code == 404

        invalid_audit = client.post(
            f"/api/research/projects/{project_id}/literature-search/evidence-cards",
            json={"audit_id": payload["audit_id"] + 100, "candidate": candidate},
            headers=owner_headers,
        )
        assert invalid_audit.status_code == 400
