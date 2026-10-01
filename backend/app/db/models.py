from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON


class Base(DeclarativeBase):
    pass


class SystemSetting(Base):
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="user", server_default="user")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), onupdate=func.now()
    )

    knowledge_bases: Mapped[list["KnowledgeBase"]] = relationship(back_populates="owner")
    chat_sessions: Mapped[list["ChatSession"]] = relationship(back_populates="owner")
    research_projects: Mapped[list["ResearchProject"]] = relationship(back_populates="owner")
    research_project_memberships: Mapped[list["ResearchProjectMember"]] = relationship(
        foreign_keys="ResearchProjectMember.user_id", back_populates="user"
    )


class KnowledgeBase(Base):
    __tablename__ = "knowledge_bases"
    __table_args__ = (UniqueConstraint("owner_user_id", "name", name="uq_knowledge_base_owner_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    default_retrieval_strategy: Mapped[str] = mapped_column(String(100), nullable=False, default="hybrid_rrf_no_rerank", server_default="hybrid_rrf_no_rerank")
    default_top_k: Mapped[int] = mapped_column(Integer, nullable=False, default=6, server_default="6")
    default_rerank_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), onupdate=func.now()
    )

    owner: Mapped[User | None] = relationship(back_populates="knowledge_bases")
    documents: Mapped[list["Document"]] = relationship(
        back_populates="knowledge_base", cascade="all, delete-orphan"
    )
    chat_sessions: Mapped[list["ChatSession"]] = relationship(
        back_populates="knowledge_base", cascade="all, delete-orphan"
    )
    jobs: Mapped[list["IndexJob"]] = relationship(back_populates="knowledge_base", cascade="all, delete-orphan")
    evaluation_reports: Mapped[list["EvaluationReport"]] = relationship(
        back_populates="knowledge_base", cascade="all, delete-orphan"
    )


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("knowledge_base_id", "sha256", name="uq_document_sha"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    knowledge_base_id: Mapped[int] = mapped_column(ForeignKey("knowledge_bases.id"), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    media_type: Mapped[str] = mapped_column(String(100), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued", server_default="queued")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    parser_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    chunker_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    embedding_dimensions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    embedding_is_fallback: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    index_table: Mapped[str | None] = mapped_column(String(255), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), onupdate=func.now()
    )

    knowledge_base: Mapped[KnowledgeBase] = relationship(back_populates="documents")
    chunks: Mapped[list["Chunk"]] = relationship(back_populates="document", cascade="all, delete-orphan")
    jobs: Mapped[list["IndexJob"]] = relationship(back_populates="document", cascade="all, delete-orphan")


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    knowledge_base_id: Mapped[int] = mapped_column(ForeignKey("knowledge_bases.id"), nullable=False)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    section: Mapped[str | None] = mapped_column(String(255), nullable=True)
    symbol_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    meta_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), server_default=func.now())

    document: Mapped[Document] = relationship(back_populates="chunks")


class IndexJob(Base):
    __tablename__ = "index_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    knowledge_base_id: Mapped[int] = mapped_column(ForeignKey("knowledge_bases.id"), nullable=False)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), nullable=False)
    job_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued", server_default="queued")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), onupdate=func.now()
    )

    knowledge_base: Mapped[KnowledgeBase] = relationship(back_populates="jobs")
    document: Mapped[Document] = relationship(back_populates="jobs")


class SymbolDependency(Base):
    __tablename__ = "symbol_dependencies"
    __table_args__ = (
        Index("ix_symbol_dep_kb_caller", "knowledge_base_id", "caller_symbol"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    knowledge_base_id: Mapped[int] = mapped_column(ForeignKey("knowledge_bases.id"), nullable=False)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), nullable=False)
    caller_symbol: Mapped[str] = mapped_column(String(255), nullable=False)
    callee_symbol: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), server_default=func.now())


class EvaluationReport(Base):
    __tablename__ = "evaluation_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    knowledge_base_id: Mapped[int | None] = mapped_column(ForeignKey("knowledge_bases.id"), nullable=True)
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    report_type: Mapped[str] = mapped_column(String(40), nullable=False)
    strategy: Mapped[str | None] = mapped_column(String(100), nullable=True)
    dataset: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="completed", server_default="completed")
    summary_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    report_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    report_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), server_default=func.now())

    knowledge_base: Mapped[KnowledgeBase | None] = relationship(back_populates="evaluation_reports")
    created_by: Mapped[User | None] = relationship()


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    knowledge_base_id: Mapped[int | None] = mapped_column(ForeignKey("knowledge_bases.id"), nullable=True)
    research_project_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    owner_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), server_default=func.now())

    knowledge_base: Mapped[KnowledgeBase | None] = relationship(back_populates="chat_sessions")
    owner: Mapped[User | None] = relationship(back_populates="chat_sessions")
    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("chat_sessions.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    citations_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    artifacts_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # Bounded, redacted Agent trace attached to the final assistant message.
    # It is intentionally separate from answer text so history can restore
    # the inspection trail without replaying a model request.
    agent_trace_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), server_default=func.now())

    session: Mapped[ChatSession] = relationship(back_populates="messages")


