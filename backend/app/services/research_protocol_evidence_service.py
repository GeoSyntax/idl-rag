from __future__ import annotations

from sqlalchemy.orm import Session

from app.api.schemas import (
    ResearchProtocolEvidenceMapDraftRequest,
    ResearchProtocolEvidenceMapDraftResponse,
    ResearchProtocolEvidenceMapEntry,
)
from app.services.research_rag_service import ResearchRagService
from app.services.research_service import ResearchService


class ResearchProtocolEvidenceMapService:
    """Build an editable protocol draft linked to explicit project-RAG excerpts.

    This is intentionally not a model-generated literature review. It only queries
    text knowledge bases the project already explicitly bound, preserves each
    citation for researcher review, and never saves the draft or promotes an
    excerpt to an EvidenceCard on the user's behalf.
    """

    def __init__(self, research_rag_service: ResearchRagService | None = None) -> None:
        self.protocol_service = ResearchService()
        self.research_rag_service = research_rag_service or ResearchRagService()

    def draft(
        self,
        db: Session,
        project_id: int,
        payload: ResearchProtocolEvidenceMapDraftRequest,
        owner_user_id: int,
    ) -> ResearchProtocolEvidenceMapDraftResponse:
        local_draft = self.protocol_service.draft_protocol(
            db,
            project_id,
            payload,
            owner_user_id,
        )
        retrieval = self.research_rag_service.search(
            db,
            project_id,
            owner_user_id,
            payload.research_question,
            payload.category,
            payload.top_k,
            payload.strategy,
        )
        evidence_map = [
            ResearchProtocolEvidenceMapEntry(
                citation=citation,
                researcher_action=(
                    "阅读原始上下文并人工核验适用数据、限制和许可后，"
                    "再决定是否创建或关联 EvidenceCard。"
                ),
            )
            for citation in retrieval.citations
        ]
        method_plan = local_draft.protocol.setdefault("method_plan", {})
        method_plan["rag_evidence_map"] = [entry.model_dump(mode="json") for entry in evidence_map]
        method_plan["rag_evidence_map_status"] = "unverified_researcher_review_required"

        if evidence_map:
            notice = (
                f"已从当前项目显式绑定的文本 RAG 生成 {len(evidence_map)} 条带引用的证据映射草案；"
                "草案未保存、未调用外部服务，也未自动创建或核验 EvidenceCard。"
            )
        else:
            notice = (
                f"{retrieval.notice} 已生成不含引用的本地协议草案；"
                "请先绑定并检索合适资料，再由研究者保存和核验。"
            )

        return ResearchProtocolEvidenceMapDraftResponse(
            protocol=local_draft.protocol,
            notice=notice,
            query=retrieval.query,
            category=retrieval.category,
            strategy=retrieval.strategy,
            searched_knowledge_base_ids=retrieval.searched_knowledge_base_ids,
            citations=retrieval.citations,
            evidence_map=evidence_map,
        )
