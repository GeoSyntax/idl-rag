export type SystemSettingsPayload = {
  provider_name: string
  api_base_url: string
  api_key: string
  chat_model: string
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
  | { type: 'token'; content: string }
  | { type: 'done'; session_id: number; citations: Citation[]; artifacts: ChatArtifact[] }
  | { type: 'error'; message: string }

export type AgentStep =
  | { step: 'thinking'; content: string }
  | { step: 'tool_call'; tool: string; args: Record<string, string> }
  | { step: 'tool_result'; tool: string; output: string }
  | { step: 'answer'; content: string }
  | { step: 'error'; content: string }

export type AgentStreamEvent =
  | { type: 'step'; step: string; content?: string; tool?: string; args?: Record<string, string>; output?: string }
  | { type: 'token'; content: string }
  | { type: 'done'; session_id: number; citations: Citation[]; artifacts: ChatArtifact[] }
  | { type: 'error'; message: string }

export type AgentChatRequest = {
  knowledge_base_ids?: number[]
  question: string
  session_id?: number
  strategy?: string
  top_k?: number
  generate_pro_file?: boolean
  attached_file_content?: string
}
