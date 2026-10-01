import type {
  AgentStreamEvent,
  AuthUser,
  ChatMessage,
  ChatRun,
  ChatResponse,
  ChatSession,
  DashboardSummary,
  GeeFetchRequest,
  GeeFetchResponse,
  GeeStatusResponse,
  IdlRunRequest,
  IdlRunResponse,
  DocumentChunk,
  DocumentItem,
  EvaluationReport,
  EvaluationRunRequest,
  ImportResult,
  KnowledgeBase,
  KnowledgeBaseUpdatePayload,
  LangSmithEvaluateRequest,
  LangSmithSyncRequest,
  LoginRequest,
  LoginResponse,
  ResetPasswordPayload,
  RegisterRequest,
  ResearchDataAsset,
  ResearchDataSnapshot,
  ResearchExperiment,
  ResearchGeeFetchResponse,
  ResearchLiteratureCandidate,
  ResearchLiteratureRagImportResponse,
  ResearchLiteratureNetworkResponse,
  ResearchLiteratureSearchResponse,
  ResearchStacCandidate,
  ResearchStacDownloadResponse,
  ResearchStacSearchResponse,
  ResearchProject,
  ResearchProtocolDraft,
  ResearchProtocolEvidenceMapDraft,
  ResearchProtocolReadiness,
  ResearchProtocolRevision,
  ResearchProjectMember,
  ResearchKnowledgeSource,
  ResearchRagSearchResponse,
  ResearchRun,
  ResearchRunVerification,
  ResearchRunReproducibility,
  ResearchRunComparison,
  ResearchValidationSample,
  ResearchValidationSampleImportResult,
  EvidenceCard,
  FormulaSpec,
  RetrievalDebugRequest,
  RetrievalDebugResponse,
  StreamEvent,
  SystemSettingsPayload,
  SystemSettingsResponse,
  TestConnectionResponse,
  UserUpdatePayload,
} from './types'

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000/api'
const TOKEN_STORAGE_KEY = 'idl-rag-access-token'
const UNAUTHORIZED_EVENT = 'idl-rag:unauthorized'

type RequestOptions = RequestInit & {
  rawBody?: BodyInit | null
}

function getStoredAccessToken(): string {
  if (typeof window === 'undefined') {
    return ''
  }
  return window.localStorage.getItem(TOKEN_STORAGE_KEY) ?? ''
}

export function saveAccessToken(token: string): void {
  if (typeof window === 'undefined') {
    return
  }
  window.localStorage.setItem(TOKEN_STORAGE_KEY, token)
}

export function clearAccessToken(notify = true): void {
  if (typeof window === 'undefined') {
    return
  }
  window.localStorage.removeItem(TOKEN_STORAGE_KEY)
  if (notify) {
    window.dispatchEvent(new Event(UNAUTHORIZED_EVENT))
  }
}

export function onUnauthorized(callback: () => void): () => void {
  if (typeof window === 'undefined') {
    return () => undefined
  }
  window.addEventListener(UNAUTHORIZED_EVENT, callback)
  return () => window.removeEventListener(UNAUTHORIZED_EVENT, callback)
}

export function hasStoredAccessToken(): boolean {
  return Boolean(getStoredAccessToken())
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { rawBody, headers, ...rest } = options
  const accessToken = getStoredAccessToken()
  const response = await fetch(`${API_BASE}${path}`, {
    headers: {
      ...(rawBody ? {} : { 'Content-Type': 'application/json' }),
      ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
      ...headers,
    },
    body: rawBody ?? rest.body,
    ...rest,
  })

  if (!response.ok) {
    if (response.status === 401) {
      clearAccessToken()
    }
    const data = await response.json().catch(() => ({}))
    const message = typeof data.detail === 'string' ? data.detail : '请求失败'
    throw new Error(message)
  }

  if (response.status === 204) {
    return undefined as T
  }

  return response.json() as Promise<T>
}

type SseEvent = StreamEvent | AgentStreamEvent

