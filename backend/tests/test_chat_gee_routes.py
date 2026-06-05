from pathlib import Path

from fastapi.testclient import TestClient


def _prepare_state(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("IDLRAG_BASE_DIR", str(tmp_path))

    from app.core.config import get_app_settings
    from app.db.database import get_engine, get_session_factory, init_database

    get_app_settings.cache_clear()
    get_engine.cache_clear()
    get_session_factory.cache_clear()
    init_database()

    from app.api.routes.auth import _clear_register_rate_limits

    _clear_register_rate_limits()


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _register(client: TestClient, username: str) -> dict:
    response = client.post("/api/auth/register", json={"username": username, "password": "secret123"})
    assert response.status_code == 201
    return response.json()


def test_fetch_gee_data_creates_chat_artifact(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("IDLRAG_GEE_ENABLED", "true")
    monkeypatch.setenv("IDLRAG_GEE_ALLOWED_DATASETS", "CGIAR/SRTM90_V4")
    _prepare_state(monkeypatch, tmp_path)

    from app.main import create_app
    from app.services.gee_service import GeeService

    def fake_download(self, payload):
        return b"fake-geotiff", None

    monkeypatch.setattr(GeeService, "_download_image", fake_download)

    with TestClient(create_app()) as client:
        registered = _register(client, "owner")
        response = client.post(
            "/api/chat/gee/fetch",
            json={
                "dataset_id": "CGIAR/SRTM90_V4",
                "bbox": [116.3, 39.8, 116.4, 39.9],
                "bands": ["elevation"],
                "scale": 90,
                "label": "beijing_srtm",
            },
            headers=_auth_headers(registered["access_token"]),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["session_id"] > 0
    artifact = payload["artifact"]
    assert artifact["file_name"] == "beijing_srtm.tif"
    assert artifact["kind"] == "gee_data"
    assert artifact["previewable"] is False
    assert artifact["metadata"]["dataset_id"] == "CGIAR/SRTM90_V4"
    assert artifact["metadata"]["idl_input_path"] == "../inputs/beijing_srtm.tif"
    assert "storage_path" not in artifact
    assert payload["message"]["artifacts"][0]["id"] == artifact["id"]


def test_fetch_gee_data_requires_enabled(monkeypatch, tmp_path: Path) -> None:
    _prepare_state(monkeypatch, tmp_path)

    from app.main import create_app

    with TestClient(create_app()) as client:
        registered = _register(client, "owner")
        response = client.post(
            "/api/chat/gee/fetch",
            json={
                "dataset_id": "CGIAR/SRTM90_V4",
                "bbox": [116.3, 39.8, 116.4, 39.9],
                "scale": 90,
            },
            headers=_auth_headers(registered["access_token"]),
        )

    assert response.status_code == 400
    assert "GEE 未启用" in response.json()["detail"]


def test_fetch_gee_data_enforces_session_owner(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("IDLRAG_GEE_ENABLED", "true")
    monkeypatch.setenv("IDLRAG_GEE_ALLOWED_DATASETS", "CGIAR/SRTM90_V4")
    _prepare_state(monkeypatch, tmp_path)

    from app.db.database import get_session_factory
    from app.db.models import ChatSession
    from app.main import create_app

    with TestClient(create_app()) as client:
        owner = _register(client, "owner")
        other = _register(client, "other")
        db = get_session_factory()()
        try:
            session = ChatSession(owner_user_id=owner["user"]["id"], title="owner session")
            db.add(session)
            db.commit()
            db.refresh(session)
            session_id = session.id
        finally:
            db.close()
        response = client.post(
            "/api/chat/gee/fetch",
            json={
                "session_id": session_id,
                "dataset_id": "CGIAR/SRTM90_V4",
                "bbox": [116.3, 39.8, 116.4, 39.9],
                "scale": 90,
            },
            headers=_auth_headers(other["access_token"]),
        )

    assert response.status_code == 404


def test_fetch_gee_data_rejects_disallowed_dataset(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("IDLRAG_GEE_ENABLED", "true")
    monkeypatch.setenv("IDLRAG_GEE_ALLOWED_DATASETS", "CGIAR/SRTM90_V4")
    _prepare_state(monkeypatch, tmp_path)

    from app.main import create_app

    with TestClient(create_app()) as client:
        registered = _register(client, "owner")
        response = client.post(
            "/api/chat/gee/fetch",
            json={
                "dataset_id": "USGS/UNKNOWN",
                "bbox": [116.3, 39.8, 116.4, 39.9],
                "scale": 90,
            },
            headers=_auth_headers(registered["access_token"]),
        )

    assert response.status_code == 400
    assert "允许列表" in response.json()["detail"]
