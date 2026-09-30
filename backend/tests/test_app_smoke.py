import time
from pathlib import Path

from fastapi.testclient import TestClient


def test_app_smoke_flow(monkeypatch, tmp_path: Path) -> None:
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
    get_index_session_factory.cache_clear()
    get_session_factory.cache_clear()

    from app.main import create_app

    with TestClient(create_app()) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"

        register = client.post(
            "/api/auth/register",
            json={"username": "admin", "password": "secret123"},
        )
        assert register.status_code == 201
        token = register.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        create_kb = client.post(
            "/api/knowledge-bases",
            json={"name": "IDL Manual", "description": "Test knowledge base"},
            headers=headers,
        )
        assert create_kb.status_code == 201
        kb_id = create_kb.json()["id"]

        sample_file = tmp_path / "sample.pro"
        sample_file.write_text(
            "; Overview\npro hello_world\n  print, 'hello'\nend\n",
            encoding="utf-8",
        )

        imported = client.post(
            f"/api/knowledge-bases/{kb_id}/documents/import-path",
            json={"path": sample_file.as_posix(), "recursive": True},
            headers=headers,
        )
        assert imported.status_code == 200
        assert len(imported.json()["imported"]) == 1

        documents = []
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            listed = client.get(f"/api/knowledge-bases/{kb_id}/documents", headers=headers)
            assert listed.status_code == 200
            documents = listed.json()
            if documents and documents[0]["status"] == "ready":
                break
            time.sleep(0.1)
        assert documents
        assert documents[0]["status"] == "ready"

        chunks = client.get(f"/api/knowledge-bases/{kb_id}/documents/{documents[0]['id']}/chunks", headers=headers)
        assert chunks.status_code == 200
        chunk_payload = chunks.json()
        assert chunk_payload
        assert chunk_payload[0]["document_id"] == documents[0]["id"]
        assert any("hello_world" in chunk["content"] for chunk in chunk_payload)
        assert all("chunk_kind" in chunk["metadata"] for chunk in chunk_payload)

        updated_kb = client.patch(
            f"/api/knowledge-bases/{kb_id}",
            json={"default_retrieval_strategy": "fts_only", "default_top_k": 1},
            headers=headers,
        )
        assert updated_kb.status_code == 200
        assert updated_kb.json()["default_retrieval_strategy"] == "fts_only"
        assert updated_kb.json()["default_top_k"] == 1

        asked = client.post(
            "/api/chat/ask",
            json={"knowledge_base_ids": [kb_id], "question": "hello_world 是什么？"},
            headers=headers,
        )
        assert asked.status_code == 200
        payload = asked.json()
        assert payload["session_id"] >= 1
        assert len(payload["messages"]) == 2
        assert payload["citations"]
        assert payload["citations"][0]["knowledge_base_id"] == kb_id
        assert payload["citations"][0]["source_strategy"] == "fts_only"

        debug = client.post(
            "/api/chat/retrieve-debug",
            json={"knowledge_base_ids": [kb_id], "query": "hello_world 是什么？", "strategy": "hybrid_rrf", "top_k": 3},
            headers=headers,
        )
        assert debug.status_code == 200
        debug_payload = debug.json()
        assert debug_payload["strategy"] == "hybrid_rrf"
        assert debug_payload["candidates"]
        assert debug_payload["candidates"][0]["rank"] == 1
        assert debug_payload["candidates"][0]["knowledge_base_id"] == kb_id