async function consumeSse<T extends SseEvent>(
  response: Response,
  onEvent: (event: T) => void,
): Promise<void> {
  if (!response.body) {
    throw new Error('模型服务没有返回流式响应。')
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let terminal = false

  const consumeBlock = (block: string) => {
    const data = block
      .split(/\r?\n/)
      .filter((line) => line.startsWith('data:'))
      .map((line) => line.slice(5).trimStart())
      .join('\n')
    if (!data || terminal) return

    let event: T
    try {
      event = JSON.parse(data) as T
    } catch (error) {
      throw new Error(`模型服务返回了无法解析的 SSE 数据：${String(error)}`)
    }
    if (event.type === 'done' || event.type === 'error') {
      terminal = true
    }
    onEvent(event)
  }

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const blocks = buffer.split(/\r?\n\r?\n/)
      buffer = blocks.pop() ?? ''
      blocks.forEach(consumeBlock)
    }
    buffer += decoder.decode()
    if (buffer.trim()) consumeBlock(buffer)
    if (!terminal) {
      throw new Error('流式连接在收到完成事件前关闭，请重试。')
    }
  } finally {
    // Release the underlying stream on abort, callback failure, and truncated
    // responses. Merely releasing the lock can leave a fetch reader alive in
    // some browsers, which becomes visible after several Agent turns.
    try {
      await reader.cancel()
    } catch {
      // The reader may already be closed; cleanup should never mask the
      // original SSE or AbortError.
    }
    reader.releaseLock()
  }
}

