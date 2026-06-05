from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def _prepare_state(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("IDLRAG_BASE_DIR", str(tmp_path))

    from app.core.config import get_app_settings
    from app.db.database import get_engine, get_session_factory, init_database

    get_app_settings.cache_clear()
    get_engine.cache_clear()
    get_session_factory.cache_clear()
    init_database()

    # 清除注册频率限制状态，避免测试间互相干扰
    from app.api.routes.auth import _clear_register_rate_limits
    _clear_register_rate_limits()


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_production_rejects_default_auth_secret() -> None:
    from app.core.config import AppSettings

    settings = AppSettings(environment="production")
    with pytest.raises(RuntimeError, match="IDLRAG_AUTH_SECRET"):
        settings.validate_auth_secret()


def test_development_allows_default_auth_secret(caplog) -> None:
    from app.core.config import AppSettings

    settings = AppSettings(environment="development")
    settings.validate_auth_secret()
    assert "默认 IDLRAG_AUTH_SECRET" in caplog.text


def test_first_registered_user_becomes_admin_and_claims_legacy_data(monkeypatch, tmp_path: Path) -> None:
    _prepare_state(monkeypatch, tmp_path)

    from app.db.database import get_session_factory
    from app.db.models import ChatSession, KnowledgeBase
    from app.main import create_app

    db = get_session_factory()()
    try:
        knowledge_base = KnowledgeBase(name="Legacy KB", description="legacy")
        db.add(knowledge_base)
        db.commit()
        db.refresh(knowledge_base)

        session = ChatSession(knowledge_base_id=knowledge_base.id, title="legacy session")
        db.add(session)
        db.commit()
    finally:
        db.close()

    with TestClient(create_app()) as client:
        register = client.post(
            "/api/auth/register",
            json={"username": "admin", "password": "secret123"},
        )
        assert register.status_code == 201
        payload = register.json()
        assert payload["user"]["role"] == "admin"

        me = client.get("/api/auth/me", headers=_auth_headers(payload["access_token"]))
        assert me.status_code == 200
        assert me.json()["username"] == "admin"

    db = get_session_factory()()
    try:
        owned_kb = db.query(KnowledgeBase).filter(KnowledgeBase.name == "Legacy KB").one()
        owned_session = db.query(ChatSession).filter(ChatSession.title == "legacy session").one()
        assert owned_kb.owner_user_id == payload["user"]["id"]
        assert owned_session.owner_user_id == payload["user"]["id"]
    finally:
        db.close()


def test_second_registered_user_is_user_and_admin_endpoints_are_forbidden(monkeypatch, tmp_path: Path) -> None:
    _prepare_state(monkeypatch, tmp_path)

    from app.main import create_app

    with TestClient(create_app()) as client:
        admin = client.post(
            "/api/auth/register",
            json={"username": "admin", "password": "secret123"},
        )
        user = client.post(
            "/api/auth/register",
            json={"username": "alice", "password": "secret123"},
        )

        assert admin.status_code == 201
        assert user.status_code == 201
        assert user.json()["user"]["role"] == "user"

        headers = _auth_headers(user.json()["access_token"])
        assert client.get("/api/users", headers=headers).status_code == 403
        assert client.get("/api/settings", headers=headers).status_code == 403


def test_users_are_isolated_and_can_create_same_named_knowledge_bases(monkeypatch, tmp_path: Path) -> None:
    _prepare_state(monkeypatch, tmp_path)

    from app.main import create_app

    with TestClient(create_app()) as client:
        first = client.post(
            "/api/auth/register",
            json={"username": "admin", "password": "secret123"},
        ).json()
        second = client.post(
            "/api/auth/register",
            json={"username": "alice", "password": "secret123"},
        ).json()

        first_headers = _auth_headers(first["access_token"])
        second_headers = _auth_headers(second["access_token"])

        first_create = client.post(
            "/api/knowledge-bases",
            json={"name": "Shared Name", "description": "first"},
            headers=first_headers,
        )
        second_create = client.post(
            "/api/knowledge-bases",
            json={"name": "Shared Name", "description": "second"},
            headers=second_headers,
        )

        assert first_create.status_code == 201
        assert second_create.status_code == 201
        assert first_create.json()["id"] != second_create.json()["id"]

        first_list = client.get("/api/knowledge-bases", headers=first_headers)
        second_list = client.get("/api/knowledge-bases", headers=second_headers)
        assert first_list.status_code == 200
        assert second_list.status_code == 200
        assert [item["id"] for item in first_list.json()] == [first_create.json()["id"]]
        assert [item["id"] for item in second_list.json()] == [second_create.json()["id"]]

        forbidden = client.get(
            f"/api/knowledge-bases/{first_create.json()['id']}/documents",
            headers=second_headers,
        )
        assert forbidden.status_code == 404


def test_disabled_user_cannot_login_or_use_existing_token(monkeypatch, tmp_path: Path) -> None:
    _prepare_state(monkeypatch, tmp_path)

    from app.main import create_app

    with TestClient(create_app()) as client:
        admin = client.post(
            "/api/auth/register",
            json={"username": "admin", "password": "secret123"},
        ).json()
        user_response = client.post(
            "/api/auth/register",
            json={"username": "alice", "password": "secret123"},
        ).json()

        admin_headers = _auth_headers(admin["access_token"])
        user_headers = _auth_headers(user_response["access_token"])

        disable = client.patch(
            f"/api/users/{user_response['user']['id']}",
            json={"is_active": False},
            headers=admin_headers,
        )
        assert disable.status_code == 200
        assert disable.json()["is_active"] is False

        me = client.get("/api/auth/me", headers=user_headers)
        assert me.status_code == 401

        login = client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "secret123"},
        )
        assert login.status_code == 401
        assert login.json()["detail"] == "账号已被禁用。"


