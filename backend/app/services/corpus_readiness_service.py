"""Production gate for knowledge-base material.

The workbench can answer with a fallback vector index or a partially indexed
corpus, but that is not the same as being safe to use for a reproducible
research workflow.  This service keeps that distinction explicit and makes
the gate inspectable from the UI/API instead of relying on a README promise.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Chunk, Document, EvaluationReport, KnowledgeBase

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
        fallback_count = int(
            db.execute(
                select(func.count(Document.id)).where(
                    Document.knowledge_base_id == knowledge_base.id,
                    Document.embedding_is_fallback.is_(True),
                )
            ).scalar_one()
        )
        chunk_count = int(
            db.execute(
                select(func.count(Chunk.id)).where(Chunk.knowledge_base_id == knowledge_base.id)
            ).scalar_one()
        )
        has_eval = bool(
            db.execute(
                select(EvaluationReport.id)
                .where(
                    EvaluationReport.knowledge_base_id == knowledge_base.id,
                    EvaluationReport.report_type == "local",
                    EvaluationReport.status == "completed",
                )
                .limit(1)
            ).scalar_one_or_none()
        )

        blockers: list[str] = []
        non_ready_count = document_count - ready_count
        if document_count == 0:
            blockers.append("没有已导入资料")
        if non_ready_count:
            blockers.append(f"{non_ready_count} 份资料尚未完成索引")
        if fallback_count:
            blockers.append(f"{fallback_count} 份资料使用 fallback embedding")
        if not has_eval:
            blockers.append("尚未完成本地检索评测")

        if blockers:
            overall_blockers.extend([f"{knowledge_base.name}：{blocker}" for blocker in blockers])
        reports.append(
            {
                "knowledge_base_id": knowledge_base.id,
                "knowledge_base_name": knowledge_base.name,
                "document_count": document_count,
                "ready_document_count": ready_count,
                "stale_document_count": int(status_counts.get("stale", 0)),
                "failed_document_count": int(status_counts.get("failed", 0)),
                "queued_document_count": int(status_counts.get("queued", 0)),
                "processing_document_count": int(status_counts.get("processing", 0)),
                "fallback_document_count": fallback_count,
                "chunk_count": chunk_count,
                "evaluation_completed": has_eval,
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

