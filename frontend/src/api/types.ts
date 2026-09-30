export type SystemSettingsPayload = {
  provider_name: string
  api_base_url: string
  api_key: string
  chat_model: string
  embedding_api_base_url: string
  embedding_api_key: string
  embedding_model: string
  system_prompt: string
  temperature: number
  rerank_api_url: string
  rerank_api_key: string
  rerank_model: string
  langsmith_enabled: boolean
  langsmith_api_key: string
  langsmith_project: string
  langsmith_dataset: string
  langsmith_endpoint: string
}

export type SystemSettingsResponse = SystemSettingsPayload & {
  has_api_key: boolean
  has_embedding_api_key: boolean
  has_rerank_api_key: boolean
  has_langsmith_api_key: boolean
}

export type TestConnectionResponse = {
  ok: boolean
  message: string
}

export type AuthUser = {
  id: number
  username: string
  role: 'admin' | 'user'
  is_active: boolean
  created_at: string
  updated_at: string
}

export type RegisterRequest = {
  username: string
  password: string
}

export type LoginRequest = {
  username: string
  password: string
}

export type LoginResponse = {
  access_token: string
  token_type: string
  user: AuthUser
}

export type UserUpdatePayload = {
  role?: 'admin' | 'user'
  is_active?: boolean
}

export type ResetPasswordPayload = {
  new_password: string
}

export type KnowledgeBase = {
  id: number
  name: string
  description: string | null
  document_count: number
  default_retrieval_strategy: string
  default_top_k: number
  default_rerank_enabled: boolean
  created_at: string
  updated_at: string
}

export type KnowledgeBaseUpdatePayload = {
  name?: string
  description?: string | null
  default_retrieval_strategy?: string
  default_top_k?: number
  default_rerank_enabled?: boolean
}

export type DocumentItem = {
  id: number
  knowledge_base_id: number
  file_name: string
  file_path: string
  media_type: string
  status: string
  error_message: string | null
  chunk_count: number
  retry_count: number
  parser_version: string | null
  chunker_version: string | null
  embedding_model: string | null
  embedding_dimensions: number | null
  embedding_is_fallback: boolean
  index_table: string | null
  last_indexed_at: string | null
  created_at: string
  updated_at: string
}

export type DocumentChunk = {
  id: number
  knowledge_base_id: number
  document_id: number
  chunk_index: number
  title: string | null
  section: string | null
  symbol_name: string | null
  content: string
  metadata: Record<string, unknown>
  created_at: string
}

export type ImportResult = {
  imported: DocumentItem[]
  skipped: string[]
}

export type Citation = {
  chunk_id: number
  document_id: number
  file_name: string
  file_path: string
  title?: string | null
  section?: string | null
  symbol_name?: string | null
  excerpt: string
  knowledge_base_id?: number | null
  knowledge_base_name?: string | null
  chunk_kind?: string | null
  score?: number | null
  source_strategy?: string | null
  match_type?: string | null
  line_start?: number | null
  line_end?: number | null
  metadata?: Record<string, unknown>
}

export type RetrievalDebugRequest = {
  knowledge_base_ids: number[]
  query: string
  strategy?: string
  top_k?: number
}

export type RetrievalDebugInfo = {
  score?: number | null
  scores?: Record<string, number>
  match?: Record<string, unknown>
  fusion?: Record<string, unknown>
  [key: string]: unknown
}

export type RetrievalDebugCandidate = Citation & {
  rank: number
  debug: RetrievalDebugInfo
}

export type RetrievalDebugResponse = {
  query: string
  strategy: string
  candidate_count: number
  candidates: RetrievalDebugCandidate[]
}

export type EvaluationRunRequest = {
  knowledge_base_id: number
  categories?: string[] | null
  strategies?: string[] | null
  top_k?: number
  limit?: number | null
}

export type LangSmithSyncRequest = {
  dataset?: string | null
  dry_run?: boolean
}

export type LangSmithEvaluateRequest = {
  knowledge_base_id: number
  dataset?: string | null
  strategies?: string[] | null
  top_k?: number
}

