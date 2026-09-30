from pathlib import Path
from types import SimpleNamespace

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


def _create_artifact(owner_user_id: int, code: str = "pro demo_run\n  compile_opt idl2\nend\n") -> tuple[int, str]:
    from app.core.config import get_app_settings
    from app.db.database import get_session_factory
    from app.db.models import ChatMessage, ChatSession

    db = get_session_factory()()
    try:
        session = ChatSession(owner_user_id=owner_user_id, title="IDL run")
        db.add(session)
        db.commit()
        db.refresh(session)

        artifact_id = "a" * 32
        artifact_dir = get_app_settings().chat_artifacts_dir / f"user-{owner_user_id}" / f"session-{session.id}"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        file_path = artifact_dir / f"{artifact_id}_demo_run.pro"
        file_path.write_text(code, encoding="utf-8")
        message = ChatMessage(
            session_id=session.id,
            role="assistant",
            content="artifact",
            citations_json=[],
            artifacts_json=[
                {
                    "id": artifact_id,
                    "file_name": "demo_run.pro",
                    "media_type": "text/plain",
                    "size": file_path.stat().st_size,
                    "storage_path": file_path.as_posix(),
                    "kind": "pro",
                    "previewable": False,
                }
            ],
        )
        db.add(message)
        db.commit()
        return session.id, artifact_id
    finally:
        db.close()


