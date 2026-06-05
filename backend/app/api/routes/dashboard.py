from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.api.schemas import DashboardSummaryResponse
from app.db.database import get_db
from app.db.models import ChatSession, Chunk, Document, EvaluationReport, IndexJob, KnowledgeBase, User
from app.services.embedding_service import get_embedding_status
from app.services.runtime_metrics import runtime_metrics

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
    chunk_count = db.execute(
        select(func.count(Chunk.id))
        .join(KnowledgeBase, KnowledgeBase.id == Chunk.knowledge_base_id)
        .where(KnowledgeBase.owner_user_id == current_user.id)
    ).scalar_one()
    avg_index_job_seconds = db.execute(
        select((func.avg(func.julianday(IndexJob.finished_at) - func.julianday(IndexJob.started_at))) * 86400)
        .join(KnowledgeBase, KnowledgeBase.id == IndexJob.knowledge_base_id)
        .where(
            KnowledgeBase.owner_user_id == current_user.id,
            IndexJob.status == "completed",
            IndexJob.started_at.is_not(None),
            IndexJob.finished_at.is_not(None),
        )
    ).scalar_one_or_none()
    latest_eval = db.execute(
        select(EvaluationReport)
        .join(KnowledgeBase, KnowledgeBase.id == EvaluationReport.knowledge_base_id)
        .where(KnowledgeBase.owner_user_id == current_user.id, EvaluationReport.report_type == "local")
        .order_by(EvaluationReport.created_at.desc(), EvaluationReport.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    chat_session_count = db.execute(
        select(func.count(ChatSession.id)).where(ChatSession.owner_user_id == current_user.id)
    ).scalar_one()
    chat_metrics = runtime_metrics.chat_summary(current_user.id)
    document_count = sum(int(value) for value in document_status_counts.values())
    worker = getattr(request.app.state, "index_worker", None)
    worker_state = getattr(request.app.state, "index_worker_state", {}) or {}
    embedding_status = get_embedding_status()
    latest_eval_summary = latest_eval.summary_json if latest_eval is not None else {}
    return DashboardSummaryResponse(
        knowledge_base_count=int(knowledge_base_count),
        document_count=document_count,
        ready_document_count=int(document_status_counts.get("ready", 0)),
        queued_document_count=int(document_status_counts.get("queued", 0)),
        processing_document_count=int(document_status_counts.get("processing", 0)),
        stale_document_count=int(document_status_counts.get("stale", 0)),
        failed_document_count=int(document_status_counts.get("failed", 0)),
        fallback_document_count=int(fallback_document_count),
        chunk_count=int(chunk_count),
        avg_chunks_per_document=(int(chunk_count) / document_count) if document_count else None,
        avg_index_job_seconds=float(avg_index_job_seconds) if avg_index_job_seconds is not None else None,
        queued_index_job_count=int(job_status_counts.get("queued", 0)),
        processing_index_job_count=int(job_status_counts.get("processing", 0)),
        failed_index_job_count=int(job_status_counts.get("failed", 0)),
        worker_alive=bool(worker and worker.is_alive()),
        worker_last_error=worker_state.get("last_error"),
        embedding_fallback_active=bool(embedding_status.get("last_embedding_fallback") or fallback_document_count),
        embedding_last_error=embedding_status.get("last_embedding_error"),
        chat_session_count=int(chat_session_count),
        chat_request_count=int(chat_metrics["chat_request_count"] or 0),
        chat_latency_p95_ms=chat_metrics["chat_latency_p95_ms"],
        chat_latency_p99_ms=chat_metrics["chat_latency_p99_ms"],
        chat_first_token_count=int(chat_metrics["chat_first_token_count"] or 0),
        chat_first_token_p95_ms=chat_metrics["chat_first_token_p95_ms"],
        chat_first_token_p99_ms=chat_metrics["chat_first_token_p99_ms"],
        latest_eval_hit_rate=latest_eval_summary.get("hit_rate") if isinstance(latest_eval_summary.get("hit_rate"), int | float) else None,
        latest_eval_top_k=latest_eval_summary.get("top_k") if isinstance(latest_eval_summary.get("top_k"), int) else None,
        latest_eval_strategy=latest_eval.strategy if latest_eval is not None else None,
    )
