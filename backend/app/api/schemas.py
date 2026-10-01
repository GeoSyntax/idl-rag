from datetime import date, datetime, datetime as datetime_type
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class HealthResponse(BaseModel):
    status: str
    app_name: str


class ReadinessResponse(BaseModel):
    status: Literal["ready", "degraded"]
    app_name: str
    checks: dict[str, str] = Field(default_factory=dict)


class SystemSettingsPayload(BaseModel):
    provider_name: str = "openai-compatible"
    api_base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    chat_model: str = "gpt-4.1-mini"
    # 可与聊天服务分离。留空时向后兼容，沿用 api_base_url/api_key。
    embedding_api_base_url: str = ""
    embedding_api_key: str = ""
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
    embedding_api_key: str = ""
    rerank_api_key: str = ""
    langsmith_api_key: str = ""
    has_api_key: bool = False
    has_embedding_api_key: bool = False
    has_rerank_api_key: bool = False
    has_langsmith_api_key: bool = False


class TestConnectionResponse(BaseModel):
    ok: bool
    message: str


class ChatModelStatusResponse(BaseModel):
    """Safe model configuration summary for the authenticated workbench.

    This deliberately omits API keys and the full endpoint URL.  Users need
    to know which provider/model a request will use, but the connection
    credentials remain an administrator-only concern.
    """

    configured: bool
    provider_name: str
    chat_model: str
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
    # Retries reuse the original user message instead of appending a second
    # identical prompt to the same session. The service validates ownership,
    # session membership, role and question text before reusing it.
    retry_message_id: int | None = Field(default=None, ge=1)
    strategy: str | None = Field(default=None, min_length=1, max_length=100)
    top_k: int | None = Field(default=None, ge=1, le=20)
    generate_pro_file: bool = False
    attached_file_content: str | None = None
    attached_file_name: str | None = Field(default=None, max_length=255)
    input_artifact_ids: list[str] = Field(default_factory=list, max_length=8)
    # Optional project context for the research-aware Agent. The project is
    # always revalidated server-side against the authenticated user's
    # ownership/membership before any project tool can run.
    research_project_id: int | None = Field(default=None, ge=1)
    allow_external_research: bool = False
    # Explicit UI consent for bounded Agent mutations: create/queue a Python preview only after confirmation;
    # formal/IDL execution, formula freezing and protocol edits remain researcher actions.
    allow_research_execution: bool = False
    # Separate consent for an outbound GEE request that creates a private
    # DataAsset; it never implies permission to freeze a snapshot or run code.
    allow_gee_fetch: bool = False


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
    kind: Literal["pro", "idl_output", "idl_log", "gee_data", "gee_preview", "chat_input"] | None = None
    previewable: bool = False
    run_id: str | None = None
    input_artifact_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChatMessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role: str
    content: str
    citations: list[Citation] = Field(default_factory=list)
    artifacts: list[ChatArtifact] = Field(default_factory=list)
    agent_trace: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class ChatResponse(BaseModel):
    session_id: int
    answer: str
    citations: list[Citation]
    messages: list[ChatMessageResponse]


class IdlRunRequest(BaseModel):
    entrypoint: str | None = Field(default=None, min_length=1, max_length=80)
    timeout_seconds: int | None = Field(default=None, ge=1, le=600)
    input_artifact_ids: list[str] = Field(default_factory=list, max_length=8)


class IdlRunResponse(BaseModel):
    run_id: str
    session_id: int
    message: ChatMessageResponse
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    duration_ms: int
    artifacts: list[ChatArtifact]


class GeeStatusResponse(BaseModel):
    enabled: bool
    initialized: bool
    project: str | None = None
    auth_mode: str | None = None
    has_credentials: bool = False
    message: str = ""


class GeeFetchRequest(BaseModel):
    session_id: int | None = None
    dataset_id: str = Field(min_length=1, max_length=200)
    start_date: str | None = Field(default=None, max_length=20)
    end_date: str | None = Field(default=None, max_length=20)
    bbox: list[float] = Field(min_length=4, max_length=4)
    bands: list[str] = Field(default_factory=list, max_length=12)
    scale: int = Field(default=30, ge=1, le=10000)
    crs: str = Field(default="EPSG:4326", min_length=1, max_length=40)
    composite: Literal["median", "mean", "first"] = "median"
    label: str | None = Field(default=None, max_length=80)


