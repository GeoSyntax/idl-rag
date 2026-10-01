import asyncio
import json
import logging
import time
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.api.schemas import (
    ChatMessageResponse,
    ChatRequest,
    ChatResponse,
    ChatRunResponse,
    ChatSessionRenameRequest,
    ChatSessionResponse,
    GeeFetchRequest,
    GeeFetchResponse,
    GeeStatusResponse,
    IdlRunRequest,
    IdlRunResponse,
    RetrievalDebugRequest,
    RetrievalDebugResponse,
)
from app.core.config import get_app_settings
from app.db.database import get_db, get_session_factory
from app.db.models import ChatRequestLog, ChatSession, KnowledgeBase, User
from app.services.agent_service import AgentService
from app.services.gee_service import GeeService
from app.services.idl_execution_service import IdlExecutionService
from app.services.retrieve_service import RetrievalService
from app.services.runtime_metrics import runtime_metrics

router = APIRouter(prefix="/chat", tags=["chat"])
logger = logging.getLogger(__name__)
service = AgentService()
gee_service = GeeService()
idl_execution_service = IdlExecutionService()
retrieval_service = RetrievalService()

SUPPORTED_UPLOAD_SUFFIXES = {".pdf", ".md", ".markdown", ".txt", ".pro", ".idl"}


def _decorate_stream_event(
    event: dict,
    *,
    stream_id: str,
    started_at: float,
    first_token_ms: float | None,
) -> dict:
    """Attach bounded, non-sensitive provenance to one SSE event.

    The identifier is only a correlation handle for the current request; it
    does not contain user, project, path, prompt, or provider credentials.
    Terminal events additionally carry server timing so the UI can explain a
    slow Agent run without exposing internal logs.
    """
    decorated = dict(event)
    decorated.setdefault("stream_id", stream_id)
    if decorated.get("type") in {"done", "error"}:
        decorated["server_elapsed_ms"] = round((time.perf_counter() - started_at) * 1000, 1)
        if first_token_ms is not None:
            decorated["first_token_ms"] = round(first_token_ms, 1)
    return decorated


def _persist_chat_request_log(
    *,
    owner_user_id: int,
    session_id: int | None,
    mode: str,
    payload: ChatRequest,
    retrieve_timing: dict,
    llm_timing: dict,
    total_ms: float,
    citation_count: int,
    artifact_count: int,
    has_error: bool,
    stream_id: str | None = None,
    terminal_status: str | None = None,
    error_message: str | None = None,
    agent_step_count: int = 0,
) -> None:
    """在独立 session 中写入请求日志，不阻塞响应流。"""
    try:
        SessionLocal = get_session_factory()
        with SessionLocal() as log_db:
            log_db.add(ChatRequestLog(
                owner_user_id=owner_user_id,
                session_id=session_id,
                mode=mode,
                strategy=payload.strategy,
                top_k=payload.top_k,
                retrieve_ms=retrieve_timing.get("retrieve_ms"),
                rerank_ms=retrieve_timing.get("rerank_ms"),
                llm_first_token_ms=llm_timing.get("first_token_ms"),
                llm_total_ms=llm_timing.get("total_ms"),
                total_ms=total_ms,
                citation_count=citation_count,
                artifact_count=artifact_count,
                has_error=has_error,
                stream_id=stream_id,
                terminal_status=terminal_status,
                error_message=(error_message or "")[:500] or None,
                agent_step_count=max(0, int(agent_step_count)),
            ))
            log_db.commit()
    except Exception:  # noqa: BLE001
        pass  # 日志写入不应阻断主流程


def _validate_artifact_path(storage_path: str) -> Path:
    """确保附件路径在沙箱目录内，防止路径遍历攻击。

    使用 is_relative_to() 替代 str.startswith()，避免
    /data/generated/chatEvil 误通过 /data/generated/chat 前缀检查。
    """
    resolved = Path(storage_path).resolve()
    sandbox = get_app_settings().chat_artifacts_dir.resolve()
    if not resolved.is_relative_to(sandbox):
        raise HTTPException(status_code=403, detail="访问被拒绝。")
    return resolved


@router.get("/gee/status", response_model=GeeStatusResponse)
def gee_status(
    current_user: User = Depends(get_current_user),
) -> GeeStatusResponse:
    return gee_service.status()


