from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.api.schemas import DashboardSummaryResponse
from app.db.database import get_db
from app.db.models import ChatSession, Document, IndexJob, KnowledgeBase, User
from app.services.embedding_service import get_embedding_status

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/summary", response_model=DashboardSummaryResponse)
def dashboard_summary(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DashboardSummaryResponse:
    knowledge_base_count = db.execute(
        select(func.count(KnowledgeBase.id)).where(KnowledgeBase.owner_user_id == current_user.id)
    ).scalar_one()
    document_status_counts = dict(
        db.execute(
            select(Document.status, func.count(Document.id))
            .join(KnowledgeBase, KnowledgeBase.id == Document.knowledge_base_id)
            .where(KnowledgeBase.owner_user_id == current_user.id)
            .group_by(Document.status)
        ).all()
    )
    job_status_counts = dict(
        db.execute(
            select(IndexJob.status, func.count(IndexJob.id))
            .join(KnowledgeBase, KnowledgeBase.id == IndexJob.knowledge_base_id)
            .where(KnowledgeBase.owner_user_id == current_user.id)
            .group_by(IndexJob.status)
        ).all()
    )
    fallback_document_count = db.execute(
        select(func.count(Document.id))
        .join(KnowledgeBase, KnowledgeBase.id == Document.knowledge_base_id)
        .where(KnowledgeBase.owner_user_id == current_user.id, Document.embedding_is_fallback.is_(True))
    ).scalar_one()
    chat_session_count = db.execute(
        select(func.count(ChatSession.id)).where(ChatSession.owner_user_id == current_user.id)
    ).scalar_one()
    worker = getattr(request.app.state, "index_worker", None)
    worker_state = getattr(request.app.state, "index_worker_state", {}) or {}
    embedding_status = get_embedding_status()
    return DashboardSummaryResponse(
        knowledge_base_count=int(knowledge_base_count),
        document_count=sum(int(value) for value in document_status_counts.values()),
        ready_document_count=int(document_status_counts.get("ready", 0)),
        queued_document_count=int(document_status_counts.get("queued", 0)),
        processing_document_count=int(document_status_counts.get("processing", 0)),
        stale_document_count=int(document_status_counts.get("stale", 0)),
        failed_document_count=int(document_status_counts.get("failed", 0)),
        fallback_document_count=int(fallback_document_count),
        queued_index_job_count=int(job_status_counts.get("queued", 0)),
        processing_index_job_count=int(job_status_counts.get("processing", 0)),
        failed_index_job_count=int(job_status_counts.get("failed", 0)),
        worker_alive=bool(worker and worker.is_alive()),
        worker_last_error=worker_state.get("last_error"),
        embedding_fallback_active=bool(embedding_status.get("last_embedding_fallback") or fallback_document_count),
        embedding_last_error=embedding_status.get("last_embedding_error"),
        chat_session_count=int(chat_session_count),
    )
