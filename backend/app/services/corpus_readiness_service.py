"""Production gate for knowledge-base material.

The workbench can answer with a fallback vector index or a partially indexed
corpus, but that is not the same as being safe to use for a reproducible
research workflow.  This service keeps that distinction explicit and makes
the gate inspectable from the UI/API instead of relying on a README promise.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db.models import Chunk, Document, EvaluationReport, IndexJob, KnowledgeBase
from app.services.embedding_service import EmbeddingService
from app.services.settings_service import get_runtime_settings

PRODUCTION_REQUIREMENTS = [
    "所有资料必须完成索引（ready），不能有 queued、processing、stale 或 failed 文档",
    "生产知识库不能使用 hash fallback embedding；需要配置可用的语义向量服务并重建索引",
    "每个知识库至少完成一次本地检索评测，确认命中率和引用质量后才能作为生产资料源",
]


def build_corpus_readiness(db: Session, owner_user_id: int) -> dict[str, object]:
    knowledge_bases = db.execute(
        select(KnowledgeBase)
        .where(KnowledgeBase.owner_user_id == owner_user_id)
        .order_by(KnowledgeBase.id)
    ).scalars().all()

    reports: list[dict[str, object]] = []
    overall_blockers: list[str] = []
    for knowledge_base in knowledge_bases:
        status_counts = dict(
            db.execute(
                select(Document.status, func.count(Document.id))
                .where(Document.knowledge_base_id == knowledge_base.id)
                .group_by(Document.status)
            ).all()
        )
        document_count = sum(int(value) for value in status_counts.values())
        ready_count = int(status_counts.get("ready", 0))
        documents = db.query(Document).filter(Document.knowledge_base_id == knowledge_base.id).all()
        missing_source_count = sum(1 for document in documents if not Path(document.file_path).exists())
        fallback_count = int(
            db.execute(
                select(func.count(Document.id)).where(
                    Document.knowledge_base_id == knowledge_base.id,
                    Document.embedding_is_fallback.is_(True),
                )
            ).scalar_one()
        )
        runtime_settings = get_runtime_settings(db)
        embedding_service = EmbeddingService()
        expected_embedding_model = runtime_settings.embedding_model
        expected_embedding_dimensions = embedding_service.get_dimensions()
        expected_index_table = embedding_service.get_table_name(db)
        embedding_mismatch_count = int(
            db.execute(
                select(func.count(Document.id)).where(
                    Document.knowledge_base_id == knowledge_base.id,
                    or_(
                        Document.embedding_model.is_(None),
                        Document.embedding_model != expected_embedding_model,
                        Document.embedding_dimensions.is_(None),
                        Document.embedding_dimensions != expected_embedding_dimensions,
                        Document.index_table.is_(None),
                        Document.index_table != expected_index_table,
                    ),
                )
            ).scalar_one()
        )
        unresolved_failed_job_count = 0
        failed_jobs = (
            db.query(IndexJob)
            .filter(IndexJob.knowledge_base_id == knowledge_base.id, IndexJob.status == "failed")
            .all()
        )
        for failed_job in failed_jobs:
            completed_after = db.execute(
                select(IndexJob.id)
                .where(
                    IndexJob.document_id == failed_job.document_id,
                    IndexJob.status == "completed",
                    IndexJob.id > failed_job.id,
                )
                .limit(1)
            ).scalar_one_or_none()
            if completed_after is None:
                unresolved_failed_job_count += 1
        chunk_count = int(
            db.execute(
                select(func.count(Chunk.id)).where(Chunk.knowledge_base_id == knowledge_base.id)
            ).scalar_one()
        )
        latest_indexed_at = db.execute(
            select(func.max(Document.last_indexed_at)).where(
                Document.knowledge_base_id == knowledge_base.id,
            )
        ).scalar_one()
        latest_eval = db.execute(
            select(EvaluationReport)
            .where(
                EvaluationReport.knowledge_base_id == knowledge_base.id,
                EvaluationReport.report_type == "local",
                EvaluationReport.status == "completed",
            )
            .order_by(EvaluationReport.created_at.desc(), EvaluationReport.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        has_eval = latest_eval is not None
        evaluation_stale = bool(
            latest_eval is not None
            and latest_indexed_at is not None
            and latest_eval.created_at is not None
            and latest_eval.created_at < latest_indexed_at
        )
        if evaluation_stale:
            has_eval = False

        blockers: list[str] = []
        non_ready_count = document_count - ready_count
        if document_count == 0:
            blockers.append("没有已导入资料")
        if non_ready_count:
            blockers.append(f"{non_ready_count} 份资料尚未完成索引")
        if missing_source_count:
            blockers.append(f"{missing_source_count} 份资料的源文件不存在，无法复核引用")
        if fallback_count:
            blockers.append(f"{fallback_count} 份资料使用 fallback embedding")
        if embedding_mismatch_count:
            blockers.append(f"{embedding_mismatch_count} 份资料的 embedding 模型、维度或索引签名不一致")
        if unresolved_failed_job_count:
            blockers.append(f"{unresolved_failed_job_count} 个索引任务失败且尚未被后续成功任务覆盖")
        if evaluation_stale:
            blockers.append("索引在最近一次评测后发生变化，需要重新评测")
        elif not has_eval:
            blockers.append("尚未完成本地检索评测")

        if blockers:
            overall_blockers.extend([f"{knowledge_base.name}：{blocker}" for blocker in blockers])
        reports.append(
            {
                "knowledge_base_id": knowledge_base.id,
                "knowledge_base_name": knowledge_base.name,
                "document_count": document_count,
                "missing_source_count": missing_source_count,
                "ready_document_count": ready_count,
                "stale_document_count": int(status_counts.get("stale", 0)),
                "failed_document_count": int(status_counts.get("failed", 0)),
                "queued_document_count": int(status_counts.get("queued", 0)),
                "processing_document_count": int(status_counts.get("processing", 0)),
                "fallback_document_count": fallback_count,
                "embedding_mismatch_count": embedding_mismatch_count,
                "unresolved_failed_job_count": unresolved_failed_job_count,
                "expected_embedding_model": expected_embedding_model,
                "expected_embedding_dimensions": expected_embedding_dimensions,
                "expected_index_table": expected_index_table,
                "chunk_count": chunk_count,
                "evaluation_completed": has_eval,
                "evaluation_stale": evaluation_stale,
                "latest_indexed_at": latest_indexed_at,
                "latest_evaluation_at": latest_eval.created_at if latest_eval else None,
                "production_ready": not blockers,
                "blockers": blockers,
            }
        )

    return {
        "generated_at": datetime.now(UTC),
        "status": "ready" if knowledge_bases and not overall_blockers else "blocked",
        "production_ready": bool(knowledge_bases) and not overall_blockers,
        "requirements": PRODUCTION_REQUIREMENTS,
        "blockers": overall_blockers,
        "knowledge_bases": reports,
    }