export type EvaluationReport = {
  id: number
  knowledge_base_id: number | null
  created_by_user_id: number | null
  report_type: string
  strategy: string | null
  dataset: string | null
  status: string
  summary_json: Record<string, unknown>
  report_json: Record<string, unknown>
  report_path: string | null
  error_message: string | null
  created_at: string
}

export type ChatArtifact = {
  id: string
  file_name: string
  media_type: string
  size: number
  download_url: string
  kind?: 'pro' | 'idl_output' | 'idl_log' | 'gee_data' | 'gee_preview' | null
  previewable?: boolean
  run_id?: string | null
  input_artifact_ids?: string[]
  metadata?: Record<string, unknown>
}

export type ChatMessage = {
  id: number
  role: string
  content: string
  citations: Citation[]
  artifacts: ChatArtifact[]
  created_at: string
}

export type ChatResponse = {
  session_id: number
  answer: string
  citations: Citation[]
  messages: ChatMessage[]
}

export type GeeStatusResponse = {
  enabled: boolean
  initialized: boolean
  project?: string | null
  auth_mode?: string | null
  has_credentials: boolean
  message: string
}

export type GeeFetchRequest = {
  session_id?: number | null
  dataset_id: string
  start_date?: string | null
  end_date?: string | null
  bbox: number[]
  bands?: string[]
  scale?: number
  crs?: string
  composite?: 'median' | 'mean' | 'first'
  label?: string | null
}

export type GeeFetchResponse = {
  session_id: number
  message: ChatMessage
  artifact: ChatArtifact
}

export type IdlRunRequest = {
  entrypoint?: string
  timeout_seconds?: number
  input_artifact_ids?: string[]
}

export type IdlRunResponse = {
  run_id: string
  session_id: number
  message: ChatMessage
  exit_code: number | null
  stdout: string
  stderr: string
  timed_out: boolean
  duration_ms: number
  artifacts: ChatArtifact[]
}

export type ChatSession = {
  id: number
  knowledge_base_id: number | null
  research_project_id: number | null
  title: string | null
  created_at: string
}

export type DashboardSummary = {
  knowledge_base_count: number
  document_count: number
  ready_document_count: number
  queued_document_count: number
  processing_document_count: number
  stale_document_count: number
  failed_document_count: number
  fallback_document_count: number
  chunk_count: number
  avg_chunks_per_document: number | null
  avg_index_job_seconds: number | null
  queued_index_job_count: number
  processing_index_job_count: number
  failed_index_job_count: number
  worker_alive: boolean
  worker_mode: 'embedded' | 'external' | 'disabled'
  worker_last_error: string | null
  embedding_fallback_active: boolean
  embedding_last_error: string | null
  chat_session_count: number
  chat_request_count: number
  chat_latency_p95_ms: number | null
  chat_latency_p99_ms: number | null
  chat_first_token_count: number
  chat_first_token_p95_ms: number | null
  chat_first_token_p99_ms: number | null
  latest_eval_hit_rate: number | null
  latest_eval_top_k: number | null
  latest_eval_strategy: string | null
  avg_retrieve_ms: number | null
  avg_rerank_ms: number | null
  avg_llm_first_token_ms: number | null
  avg_total_ms: number | null
  citation_coverage: number | null
  error_rate: number | null
}

export type StreamEvent =
  | { type: 'token'; content: string; stream_id?: string }
  | { type: 'done'; session_id: number; citations: Citation[]; artifacts: ChatArtifact[]; stream_id?: string; server_elapsed_ms?: number; first_token_ms?: number }
  | { type: 'error'; message: string; stream_id?: string; server_elapsed_ms?: number; first_token_ms?: number }

export type AgentStep =
  | { step: 'thinking'; content: string }
  | { step: 'tool_call'; tool: string; args: Record<string, string> }
  | { step: 'tool_result'; tool: string; output: string }
  | { step: 'answer'; content: string }
  | { step: 'error'; content: string }

export type AgentStreamEvent =
  | { type: 'step'; step: string; content?: string; tool?: string; args?: Record<string, unknown>; output?: string; metadata?: Record<string, unknown>; stream_id?: string }
  | { type: 'token'; content: string; stream_id?: string }
  | { type: 'done'; session_id: number; citations: Citation[]; artifacts: ChatArtifact[]; stream_id?: string; server_elapsed_ms?: number; first_token_ms?: number }
  | { type: 'error'; message: string; stream_id?: string; server_elapsed_ms?: number; first_token_ms?: number }