@router.post("/gee/fetch", response_model=GeeFetchResponse)
def fetch_gee_data(
    payload: GeeFetchRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> GeeFetchResponse:
    try:
        return gee_service.fetch_data(db, payload, current_user.id)
    except ValueError as exc:
        message = str(exc)
        status_code = 404 if "不存在" in message else 400
        raise HTTPException(status_code=status_code, detail=message) from exc


@router.post("/retrieve-debug", response_model=RetrievalDebugResponse)
def retrieve_debug(
    payload: RetrievalDebugRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> RetrievalDebugResponse:
    owned_ids = set(
        db.scalars(
            select(KnowledgeBase.id).where(
                KnowledgeBase.owner_user_id == current_user.id,
                KnowledgeBase.id.in_(payload.knowledge_base_ids),
            )
        )
    )
    if owned_ids != set(payload.knowledge_base_ids):
        raise HTTPException(status_code=404, detail="知识库不存在。")
    return retrieval_service.debug_search(
        db,
        payload.knowledge_base_ids,
        payload.query,
        payload.strategy,
        top_k=payload.top_k,
    )


@router.post("/ask", response_model=ChatResponse)
def ask_question(
    payload: ChatRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ChatResponse:
    started_at = time.perf_counter()
    try:
        response = service.answer(db, payload, current_user.id)
    except ValueError as exc:
        log = ChatRequestLog(
            owner_user_id=current_user.id,
            mode="ask",
            strategy=payload.strategy,
            top_k=payload.top_k,
            retrieve_ms=service.retrieval_service.last_timing.get("retrieve_ms"),
            total_ms=(time.perf_counter() - started_at) * 1000,
            citation_count=0,
            artifact_count=0,
            has_error=True,
            terminal_status="failed",
            error_message=str(exc),
        )
        db.add(log)
        db.commit()
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    log = ChatRequestLog(
        owner_user_id=current_user.id,
        session_id=getattr(response, "session_id", None),
        mode="ask",
        strategy=payload.strategy,
        top_k=payload.top_k,
        retrieve_ms=service.retrieval_service.last_timing.get("retrieve_ms"),
        rerank_ms=service.retrieval_service.last_timing.get("rerank_ms"),
        llm_first_token_ms=service.llm_service.last_timing.get("first_token_ms"),
        llm_total_ms=service.llm_service.last_timing.get("total_ms"),
        total_ms=(time.perf_counter() - started_at) * 1000,
        citation_count=len(response.citations),
        artifact_count=len(response.messages[-1].artifacts) if response.messages else 0,
        has_error=False,
        terminal_status="completed",
    )
    db.add(log)
    db.commit()
    runtime_metrics.record_chat_request(
        current_user.id,
        latency_ms=(time.perf_counter() - started_at) * 1000,
    )
    return response


@router.post("/ask-stream")
async def ask_question_stream(
    payload: ChatRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> StreamingResponse:
    """真异步 SSE 流式端点 — 使用 AsyncClient 避免阻塞事件循环。"""
    async def event_generator():
        started_at = time.perf_counter()
        stream_id = uuid4().hex[:12]
        first_token_ms: float | None = None
        has_error = False
        terminal_sent = False
        citation_count = 0
        artifact_count = 0
        result_session_id = None
        terminal_status = "running"
        error_message = None
        agent_step_count = 0
        try:
            async for token in service.answer_stream_async(db, payload, current_user.id):
                if terminal_sent:
                    continue
                if token.get("type") == "run_started":
                    result_session_id = token.get("session_id")
                if token.get("type") == "step":
                    agent_step_count += 1
                if token.get("type") == "token" and first_token_ms is None:
                    first_token_ms = (time.perf_counter() - started_at) * 1000
                if token.get("type") == "error":
                    has_error = True
                    terminal_status = "failed"
                    error_message = token.get("message") or "流式请求失败"
                if token.get("type") == "done":
                    terminal_sent = True
                    terminal_status = "completed"
                    result_session_id = token.get("session_id")
                    citation_count = len(token.get("citations", []))
                    artifact_count = len(token.get("artifacts", []))
                if token.get("type") == "error":
                    terminal_sent = True
                yield f"data: {json.dumps(_decorate_stream_event(token, stream_id=stream_id, started_at=started_at, first_token_ms=first_token_ms), ensure_ascii=False)}\n\n"
        except asyncio.CancelledError:
            has_error = True
            terminal_status = "cancelled"
            error_message = "客户端取消了流式请求。"
            raise
        except Exception as exc:  # noqa: BLE001
            has_error = True
            terminal_status = "failed"
            error_message = str(exc) or "流式请求失败"
            logger.exception("ask-stream failed")
            if not terminal_sent:
                error_event = {"type": "error", "message": str(exc) or "流式请求失败"}
                terminal_sent = True
                yield f"data: {json.dumps(_decorate_stream_event(error_event, stream_id=stream_id, started_at=started_at, first_token_ms=first_token_ms), ensure_ascii=False)}\n\n"
        else:
            if not terminal_sent:
                has_error = True
                terminal_status = "failed"
                error_message = "流式请求未返回完成事件，请重试。"
                error_event = {"type": "error", "message": "流式请求未返回完成事件，请重试。"}
                yield f"data: {json.dumps(_decorate_stream_event(error_event, stream_id=stream_id, started_at=started_at, first_token_ms=first_token_ms), ensure_ascii=False)}\n\n"
        finally:
            runtime_metrics.record_chat_request(
                current_user.id,
                latency_ms=(time.perf_counter() - started_at) * 1000,
                first_token_ms=first_token_ms,
            )
            _persist_chat_request_log(
                owner_user_id=current_user.id,
                session_id=result_session_id,
                mode="ask-stream",
                payload=payload,
                retrieve_timing=service.retrieval_service.last_timing,
                llm_timing=service.llm_service.last_timing,
                total_ms=(time.perf_counter() - started_at) * 1000,
                citation_count=citation_count,
                artifact_count=artifact_count,
                has_error=has_error,
                stream_id=stream_id,
                terminal_status=terminal_status,
                error_message=error_message,
                agent_step_count=agent_step_count,
            )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/agent-stream")
async def agent_stream(
    payload: ChatRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> StreamingResponse:
    """Agent 模式 SSE 端点 — 支持任务拆解、工具调用、多轮迭代。"""
    async def event_generator():
        started_at = time.perf_counter()
        stream_id = uuid4().hex[:12]
        first_token_ms: float | None = None
        has_error = False
        terminal_sent = False
        citation_count = 0
        artifact_count = 0
        result_session_id = None
        terminal_status = "running"
        error_message = None
        agent_step_count = 0
        try:
            async for event in service.agent_answer_stream_async(db, payload, current_user.id):
                if terminal_sent:
                    continue
                if event.get("type") == "run_started":
                    result_session_id = event.get("session_id")
                if event.get("type") == "step":
                    agent_step_count += 1
                if event.get("type") == "token" and first_token_ms is None:
                    first_token_ms = (time.perf_counter() - started_at) * 1000
                if event.get("type") == "error":
                    has_error = True
                    terminal_status = "failed"
                    error_message = event.get("message") or "Agent 流式请求失败"
                if event.get("type") == "done":
                    terminal_sent = True
                    terminal_status = "completed"
                    result_session_id = event.get("session_id")
                    citation_count = len(event.get("citations", []))
                    artifact_count = len(event.get("artifacts", []))
                if event.get("type") == "error":
                    terminal_sent = True
                yield f"data: {json.dumps(_decorate_stream_event(event, stream_id=stream_id, started_at=started_at, first_token_ms=first_token_ms), ensure_ascii=False)}\n\n"
        except asyncio.CancelledError:
            has_error = True
            terminal_status = "cancelled"
            error_message = "客户端取消了 Agent 流式请求。"
            raise
        except Exception as exc:  # noqa: BLE001
            has_error = True
            terminal_status = "failed"
            error_message = str(exc) or "Agent 流式请求失败"
            logger.exception("agent-stream failed")
            if not terminal_sent:
                error_event = {"type": "error", "message": str(exc) or "Agent 流式请求失败"}
                terminal_sent = True
                yield f"data: {json.dumps(_decorate_stream_event(error_event, stream_id=stream_id, started_at=started_at, first_token_ms=first_token_ms), ensure_ascii=False)}\n\n"
        else:
            if not terminal_sent:
                has_error = True
                terminal_status = "failed"
                error_message = "Agent 流式请求未返回完成事件，请重试。"
                error_event = {"type": "error", "message": "Agent 流式请求未返回完成事件，请重试。"}
                yield f"data: {json.dumps(_decorate_stream_event(error_event, stream_id=stream_id, started_at=started_at, first_token_ms=first_token_ms), ensure_ascii=False)}\n\n"
        finally:
            runtime_metrics.record_chat_request(
                current_user.id,
                latency_ms=(time.perf_counter() - started_at) * 1000,
                first_token_ms=first_token_ms,
            )
            _persist_chat_request_log(
                owner_user_id=current_user.id,
                session_id=result_session_id,
                mode="agent-stream",
                payload=payload,
                retrieve_timing=service.retrieval_service.last_timing,
                llm_timing=service.llm_service.last_timing,
                total_ms=(time.perf_counter() - started_at) * 1000,
                citation_count=citation_count,
                artifact_count=artifact_count,
                has_error=has_error,
                stream_id=stream_id,
                terminal_status=terminal_status,
                error_message=error_message,
                agent_step_count=agent_step_count,
            )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/sessions/{session_id}/messages", response_model=list[ChatMessageResponse])
def list_messages(
    session_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ChatMessageResponse]:
    try:
        return service.list_messages(db, session_id, current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/sessions/{session_id}/artifacts/{artifact_id}")
def download_artifact(
    session_id: int,
    artifact_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> FileResponse:
    try:
        file_path, file_name, media_type = service.get_artifact_file(db, session_id, artifact_id, current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    validated_path = _validate_artifact_path(str(file_path))
    return FileResponse(path=validated_path, media_type=media_type, filename=file_name)


@router.post("/sessions/{session_id}/artifacts/{artifact_id}/run-idl", response_model=IdlRunResponse)
def run_artifact_with_idl(
    session_id: int,
    artifact_id: str,
    payload: IdlRunRequest | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> IdlRunResponse:
    try:
        request = payload or IdlRunRequest()
        return idl_execution_service.run_artifact(
            db,
            session_id,
            artifact_id,
            current_user.id,
            entrypoint=request.entrypoint,
            timeout_seconds=request.timeout_seconds,
            input_artifact_ids=request.input_artifact_ids,
        )
    except ValueError as exc:
        message = str(exc)
        status_code = 404 if "不存在" in message else 400
        raise HTTPException(status_code=status_code, detail=message) from exc


@router.post("/upload-temp")
async def upload_temp_file(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
) -> dict:
    """接收上传文件，解析文本内容返回。用于对话中附带文件上下文。"""
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in SUPPORTED_UPLOAD_SUFFIXES:
        raise HTTPException(status_code=400, detail=f"不支持的文件格式：{suffix}")

    content_bytes = await file.read()
    if len(content_bytes) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="文件大小不能超过 10MB")

    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
            import io
            reader = PdfReader(io.BytesIO(content_bytes))
            text = "\n\n".join(page.extract_text() or "" for page in reader.pages).strip()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"PDF 解析失败：{exc}") from exc
    else:
        text = content_bytes.decode("utf-8", errors="ignore").strip()

    return {"file_name": file.filename, "content": text[:50000]}


@router.get("/sessions", response_model=list[ChatSessionResponse])
def list_sessions(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ChatSessionResponse]:
    """列出当前用户的所有对话会话，按最近活跃时间倒序。"""
    sessions = (
        db.query(ChatSession)
        .filter(ChatSession.owner_user_id == current_user.id)
        .order_by(ChatSession.created_at.desc())
        .all()
    )
    return [ChatSessionResponse.model_validate(s) for s in sessions]


@router.get("/sessions/{session_id}/runs", response_model=list[ChatRunResponse])
def list_chat_runs(
    session_id: int,
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ChatRunResponse]:
    """Return safe terminal history for one chat session.

    Request logs intentionally contain no prompt or document content. Older
    rows created before terminal_status existed are mapped conservatively from
    ``has_error`` so existing installations remain readable after migration.
    """
    session = db.get(ChatSession, session_id)
    if session is None or session.owner_user_id != current_user.id:
        raise HTTPException(status_code=404, detail="会话不存在。")
    logs = (
        db.query(ChatRequestLog)
        .filter(
            ChatRequestLog.owner_user_id == current_user.id,
            ChatRequestLog.session_id == session_id,
        )
        .order_by(ChatRequestLog.created_at.desc(), ChatRequestLog.id.desc())
        .limit(limit)
        .all()
    )
    runs: list[ChatRunResponse] = []
    for log in logs:
        status = log.terminal_status or ("failed" if log.has_error else "completed")
        if status not in {"completed", "failed", "cancelled"}:
            status = "unknown"
        runs.append(ChatRunResponse(
            id=log.id,
            session_id=log.session_id,
            mode=log.mode,
            stream_id=log.stream_id,
            terminal_status=status,
            total_ms=float(log.total_ms or 0),
            llm_first_token_ms=log.llm_first_token_ms,
            citation_count=log.citation_count,
            artifact_count=log.artifact_count,
            agent_step_count=log.agent_step_count,
            error_message=log.error_message,
            created_at=log.created_at,
        ))
    return runs


@router.delete("/sessions/{session_id}", status_code=204)
def delete_session(
    session_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    """删除指定会话及其所有消息。"""
    session = db.get(ChatSession, session_id)
    if session is None or session.owner_user_id != current_user.id:
        raise HTTPException(status_code=404, detail="会话不存在。")
    db.delete(session)
    db.commit()


@router.patch("/sessions/{session_id}", response_model=ChatSessionResponse)
def rename_session(
    session_id: int,
    payload: ChatSessionRenameRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ChatSessionResponse:
    """重命名会话。"""
    session = db.get(ChatSession, session_id)
    if session is None or session.owner_user_id != current_user.id:
        raise HTTPException(status_code=404, detail="会话不存在。")
    session.title = payload.title
    db.commit()
    db.refresh(session)
    return ChatSessionResponse.model_validate(session)
