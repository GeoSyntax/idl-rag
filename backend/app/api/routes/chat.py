import asyncio
import json
import logging
import time
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, UploadFile, File
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.api.schemas import (
    ChatMessageResponse,
    ChatModelStatusResponse,
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
from app.services.settings_service import get_chat_model_status

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
    phase_timing: dict[str, float] | None = None,
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
        if phase_timing:
            decorated["phase_timing"] = phase_timing
    return decorated


def _phase_timing_snapshot() -> dict[str, float]:
    """Expose coarse phase timings on terminal SSE events.

    These values are deliberately limited to duration fields already used by
    request logs. They help the UI distinguish retrieval latency from model
    latency without exposing prompts, paths, provider details, or credentials.
    """
    timing: dict[str, float] = {}
    for key, value in (
        ("retrieve_ms", service.retrieval_service.last_timing.get("retrieve_ms")),
        ("rerank_ms", service.retrieval_service.last_timing.get("rerank_ms")),
        ("llm_first_token_ms", service.llm_service.last_timing.get("first_token_ms")),
        ("llm_total_ms", service.llm_service.last_timing.get("total_ms")),
    ):
        if isinstance(value, (int, float)):
            timing[key] = round(float(value), 1)
    return timing


def _phase_timing_for_log(phase_timing: dict[str, float]) -> tuple[dict, dict]:
    """Translate public Agent phase names to the request-log schema."""
    return (
        {
            key: phase_timing[key]
            for key in ("retrieve_ms", "rerank_ms")
            if key in phase_timing
        },
        {
            "first_token_ms": phase_timing.get("llm_first_token_ms"),
            "total_ms": phase_timing.get("llm_total_ms"),
        },
    )


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
    message_id: int | None = None,
    retry_context: dict | None = None,
) -> None:
    """在独立 session 中写入请求日志，不阻塞响应流。"""
    try:
        SessionLocal = get_session_factory()
        with SessionLocal() as log_db:
            safe_context = dict(retry_context or {})
            safe_context.setdefault("message_id", message_id)
            safe_context.setdefault("generate_pro_file", bool(payload.generate_pro_file))
            safe_context.setdefault("input_artifact_ids", [str(item) for item in payload.input_artifact_ids[:8]])
            safe_context.setdefault("has_attached_file", bool(payload.attached_file_content))
            safe_context.setdefault("attached_file_name", (payload.attached_file_name or "")[:255] or None)
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
                retry_context_json=safe_context,
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


