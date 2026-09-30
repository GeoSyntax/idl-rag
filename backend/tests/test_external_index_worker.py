import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError


def _prepare_state(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("IDLRAG_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("IDLRAG_INDEX_WORKER_ENABLED", "false")

    from app.core.config import get_app_settings
    from app.db.database import get_engine, get_index_engine, get_index_session_factory, get_session_factory

    get_app_settings.cache_clear()
    get_engine.cache_clear()
    get_index_engine.cache_clear()
    get_session_factory.cache_clear()
    get_index_session_factory.cache_clear()


def _headers(auth: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {auth['access_token']}"}


def test_external_worker_heartbeat_is_reported_and_stale_worker_is_not_healthy(monkeypatch, tmp_path: Path) -> None:
    _prepare_state(monkeypatch, tmp_path)

    from app.core.config import get_app_settings
    from app.main import create_app

    with TestClient(create_app()) as client:
        auth = client.post(
            "/api/auth/register",
            json={"username": "external-worker-user", "password": "secret123"},
        ).json()
        settings = get_app_settings()
        settings.index_worker_heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
        settings.index_worker_heartbeat_path.write_text(
            json.dumps(
                {
                    "mode": "external",
                    "status": "running",
                    "last_heartbeat_at": datetime.now(UTC).isoformat(),
                    "processed_count": 4,
                    "last_error": None,
                }
            ),
            encoding="utf-8",
        )
        healthy = client.get("/api/dashboard/summary", headers=_headers(auth))
        assert healthy.status_code == 200, healthy.text
        assert healthy.json()["worker_mode"] == "external"
        assert healthy.json()["worker_alive"] is True
        ready = client.get("/api/ready")
        assert ready.status_code == 200, ready.text
        assert ready.json()["status"] == "ready"
        assert ready.json()["checks"] == {"database": "ok", "worker": "ok"}

        settings.index_worker_heartbeat_path.write_text(
            json.dumps(
                {
                    "mode": "external",
                    "status": "running",
                    "last_heartbeat_at": (datetime.now(UTC) - timedelta(minutes=10)).isoformat(),
                    "processed_count": 4,
                    "last_error": None,
                }
            ),
            encoding="utf-8",
        )
        stale = client.get("/api/dashboard/summary", headers=_headers(auth))
        assert stale.status_code == 200, stale.text
        assert stale.json()["worker_mode"] == "external"
        assert stale.json()["worker_alive"] is False
        not_ready = client.get("/api/ready")
        assert not_ready.status_code == 503
        assert not_ready.json()["detail"]["checks"]["worker"] == "unhealthy"


def test_standalone_worker_writes_shutdown_heartbeat(monkeypatch, tmp_path: Path) -> None:
    _prepare_state(monkeypatch, tmp_path)

    from app.core.config import get_app_settings
    from app.index_worker import run_index_worker

    stop_event = Event()
    stop_event.set()
    state: dict[str, object] = {}
    run_index_worker(stop_event=stop_event, state=state)

    heartbeat_path = get_app_settings().index_worker_heartbeat_path
    assert heartbeat_path.is_file()
    heartbeat = json.loads(heartbeat_path.read_text(encoding="utf-8"))
    assert heartbeat["mode"] == "external"
    assert heartbeat["status"] == "stopped"


def test_worker_healthcheck_accepts_fresh_and_rejects_stale_or_invalid_heartbeat(monkeypatch, tmp_path: Path) -> None:
    _prepare_state(monkeypatch, tmp_path)

    from app.core.config import get_app_settings
    from app.worker_healthcheck import check_worker_heartbeat

    heartbeat_path = get_app_settings().index_worker_heartbeat_path
    heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
    heartbeat_path.write_text(
        json.dumps({"status": "running", "last_heartbeat_at": datetime.now(UTC).isoformat()}),
        encoding="utf-8",
    )
    check_worker_heartbeat(timeout_seconds=30)

    heartbeat_path.write_text(
        json.dumps(
            {
                "status": "running",
                "last_heartbeat_at": (datetime.now(UTC) - timedelta(minutes=2)).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="stale"):
        check_worker_heartbeat(timeout_seconds=30)

    heartbeat_path.write_text("not-json", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid"):
        check_worker_heartbeat(timeout_seconds=30)


def test_readiness_reports_database_failure(monkeypatch, tmp_path: Path) -> None:
    _prepare_state(monkeypatch, tmp_path)

    from app.core.config import get_app_settings
    from app.db.database import get_db
    from app.main import create_app

    class BrokenSession:
        def execute(self, _statement):
            raise SQLAlchemyError("database unavailable")

    def broken_db():
        yield BrokenSession()

    app = create_app()
    app.dependency_overrides[get_db] = broken_db
    with TestClient(app) as client:
        response = client.get("/api/ready")
    assert response.status_code == 503
    assert response.json()["detail"]["status"] == "degraded"
    assert response.json()["detail"]["checks"]["database"] == "unhealthy"
    get_app_settings.cache_clear()