export type AgentChatRequest = {
  knowledge_base_ids?: number[]
  question: string
  session_id?: number
  strategy?: string
  top_k?: number
  generate_pro_file?: boolean
  attached_file_content?: string
  input_artifact_ids?: string[]
  research_project_id?: number
  allow_external_research?: boolean
  allow_research_execution?: boolean
  allow_gee_fetch?: boolean
}

export type ResearchProject = {
  id: number
  owner_user_id: number
  name: string
  description: string | null
  entry_mode: 'template' | 'open'
  visibility: 'my'
  egress_policy: 'private-local'
  status: 'exploratory'
  protocol: Record<string, unknown>
  created_at: string
  updated_at: string
}

export type ResearchProtocolDraft = {
  protocol: Record<string, unknown>
  notice: string
}

export type ResearchProtocolEvidenceMapEntry = {
  citation: Citation
  protocol_section: 'method_plan'
  review_status: 'unverified'
  researcher_action: string
}

export type ResearchProtocolEvidenceMapDraft = ResearchProtocolDraft & {
  query: string
  category: 'method' | 'idl_code' | 'python_code' | 'all'
  strategy: string
  searched_knowledge_base_ids: number[]
  citations: Citation[]
  evidence_map: ResearchProtocolEvidenceMapEntry[]
}

export type ResearchProtocolRevision = {
  id: number
  project_id: number
  version: number
  protocol: Record<string, unknown>
  protocol_hash: string
  saved_by_user_id: number
  created_at: string
}

export type ResearchProtocolReadinessItem = {
  code: string
  path: string
  message: string
}

export type ResearchProtocolReadiness = {
  project_id: number
  ready: boolean
  protocol_hash: string
  protocol_revision_id: number | null
  missing: ResearchProtocolReadinessItem[]
  notice: string
}

export type ResearchProjectMember = {
  id: number
  project_id: number
  user_id: number
  username: string
  added_by_user_id: number
  created_at: string
}

export type ResearchKnowledgeSource = {
  id: number
  project_id: number
  knowledge_base_id: number
  knowledge_base_name: string
  category: 'method' | 'idl_code' | 'python_code'
  document_count: number
  added_by_user_id: number
  created_at: string
}

export type ResearchRagSearchResponse = {
  query: string
  category: 'method' | 'idl_code' | 'python_code' | 'all'
  strategy: string
  searched_knowledge_base_ids: number[]
  citations: Citation[]
  notice: string
}

export type ResearchDataAsset = {
  id: number
  project_id: number
  name: string
  asset_kind: 'raster' | 'vector' | 'table' | 'roi' | 'reference' | 'derived'
  source_type: 'local' | 'gee' | 'reference'
  source_uri: string
  sha256: string | null
  metadata: Record<string, unknown>
  access_policy: 'private-local'
  created_at: string
}

export type ResearchDataSnapshot = {
  id: number
  project_id: number
  name: string
  description: string | null
  asset_ids: number[]
  snapshot_hash: string
  is_frozen: true
  frozen_at: string
  created_at: string
}

export type EvidenceCard = {
  id: number
  project_id: number
  title: string
  status: 'candidate' | 'verified' | 'imported' | 'experiment_pinned'
  source_type: 'paper' | 'official_document' | 'code' | 'dataset' | 'web'
  source_url: string | null
  doi: string | null
  license_note: string | null
  applicability: string | null
  limitations: string | null
  metadata: Record<string, unknown>
  retrieved_at: string
  created_at: string
}

export type FormulaSpec = {
  id: number
  project_id: number
  name: string
  version: number
  status: 'draft' | 'candidate' | 'frozen'
  spec: Record<string, unknown>
  evidence_card_ids: number[]
  created_at: string
  updated_at: string
}

export type ResearchExperiment = {
  id: number
  project_id: number
  formula_spec_id: number
  data_snapshot_id: number
  name: string
  runner_type: 'python' | 'idl'
  execution_mode: 'preview' | 'formal'
  status: 'planned' | 'running' | 'completed' | 'failed' | 'cancelled' | 'unavailable'
  parameters: Record<string, unknown>
  validation_plan: Record<string, unknown>
  visualization_contract: string[]
  project_protocol_hash: string
  created_at: string
}