def test_run_idl_artifact_collects_previewable_image(monkeypatch, tmp_path: Path) -> None:
    _prepare_state(monkeypatch, tmp_path)

    from app.main import create_app
    from app.services import idl_execution_service

    def fake_run(args, cwd, stdout, stderr, timeout, shell):
        run_dir = Path(cwd)
        assert args == ["idl", "-batch", str(run_dir / "__idlrag_runner.pro")]
        assert (run_dir / "source.pro").is_file()
        runner = (run_dir / "__idlrag_runner.pro").read_text(encoding="utf-8")
        assert "output_dir =" in runner
        assert "CD, output_dir" in runner
        assert ".compile '../source.pro'" in runner
        assert "demo_run" in runner
        assert shell is False
        assert timeout == 30
        stdout.write("IDL ok")
        stdout.flush()
        outputs_dir = run_dir / "outputs"
        outputs_dir.joinpath("result.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        run_dir.joinpath("root.png").write_bytes(b"ignored")
        outputs_dir.joinpath("notes.txt").write_text("ignored", encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(idl_execution_service.subprocess, "run", fake_run)

    with TestClient(create_app()) as client:
        registered = _register(client, "owner")
        user_id = registered["user"]["id"]
        session_id, artifact_id = _create_artifact(user_id)
        response = client.post(
            f"/api/chat/sessions/{session_id}/artifacts/{artifact_id}/run-idl",
            json={},
            headers=_auth_headers(registered["access_token"]),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["exit_code"] == 0
    assert payload["stdout"] == "IDL ok"
    assert payload["timed_out"] is False
    assert len(payload["artifacts"]) == 1
    artifact = payload["artifacts"][0]
    assert artifact["file_name"] == "result.png"
    assert artifact["kind"] == "idl_output"
    assert artifact["previewable"] is True
    assert artifact["download_url"].startswith(f"/chat/sessions/{session_id}/artifacts/")
    assert payload["message"]["artifacts"][0]["id"] == artifact["id"]


def test_run_idl_rejects_invalid_entrypoint_before_subprocess(monkeypatch, tmp_path: Path) -> None:
    _prepare_state(monkeypatch, tmp_path)

    from app.main import create_app
    from app.services import idl_execution_service

    called = False

    def fake_run(*args, **kwargs):
        nonlocal called
        called = True
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(idl_execution_service.subprocess, "run", fake_run)

    with TestClient(create_app()) as client:
        registered = _register(client, "owner")
        session_id, artifact_id = _create_artifact(registered["user"]["id"])
        response = client.post(
            f"/api/chat/sessions/{session_id}/artifacts/{artifact_id}/run-idl",
            json={"entrypoint": "demo_run & SPAWN, 'cmd'"},
            headers=_auth_headers(registered["access_token"]),
        )

    assert response.status_code == 400
    assert "入口" in response.json()["detail"]
    assert called is False


def test_run_idl_enforces_session_owner(monkeypatch, tmp_path: Path) -> None:
    _prepare_state(monkeypatch, tmp_path)

    from app.main import create_app

    with TestClient(create_app()) as client:
        owner = _register(client, "owner")
        other = _register(client, "other")
        session_id, artifact_id = _create_artifact(owner["user"]["id"])
        response = client.post(
            f"/api/chat/sessions/{session_id}/artifacts/{artifact_id}/run-idl",
            json={},
            headers=_auth_headers(other["access_token"]),
        )

    assert response.status_code == 404


def test_run_idl_returns_clear_error_when_executable_missing(monkeypatch, tmp_path: Path) -> None:
    _prepare_state(monkeypatch, tmp_path)

    from app.main import create_app
    from app.services import idl_execution_service

    def fake_run(*args, **kwargs):
        raise FileNotFoundError("idl")

    monkeypatch.setattr(idl_execution_service.subprocess, "run", fake_run)

    with TestClient(create_app()) as client:
        registered = _register(client, "owner")
        session_id, artifact_id = _create_artifact(registered["user"]["id"])
        response = client.post(
            f"/api/chat/sessions/{session_id}/artifacts/{artifact_id}/run-idl",
            json={},
            headers=_auth_headers(registered["access_token"]),
        )

    assert response.status_code == 400
    assert "IDL 可执行文件不可用" in response.json()["detail"]


def test_run_idl_timeout_returns_log_message(monkeypatch, tmp_path: Path) -> None:
    _prepare_state(monkeypatch, tmp_path)

    from app.main import create_app
    from app.services import idl_execution_service

    def fake_run(*args, **kwargs):
        kwargs["stdout"].write("partial")
        kwargs["stdout"].flush()
        raise idl_execution_service.subprocess.TimeoutExpired(cmd="idl", timeout=30)

    monkeypatch.setattr(idl_execution_service.subprocess, "run", fake_run)

    with TestClient(create_app()) as client:
        registered = _register(client, "owner")
        session_id, artifact_id = _create_artifact(registered["user"]["id"])
        response = client.post(
            f"/api/chat/sessions/{session_id}/artifacts/{artifact_id}/run-idl",
            json={},
            headers=_auth_headers(registered["access_token"]),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["timed_out"] is True
    assert payload["exit_code"] is None
    assert payload["stdout"] == "partial"
    assert "超过 30 秒" in payload["stderr"]
    assert "IDL 运行超时" in payload["message"]["content"]


def test_run_idl_filters_output_files_and_limits_count(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("IDLRAG_IDL_RUN_MAX_OUTPUT_FILES", "1")
    _prepare_state(monkeypatch, tmp_path)

    from app.main import create_app
    from app.services import idl_execution_service

    def fake_run(args, cwd, stdout, stderr, timeout, shell):
        outputs_dir = Path(cwd) / "outputs"
        outputs_dir.joinpath("a.png").write_bytes(b"png")
        outputs_dir.joinpath("b.jpg").write_bytes(b"jpg")
        outputs_dir.joinpath("notes.txt").write_text("ignored", encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(idl_execution_service.subprocess, "run", fake_run)

    with TestClient(create_app()) as client:
        registered = _register(client, "owner")
        session_id, artifact_id = _create_artifact(registered["user"]["id"])
        response = client.post(
            f"/api/chat/sessions/{session_id}/artifacts/{artifact_id}/run-idl",
            json={},
            headers=_auth_headers(registered["access_token"]),
        )

    assert response.status_code == 200
    artifacts = response.json()["artifacts"]
    assert len(artifacts) == 1
    assert artifacts[0]["file_name"] == "a.png"


def test_run_idl_output_download_enforces_owner(monkeypatch, tmp_path: Path) -> None:
    _prepare_state(monkeypatch, tmp_path)

    from app.main import create_app
    from app.services import idl_execution_service

    def fake_run(args, cwd, stdout, stderr, timeout, shell):
        (Path(cwd) / "outputs" / "result.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(idl_execution_service.subprocess, "run", fake_run)

    with TestClient(create_app()) as client:
        owner = _register(client, "owner")
        other = _register(client, "other")
        session_id, artifact_id = _create_artifact(owner["user"]["id"])
        run_response = client.post(
            f"/api/chat/sessions/{session_id}/artifacts/{artifact_id}/run-idl",
            json={},
            headers=_auth_headers(owner["access_token"]),
        )
        output_artifact = run_response.json()["artifacts"][0]
        owner_download = client.get(
            f"/api{output_artifact['download_url']}",
            headers=_auth_headers(owner["access_token"]),
        )
        other_download = client.get(
            f"/api{output_artifact['download_url']}",
            headers=_auth_headers(other["access_token"]),
        )

    assert run_response.status_code == 200
    assert owner_download.status_code == 200
    assert owner_download.content.startswith(b"\x89PNG")
    assert other_download.status_code == 404


def test_run_idl_stages_input_artifacts(monkeypatch, tmp_path: Path) -> None:
    _prepare_state(monkeypatch, tmp_path)

    from app.core.config import get_app_settings
    from app.db.database import get_session_factory
    from app.db.models import ChatMessage, ChatSession
    from app.main import create_app
    from app.services import idl_execution_service

    def fake_run(args, cwd, stdout, stderr, timeout, shell):
        run_dir = Path(cwd)
        staged_input = run_dir / "inputs" / "srtm.tif"
        assert staged_input.is_file()
        assert staged_input.read_bytes() == b"gee-data"
        (run_dir / "outputs" / "result.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(idl_execution_service.subprocess, "run", fake_run)

    with TestClient(create_app()) as client:
        owner = _register(client, "owner")
        owner_id = owner["user"]["id"]
        db = get_session_factory()()
        try:
            session = ChatSession(owner_user_id=owner_id, title="GEE IDL")
            db.add(session)
            db.commit()
            db.refresh(session)
            artifact_dir = get_app_settings().chat_artifacts_dir / f"user-{owner_id}" / f"session-{session.id}"
            artifact_dir.mkdir(parents=True, exist_ok=True)
            gee_id = "b" * 32
            gee_path = artifact_dir / f"{gee_id}_srtm.tif"
            gee_path.write_bytes(b"gee-data")
            pro_id = "c" * 32
            pro_path = artifact_dir / f"{pro_id}_read_srtm.pro"
            pro_path.write_text("pro read_srtm\n  compile_opt idl2\nend\n", encoding="utf-8")
            db.add(
                ChatMessage(
                    session_id=session.id,
                    role="assistant",
                    content="artifacts",
                    citations_json=[],
                    artifacts_json=[
                        {
                            "id": gee_id,
                            "file_name": "srtm.tif",
                            "media_type": "image/tiff",
                            "size": gee_path.stat().st_size,
                            "storage_path": gee_path.as_posix(),
                            "kind": "gee_data",
                            "previewable": False,
                        },
                        {
                            "id": pro_id,
                            "file_name": "read_srtm.pro",
                            "media_type": "text/plain",
                            "size": pro_path.stat().st_size,
                            "storage_path": pro_path.as_posix(),
                            "kind": "pro",
                            "previewable": False,
                            "input_artifact_ids": [gee_id],
                        },
                    ],
                )
            )
            db.commit()
            session_id = session.id
        finally:
            db.close()
        response = client.post(
            f"/api/chat/sessions/{session_id}/artifacts/{pro_id}/run-idl",
            json={},
            headers=_auth_headers(owner["access_token"]),
        )

    assert response.status_code == 200
    assert response.json()["artifacts"][0]["file_name"] == "result.png"