class ChatRequestLog(Base):
    """持久化每次 Chat 请求的耗时和质量信号，用于 Dashboard 指标和历史趋势。"""

    __tablename__ = "chat_request_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    session_id: Mapped[int | None] = mapped_column(ForeignKey("chat_sessions.id"), nullable=True)
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    strategy: Mapped[str | None] = mapped_column(String(100), nullable=True)
    top_k: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retrieve_ms: Mapped[float | None] = mapped_column(nullable=True)
    rerank_ms: Mapped[float | None] = mapped_column(nullable=True)
    llm_first_token_ms: Mapped[float | None] = mapped_column(nullable=True)
    llm_total_ms: Mapped[float | None] = mapped_column(nullable=True)
    total_ms: Mapped[float] = mapped_column(nullable=False)
    citation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    artifact_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    has_error: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), server_default=func.now())

    owner: Mapped[User] = relationship()


class ResearchProject(Base):
    """研究工作流顶层容器；所有者可邀请无需角色分级的协作成员。"""

    __tablename__ = "research_projects"
    __table_args__ = (
        UniqueConstraint("owner_user_id", "name", name="uq_research_project_owner_name"),
        Index("ix_research_projects_owner_created", "owner_user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    entry_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="open", server_default="open")
    visibility: Mapped[str] = mapped_column(String(20), nullable=False, default="my", server_default="my")
    egress_policy: Mapped[str] = mapped_column(
        String(40), nullable=False, default="private-local", server_default="private-local"
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="exploratory", server_default="exploratory"
    )
    protocol_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), onupdate=func.now()
    )

    owner: Mapped[User] = relationship(back_populates="research_projects")
    data_assets: Mapped[list["ResearchDataAsset"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    data_snapshots: Mapped[list["ResearchDataSnapshot"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    evidence_cards: Mapped[list["EvidenceCard"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    formula_specs: Mapped[list["FormulaSpec"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    experiments: Mapped[list["ResearchExperiment"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    runs: Mapped[list["ResearchRun"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    members: Mapped[list["ResearchProjectMember"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    protocol_revisions: Mapped[list["ResearchProtocolRevision"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    knowledge_sources: Mapped[list["ResearchKnowledgeSource"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class ResearchProtocolRevision(Base):
    """Immutable, project-scoped record created only for a changed saved protocol."""

    __tablename__ = "research_protocol_revisions"
    __table_args__ = (
        UniqueConstraint("project_id", "version", name="uq_research_protocol_revision_project_version"),
        Index("ix_research_protocol_revisions_project_created", "project_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("research_projects.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    protocol_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    protocol_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    saved_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), server_default=func.now())

    project: Mapped[ResearchProject] = relationship(back_populates="protocol_revisions")
    saved_by: Mapped[User] = relationship(foreign_keys=[saved_by_user_id])


class ResearchProjectMember(Base):
    """简单项目协作成员；成员共享项目工作流，无教师/学生角色区分。"""

    __tablename__ = "research_project_members"
    __table_args__ = (
        UniqueConstraint("project_id", "user_id", name="uq_research_project_member"),
        Index("ix_research_project_members_user_project", "user_id", "project_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("research_projects.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    added_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), server_default=func.now())

    project: Mapped[ResearchProject] = relationship(back_populates="members")
    user: Mapped[User] = relationship(foreign_keys=[user_id], back_populates="research_project_memberships")
    added_by: Mapped[User] = relationship(foreign_keys=[added_by_user_id])


class ResearchKnowledgeSource(Base):
    """Project-approved textual RAG source; raster data remains in the Data Catalog."""

    __tablename__ = "research_knowledge_sources"
    __table_args__ = (
        UniqueConstraint("project_id", "knowledge_base_id", name="uq_research_knowledge_source"),
        Index("ix_research_knowledge_sources_project_category", "project_id", "category"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("research_projects.id"), nullable=False)
    knowledge_base_id: Mapped[int] = mapped_column(ForeignKey("knowledge_bases.id"), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    added_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), server_default=func.now())

    project: Mapped[ResearchProject] = relationship(back_populates="knowledge_sources")
    knowledge_base: Mapped[KnowledgeBase] = relationship()
    added_by: Mapped[User] = relationship(foreign_keys=[added_by_user_id])


class ResearchDataAsset(Base):
    """数据目录中的资产记录，不读取或嵌入原始影像内容。"""

    __tablename__ = "research_data_assets"
    __table_args__ = (Index("ix_research_data_assets_project_created", "project_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("research_projects.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    asset_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_uri: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    access_policy: Mapped[str] = mapped_column(
        String(40), nullable=False, default="private-local", server_default="private-local"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), server_default=func.now())

    project: Mapped[ResearchProject] = relationship(back_populates="data_assets")


class ResearchDataSnapshot(Base):
    """冻结的资产集合；asset_ids_json 指向同一项目内的 DataAsset。"""

    __tablename__ = "research_data_snapshots"
    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_research_data_snapshot_project_name"),
        Index("ix_research_data_snapshots_project_created", "project_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("research_projects.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    asset_ids_json: Mapped[list[int]] = mapped_column(JSON, nullable=False, default=list)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    is_frozen: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    frozen_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False, server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), server_default=func.now())

    project: Mapped[ResearchProject] = relationship(back_populates="data_snapshots")


class EvidenceCard(Base):
    """公式、算法或代码候选的可审计来源与适用边界。"""

    __tablename__ = "evidence_cards"
    __table_args__ = (Index("ix_evidence_cards_project_status", "project_id", "status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("research_projects.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="candidate", server_default="candidate")
    source_type: Mapped[str] = mapped_column(String(40), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    doi: Mapped[str | None] = mapped_column(String(255), nullable=True)
    license_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    applicability: Mapped[str | None] = mapped_column(Text, nullable=True)
    limitations: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False, server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), server_default=func.now())

    project: Mapped[ResearchProject] = relationship(back_populates="evidence_cards")


class FormulaSpec(Base):
    """与执行语言无关的公式、输入、参数和输出约定。"""

    __tablename__ = "formula_specs"
    __table_args__ = (
        UniqueConstraint("project_id", "name", "version", name="uq_formula_spec_project_name_version"),
        Index("ix_formula_specs_project_status", "project_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("research_projects.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft", server_default="draft")
    spec_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    evidence_card_ids_json: Mapped[list[int]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), onupdate=func.now()
    )

    project: Mapped[ResearchProject] = relationship(back_populates="formula_specs")


class ResearchExperiment(Base):
    """尚未执行或已冻结的预览/正式实验计划；Run 资产将在 Runner 阶段追加。"""

    __tablename__ = "research_experiments"
    __table_args__ = (Index("ix_research_experiments_project_created", "project_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("research_projects.id"), nullable=False)
    formula_spec_id: Mapped[int] = mapped_column(ForeignKey("formula_specs.id"), nullable=False)
    data_snapshot_id: Mapped[int] = mapped_column(ForeignKey("research_data_snapshots.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    runner_type: Mapped[str] = mapped_column(String(20), nullable=False, default="python", server_default="python")
    execution_mode: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="planned", server_default="planned")
    parameters_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    validation_plan_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    visualization_contract_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    project_protocol_revision_id: Mapped[int | None] = mapped_column(
        ForeignKey("research_protocol_revisions.id"), nullable=True
    )
    project_protocol_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    project_protocol_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), server_default=func.now())

    project: Mapped[ResearchProject] = relationship(back_populates="experiments")
    project_protocol_revision: Mapped[ResearchProtocolRevision | None] = relationship()
    formula_spec: Mapped[FormulaSpec] = relationship()
    data_snapshot: Mapped[ResearchDataSnapshot] = relationship()
    runs: Mapped[list["ResearchRun"]] = relationship(back_populates="experiment", cascade="all, delete-orphan")


class ResearchRun(Base):
    """受控执行器的一次运行，以及其不可变的产物 manifest。"""

    __tablename__ = "research_runs"
    __table_args__ = (Index("ix_research_runs_experiment_created", "experiment_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("research_projects.id"), nullable=False)
    experiment_id: Mapped[int] = mapped_column(ForeignKey("research_experiments.id"), nullable=False)
    runner_type: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued", server_default="queued")
    run_token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    run_dir: Mapped[str | None] = mapped_column(Text, nullable=True)
    manifest_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    outputs_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), server_default=func.now())

    project: Mapped[ResearchProject] = relationship(back_populates="runs")
    experiment: Mapped[ResearchExperiment] = relationship(back_populates="runs")


class ResearchExternalSearchLog(Base):
    """用户主动发起的外部资料检索审计；不存储或外发私有数据资产。"""

    __tablename__ = "research_external_search_logs"
    __table_args__ = (Index("ix_research_external_search_logs_project_created", "project_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("research_projects.id"), nullable=False)
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    query: Mapped[str] = mapped_column(String(500), nullable=False)
    result_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="completed", server_default="completed")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), server_default=func.now())

    project: Mapped[ResearchProject] = relationship()
    owner: Mapped[User] = relationship()


class ResearchValidationSample(Base):
    """可审计的人工/外部参考样本，不将质量信息压缩到自由文本。"""

    __tablename__ = "research_validation_samples"
    __table_args__ = (Index("ix_research_validation_samples_project_split", "project_id", "split"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("research_projects.id"), nullable=False)
    data_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("research_data_snapshots.id"), nullable=True)
    source_asset_id: Mapped[int | None] = mapped_column(ForeignKey("research_data_assets.id"), nullable=True)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    label: Mapped[int] = mapped_column(Integer, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)
    annotator: Mapped[str] = mapped_column(String(200), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    split: Mapped[str] = mapped_column(String(32), nullable=False)
    spatial_block: Mapped[str] = mapped_column(String(120), nullable=False)
    temporal_stratum: Mapped[str] = mapped_column(String(120), nullable=False)
    conflict_status: Mapped[str] = mapped_column(String(32), nullable=False, default="none", server_default="none")
    source_note: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), server_default=func.now())

    project: Mapped[ResearchProject] = relationship()
