from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def _prepare_state(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("IDLRAG_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("IDLRAG_AUTH_SECRET", "stream-route-test-secret")

    from app.core.config import get_app_settings
    from app.db.database import get_engine, get_session_factory, init_database

    get_app_settings.cache_clear()
    get_engine.cache_clear()
    get_session_factory.cache_clear()
    init_database()

    from app.api.routes.auth import _clear_register_rate_limits

    _clear_register_rate_limits()


def _register(client: TestClient, username: str = "stream-reviewer") -> dict:
    response = client.post(
        "/api/auth/register",
        json={"username": username, "password": "secret123"},
    )
    assert response.status_code == 201
    return response.json()


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _sse_payloads(body: str) -> list[dict]:
    payloads: list[dict] = []
    for line in body.splitlines():
        if line.startswith("data: "):
            payloads.append(json.loads(line.removeprefix("data: ")))
    return payloads


def test_agent_stream_converts_backend_failure_to_one_sse_error(
    monkeypatch, tmp_path: Path
) -> None:
    """A provider/tool failure must be a single terminal SSE error, not a second answer."""
    _prepare_state(monkeypatch, tmp_path)

    from app.api.routes import chat
    from app.main import create_app

    async def broken_stream(db, payload, user_id):
        yield {"type": "status", "status": "thinking"}
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(chat.service, "agent_answer_stream_async", broken_stream)

    with TestClient(create_app()) as client:
        registered = _register(client)
        response = client.post(
            "/api/chat/agent-stream",
            json={"question": "测试流式错误"},
            headers=_headers(registered["access_token"]),
        )

    assert response.status_code == 200
    payloads = _sse_payloads(response.text)
    assert [payload["type"] for payload in payloads] == ["status", "error"]
    assert payloads[-1]["message"] == "provider unavailable"
    assert "answer" not in response.text


def test_agent_stream_suppresses_events_after_terminal_done(
    monkeypatch, tmp_path: Path
) -> None:
    """A late provider failure must not turn one answer into two terminals."""
    _prepare_state(monkeypatch, tmp_path)

    from app.api.routes import chat
    from app.main import create_app

    async def done_then_fail(db, payload, user_id):
        yield {"type": "done", "session_id": 17, "citations": [], "artifacts": []}
        yield {"type": "error", "message": "late provider failure"}
        raise RuntimeError("late provider failure")

    monkeypatch.setattr(chat.service, "agent_answer_stream_async", done_then_fail)

    with TestClient(create_app()) as client:
        registered = _register(client, username="terminal-reviewer")
        response = client.post(
            "/api/chat/agent-stream",
            json={"question": "测试完成事件收束"},
            headers=_headers(registered["access_token"]),
        )

    payloads = _sse_payloads(response.text)
    assert [payload["type"] for payload in payloads] == ["done"]


def test_agent_stream_emits_error_when_provider_ends_without_terminal(
    monkeypatch, tmp_path: Path
) -> None:
    """A clean provider return without done/error is still a failed stream."""
    _prepare_state(monkeypatch, tmp_path)

    from app.api.routes import chat
    from app.main import create_app

    async def truncated_stream(db, payload, user_id):
        yield {"type": "token", "content": "半条回答"}

    monkeypatch.setattr(chat.service, "agent_answer_stream_async", truncated_stream)

    with TestClient(create_app()) as client:
        registered = _register(client, username="truncated-reviewer")
        response = client.post(
            "/api/chat/agent-stream",
            json={"question": "测试截断流"},
            headers=_headers(registered["access_token"]),
        )

    payloads = _sse_payloads(response.text)
    assert [payload["type"] for payload in payloads] == ["token", "error"]
    assert payloads[-1]["message"] == "Agent 流式请求未返回完成事件，请重试。"


def test_agent_stream_terminal_event_contains_safe_timing_provenance(
    monkeypatch, tmp_path: Path
) -> None:
    """The UI can correlate one run without receiving private request data."""
    _prepare_state(monkeypatch, tmp_path)

    from app.api.routes import chat
    from app.main import create_app

    async def completed_stream(db, payload, user_id):
        yield {"type": "token", "content": "完成"}
        yield {"type": "done", "session_id": 19, "citations": [], "artifacts": []}

    monkeypatch.setattr(chat.service, "agent_answer_stream_async", completed_stream)
    chat.service.retrieval_service.last_timing = {"retrieve_ms": 12.34, "rerank_ms": 4.56}
    chat.service.llm_service.last_timing = {"first_token_ms": 78.9, "total_ms": 123.4}

    with TestClient(create_app()) as client:
        registered = _register(client, username="provenance-reviewer")
        response = client.post(
            "/api/chat/agent-stream",
            json={"question": "测试运行元数据"},
            headers=_headers(registered["access_token"]),
        )

    payloads = _sse_payloads(response.text)
    terminal = payloads[-1]
    assert terminal["type"] == "done"
    assert len(terminal["stream_id"]) == 12
    assert terminal["server_elapsed_ms"] >= 0
    assert terminal["phase_timing"] == {
        "retrieve_ms": 12.3,
        "rerank_ms": 4.6,
        "llm_first_token_ms": 78.9,
        "llm_total_ms": 123.4,
    }
    assert "测试运行元数据" not in json.dumps(terminal, ensure_ascii=False)
    assert "private" not in json.dumps(terminal, ensure_ascii=False).lower()


def test_agent_stream_persists_run_status_and_safe_correlation(
    monkeypatch, tmp_path: Path
) -> None:
    """A completed stream exposes a replayable, prompt-free run record."""
    _prepare_state(monkeypatch, tmp_path)

    from app.api.routes import chat
    from app.db.database import get_session_factory
    from app.db.models import ChatRequestLog, ChatSession
    from app.main import create_app

    with TestClient(create_app()) as client:
        registered = _register(client, username="run-history-reviewer")
        with get_session_factory()() as db:
            session = ChatSession(owner_user_id=registered["user"]["id"], title="run history")
            db.add(session)
            db.commit()
            db.refresh(session)
            session_id = session.id

        async def completed_stream(db, payload, user_id):
            yield {
                "type": "run_started",
                "session_id": session_id,
                "message_id": 123,
                "retry_context": {
                    "generate_pro_file": True,
                    "input_artifact_ids": ["artifact-1"],
                    "has_attached_file": True,
                    "attached_file_name": "scene.pro",
                },
            }
            yield {"type": "step", "step": "tool_call", "tool": "research_project_context"}
            yield {"type": "done", "session_id": session_id, "citations": [], "artifacts": []}

        monkeypatch.setattr(chat.service, "agent_answer_stream_async", completed_stream)
        response = client.post(
            "/api/chat/agent-stream",
            json={"question": "检查运行记录"},
            headers=_headers(registered["access_token"]),
        )

        assert response.status_code == 200
        with get_session_factory()() as db:
            log = (
                db.query(ChatRequestLog)
                .filter(ChatRequestLog.session_id == session_id)
                .order_by(ChatRequestLog.id.desc())
                .first()
            )
            assert log is not None
            assert log.stream_id
            assert log.terminal_status == "completed"
            assert log.agent_step_count == 1
            assert log.error_message is None

        history = client.get(
            f"/api/chat/sessions/{session_id}/runs",
            headers=_headers(registered["access_token"]),
        )
        assert history.status_code == 200
        assert history.json()[0]["terminal_status"] == "completed"
        assert history.json()[0]["agent_step_count"] == 1
        assert history.json()[0]["message_id"] == 123
        assert history.json()[0]["generate_pro_file"] is True
        assert history.json()[0]["input_artifact_ids"] == ["artifact-1"]
        assert history.json()[0]["has_attached_file"] is True
        assert history.json()[0]["attached_file_name"] == "scene.pro"
        assert "检查运行记录" not in history.text

        sessions = client.get("/api/chat/sessions", headers=_headers(registered["access_token"]))
        assert sessions.status_code == 200
        assert sessions.json()[0]["last_mode"] == "agent"


@pytest.mark.asyncio
async def test_agent_stream_persists_cancelled_request_and_does_not_emit_fallback(
    monkeypatch, tmp_path: Path
) -> None:
    """Client disconnect must cancel the route generator without manufacturing an answer."""
    _prepare_state(monkeypatch, tmp_path)

    from app.api.routes import chat
    from app.api.schemas import ChatRequest
    from app.db.database import get_session_factory
    from app.db.models import User

    db = get_session_factory()()
    try:
        user = User(username="cancel-reviewer", password_hash="not-used")
        db.add(user)
        db.commit()
        db.refresh(user)

        persisted: list[dict] = []

        def capture_persist(**kwargs):
            persisted.append(kwargs)

        monkeypatch.setattr(chat, "_persist_chat_request_log", capture_persist)

        async def hanging_stream(db, payload, user_id):
            yield {"type": "token", "text": "尚未完成"}
            await asyncio.sleep(3600)

        monkeypatch.setattr(chat.service, "agent_answer_stream_async", hanging_stream)

        response = await chat.agent_stream(
            ChatRequest(question="测试客户端断开"),
            db,
            user,
        )
        iterator = response.body_iterator
        first_event = await anext(iterator)
        assert json.loads(first_event.removeprefix("data: ").strip())["type"] == "token"

        with pytest.raises(asyncio.CancelledError):
            await iterator.athrow(asyncio.CancelledError())

        assert len(persisted) == 1
        assert persisted[0]["mode"] == "agent-stream"
        assert persisted[0]["has_error"] is True
        assert persisted[0]["citation_count"] == 0
        assert persisted[0]["artifact_count"] == 0
    finally:
        db.close()
