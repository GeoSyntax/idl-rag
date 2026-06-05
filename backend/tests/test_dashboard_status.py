from pathlib import Path

from fastapi.testclient import TestClient


def _prepare_state(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("IDLRAG_BASE_DIR", str(tmp_path))

    from app.core.config import get_app_settings
    from app.db.database import get_engine, get_index_engine, get_index_session_factory, get_session_factory, init_database

    get_app_settings.cache_clear()
    get_engine.cache_clear()
    get_index_engine.cache_clear()
    get_session_factory.cache_clear()
    get_index_session_factory.cache_clear()
    init_database()


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_dashboard_summary_includes_index_and_fallback_status(monkeypatch, tmp_path: Path) -> None:
    _prepare_state(monkeypatch, tmp_path)

    from app.db.database import get_session_factory
    from app.db.models import Document, IndexJob, KnowledgeBase
    from app.main import create_app

    with TestClient(create_app()) as client:
        admin = client.post(
            "/api/auth/register",
            json={"username": "admin", "password": "secret123"},
        ).json()
        headers = _auth_headers(admin["access_token"])

        db = get_session_factory()()
        try:
            kb = KnowledgeBase(name="Status KB", owner_user_id=admin["user"]["id"])
            db.add(kb)
            db.flush()
            ready = Document(
                knowledge_base_id=kb.id,
                file_name="ready.md",
                file_path="ready.md",
                media_type="text/markdown",
                sha256="ready",
                status="ready",
                embedding_is_fallback=True,
            )
            queued = Document(
                knowledge_base_id=kb.id,
                file_name="queued.md",
                file_path="queued.md",
                media_type="text/markdown",
                sha256="queued",
                status="queued",
            )
            db.add_all([ready, queued])
            db.flush()
            db.add(
                IndexJob(
                    knowledge_base_id=kb.id,
                    document_id=queued.id,
                    job_type="ingest",
                    status="failed",
                    payload_json={},
                )
            )
            db.commit()
        finally:
            db.close()

        response = client.get("/api/dashboard/summary", headers=headers)
        assert response.status_code == 200
        payload = response.json()
        assert payload["document_count"] == 2
        assert payload["ready_document_count"] == 1
        assert payload["queued_document_count"] == 1
        assert payload["failed_index_job_count"] == 1
        assert payload["fallback_document_count"] == 1
        assert payload["embedding_fallback_active"] is True
        assert payload["worker_alive"] is True