export const api = {
  register: (payload: RegisterRequest) =>
    request<LoginResponse>('/auth/register', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  login: (payload: LoginRequest) =>
    request<LoginResponse>('/auth/login', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  getCurrentUser: () => request<AuthUser>('/auth/me'),
  listUsers: () => request<AuthUser[]>('/users'),
  updateUser: (userId: number, payload: UserUpdatePayload) =>
    request<AuthUser>(`/users/${userId}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  resetUserPassword: (userId: number, payload: ResetPasswordPayload) =>
    request<AuthUser>(`/users/${userId}/reset-password`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  getDashboardSummary: () => request<DashboardSummary>('/dashboard/summary'),
  getSettings: () => request<SystemSettingsResponse>('/settings'),
  updateSettings: (payload: SystemSettingsPayload) =>
    request<SystemSettingsResponse>('/settings', {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),
  testSettingsConnection: (payload: SystemSettingsPayload) =>
    request<TestConnectionResponse>('/settings/test-connection', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  testEmbeddingConnection: (payload: SystemSettingsPayload) =>
    request<TestConnectionResponse>('/settings/test-embedding-connection', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  testLangSmithConnection: (payload: SystemSettingsPayload) =>
    request<TestConnectionResponse>('/settings/test-langsmith-connection', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  listKnowledgeBases: () => request<KnowledgeBase[]>('/knowledge-bases'),
  createKnowledgeBase: (payload: { name: string; description?: string }) =>
    request<KnowledgeBase>('/knowledge-bases', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  updateKnowledgeBase: (knowledgeBaseId: number, payload: KnowledgeBaseUpdatePayload) =>
    request<KnowledgeBase>(`/knowledge-bases/${knowledgeBaseId}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  deleteKnowledgeBase: (knowledgeBaseId: number) =>
    request<void>(`/knowledge-bases/${knowledgeBaseId}`, { method: 'DELETE' }),
  listDocuments: (knowledgeBaseId: number) =>
    request<DocumentItem[]>(`/knowledge-bases/${knowledgeBaseId}/documents`),
  listDocumentChunks: (knowledgeBaseId: number, documentId: number) =>
    request<DocumentChunk[]>(`/knowledge-bases/${knowledgeBaseId}/documents/${documentId}/chunks`),
  importPath: (knowledgeBaseId: number, payload: { path: string; recursive: boolean }) =>
    request<ImportResult>(`/knowledge-bases/${knowledgeBaseId}/documents/import-path`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  uploadDocuments: async (knowledgeBaseId: number, files: File[]) => {
    const formData = new FormData()
    files.forEach((file) => formData.append('files', file))
    return request<ImportResult>(`/knowledge-bases/${knowledgeBaseId}/documents/upload`, {
      method: 'POST',
      rawBody: formData,
    })
  },
  retryDocument: (knowledgeBaseId: number, documentId: number) =>
    request<DocumentItem>(`/knowledge-bases/${knowledgeBaseId}/documents/${documentId}/retry`, {
      method: 'POST',
    }),
  reindexDocument: (knowledgeBaseId: number, documentId: number) =>
    request<DocumentItem>(`/knowledge-bases/${knowledgeBaseId}/documents/${documentId}/reindex`, {
      method: 'POST',
    }),
  deleteDocument: (knowledgeBaseId: number, documentId: number) =>
    request<void>(`/knowledge-bases/${knowledgeBaseId}/documents/${documentId}`, {
      method: 'DELETE',
    }),
  retrieveDebug: (payload: RetrievalDebugRequest) =>
    request<RetrievalDebugResponse>('/chat/retrieve-debug', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  getGeeStatus: () => request<GeeStatusResponse>('/chat/gee/status'),
  fetchGeeData: (payload: GeeFetchRequest) =>
    request<GeeFetchResponse>('/chat/gee/fetch', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  runLocalEvaluation: (payload: EvaluationRunRequest) =>
    request<EvaluationReport>('/evaluation/local', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  syncLangSmithDataset: (payload: LangSmithSyncRequest) =>
    request<EvaluationReport>('/evaluation/langsmith/sync', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  runLangSmithEvaluation: (payload: LangSmithEvaluateRequest) =>
    request<EvaluationReport>('/evaluation/langsmith/evaluate', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  listEvaluationReports: () => request<EvaluationReport[]>('/evaluation/reports'),
  getEvaluationReport: (reportId: number) => request<EvaluationReport>(`/evaluation/reports/${reportId}`),
  compareEvaluationReports: (leftId: number, rightId: number) =>
    request<{ left: Record<string, unknown>; right: Record<string, unknown>; deltas: Record<string, Record<string, number>> }>(
      `/evaluation/reports/compare?left=${leftId}&right=${rightId}`,
    ),
  askQuestion: (payload: {
    knowledge_base_ids?: number[]
    question: string
    session_id?: number | null
    strategy?: string
    top_k?: number
    generate_pro_file?: boolean
    attached_file_content?: string
    input_artifact_ids?: string[]
  }) =>
    request<ChatResponse>('/chat/ask', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  listSessions: () => request<ChatSession[]>('/chat/sessions'),
  renameSession: (sessionId: number, payload: { title: string }) =>
    request<ChatSession>(`/chat/sessions/${sessionId}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  deleteSession: (sessionId: number) => request<void>(`/chat/sessions/${sessionId}`, { method: 'DELETE' }),
  listMessages: (sessionId: number) =>
    request<ChatMessage[]>(`/chat/sessions/${sessionId}/messages`),
  listChatRuns: (sessionId: number, limit = 20) =>
    request<ChatRun[]>(`/chat/sessions/${sessionId}/runs?limit=${limit}`),
  runChatArtifactWithIdl: (sessionId: number, artifactId: string, payload: IdlRunRequest = {}) =>
    request<IdlRunResponse>(`/chat/sessions/${sessionId}/artifacts/${artifactId}/run-idl`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  fetchChatArtifactBlob: async (downloadPath: string) => {
    const accessToken = getStoredAccessToken()
    const response = await fetch(`${API_BASE}${downloadPath}`, {
      headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : undefined,
    })
    if (!response.ok) {
      if (response.status === 401) {
        clearAccessToken()
      }
      const data = await response.json().catch(() => ({}))
      const message = typeof data.detail === 'string' ? data.detail : '下载失败'
      throw new Error(message)
    }
    return response.blob()
  },
  downloadChatArtifact: async (downloadPath: string, fileName: string) => {
    const blob = await api.fetchChatArtifactBlob(downloadPath)
    if (typeof window === 'undefined') {
      return
    }
    const objectUrl = window.URL.createObjectURL(blob)
    const link = window.document.createElement('a')
    link.href = objectUrl
    link.download = fileName
    window.document.body.appendChild(link)
    link.click()
    link.remove()
    window.URL.revokeObjectURL(objectUrl)
  },
  listResearchProjects: () => request<ResearchProject[]>('/research/projects'),
  createResearchProject: (payload: {
    name: string
    description?: string
    entry_mode: 'template' | 'open'
    protocol?: Record<string, unknown>
  }) =>
    request<ResearchProject>('/research/projects', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  updateResearchProject: (
    projectId: number,
    payload: { name?: string; description?: string; protocol?: Record<string, unknown> },
  ) =>
    request<ResearchProject>(`/research/projects/${projectId}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  draftResearchProtocol: (projectId: number, researchQuestion: string) =>
    request<ResearchProtocolDraft>(`/research/projects/${projectId}/protocol-draft`, {
      method: 'POST',
      body: JSON.stringify({ research_question: researchQuestion }),
    }),
  draftResearchProtocolEvidenceMap: (
    projectId: number,
    researchQuestion: string,
    category: ResearchRagSearchResponse['category'] = 'method',
    topK = 6,
  ) =>
    request<ResearchProtocolEvidenceMapDraft>(`/research/projects/${projectId}/protocol-evidence-map-draft`, {
      method: 'POST',
      body: JSON.stringify({ research_question: researchQuestion, category, top_k: topK }),
    }),
  getResearchProtocolReadiness: (projectId: number) =>
    request<ResearchProtocolReadiness>(`/research/projects/${projectId}/protocol-readiness`),
  listResearchProtocolRevisions: (projectId: number) =>
    request<ResearchProtocolRevision[]>(`/research/projects/${projectId}/protocol-revisions`),
  listResearchProjectMembers: (projectId: number) =>
    request<ResearchProjectMember[]>(`/research/projects/${projectId}/members`),
  addResearchProjectMember: (projectId: number, username: string) =>
    request<ResearchProjectMember>(`/research/projects/${projectId}/members`, {
      method: 'POST',
      body: JSON.stringify({ username }),
    }),
  removeResearchProjectMember: (projectId: number, memberId: number) =>
    request<void>(`/research/projects/${projectId}/members/${memberId}`, { method: 'DELETE' }),
  listResearchRagSources: (projectId: number) =>
    request<ResearchKnowledgeSource[]>(`/research/projects/${projectId}/rag-sources`),
  addResearchRagSource: (
    projectId: number,
    payload: { knowledge_base_id: number; category: ResearchKnowledgeSource['category'] },
  ) =>
    request<ResearchKnowledgeSource>(`/research/projects/${projectId}/rag-sources`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  searchResearchRag: (
    projectId: number,
    query: string,
    category: ResearchRagSearchResponse['category'] = 'all',
    topK = 6,
  ) =>
    request<ResearchRagSearchResponse>(
      `/research/projects/${projectId}/rag-search?query=${encodeURIComponent(query)}&category=${category}&top_k=${topK}`,
    ),
  listResearchDataAssets: (projectId: number) =>
    request<ResearchDataAsset[]>(`/research/projects/${projectId}/data-assets`),
  createResearchDataSnapshot: (projectId: number, payload: { name: string; description?: string; asset_ids: number[] }) =>
    request<ResearchDataSnapshot>(`/research/projects/${projectId}/data-snapshots`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  listResearchDataSnapshots: (projectId: number) =>
    request<ResearchDataSnapshot[]>(`/research/projects/${projectId}/data-snapshots`),
  uploadResearchDataAsset: async (projectId: number, file: File, assetKind: ResearchDataAsset['asset_kind'], name?: string) => {
    const formData = new FormData()
    formData.append('file', file)
    formData.append('asset_kind', assetKind)
    if (name) {
      formData.append('name', name)
    }
    return request<ResearchDataAsset>(`/research/projects/${projectId}/data-assets/upload`, {
      method: 'POST',
      rawBody: formData,
    })
  },
  uploadResearchIdlScript: async (projectId: number, file: File, name?: string) => {
    const formData = new FormData()
    formData.append('file', file)
    if (name) {
      formData.append('name', name)
    }
    return request<ResearchDataAsset>(`/research/projects/${projectId}/idl-scripts/upload`, {
      method: 'POST',
      rawBody: formData,
    })
  },
  stackResearchRasterAssets: (
    projectId: number,
    payload: {
      name: string
      asset_ids: number[]
      reference_asset_id?: number
      band_names?: string[]
      resampling?: 'nearest' | 'bilinear' | 'cubic'
    },
  ) =>
    request<ResearchDataAsset>(`/research/projects/${projectId}/data-assets/stack`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  searchResearchLiterature: (
    projectId: number,
    query: string,
    rows = 8,
    provider: ResearchLiteratureSearchResponse['provider'] = 'crossref',
  ) =>
    request<ResearchLiteratureSearchResponse>(
      `/research/projects/${projectId}/literature-search?query=${encodeURIComponent(query)}&rows=${rows}&provider=${provider}`,
    ),
  importResearchLiteratureCandidate: (
    projectId: number,
    auditId: number,
    candidate: ResearchLiteratureCandidate,
  ) =>
    request<EvidenceCard>(`/research/projects/${projectId}/literature-search/evidence-cards`, {
      method: 'POST',
      body: JSON.stringify({ audit_id: auditId, candidate }),
    }),
  importResearchLiteratureToRag: (
    projectId: number,
    payload: {
      audit_id: number
      candidate: ResearchLiteratureCandidate
      knowledge_base_id: number
      category?: 'method'
    },
  ) =>
    request<ResearchLiteratureRagImportResponse>(`/research/projects/${projectId}/literature-search/rag-import`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  expandResearchLiteratureNetwork: (
    projectId: number,
    payload: {
      audit_id: number
      candidate: ResearchLiteratureCandidate
      relation: 'citations' | 'references'
      limit?: number
      offset?: number
    },
  ) =>
    request<ResearchLiteratureNetworkResponse>(`/research/projects/${projectId}/literature-search/semantic-scholar-network`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  searchResearchStac: (
    projectId: number,
    payload: {
      provider: ResearchStacSearchResponse['provider']
      collections: string[]
      bbox: number[]
      datetime_start?: string
      datetime_end?: string
      cloud_cover_max?: number
      limit?: number
    },
  ) =>
    request<ResearchStacSearchResponse>(`/research/projects/${projectId}/stac-search`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  importResearchStacReference: (
    projectId: number,
    auditId: number,
    candidate: ResearchStacCandidate,
    assetKey: string,
    name?: string,
  ) =>
    request<ResearchDataAsset>(`/research/projects/${projectId}/stac-search/import`, {
      method: 'POST',
      body: JSON.stringify({ audit_id: auditId, candidate, asset_key: assetKey, name }),
    }),
  downloadResearchStacAsset: (
    projectId: number,
    auditId: number,
    candidate: ResearchStacCandidate,
    assetKey: string,
    options?: { name?: string; crop_bbox?: number[]; target_resolution?: number },
  ) =>
    request<ResearchStacDownloadResponse>(`/research/projects/${projectId}/stac-search/download`, {
      method: 'POST',
      body: JSON.stringify({ audit_id: auditId, candidate, asset_key: assetKey, ...options }),
    }),
  fetchResearchGeeAsset: (
    projectId: number,
    payload: {
      dataset_id: string
      start_date?: string
      end_date?: string
      bbox: number[]
      bands: string[]
      scale: number
      crs: string
      composite: 'median' | 'mean' | 'first'
      label?: string
    },
  ) =>
    request<ResearchGeeFetchResponse>(`/research/projects/${projectId}/gee-fetch`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  listResearchValidationSamples: (projectId: number) =>
    request<ResearchValidationSample[]>(`/research/projects/${projectId}/validation-samples`),
  createResearchValidationSample: (
    projectId: number,
    payload: Omit<ResearchValidationSample, 'id' | 'project_id' | 'created_at' | 'metadata'> & { metadata?: Record<string, unknown> },
  ) =>
    request<ResearchValidationSample>(`/research/projects/${projectId}/validation-samples`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  importResearchValidationSamples: async (
    projectId: number,
    file: File,
    dataSnapshotId: number,
    sourceAssetId?: number,
  ) => {
    const formData = new FormData()
    formData.append('file', file)
    formData.append('data_snapshot_id', String(dataSnapshotId))
    if (sourceAssetId !== undefined) {
      formData.append('source_asset_id', String(sourceAssetId))
    }
    return request<ResearchValidationSampleImportResult>(`/research/projects/${projectId}/validation-samples/import`, {
      method: 'POST',
      rawBody: formData,
    })
  },
  listEvidenceCards: (projectId: number) => request<EvidenceCard[]>(`/research/projects/${projectId}/evidence-cards`),
  createEvidenceCard: (
    projectId: number,
    payload: {
      title: string
      status: EvidenceCard['status']
      source_type: EvidenceCard['source_type']
      source_url?: string
      doi?: string
      license_note?: string
      applicability?: string
      limitations?: string
      metadata?: Record<string, unknown>
    },
  ) =>
    request<EvidenceCard>(`/research/projects/${projectId}/evidence-cards`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  listFormulaSpecs: (projectId: number) => request<FormulaSpec[]>(`/research/projects/${projectId}/formula-specs`),
  createFormulaSpec: (
    projectId: number,
    payload: { name: string; version: number; status: FormulaSpec['status']; spec: Record<string, unknown>; evidence_card_ids: number[] },
  ) =>
    request<FormulaSpec>(`/research/projects/${projectId}/formula-specs`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  listResearchExperiments: (projectId: number) =>
    request<ResearchExperiment[]>(`/research/projects/${projectId}/experiments`),
  createResearchExperiment: (
    projectId: number,
    payload: Omit<ResearchExperiment, 'id' | 'project_id' | 'status' | 'created_at' | 'project_protocol_hash'>,
  ) =>
    request<ResearchExperiment>(`/research/projects/${projectId}/experiments`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  listResearchRuns: (projectId: number, experimentId: number) =>
    request<ResearchRun[]>(`/research/projects/${projectId}/experiments/${experimentId}/runs`),
  listResearchProjectRuns: (projectId: number, limit = 20) =>
    request<ResearchRun[]>(`/research/projects/${projectId}/runs?limit=${limit}`),
  startResearchRun: (projectId: number, experimentId: number, mode: 'sync' | 'queue' = 'sync') =>
    request<ResearchRun>(`/research/projects/${projectId}/experiments/${experimentId}/runs?mode=${mode}`, { method: 'POST' }),
  startResearchParameterSweep: (
    projectId: number,
    experimentId: number,
    payload: {
      candidates: Array<{ name: string; parameters: Record<string, unknown> }>
      evaluation_split: 'development' | 'model_selection'
      ranking_metric: 'overall_accuracy' | 'precision' | 'recall' | 'f1' | 'iou'
    },
    mode: 'sync' | 'queue' = 'sync',
  ) =>
    request<ResearchRun>(`/research/projects/${projectId}/experiments/${experimentId}/sweeps?mode=${mode}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  cancelResearchRun: (projectId: number, experimentId: number, runId: number) =>
    request<ResearchRun>(`/research/projects/${projectId}/experiments/${experimentId}/runs/${runId}/cancel`, { method: 'POST' }),
  retryResearchRun: (projectId: number, experimentId: number, runId: number, mode: 'sync' | 'queue' = 'queue') =>
    request<ResearchRun>(`/research/projects/${projectId}/experiments/${experimentId}/runs/${runId}/retry?mode=${mode}`, { method: 'POST' }),
  verifyResearchRun: (projectId: number, experimentId: number, runId: number) =>
    request<ResearchRunVerification>(
      `/research/projects/${projectId}/experiments/${experimentId}/runs/${runId}/verification`,
    ),
  compareResearchRun: (
    projectId: number,
    experimentId: number,
    runId: number,
    payload: { reference_run_id: number; absolute_tolerance?: number; relative_tolerance?: number },
  ) =>
    request<ResearchRunReproducibility>(
      `/research/projects/${projectId}/experiments/${experimentId}/runs/${runId}/reproducibility`,
      { method: 'POST', body: JSON.stringify(payload) },
    ),
  compareFormalResearchRuns: (projectId: number, experimentId: number, runId: number, referenceRunId: number) =>
    request<ResearchRunComparison>(
      `/research/projects/${projectId}/experiments/${experimentId}/runs/${runId}/comparison`,
      { method: 'POST', body: JSON.stringify({ reference_run_id: referenceRunId }) },
    ),
  fetchResearchRunOutputBlob: async (projectId: number, experimentId: number, runId: number, fileName: string) => {
    const accessToken = getStoredAccessToken()
    const response = await fetch(
      `${API_BASE}/research/projects/${projectId}/experiments/${experimentId}/runs/${runId}/outputs/${encodeURIComponent(fileName)}`,
      { headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : undefined },
    )
    if (!response.ok) {
      if (response.status === 401) {
        clearAccessToken()
      }
      const data = await response.json().catch(() => ({}))
      throw new Error(typeof data.detail === 'string' ? data.detail : '获取运行产物失败')
    }
    return response.blob()
  },
  askQuestionStream: async (
    payload: {
      knowledge_base_ids?: number[]
      question: string
      session_id?: number | null
      generate_pro_file?: boolean
      strategy?: string
      top_k?: number
      attached_file_content?: string
      input_artifact_ids?: string[]
    },
    callbacks: {
      onRunStarted?: (sessionId: number) => void
      onToken: (content: string) => void
      onDone: (sessionId: number, citations: unknown[], artifacts: unknown[]) => void
      onError: (message: string) => void
    },
    signal?: AbortSignal,
  ): Promise<void> => {
    const accessToken = getStoredAccessToken()
    const response = await fetch(`${API_BASE}/chat/ask-stream`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
      },
      body: JSON.stringify(payload),
      signal,
    })

    if (!response.ok) {
      if (response.status === 401) {
        clearAccessToken()
      }
      const data = await response.json().catch(() => ({}))
      const message = typeof data.detail === 'string' ? data.detail : '请求失败'
      throw new Error(message)
    }

    await consumeSse<StreamEvent>(response, (event) => {
      if (event.type === 'run_started') {
        callbacks.onRunStarted?.(event.session_id)
      } else if (event.type === 'token') {
        callbacks.onToken(event.content)
      } else if (event.type === 'done') {
        callbacks.onDone(event.session_id, event.citations, event.artifacts)
      } else if (event.type === 'error') {
        callbacks.onError(event.message)
      }
    })
  },
  agentStream: async (
    payload: {
      knowledge_base_ids?: number[]
      question: string
      session_id?: number | null
      generate_pro_file?: boolean
      strategy?: string
      top_k?: number
      attached_file_content?: string
      input_artifact_ids?: string[]
      research_project_id?: number
      allow_external_research?: boolean
      allow_research_execution?: boolean
      allow_gee_fetch?: boolean
    },
    callbacks: {
      onRunStarted?: (sessionId: number) => void
      onStep: (step: AgentStreamEvent) => void
      onToken: (content: string) => void
      onDone: (event: Extract<AgentStreamEvent, { type: 'done' }>) => void
      onError: (message: string) => void
    },
    signal?: AbortSignal,
  ): Promise<void> => {
    const accessToken = getStoredAccessToken()
    const response = await fetch(`${API_BASE}/chat/agent-stream`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
      },
      body: JSON.stringify(payload),
      signal,
    })

    if (!response.ok) {
      if (response.status === 401) {
        clearAccessToken()
      }
      const data = await response.json().catch(() => ({}))
      const message = typeof data.detail === 'string' ? data.detail : '请求失败'
      throw new Error(message)
    }

    await consumeSse<AgentStreamEvent>(response, (event) => {
      if (event.type === 'run_started') {
        callbacks.onRunStarted?.(event.session_id)
      } else if (event.type === 'step') {
        callbacks.onStep(event)
      } else if (event.type === 'token') {
        callbacks.onToken(event.content)
      } else if (event.type === 'done') {
        callbacks.onDone(event)
      } else if (event.type === 'error') {
        callbacks.onError(event.message)
      }
    })
  },
  uploadTempFile: async (file: File): Promise<{ file_name: string; content: string }> => {
    const accessToken = getStoredAccessToken()
    const formData = new FormData()
    formData.append('file', file)
    const response = await fetch(`${API_BASE}/chat/upload-temp`, {
      method: 'POST',
      headers: {
        ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
      },
      body: formData,
    })
    if (!response.ok) {
      if (response.status === 401) {
        clearAccessToken()
      }
      const data = await response.json().catch(() => ({}))
      const message = typeof data.detail === 'string' ? data.detail : '文件上传失败'
      throw new Error(message)
    }
    return response.json() as Promise<{ file_name: string; content: string }>
  },
}
