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
