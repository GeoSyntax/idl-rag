from pathlib import Path

from fastapi.testclient import TestClient


def test_evaluation_api_persists_local_and_langsmith_reports(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("IDLRAG_BASE_DIR", str(tmp_path))

    from app.core.config import get_app_settings
    from app.db.database import get_engine, get_index_engine, get_index_session_factory, get_session_factory

    get_app_settings.cache_clear()
    get_engine.cache_clear()
    get_index_engine.cache_clear()
    get_session_factory.cache_clear()
    get_index_session_factory.cache_clear()

    from app.api.routes.auth import _clear_register_rate_limits
    from app.main import create_app

    _clear_register_rate_limits()

    with TestClient(create_app()) as client:
        register = client.post("/api/auth/register", json={"username": "admin", "password": "secret123"})
        assert register.status_code == 201
        headers = {"Authorization": f"Bearer {register.json()['access_token']}"}

        created_kb = client.post(
            "/api/knowledge-bases",
            json={"name": "Eval KB", "description": "evaluation test"},
            headers=headers,
        )
        assert created_kb.status_code == 201
        kb_id = created_kb.json()["id"]

        local_report = client.post(
            "/api/evaluation/local",
            json={"knowledge_base_id": kb_id, "categories": ["fts_keyword"], "top_k": 3, "limit": 1},
            headers=headers,
        )
        assert local_report.status_code == 200
        local_payload = local_report.json()
        assert local_payload["report_type"] == "local"
        assert local_payload["knowledge_base_id"] == kb_id
        assert local_payload["summary_json"]["total"] == 2
        assert local_payload["report_json"]["strategies"] == ["hybrid_rrf_no_rerank", "hybrid_rrf"]
        assert local_payload["report_json"]["reports"]

        sync_report = client.post(
            "/api/evaluation/langsmith/sync",
            json={"dataset": "dry-run-dataset", "dry_run": True},
            headers=headers,
        )
        assert sync_report.status_code == 200
        sync_payload = sync_report.json()
        assert sync_payload["report_type"] == "langsmith_sync"
        assert sync_payload["dataset"] == "dry-run-dataset"
        assert sync_payload["summary_json"]["dry_run"] is True

        listed = client.get("/api/evaluation/reports", headers=headers)
        assert listed.status_code == 200
        reports = listed.json()
        assert {report["id"] for report in reports} >= {local_payload["id"], sync_payload["id"]}

        detail = client.get(f"/api/evaluation/reports/{local_payload['id']}", headers=headers)
        assert detail.status_code == 200
        assert detail.json()["id"] == local_payload["id"]