class GeeFetchResponse(BaseModel):
    session_id: int
    message: ChatMessageResponse
    artifact: ChatArtifact


class ChatSessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    knowledge_base_id: int | None
    research_project_id: int | None
    title: str | None
    created_at: datetime
    # Inferred from the latest request log; old databases need no migration.
    last_mode: Literal["normal", "agent"] | None = None


class ChatSessionRenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class ChatRunResponse(BaseModel):
    """Bounded, non-sensitive history entry for one chat/Agent stream."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    session_id: int | None
    mode: str
    stream_id: str | None
    terminal_status: Literal["completed", "failed", "cancelled", "unknown"]
    total_ms: float
    retrieve_ms: float | None
    rerank_ms: float | None
    llm_first_token_ms: float | None
    llm_total_ms: float | None
    citation_count: int
    artifact_count: int
    agent_step_count: int
    error_message: str | None
    message_id: int | None = None
    generate_pro_file: bool = False
    input_artifact_ids: list[str] = Field(default_factory=list)
    has_attached_file: bool = False
    attached_file_name: str | None = None
    created_at: datetime


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
    chunk_count: int = 0
    avg_chunks_per_document: float | None = None
    avg_index_job_seconds: float | None = None
    queued_index_job_count: int = 0
    processing_index_job_count: int = 0
    failed_index_job_count: int = 0
    worker_alive: bool = False
    worker_mode: Literal["embedded", "external", "disabled"] = "embedded"
    worker_last_error: str | None = None
    embedding_fallback_active: bool = False
    embedding_last_error: str | None = None
    chat_session_count: int
    chat_request_count: int = 0
    chat_latency_p95_ms: float | None = None
    chat_latency_p99_ms: float | None = None
    chat_first_token_count: int = 0
    chat_first_token_p95_ms: float | None = None
    chat_first_token_p99_ms: float | None = None
    latest_eval_hit_rate: float | None = None
    latest_eval_top_k: int | None = None
    latest_eval_strategy: str | None = None
    avg_retrieve_ms: float | None = None
    avg_rerank_ms: float | None = None
    avg_llm_first_token_ms: float | None = None
    avg_total_ms: float | None = None
    citation_coverage: float | None = None
    error_rate: float | None = None


class ResearchProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    entry_mode: Literal["template", "open"] = "open"
    protocol: dict[str, Any] = Field(default_factory=dict)


class ResearchProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    protocol: dict[str, Any] | None = None


class ResearchProtocolDraftRequest(BaseModel):
    research_question: str = Field(min_length=8, max_length=3000)


class ResearchProtocolDraftResponse(BaseModel):
    protocol: dict[str, Any]
    notice: str


class ResearchProtocolRevisionResponse(BaseModel):
    id: int
    project_id: int
    version: int
    protocol: dict[str, Any]
    protocol_hash: str
    saved_by_user_id: int
    created_at: datetime


class ResearchProtocolReadinessItem(BaseModel):
    code: str
    path: str
    message: str


class ResearchProtocolReadinessResponse(BaseModel):
    project_id: int
    ready: bool
    protocol_hash: str
    protocol_revision_id: int | None
    missing: list[ResearchProtocolReadinessItem] = Field(default_factory=list)
    notice: str


class ResearchProjectResponse(BaseModel):
    id: int
    owner_user_id: int
    name: str
    description: str | None
    entry_mode: Literal["template", "open"]
    visibility: Literal["my"]
    egress_policy: Literal["private-local"]
    status: Literal["exploratory"]
    protocol: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class ResearchProjectMemberCreate(BaseModel):
    username: str = Field(min_length=1, max_length=100)


class ResearchProjectMemberResponse(BaseModel):
    id: int
    project_id: int
    user_id: int
    username: str
    added_by_user_id: int
    created_at: datetime


class ResearchKnowledgeSourceCreate(BaseModel):
    knowledge_base_id: int = Field(ge=1)
    category: Literal["method", "idl_code", "python_code"]


class ResearchKnowledgeSourceResponse(BaseModel):
    id: int
    project_id: int
    knowledge_base_id: int
    knowledge_base_name: str
    category: Literal["method", "idl_code", "python_code"]
    document_count: int
    added_by_user_id: int
    created_at: datetime


class ResearchRagSearchResponse(BaseModel):
    query: str
    category: Literal["method", "idl_code", "python_code", "all"]
    strategy: str
    searched_knowledge_base_ids: list[int] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    notice: str


class ResearchProtocolEvidenceMapDraftRequest(ResearchProtocolDraftRequest):
    """Request an editable protocol starter annotated with local project-RAG citations."""

    category: Literal["method", "idl_code", "python_code", "all"] = "method"
    top_k: int = Field(default=6, ge=1, le=20)
    strategy: str = Field(default="hybrid_rrf_no_rerank", min_length=1, max_length=100)


class ResearchProtocolEvidenceMapEntry(BaseModel):
    citation: Citation
    protocol_section: Literal["method_plan"] = "method_plan"
    review_status: Literal["unverified"] = "unverified"
    researcher_action: str


class ResearchProtocolEvidenceMapDraftResponse(ResearchProtocolDraftResponse):
    query: str
    category: Literal["method", "idl_code", "python_code", "all"]
    strategy: str
    searched_knowledge_base_ids: list[int] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    evidence_map: list[ResearchProtocolEvidenceMapEntry] = Field(default_factory=list)


class ResearchDataAssetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    asset_kind: Literal["raster", "vector", "table", "roi", "reference", "derived"]
    source_type: Literal["local", "gee", "reference"]
    source_uri: str = Field(min_length=1, max_length=4000)
    sha256: str | None = Field(default=None, pattern=r"^[A-Fa-f0-9]{64}$")
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResearchDataAssetResponse(BaseModel):
    id: int
    project_id: int
    name: str
    asset_kind: Literal["raster", "vector", "table", "roi", "reference", "derived"]
    source_type: Literal["local", "gee", "reference"]
    source_uri: str
    sha256: str | None
    metadata: dict[str, Any] = Field(default_factory=dict)
    access_policy: Literal["private-local"]
    created_at: datetime


class ResearchRasterStackCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    asset_ids: list[int] = Field(min_length=2, max_length=8)
    reference_asset_id: int | None = Field(default=None, ge=1)
    band_names: list[str] = Field(default_factory=list, max_length=8)
    resampling: Literal["nearest", "bilinear", "cubic"] = "bilinear"


class ResearchGeeFetchRequest(BaseModel):
    dataset_id: str = Field(min_length=1, max_length=200)
    start_date: str | None = Field(default=None, max_length=20)
    end_date: str | None = Field(default=None, max_length=20)
    bbox: list[float] = Field(min_length=4, max_length=4)
    bands: list[str] = Field(default_factory=list, max_length=12)
    scale: int = Field(default=30, ge=1, le=10000)
    crs: str = Field(default="EPSG:4326", min_length=1, max_length=40)
    composite: Literal["median", "mean", "first"] = "median"
    label: str | None = Field(default=None, max_length=80)


class ResearchGeeFetchResponse(BaseModel):
    asset: ResearchDataAssetResponse
    notice: str


class ResearchDataSnapshotCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    asset_ids: list[int] = Field(min_length=1, max_length=100)


class ResearchDataSnapshotResponse(BaseModel):
    id: int
    project_id: int
    name: str
    description: str | None
    asset_ids: list[int]
    snapshot_hash: str
    is_frozen: Literal[True]
    frozen_at: datetime
    created_at: datetime


class EvidenceCardCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    status: Literal["candidate", "verified", "imported", "experiment_pinned"] = "candidate"
    source_type: Literal["paper", "official_document", "code", "dataset", "web"]
    source_url: str | None = Field(default=None, max_length=4000)
    doi: str | None = Field(default=None, max_length=255)
    license_note: str | None = Field(default=None, max_length=5000)
    applicability: str | None = Field(default=None, max_length=10000)
    limitations: str | None = Field(default=None, max_length=10000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvidenceCardResponse(BaseModel):
    id: int
    project_id: int
    title: str
    status: Literal["candidate", "verified", "imported", "experiment_pinned"]
    source_type: Literal["paper", "official_document", "code", "dataset", "web"]
    source_url: str | None
    doi: str | None
    license_note: str | None
    applicability: str | None
    limitations: str | None
    metadata: dict[str, Any] = Field(default_factory=dict)
    retrieved_at: datetime
    created_at: datetime


class FormulaSpecCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    version: int = Field(default=1, ge=1, le=10000)
    status: Literal["draft", "candidate", "frozen"] = "draft"
    spec: dict[str, Any] = Field(default_factory=dict)
    evidence_card_ids: list[int] = Field(default_factory=list, max_length=50)


class FormulaSpecResponse(BaseModel):
    id: int
    project_id: int
    name: str
    version: int
    status: Literal["draft", "candidate", "frozen"]
    spec: dict[str, Any] = Field(default_factory=dict)
    evidence_card_ids: list[int] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class ResearchExperimentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    formula_spec_id: int = Field(ge=1)
    data_snapshot_id: int = Field(ge=1)
    runner_type: Literal["python", "idl"] = "python"
    execution_mode: Literal["preview", "formal"]
    parameters: dict[str, Any] = Field(default_factory=dict)
    validation_plan: dict[str, Any] = Field(default_factory=dict)
    visualization_contract: list[str] = Field(default_factory=list, max_length=50)


class ResearchExperimentResponse(BaseModel):
    id: int
    project_id: int
    formula_spec_id: int
    data_snapshot_id: int
    name: str
    runner_type: Literal["python", "idl"]
    execution_mode: Literal["preview", "formal"]
    status: Literal["planned", "running", "completed", "failed", "cancelled", "unavailable"]
    parameters: dict[str, Any] = Field(default_factory=dict)
    validation_plan: dict[str, Any] = Field(default_factory=dict)
    visualization_contract: list[str] = Field(default_factory=list)
    project_protocol_revision_id: int | None
    project_protocol_hash: str
    created_at: datetime


class ResearchSweepCandidate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    parameters: dict[str, Any] = Field(default_factory=dict)


class ResearchParameterSweepCreate(BaseModel):
    candidates: list[ResearchSweepCandidate] = Field(min_length=2, max_length=20)
    evaluation_split: Literal["development", "model_selection"]
    ranking_metric: Literal["overall_accuracy", "precision", "recall", "f1", "iou"] = "f1"


class ResearchRunResponse(BaseModel):
    id: int
    project_id: int
    experiment_id: int
    runner_type: Literal["python", "idl"]
    status: Literal["queued", "running", "completed", "failed", "cancelled", "unavailable"]
    run_token: str
    manifest: dict[str, Any] = Field(default_factory=dict)
    outputs: list[dict[str, Any]] = Field(default_factory=list)
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


class ResearchRunVerificationResponse(BaseModel):
    run_id: int
    run_token: str
    status: Literal["verified", "failed", "not_available"]
    verified: bool
    package_file_name: str | None = None
    package_sha256: str | None = None
    checked_file_count: int = 0
    output_count: int = 0
    issues: list[str] = Field(default_factory=list)
    notice: str


class ResearchRunReproducibilityRequest(BaseModel):
    reference_run_id: int = Field(ge=1)
    absolute_tolerance: float = Field(default=1e-6, ge=0, le=1e6)
    relative_tolerance: float = Field(default=1e-6, ge=0, le=1e6)


class ResearchRunReproducibilityResponse(BaseModel):
    run_id: int
    reference_run_id: int
    status: Literal["matched", "failed"]
    matched: bool
    absolute_tolerance: float
    relative_tolerance: float
    compared_output_count: int = 0
    exact_matches: list[str] = Field(default_factory=list)
    raster_comparisons: list[dict[str, Any]] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    notice: str


class ResearchRunComparisonRequest(BaseModel):
    reference_run_id: int = Field(ge=1)


class ResearchRunComparisonResponse(BaseModel):
    run_id: int
    reference_run_id: int
    run_kind: Literal["formal_comparison"]
    status: Literal["compared", "failed"]
    comparable: bool
    snapshot_hash: str | None = None
    compared_metric_count: int = 0
    metrics: list[dict[str, Any]] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    notice: str


class ResearchLiteratureCandidate(BaseModel):
    provider: Literal["crossref", "openalex", "semantic_scholar"]
    external_id: str
    title: str
    authors: list[str] = Field(default_factory=list)
    container_title: str | None = None
    published_year: int | None = None
    doi: str | None = None
    source_url: str | None = None
    item_type: str | None = None
    abstract: str | None = None


class ResearchLiteratureSearchResponse(BaseModel):
    audit_id: int
    provider: Literal["crossref", "openalex", "semantic_scholar"] = "crossref"
    query: str
    candidates: list[ResearchLiteratureCandidate] = Field(default_factory=list)
    notice: str


class ResearchLiteratureCandidateImport(BaseModel):
    audit_id: int = Field(ge=1)
    candidate: ResearchLiteratureCandidate


class ResearchLiteratureRagImport(BaseModel):
    audit_id: int = Field(ge=1)
    candidate: ResearchLiteratureCandidate
    knowledge_base_id: int = Field(ge=1)
    category: Literal["method"] = "method"


class ResearchLiteratureRagImportResponse(BaseModel):
    document: DocumentResponse
    knowledge_base_id: int
    category: Literal["method"]
    notice: str


class ResearchLiteratureNetworkRequest(BaseModel):
    audit_id: int = Field(ge=1)
    candidate: ResearchLiteratureCandidate
    relation: Literal["citations", "references"] = "references"
    limit: int = Field(default=10, ge=1, le=20)
    offset: int = Field(default=0, ge=0, le=10_000)


class ResearchLiteratureNetworkResponse(BaseModel):
    audit_id: int
    source_audit_id: int
    source_candidate: ResearchLiteratureCandidate
    relation: Literal["citations", "references"]
    offset: int
    next_offset: int | None = None
    candidates: list[ResearchLiteratureCandidate] = Field(default_factory=list)
    notice: str


class ResearchStacSearchRequest(BaseModel):
    provider: Literal["planetary_computer", "earth_search"] = "planetary_computer"
    collections: list[str] = Field(min_length=1, max_length=5)
    bbox: list[float] = Field(min_length=4, max_length=4)
    datetime_start: date | None = None
    datetime_end: date | None = None
    cloud_cover_max: float | None = Field(default=None, ge=0, le=100)
    limit: int = Field(default=10, ge=1, le=20)


class ResearchStacAsset(BaseModel):
    href: str = Field(min_length=1, max_length=4000)
    title: str | None = None
    media_type: str | None = None
    roles: list[str] = Field(default_factory=list)


class ResearchStacCandidate(BaseModel):
    provider: Literal["planetary_computer", "earth_search"]
    external_id: str
    collection: str
    datetime: datetime_type | None = None
    cloud_cover: float | None = None
    assets: dict[str, ResearchStacAsset] = Field(default_factory=dict)


class ResearchStacSearchResponse(BaseModel):
    audit_id: int
    provider: Literal["planetary_computer", "earth_search"]
    query: ResearchStacSearchRequest
    candidates: list[ResearchStacCandidate] = Field(default_factory=list)
    notice: str


class ResearchStacCandidateImport(BaseModel):
    audit_id: int = Field(ge=1)
    candidate: ResearchStacCandidate
    asset_key: str = Field(min_length=1, max_length=120)
    name: str | None = Field(default=None, max_length=255)


class ResearchStacDownloadRequest(ResearchStacCandidateImport):
    crop_bbox: list[float] | None = Field(default=None, min_length=4, max_length=4)
    target_resolution: float | None = Field(default=None, gt=0, le=1_000_000)


class ResearchStacDownloadResponse(BaseModel):
    asset: ResearchDataAssetResponse
    notice: str


class ResearchValidationSampleCreate(BaseModel):
    data_snapshot_id: int | None = Field(default=None, ge=1)
    source_asset_id: int | None = Field(default=None, ge=1)
    longitude: float = Field(ge=-180, le=180)
    latitude: float = Field(ge=-90, le=90)
    label: Literal[0, 1]
    observed_at: datetime
    annotator: str = Field(min_length=1, max_length=200)
    confidence: float = Field(ge=0, le=1)
    split: Literal["development", "model_selection", "independent_test"]
    spatial_block: str = Field(min_length=1, max_length=120)
    temporal_stratum: str = Field(min_length=1, max_length=120)
    conflict_status: Literal["none", "flagged", "resolved"] = "none"
    source_note: str = Field(min_length=1, max_length=5000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResearchValidationSampleResponse(ResearchValidationSampleCreate):
    id: int
    project_id: int
    created_at: datetime


class ResearchValidationSampleImportResponse(BaseModel):
    imported_count: int = Field(ge=1)
    samples: list[ResearchValidationSampleResponse]
