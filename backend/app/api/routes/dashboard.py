from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.api.schemas import DashboardSummaryResponse
from app.db.database import get_db
from app.db.models import ChatRequestLog, ChatSession, Chunk, Document, EvaluationReport, IndexJob, KnowledgeBase, User
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

    # 持久化请求日志聚合
    persisted_count = db.execute(
        select(func.count(ChatRequestLog.id))
        .where(ChatRequestLog.owner_user_id == current_user.id)
    ).scalar_one()

    persisted_avg = {"avg_retrieve_ms": None, "avg_rerank_ms": None, "avg_llm_first_token_ms": None, "avg_total_ms": None}
    persisted_pcts: dict[str, float | None] = {}
    citation_coverage: float | None = None
    error_rate: float | None = None

    if persisted_count >= 1:
        avg_row = db.execute(
            select(
                func.avg(ChatRequestLog.retrieve_ms),
                func.avg(ChatRequestLog.rerank_ms),
                func.avg(ChatRequestLog.llm_first_token_ms),
                func.avg(ChatRequestLog.total_ms),
            ).where(ChatRequestLog.owner_user_id == current_user.id)
        ).one()
        persisted_avg = {
            "avg_retrieve_ms": float(avg_row[0]) if avg_row[0] is not None else None,
            "avg_rerank_ms": float(avg_row[1]) if avg_row[1] is not None else None,
            "avg_llm_first_token_ms": float(avg_row[2]) if avg_row[2] is not None else None,
            "avg_total_ms": float(avg_row[3]) if avg_row[3] is not None else None,
        }

        total_rows = db.execute(
            select(ChatRequestLog.total_ms)
            .where(ChatRequestLog.owner_user_id == current_user.id)
            .order_by(ChatRequestLog.total_ms)
        ).scalars().all()
        if total_rows:
            def _pct(vals: list[float], pct: int) -> float:
                idx = round((pct / 100) * (len(vals) - 1))
                return vals[idx]
            persisted_pcts["p95"] = _pct(total_rows, 95)
            persisted_pcts["p99"] = _pct(total_rows, 99)

        ft_rows = db.execute(
            select(ChatRequestLog.llm_first_token_ms)
            .where(ChatRequestLog.owner_user_id == current_user.id, ChatRequestLog.llm_first_token_ms.is_not(None))
            .order_by(ChatRequestLog.llm_first_token_ms)
        ).scalars().all()
        if ft_rows:
            def _pct2(vals: list[float], pct: int) -> float:
                idx = round((pct / 100) * (len(vals) - 1))
                return vals[idx]
            persisted_pcts["ft_p95"] = _pct2(ft_rows, 95)
            persisted_pcts["ft_p99"] = _pct2(ft_rows, 99)

        with_citations = db.execute(
            select(func.count(ChatRequestLog.id))
            .where(ChatRequestLog.owner_user_id == current_user.id, ChatRequestLog.citation_count > 0)
        ).scalar_one()
        citation_coverage = with_citations / persisted_count

        err_count = db.execute(
            select(func.count(ChatRequestLog.id))
            .where(ChatRequestLog.owner_user_id == current_user.id, ChatRequestLog.has_error.is_(True))
        ).scalar_one()
        error_rate = err_count / persisted_count

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
        chat_request_count=int(persisted_count) if persisted_count else int(chat_metrics["chat_request_count"] or 0),
        chat_latency_p95_ms=persisted_pcts.get("p95") or chat_metrics["chat_latency_p95_ms"],
        chat_latency_p99_ms=persisted_pcts.get("p99") or chat_metrics["chat_latency_p99_ms"],
        chat_first_token_count=int(chat_metrics["chat_first_token_count"] or 0),
        chat_first_token_p95_ms=persisted_pcts.get("ft_p95") or chat_metrics["chat_first_token_p95_ms"],
        chat_first_token_p99_ms=persisted_pcts.get("ft_p99") or chat_metrics["chat_first_token_p99_ms"],
        latest_eval_hit_rate=latest_eval_summary.get("hit_rate") if isinstance(latest_eval_summary.get("hit_rate"), int | float) else None,
        latest_eval_top_k=latest_eval_summary.get("top_k") if isinstance(latest_eval_summary.get("top_k"), int) else None,
        latest_eval_strategy=latest_eval.strategy if latest_eval is not None else None,
        avg_retrieve_ms=persisted_avg["avg_retrieve_ms"],
        avg_rerank_ms=persisted_avg["avg_rerank_ms"],
        avg_llm_first_token_ms=persisted_avg["avg_llm_first_token_ms"],
        avg_total_ms=persisted_avg["avg_total_ms"],
        citation_coverage=citation_coverage,
        error_rate=error_rate,
    )
