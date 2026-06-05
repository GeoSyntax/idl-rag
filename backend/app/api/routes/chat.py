import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.api.schemas import (
    ChatMessageResponse,
    ChatRequest,
    ChatResponse,
    ChatSessionRenameRequest,
    ChatSessionResponse,
    IdlRunRequest,
    IdlRunResponse,
    RetrievalDebugRequest,
    RetrievalDebugResponse,
)
from app.core.config import get_app_settings
from app.db.database import get_db
from app.db.models import ChatSession, KnowledgeBase, User
from app.services.agent_service import AgentService
from app.services.idl_execution_service import IdlExecutionService
from app.services.retrieve_service import RetrievalService

router = APIRouter(prefix="/chat", tags=["chat"])
service = AgentService()
idl_execution_service = IdlExecutionService()
retrieval_service = RetrievalService()

SUPPORTED_UPLOAD_SUFFIXES = {".pdf", ".md", ".markdown", ".txt", ".pro", ".idl"}


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
    try:
        return service.answer(db, payload, current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/ask-stream")
async def ask_question_stream(
    payload: ChatRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> StreamingResponse:
    """真异步 SSE 流式端点 — 使用 AsyncClient 避免阻塞事件循环。"""
    async def event_generator():
        try:
            async for token in service.answer_stream_async(db, payload, current_user.id):
                yield f"data: {json.dumps(token, ensure_ascii=False)}\n\n"
        except ValueError as exc:
            error_event = {"type": "error", "message": str(exc)}
            yield f"data: {json.dumps(error_event, ensure_ascii=False)}\n\n"

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
        try:
            async for event in service.agent_answer_stream_async(db, payload, current_user.id):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except ValueError as exc:
            error_event = {"type": "error", "message": str(exc)}
            yield f"data: {json.dumps(error_event, ensure_ascii=False)}\n\n"

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