@router.get("/model-status", response_model=ChatModelStatusResponse)
def chat_model_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ChatModelStatusResponse:
    """Expose only the provider/model readiness needed by the workbench."""
    return get_chat_model_status(db)


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
    *,
    background_tasks: BackgroundTasks = None,
) -> ChatResponse:
    background_tasks = background_tasks or BackgroundTasks()
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
    pending_artifact_ids = [
        artifact.id
        for message in response.messages[-1:]
        for artifact in message.artifacts
        if isinstance(artifact.metadata, dict)
        and isinstance(artifact.metadata.get("validation"), dict)
        and artifact.metadata["validation"].get("validation_status") == "pending"
    ]
    if pending_artifact_ids:
        background_tasks.add_task(
            service.validate_pending_artifacts,
            response.session_id,
            current_user.id,
            pending_artifact_ids,
        )
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
    *,
    background_tasks: BackgroundTasks = None,
) -> StreamingResponse:
    """真异步 SSE 流式端点 — 使用 AsyncClient 避免阻塞事件循环。"""
    background_tasks = background_tasks or BackgroundTasks()
    async def event_generator():
        started_at = time.perf_counter()
        stream_id = uuid4().hex[:12]
        first_token_ms: float | None = None
        has_error = False
        terminal_sent = False
        citation_count = 0
        artifact_count = 0
        result_session_id = None
        result_message_id = None
        retry_context: dict = {}
        terminal_status = "running"
        error_message = None
        agent_step_count = 0
        request_phase_timing: dict[str, float] = {}
        request_phase_timing_seen = False
        try:
            async for token in service.answer_stream_async(db, payload, current_user.id):
                if terminal_sent:
                    continue
                if isinstance(token.get("phase_timing"), dict):
                    request_phase_timing = {
                        str(key): float(value)
                        for key, value in token["phase_timing"].items()
                        if isinstance(value, (int, float))
                    }
                    request_phase_timing_seen = True
                if token.get("type") == "run_started":
                    result_session_id = token.get("session_id")
                    result_message_id = token.get("message_id")
                    retry_context = token.get("retry_context") or {}
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
                    pending_artifact_ids = [
                        str(artifact.get("id"))
                        for artifact in token.get("artifacts", [])
                        if isinstance(artifact, dict)
                        and isinstance(artifact.get("metadata"), dict)
                        and isinstance(artifact["metadata"].get("validation"), dict)
                        and artifact["metadata"]["validation"].get("validation_status") == "pending"
                    ]
                    if pending_artifact_ids:
                        background_tasks.add_task(
                            service.validate_pending_artifacts,
                            int(result_session_id),
                            current_user.id,
                            pending_artifact_ids,
                        )
                if token.get("type") == "error":
                    terminal_sent = True
                event_phase_timing = (
                    request_phase_timing if request_phase_timing_seen else _phase_timing_snapshot()
                )
                yield f"data: {json.dumps(_decorate_stream_event(token, stream_id=stream_id, started_at=started_at, first_token_ms=first_token_ms, phase_timing=event_phase_timing), ensure_ascii=False)}\n\n"
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
                event_phase_timing = (
                    request_phase_timing if request_phase_timing_seen else _phase_timing_snapshot()
                )
                yield f"data: {json.dumps(_decorate_stream_event(error_event, stream_id=stream_id, started_at=started_at, first_token_ms=first_token_ms, phase_timing=event_phase_timing), ensure_ascii=False)}\n\n"
        else:
            if not terminal_sent:
                has_error = True
                terminal_status = "failed"
                error_message = "流式请求未返回完成事件，请重试。"
                error_event = {"type": "error", "message": "流式请求未返回完成事件，请重试。"}
                event_phase_timing = (
                    request_phase_timing if request_phase_timing_seen else _phase_timing_snapshot()
                )
                yield f"data: {json.dumps(_decorate_stream_event(error_event, stream_id=stream_id, started_at=started_at, first_token_ms=first_token_ms, phase_timing=event_phase_timing), ensure_ascii=False)}\n\n"
        finally:
            runtime_metrics.record_chat_request(
                current_user.id,
                latency_ms=(time.perf_counter() - started_at) * 1000,
                first_token_ms=first_token_ms,
            )
            request_retrieve_timing, request_llm_timing = _phase_timing_for_log(request_phase_timing)
            _persist_chat_request_log(
                owner_user_id=current_user.id,
                session_id=result_session_id,
                mode="ask-stream",
                payload=payload,
                retrieve_timing=request_retrieve_timing if request_phase_timing_seen else service.retrieval_service.last_timing,
                llm_timing=request_llm_timing if request_phase_timing_seen else service.llm_service.last_timing,
                total_ms=(time.perf_counter() - started_at) * 1000,
                citation_count=citation_count,
                artifact_count=artifact_count,
                has_error=has_error,
                stream_id=stream_id,
                terminal_status=terminal_status,
                error_message=error_message,
                agent_step_count=agent_step_count,
                message_id=result_message_id,
                retry_context=retry_context,
            )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        background=background_tasks,
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
    *,
    background_tasks: BackgroundTasks = None,
) -> StreamingResponse:
    """Agent 模式 SSE 端点 — 支持任务拆解、工具调用、多轮迭代。"""
    background_tasks = background_tasks or BackgroundTasks()
    async def event_generator():
        started_at = time.perf_counter()
        stream_id = uuid4().hex[:12]
        first_token_ms: float | None = None
        has_error = False
        terminal_sent = False
        citation_count = 0
        artifact_count = 0
        result_session_id = None
        result_message_id = None
        retry_context: dict = {}
        terminal_status = "running"
        error_message = None
        agent_step_count = 0
        agent_phase_timing: dict[str, float] = {}
        agent_phase_timing_seen = False
        try:
            async for event in service.agent_answer_stream_async(db, payload, current_user.id):
                if terminal_sent:
                    continue
                if event.get("type") == "run_started":
                    result_session_id = event.get("session_id")
                    result_message_id = event.get("message_id")
                    retry_context = event.get("retry_context") or {}
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
                    pending_artifact_ids = [
                        str(artifact.get("id"))
                        for artifact in event.get("artifacts", [])
                        if isinstance(artifact, dict)
                        and isinstance(artifact.get("metadata"), dict)
                        and isinstance(artifact["metadata"].get("validation"), dict)
                        and artifact["metadata"]["validation"].get("validation_status") == "pending"
                    ]
                    if pending_artifact_ids:
                        background_tasks.add_task(
                            service.validate_pending_artifacts,
                            int(result_session_id),
                            current_user.id,
                            pending_artifact_ids,
                        )
                if isinstance(event.get("phase_timing"), dict):
                    agent_phase_timing = {
                        str(key): value
                        for key, value in event["phase_timing"].items()
                        if isinstance(value, (int, float))
                    }
                    agent_phase_timing_seen = True
                if event.get("type") == "error":
                    terminal_sent = True
                event_phase_timing = event.get("phase_timing") if isinstance(event.get("phase_timing"), dict) else _phase_timing_snapshot()
                yield f"data: {json.dumps(_decorate_stream_event(event, stream_id=stream_id, started_at=started_at, first_token_ms=first_token_ms, phase_timing=event_phase_timing), ensure_ascii=False)}\n\n"
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
                yield f"data: {json.dumps(_decorate_stream_event(error_event, stream_id=stream_id, started_at=started_at, first_token_ms=first_token_ms, phase_timing=_phase_timing_snapshot()), ensure_ascii=False)}\n\n"
        else:
            if not terminal_sent:
                has_error = True
                terminal_status = "failed"
                error_message = "Agent 流式请求未返回完成事件，请重试。"
                error_event = {"type": "error", "message": "Agent 流式请求未返回完成事件，请重试。"}
                yield f"data: {json.dumps(_decorate_stream_event(error_event, stream_id=stream_id, started_at=started_at, first_token_ms=first_token_ms, phase_timing=_phase_timing_snapshot()), ensure_ascii=False)}\n\n"
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
                retrieve_timing=(
                    _phase_timing_for_log(agent_phase_timing)[0]
                    if agent_phase_timing_seen
                    else service.retrieval_service.last_timing
                ),
                llm_timing=(
                    _phase_timing_for_log(agent_phase_timing)[1]
                    if agent_phase_timing_seen
                    else service.llm_service.last_timing
                ),
                total_ms=(time.perf_counter() - started_at) * 1000,
                citation_count=citation_count,
                artifact_count=artifact_count,
                has_error=has_error,
                stream_id=stream_id,
                terminal_status=terminal_status,
                error_message=error_message,
                agent_step_count=agent_step_count,
                message_id=result_message_id,
                retry_context=retry_context,
            )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        background=background_tasks,
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
    session_ids = [session.id for session in sessions]
    latest_modes: dict[int, str] = {}
    if session_ids:
        logs = (
            db.query(ChatRequestLog)
            .filter(
                ChatRequestLog.owner_user_id == current_user.id,
                ChatRequestLog.session_id.in_(session_ids),
            )
            .order_by(ChatRequestLog.created_at.desc(), ChatRequestLog.id.desc())
            .all()
        )
        for log in logs:
            if log.session_id is None or log.session_id in latest_modes:
                continue
            latest_modes[log.session_id] = "agent" if log.mode == "agent-stream" else "normal"
    return [
        ChatSessionResponse.model_validate(session).model_copy(
            update={"last_mode": latest_modes.get(session.id)},
        )
        for session in sessions
    ]


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
        context = log.retry_context_json if isinstance(log.retry_context_json, dict) else {}
        input_ids = context.get("input_artifact_ids")
        if not isinstance(input_ids, list):
            input_ids = []
        runs.append(ChatRunResponse(
            id=log.id,
            session_id=log.session_id,
            mode=log.mode,
            stream_id=log.stream_id,
            terminal_status=status,
            total_ms=float(log.total_ms or 0),
            retrieve_ms=log.retrieve_ms,
            rerank_ms=log.rerank_ms,
            llm_first_token_ms=log.llm_first_token_ms,
            llm_total_ms=log.llm_total_ms,
            citation_count=log.citation_count,
            artifact_count=log.artifact_count,
            agent_step_count=log.agent_step_count,
            error_message=log.error_message,
            message_id=context.get("message_id") if isinstance(context.get("message_id"), int) else None,
            generate_pro_file=bool(context.get("generate_pro_file")),
            input_artifact_ids=[str(item) for item in input_ids[:8]],
            has_attached_file=bool(context.get("has_attached_file")),
            attached_file_name=(str(context.get("attached_file_name"))[:255] or None)
            if context.get("attached_file_name") else None,
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
