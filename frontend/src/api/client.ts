import type {
  AgentStreamEvent,
  AuthUser,
  ChatMessage,
  ChatResponse,
  ChatSession,
  DashboardSummary,
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
  askQuestion: (payload: {
    knowledge_base_ids?: number[]
    question: string
    session_id?: number | null
    strategy?: string
    top_k?: number
    generate_pro_file?: boolean
    attached_file_content?: string
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
  askQuestionStream: async (
    payload: {
      knowledge_base_ids?: number[]
      question: string
      session_id?: number | null
      generate_pro_file?: boolean
      strategy?: string
      top_k?: number
      attached_file_content?: string
    },
    callbacks: {
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

    const reader = response.body!.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    try {
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop()!
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          try {
            const event: StreamEvent = JSON.parse(line.slice(6))
            if (event.type === 'token') {
              callbacks.onToken(event.content)
            } else if (event.type === 'done') {
              callbacks.onDone(event.session_id, event.citations, event.artifacts)
            } else if (event.type === 'error') {
              callbacks.onError(event.message)
            }
          } catch {
            // skip malformed SSE lines
          }
        }
      }
    } finally {
      reader.releaseLock()
    }
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
    },
    callbacks: {
      onStep: (step: AgentStreamEvent) => void
      onToken: (content: string) => void
      onDone: (sessionId: number, citations: unknown[], artifacts: unknown[]) => void
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

    const reader = response.body!.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    try {
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop()!
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          try {
            const event = JSON.parse(line.slice(6)) as AgentStreamEvent
            if (event.type === 'step') {
              callbacks.onStep(event)
            } else if (event.type === 'token') {
              callbacks.onToken(event.content)
            } else if (event.type === 'done') {
              callbacks.onDone(event.session_id, event.citations, event.artifacts)
            } else if (event.type === 'error') {
              callbacks.onError(event.message)
            }
          } catch {
            // skip malformed SSE lines
          }
        }
      }
    } finally {
      reader.releaseLock()
    }
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
