from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class HealthResponse(BaseModel):
    status: str
    app_name: str


class SystemSettingsPayload(BaseModel):
    provider_name: str = "openai-compatible"
    api_base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    chat_model: str = "gpt-4.1-mini"
    embedding_model: str = "text-embedding-3-small"
    system_prompt: str = (
        "你是一个 ENVI/IDL 资料助手。回答必须优先基于检索到的资料，不确定时要明确说明。"
    )
    temperature: float = Field(default=0.2, ge=0, le=2)
    rerank_api_url: str = ""
    rerank_api_key: str = ""
    rerank_model: str = ""
    langsmith_enabled: bool = False
    langsmith_api_key: str = ""
    langsmith_project: str = "IDL-RAG"
    langsmith_dataset: str = "idl-rag-golden-qa"
    langsmith_endpoint: str = "https://api.smith.langchain.com"


class SystemSettingsResponse(SystemSettingsPayload):
    api_key: str = ""
    rerank_api_key: str = ""
    langsmith_api_key: str = ""
    has_api_key: bool = False
    has_rerank_api_key: bool = False
    has_langsmith_api_key: bool = False


class TestConnectionResponse(BaseModel):
    ok: bool
    message: str


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=100)
    password: str = Field(min_length=6, max_length=128)


class LoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=100)
    password: str = Field(min_length=6, max_length=128)


class AuthUserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    role: Literal["admin", "user"]
    is_active: bool
    created_at: datetime
    updated_at: datetime


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: AuthUserResponse


class UserUpdateRequest(BaseModel):
    role: Literal["admin", "user"] | None = None
    is_active: bool | None = None


class ResetPasswordRequest(BaseModel):
    new_password: str = Field(min_length=6, max_length=128)


class KnowledgeBaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None


class KnowledgeBaseUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    default_retrieval_strategy: str | None = Field(default=None, min_length=1, max_length=100)
    default_top_k: int | None = Field(default=None, ge=1, le=20)
    default_rerank_enabled: bool | None = None


class KnowledgeBaseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    document_count: int
    default_retrieval_strategy: str
    default_top_k: int
    default_rerank_enabled: bool
    created_at: datetime
    updated_at: datetime


class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    knowledge_base_id: int
    file_name: str
    file_path: str
    media_type: str
    status: str
    error_message: str | None
    chunk_count: int
    retry_count: int
    parser_version: str | None
    chunker_version: str | None
    embedding_model: str | None
    embedding_dimensions: int | None
    embedding_is_fallback: bool
    index_table: str | None
    last_indexed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class DocumentChunkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    knowledge_base_id: int
    document_id: int
    chunk_index: int
    title: str | None
    section: str | None
    symbol_name: str | None
    content: str
    metadata: dict = Field(default_factory=dict)
    created_at: datetime


class ImportPathRequest(BaseModel):
    path: str
    recursive: bool = True


class ImportResult(BaseModel):
    imported: list[DocumentResponse]
    skipped: list[str]


class ChatRequest(BaseModel):
    knowledge_base_ids: list[int] | None = None
    question: str = Field(min_length=1)
    session_id: int | None = None
    strategy: str | None = Field(default=None, min_length=1, max_length=100)
    top_k: int | None = Field(default=None, ge=1, le=20)
    generate_pro_file: bool = False
    attached_file_content: str | None = None


class Citation(BaseModel):
    chunk_id: int
    document_id: int
    file_name: str
    file_path: str
    title: str | None = None
    section: str | None = None
    symbol_name: str | None = None
    excerpt: str
    knowledge_base_id: int | None = None
    knowledge_base_name: str | None = None
    chunk_kind: str | None = None
    score: float | None = None
    source_strategy: str | None = None
    match_type: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    metadata: dict = Field(default_factory=dict)


class RetrievalDebugRequest(BaseModel):
    knowledge_base_ids: list[int] = Field(min_length=1)
    query: str = Field(min_length=1)
    strategy: str = "hybrid_rrf_no_rerank"
    top_k: int = Field(default=6, ge=1, le=12)


class RetrievalDebugCandidate(Citation):
    rank: int
    debug: dict = Field(default_factory=dict)


class RetrievalDebugResponse(BaseModel):
    query: str
    strategy: str
    candidate_count: int
    candidates: list[RetrievalDebugCandidate]


class ChatArtifact(BaseModel):
    id: str
    file_name: str
    media_type: str
    size: int
    download_url: str


class ChatMessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role: str
    content: str
    citations: list[Citation] = Field(default_factory=list)
    artifacts: list[ChatArtifact] = Field(default_factory=list)
    created_at: datetime


class ChatResponse(BaseModel):
    session_id: int
    answer: str
    citations: list[Citation]
    messages: list[ChatMessageResponse]


class ChatSessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    knowledge_base_id: int | None
    title: str | None
    created_at: datetime


class ChatSessionRenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class EvaluationRunRequest(BaseModel):
    knowledge_base_id: int
    categories: list[str] | None = None
    strategies: list[str] | None = None
    top_k: int = Field(default=6, ge=1, le=12)
    limit: int | None = Field(default=None, ge=1, le=100)


class LangSmithSyncRequest(BaseModel):
    dataset: str | None = None
    dry_run: bool = True


class LangSmithEvaluateRequest(BaseModel):
    knowledge_base_id: int
    dataset: str | None = None
    strategies: list[str] | None = None
    top_k: int = Field(default=6, ge=1, le=12)


class EvaluationReportResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    knowledge_base_id: int | None
    created_by_user_id: int | None
    report_type: str
    strategy: str | None
    dataset: str | None
    status: str
    summary_json: dict
    report_json: dict
    report_path: str | None
    error_message: str | None
    created_at: datetime


class DashboardSummaryResponse(BaseModel):
    knowledge_base_count: int
    document_count: int
    ready_document_count: int
    queued_document_count: int = 0
    processing_document_count: int = 0
    stale_document_count: int = 0
    failed_document_count: int
    fallback_document_count: int = 0
    queued_index_job_count: int = 0
    processing_index_job_count: int = 0
    failed_index_job_count: int = 0
    worker_alive: bool = False
    worker_last_error: str | None = None
    embedding_fallback_active: bool = False
    embedding_last_error: str | None = None
    chat_session_count: int