export type ResearchRunOutput = {
  kind: string
  file_name: string
  uri: string
  sha256: string
  size: number
  metadata: Record<string, unknown>
}

export type ResearchRun = {
  id: number
  project_id: number
  experiment_id: number
  runner_type: 'python' | 'idl'
  status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled' | 'unavailable'
  run_token: string
  manifest: Record<string, unknown>
  outputs: ResearchRunOutput[]
  error_message: string | null
  started_at: string | null
  finished_at: string | null
  created_at: string
}

export type ResearchRunVerification = {
  run_id: number
  run_token: string
  status: 'verified' | 'failed' | 'not_available'
  verified: boolean
  package_file_name: string | null
  package_sha256: string | null
  checked_file_count: number
  output_count: number
  issues: string[]
  notice: string
}

export type ResearchRunReproducibility = {
  run_id: number
  reference_run_id: number
  status: 'matched' | 'failed'
  matched: boolean
  absolute_tolerance: number
  relative_tolerance: number
  compared_output_count: number
  exact_matches: string[]
  raster_comparisons: Array<Record<string, unknown>>
  issues: string[]
  notice: string
}

export type ResearchRunComparison = {
  run_id: number
  reference_run_id: number
  run_kind: 'formal_comparison'
  status: 'compared' | 'failed'
  comparable: boolean
  snapshot_hash: string | null
  compared_metric_count: number
  metrics: Array<{
    scope: string
    metric: string
    baseline: number
    candidate: number
    delta: number
    direction: string
  }>
  issues: string[]
  notice: string
}

export type ResearchLiteratureCandidate = {
  provider: 'crossref' | 'openalex' | 'semantic_scholar'
  external_id: string
  title: string
  authors: string[]
  container_title: string | null
  published_year: number | null
  doi: string | null
  source_url: string | null
  item_type: string | null
  abstract: string | null
}

export type ResearchLiteratureSearchResponse = {
  audit_id: number
  provider: 'crossref' | 'openalex' | 'semantic_scholar'
  query: string
  candidates: ResearchLiteratureCandidate[]
  notice: string
}

export type ResearchLiteratureRagImportResponse = {
  document: DocumentItem
  knowledge_base_id: number
  category: 'method'
  notice: string
}

export type ResearchLiteratureNetworkResponse = {
  audit_id: number
  source_audit_id: number
  source_candidate: ResearchLiteratureCandidate
  relation: 'citations' | 'references'
  offset: number
  next_offset: number | null
  candidates: ResearchLiteratureCandidate[]
  notice: string
}

export type ResearchStacAsset = {
  href: string
  title: string | null
  media_type: string | null
  roles: string[]
}

export type ResearchStacCandidate = {
  provider: 'planetary_computer' | 'earth_search'
  external_id: string
  collection: string
  datetime: string | null
  cloud_cover: number | null
  assets: Record<string, ResearchStacAsset>
}

export type ResearchStacSearchResponse = {
  audit_id: number
  provider: 'planetary_computer' | 'earth_search'
  query: {
    provider: 'planetary_computer' | 'earth_search'
    collections: string[]
    bbox: number[]
    datetime_start: string | null
    datetime_end: string | null
    cloud_cover_max: number | null
    limit: number
  }
  candidates: ResearchStacCandidate[]
  notice: string
}

export type ResearchStacDownloadResponse = {
  asset: ResearchDataAsset
  notice: string
}

export type ResearchGeeFetchResponse = {
  asset: ResearchDataAsset
  notice: string
}

export type ResearchValidationSample = {
  id: number
  project_id: number
  data_snapshot_id: number | null
  source_asset_id: number | null
  longitude: number
  latitude: number
  label: 0 | 1
  observed_at: string
  annotator: string
  confidence: number
  split: 'development' | 'model_selection' | 'independent_test'
  spatial_block: string
  temporal_stratum: string
  conflict_status: 'none' | 'flagged' | 'resolved'
  source_note: string
  metadata: Record<string, unknown>
  created_at: string
}

export type ResearchValidationSampleImportResult = {
  imported_count: number
  samples: ResearchValidationSample[]
}
