from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.schemas import KnowledgeBaseCreate, KnowledgeBaseResponse, KnowledgeBaseUpdate
from app.db.models import Document, KnowledgeBase, ResearchKnowledgeSource
from app.services.retrieve_service import RetrievalService
from app.services.storage_stores import SQLiteFullTextStore


class KnowledgeBaseService:
    def __init__(self) -> None:
        self.retrieval_service = RetrievalService()
        self.full_text_store = SQLiteFullTextStore()

    def list_knowledge_bases(self, db: Session, owner_user_id: int) -> list[KnowledgeBaseResponse]:
        knowledge_bases = (
            db.query(KnowledgeBase)
            .filter(KnowledgeBase.owner_user_id == owner_user_id)
            .order_by(KnowledgeBase.created_at.desc(), KnowledgeBase.id.desc())
            .all()
        )
        return [self._to_response(db, knowledge_base) for knowledge_base in knowledge_bases]

    def create_knowledge_base(
        self,
        db: Session,
        payload: KnowledgeBaseCreate,
        owner_user_id: int,
    ) -> KnowledgeBaseResponse:
        knowledge_base = KnowledgeBase(
            owner_user_id=owner_user_id,
            name=payload.name.strip(),
            description=payload.description,
        )
        db.add(knowledge_base)
        db.commit()
        db.refresh(knowledge_base)
        return self._to_response(db, knowledge_base)

    def update_knowledge_base(
        self,
        db: Session,
        knowledge_base_id: int,
        payload: KnowledgeBaseUpdate,
        owner_user_id: int,
    ) -> KnowledgeBaseResponse:
        knowledge_base = self.get_owned_knowledge_base(db, knowledge_base_id, owner_user_id)
        if payload.name is not None:
            knowledge_base.name = payload.name.strip()
        if payload.description is not None:
            knowledge_base.description = payload.description
        if payload.default_retrieval_strategy is not None:
            knowledge_base.default_retrieval_strategy = payload.default_retrieval_strategy.strip()
        if payload.default_top_k is not None:
            knowledge_base.default_top_k = payload.default_top_k
        if payload.default_rerank_enabled is not None:
            knowledge_base.default_rerank_enabled = payload.default_rerank_enabled
        db.commit()
        db.refresh(knowledge_base)
        return self._to_response(db, knowledge_base)

    def delete_knowledge_base(self, db: Session, knowledge_base_id: int, owner_user_id: int) -> None:
        knowledge_base = self.get_owned_knowledge_base(db, knowledge_base_id, owner_user_id)
        self.retrieval_service.remove_knowledge_base(knowledge_base_id)
        self.full_text_store.remove_knowledge_base(db, knowledge_base_id)
        db.query(ResearchKnowledgeSource).filter(
            ResearchKnowledgeSource.knowledge_base_id == knowledge_base_id
        ).delete(synchronize_session=False)
        db.delete(knowledge_base)
        db.commit()

    def get_owned_knowledge_base(self, db: Session, knowledge_base_id: int, owner_user_id: int) -> KnowledgeBase:
        knowledge_base = db.get(KnowledgeBase, knowledge_base_id)
        if knowledge_base is None or knowledge_base.owner_user_id != owner_user_id:
            raise ValueError("知识库不存在。")
        return knowledge_base

    def _to_response(self, db: Session, knowledge_base: KnowledgeBase) -> KnowledgeBaseResponse:
        document_count = db.execute(
            select(func.count(Document.id)).where(Document.knowledge_base_id == knowledge_base.id)
        ).scalar_one()
        return KnowledgeBaseResponse(
            id=knowledge_base.id,
            name=knowledge_base.name,
            description=knowledge_base.description,
            document_count=int(document_count),
            default_retrieval_strategy=knowledge_base.default_retrieval_strategy,
            default_top_k=knowledge_base.default_top_k,
            default_rerank_enabled=knowledge_base.default_rerank_enabled,
            created_at=knowledge_base.created_at,
            updated_at=knowledge_base.updated_at,
        )
