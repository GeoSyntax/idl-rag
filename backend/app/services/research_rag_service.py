from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.schemas import (
    ResearchKnowledgeSourceCreate,
    ResearchKnowledgeSourceResponse,
    ResearchRagSearchResponse,
)
from app.db.models import Document, KnowledgeBase, ResearchKnowledgeSource
from app.services.research_service import ResearchService
from app.services.retrieve_service import DEFAULT_RETRIEVAL_STRATEGY, RetrievalService


class ResearchRagService:
    """Project-scoped gateway to textual Method/Code RAG sources.

    A project member explicitly links one of their own knowledge bases before it
    can be searched by collaborators in that research project. The gateway never
    treats ``DataAsset`` rasters, vectors, sample tables, or pixels as text RAG.
    """

    def __init__(self) -> None:
        self.project_service = ResearchService()
        self.retrieval_service = RetrievalService()

    def list_sources(
        self, db: Session, project_id: int, owner_user_id: int
    ) -> list[ResearchKnowledgeSourceResponse]:
        self.project_service._get_owned_project(db, project_id, owner_user_id)
        sources = (
            db.query(ResearchKnowledgeSource)
            .filter(ResearchKnowledgeSource.project_id == project_id)
            .order_by(ResearchKnowledgeSource.category.asc(), ResearchKnowledgeSource.id.asc())
            .all()
        )
        return [self._response(db, source) for source in sources]

    def add_source(
        self,
        db: Session,
        project_id: int,
        payload: ResearchKnowledgeSourceCreate,
        owner_user_id: int,
    ) -> ResearchKnowledgeSourceResponse:
        self.project_service._get_owned_project(db, project_id, owner_user_id)
        knowledge_base = (
            db.query(KnowledgeBase)
            .filter(KnowledgeBase.id == payload.knowledge_base_id, KnowledgeBase.owner_user_id == owner_user_id)
            .first()
        )
        if knowledge_base is None:
            raise ValueError("只能将自己拥有的知识库显式共享到当前研究项目。")
        existing = (
            db.query(ResearchKnowledgeSource)
            .filter(
                ResearchKnowledgeSource.project_id == project_id,
                ResearchKnowledgeSource.knowledge_base_id == knowledge_base.id,
            )
            .first()
        )
        if existing is not None:
            raise ValueError("该知识库已绑定到当前研究项目。")
        source = ResearchKnowledgeSource(
            project_id=project_id,
            knowledge_base_id=knowledge_base.id,
            category=payload.category,
            added_by_user_id=owner_user_id,
        )
        db.add(source)
        db.commit()
        db.refresh(source)
        return self._response(db, source)

    def search(
        self,
        db: Session,
        project_id: int,
        owner_user_id: int,
        query: str,
        category: str,
        top_k: int,
        strategy: str,
    ) -> ResearchRagSearchResponse:
        self.project_service._get_owned_project(db, project_id, owner_user_id)
        normalized_query = query.strip()
        if len(normalized_query) < 2:
            raise ValueError("研究 RAG 检索词至少需要 2 个字符。")
        if category not in {"method", "idl_code", "python_code", "all"}:
            raise ValueError("RAG 分类必须是 method、idl_code、python_code 或 all。")
        sources_query = db.query(ResearchKnowledgeSource).filter(ResearchKnowledgeSource.project_id == project_id)
        if category != "all":
            sources_query = sources_query.filter(ResearchKnowledgeSource.category == category)
        source_ids = [source.knowledge_base_id for source in sources_query.order_by(ResearchKnowledgeSource.id.asc()).all()]
        if not source_ids:
            return ResearchRagSearchResponse(
                query=normalized_query,
                category=category,
                strategy=strategy,
                searched_knowledge_base_ids=[],
                citations=[],
                notice="当前项目尚未绑定可检索的文本知识库；请先绑定论文/方法、IDL 代码或 Python 代码知识库。",
            )
        citations = self.retrieval_service.search_multiple(
            db,
            source_ids,
            normalized_query,
            top_k=top_k,
            strategy=strategy or DEFAULT_RETRIEVAL_STRATEGY,
        )
        return ResearchRagSearchResponse(
            query=normalized_query,
            category=category,
            strategy=strategy or DEFAULT_RETRIEVAL_STRATEGY,
            searched_knowledge_base_ids=source_ids,
            citations=citations,
            notice=(
                "检索只访问已显式绑定到当前项目的文本知识库；"
                "遥感影像、样本表和像元仍仅保留在 Data Catalog，不参与向量检索。"
            ),
        )

    @staticmethod
    def _response(db: Session, source: ResearchKnowledgeSource) -> ResearchKnowledgeSourceResponse:
        document_count = (
            db.query(func.count(Document.id))
            .filter(Document.knowledge_base_id == source.knowledge_base_id)
            .scalar()
        )
        return ResearchKnowledgeSourceResponse(
            id=source.id,
            project_id=source.project_id,
            knowledge_base_id=source.knowledge_base_id,
            knowledge_base_name=source.knowledge_base.name,
            category=source.category,
            document_count=int(document_count or 0),
            added_by_user_id=source.added_by_user_id,
            created_at=source.created_at,
        )