def test_login_rate_limit_blocks_repeated_failures(monkeypatch, tmp_path: Path) -> None:
    _prepare_state(monkeypatch, tmp_path)

    from app.main import create_app

    with TestClient(create_app()) as client:
        client.post("/api/auth/register", json={"username": "admin", "password": "secret123"})
        for _ in range(5):
            response = client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "wrong123"},
            )
            assert response.status_code == 401

        blocked = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "wrong123"},
        )
        assert blocked.status_code == 429


def test_admin_cannot_remove_last_active_admin_or_disable_self(monkeypatch, tmp_path: Path) -> None:
    _prepare_state(monkeypatch, tmp_path)

    from app.main import create_app

    with TestClient(create_app()) as client:
        admin = client.post(
            "/api/auth/register",
            json={"username": "admin", "password": "secret123"},
        ).json()
        admin_headers = _auth_headers(admin["access_token"])
        admin_id = admin["user"]["id"]

        disable_self = client.patch(
            f"/api/users/{admin_id}",
            json={"is_active": False},
            headers=admin_headers,
        )
        assert disable_self.status_code == 400

        demote_last_admin = client.patch(
            f"/api/users/{admin_id}",
            json={"role": "user"},
            headers=admin_headers,
        )
        assert demote_last_admin.status_code == 400


def test_admin_can_demote_another_admin_when_one_remains(monkeypatch, tmp_path: Path) -> None:
    _prepare_state(monkeypatch, tmp_path)

    from app.db.database import get_session_factory
    from app.db.models import User
    from app.main import create_app

    with TestClient(create_app()) as client:
        admin = client.post(
            "/api/auth/register",
            json={"username": "admin", "password": "secret123"},
        ).json()
        admin_headers = _auth_headers(admin["access_token"])
        second = client.post(
            "/api/auth/register",
            json={"username": "second", "password": "secret123"},
        ).json()

        db = get_session_factory()()
        try:
            user = db.get(User, second["user"]["id"])
            user.role = "admin"
            db.commit()
        finally:
            db.close()

        demote = client.patch(
            f"/api/users/{second['user']['id']}",
            json={"role": "user"},
            headers=admin_headers,
        )
        assert demote.status_code == 200
        assert demote.json()["role"] == "user"


def test_chat_pro_artifact_download_is_owner_scoped(monkeypatch, tmp_path: Path) -> None:
    _prepare_state(monkeypatch, tmp_path)

    from app.main import create_app

    with TestClient(create_app()) as client:
        owner = client.post(
            "/api/auth/register",
            json={"username": "admin", "password": "secret123"},
        ).json()
        other_user = client.post(
            "/api/auth/register",
            json={"username": "alice", "password": "secret123"},
        ).json()

        owner_headers = _auth_headers(owner["access_token"])
        other_headers = _auth_headers(other_user["access_token"])

        created = client.post(
            "/api/knowledge-bases",
            json={"name": "IDL KB", "description": "artifact test"},
            headers=owner_headers,
        )
        assert created.status_code == 201
        kb_id = created.json()["id"]

        asked = client.post(
            "/api/chat/ask",
            json={
                "knowledge_base_id": kb_id,
                "question": "请生成 load_scene 的 .pro 示例",
                "generate_pro_file": True,
            },
            headers=owner_headers,
        )
        assert asked.status_code == 200

        payload = asked.json()
        assert len(payload["messages"]) == 2
        artifact = payload["messages"][-1]["artifacts"][0]
        assert artifact["file_name"] == "load_scene.pro"

        listed = client.get(f"/api/chat/sessions/{payload['session_id']}/messages", headers=owner_headers)
        assert listed.status_code == 200
        assert listed.json()[-1]["artifacts"][0]["id"] == artifact["id"]

        downloaded = client.get(f"/api{artifact['download_url']}", headers=owner_headers)
        assert downloaded.status_code == 200
        assert artifact["file_name"] in downloaded.headers["content-disposition"]
        assert downloaded.content.decode("utf-8") == (
            "; Generated by IDL RAG Panel\n"
            "; Request: 请生成 load_scene 的 .pro 示例\n"
            "\n"
            "pro load_scene\n"
            "  compile_opt idl2\n"
            "\n"
            "  ; TODO: refine the generated logic for your scenario.\n"
            "  print, 'Generated procedure load_scene'\n"
            "end\n"
        )

        denied_messages = client.get(f"/api/chat/sessions/{payload['session_id']}/messages", headers=other_headers)
        assert denied_messages.status_code == 404

        denied_download = client.get(f"/api{artifact['download_url']}", headers=other_headers)
        assert denied_download.status_code == 404
