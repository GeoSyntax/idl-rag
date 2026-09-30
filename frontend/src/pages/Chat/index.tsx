import { Button, Checkbox, Drawer, Form, Input, InputNumber, Select, Segmented, Space, Tag, Upload, message, Tooltip } from 'antd'
import {
  UploadOutlined,
  CloseCircleFilled,
  SendOutlined,
  StopOutlined,
  PaperClipOutlined,
  FileTextOutlined,
  PlusOutlined,
  EditOutlined,
  DeleteOutlined,
  CloudDownloadOutlined,
} from '@ant-design/icons'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { api } from '../../api/client'
import type {
  AgentStreamEvent,
  ChatArtifact,
  ChatMessage,
  ChatSession,
  GeeFetchRequest,
  KnowledgeBase,
  ResearchDataAsset,
  ResearchKnowledgeSource,
  ResearchProject,
  ResearchProtocolReadiness,
} from '../../api/types'
import { DisplayEmpty, InlineIllustration } from '../../components/DisplayPrimitives'
import { KnowledgeStatusBar, MessageList, ResearchContextBar } from './components'
import { useKnowledgeStatus } from './hooks'
import type { AgentStepItem, AttachedFile } from './types'

type ChatPageProps = {
  knowledgeBases: KnowledgeBase[]
  initialKnowledgeBaseId?: number
  initialResearchProjectId?: number
}

type GeeFetchFormValues = {
  dataset_id: string
  start_date?: string
  end_date?: string
  bbox: string
  bands?: string
  scale?: number
  crs?: string
  composite?: 'median' | 'mean' | 'first'
  label?: string
}

export function ChatPage({ knowledgeBases, initialKnowledgeBaseId, initialResearchProjectId }: ChatPageProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [sessionId, setSessionId] = useState<number | null>(null)
  const [sessions, setSessions] = useState<ChatSession[]>([])
  const [sessionsLoading, setSessionsLoading] = useState(false)
  const [messageApi, contextHolder] = message.useMessage()

  const [isStreaming, setIsStreaming] = useState(false)
  const [streamingContent, setStreamingContent] = useState('')
  const [streamError, setStreamError] = useState('')
  const [agentSteps, setAgentSteps] = useState<AgentStepItem[]>([])
  const [agentRunComplete, setAgentRunComplete] = useState(false)
  const [chatMode, setChatMode] = useState<'normal' | 'agent'>('normal')
  const [fixTarget, setFixTarget] = useState<{ artifactId: string; fileName: string } | null>(null)
  const [generateProFile, setGenerateProFile] = useState(false)
  const [runningArtifactId, setRunningArtifactId] = useState<string | null>(null)
  const [geeDrawerOpen, setGeeDrawerOpen] = useState(false)
  const [fetchingGee, setFetchingGee] = useState(false)
  const [selectedInputArtifacts, setSelectedInputArtifacts] = useState<ChatArtifact[]>([])
  const [geeForm] = Form.useForm<GeeFetchFormValues>()

  const [selectedKBIds, setSelectedKBIds] = useState<number[]>([])
  const [researchProjects, setResearchProjects] = useState<ResearchProject[]>([])
  const [researchProjectId, setResearchProjectId] = useState<number | undefined>()
  const [researchContext, setResearchContext] = useState<{
    project: ResearchProject
    readiness: ResearchProtocolReadiness | null
    sources: ResearchKnowledgeSource[]
    assets: ResearchDataAsset[]
    loading: boolean
    error: string
  } | null>(null)
  const [allowExternalResearch, setAllowExternalResearch] = useState(false)
  const [allowResearchExecution, setAllowResearchExecution] = useState(false)
  const [allowGeeFetch, setAllowGeeFetch] = useState(false)
  const [attachedFile, setAttachedFile] = useState<AttachedFile | null>(null)
  const [uploading, setUploading] = useState(false)
  const [inputValue, setInputValue] = useState('')

  const abortRef = useRef<AbortController | null>(null)
  const streamTerminalRef = useRef(false)
  const streamRequestIdRef = useRef(0)
  const messagesEndRef = useRef<HTMLDivElement | null>(null)
  const inputRef = useRef<HTMLTextAreaElement | null>(null)
  const stepIdRef = useRef(0)
  const restoringSessionRef = useRef(false)
  const streamingBufferRef = useRef('')
  const streamingFlushRef = useRef<number | null>(null)

  const clearStreamingBuffer = useCallback(() => {
    if (streamingFlushRef.current !== null && typeof window !== 'undefined') {
      window.cancelAnimationFrame(streamingFlushRef.current)
      streamingFlushRef.current = null
    }
    streamingBufferRef.current = ''
    setStreamingContent('')
  }, [])

  const appendStreamingText = useCallback((content: string, requestId: number) => {
    if (streamTerminalRef.current || streamRequestIdRef.current !== requestId) return
    streamingBufferRef.current += content
    if (streamingFlushRef.current !== null || typeof window === 'undefined') return
    streamingFlushRef.current = window.requestAnimationFrame(() => {
      streamingFlushRef.current = null
      if (!streamTerminalRef.current && streamRequestIdRef.current === requestId) {
        setStreamingContent(streamingBufferRef.current)
      }
    })
  }, [])

  const showError = useCallback((text: string) => {
    messageApi.error(text)
  }, [messageApi])

  const { loading: documentsLoading, status: knowledgeStatus } = useKnowledgeStatus(selectedKBIds, showError)

  useEffect(() => () => clearStreamingBuffer(), [clearStreamingBuffer])

  const refreshSessions = async () => {
    setSessionsLoading(true)
    try {
      setSessions(await api.listSessions())
    } catch (err) {
      messageApi.error((err as Error).message || '会话列表加载失败')
    } finally {
      setSessionsLoading(false)
    }
  }

  useEffect(() => {
    void refreshSessions()
    void api.listResearchProjects().then(setResearchProjects).catch(() => {
      // The research selector is an optional enhancement; ordinary chat must
      // remain usable if a deployment has not enabled research storage yet.
    })
  }, [])

  useEffect(() => {
    const project = researchProjects.find((item) => item.id === researchProjectId)
    if (!project) {
      setResearchContext(null)
      return
    }

    let active = true
    setResearchContext({ project, readiness: null, sources: [], assets: [], loading: true, error: '' })
    Promise.all([
      api.getResearchProtocolReadiness(project.id),
      api.listResearchRagSources(project.id),
      api.listResearchDataAssets(project.id),
    ])
      .then(([readiness, sources, assets]) => {
        if (active) setResearchContext({ project, readiness, sources, assets, loading: false, error: '' })
      })
      .catch((err) => {
        if (active) {
          setResearchContext({
            project,
            readiness: null,
            sources: [],
            assets: [],
            loading: false,
            error: (err as Error).message || '研究项目上下文加载失败',
          })
        }
      })

    return () => {
      active = false
    }
  }, [researchProjectId, researchProjects])

  useEffect(() => {
    if (initialKnowledgeBaseId && knowledgeBases.some((kb) => kb.id === initialKnowledgeBaseId)) {
      setSelectedKBIds([initialKnowledgeBaseId])
    } else if (knowledgeBases.length > 0 && selectedKBIds.length === 0) {
      setSelectedKBIds([knowledgeBases[0].id])
    }
  }, [initialKnowledgeBaseId, knowledgeBases])

  useEffect(() => {
    if (!initialResearchProjectId || !researchProjects.some((project) => project.id === initialResearchProjectId)) return
    setResearchProjectId((current) => current === initialResearchProjectId ? current : initialResearchProjectId)
    setChatMode('agent')
  }, [initialResearchProjectId, researchProjects])

  useEffect(() => {
    if (restoringSessionRef.current) {
      restoringSessionRef.current = false
      return
    }
    streamRequestIdRef.current += 1
    abortRef.current?.abort()
    streamTerminalRef.current = true
    setMessages([])
    setSessionId(null)
    clearStreamingBuffer()
    setStreamError('')
    setIsStreaming(false)
    setAgentSteps([])
    setAgentRunComplete(false)
    setFixTarget(null)
    setGenerateProFile(false)
    setInputValue('')
    setAttachedFile(null)
    setSelectedInputArtifacts([])
  }, [selectedKBIds.join(',')])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [streamingContent, messages, agentSteps])

  useEffect(() => {
    if (!isStreaming) {
      inputRef.current?.focus()
    }
  }, [isStreaming])

  const handleNewSession = () => {
    streamRequestIdRef.current += 1
    abortRef.current?.abort()
    streamTerminalRef.current = true
    setMessages([])
    setSessionId(null)
    clearStreamingBuffer()
    setStreamError('')
    setIsStreaming(false)
    setAgentSteps([])
    setAgentRunComplete(false)
    setFixTarget(null)
    setGenerateProFile(false)
    setInputValue('')
    setAttachedFile(null)
    setSelectedInputArtifacts([])
  }

  const handleLoadSession = async (targetSessionId: number) => {
    const session = sessions.find((item) => item.id === targetSessionId)
    const requestId = ++streamRequestIdRef.current
    abortRef.current?.abort()
    streamTerminalRef.current = true
    setSessionsLoading(true)
    try {
      const fullMessages = await api.listMessages(targetSessionId)
      if (streamRequestIdRef.current !== requestId) return
      restoringSessionRef.current = true
      setSessionId(targetSessionId)
      setMessages(fullMessages)
      clearStreamingBuffer()
      setStreamError('')
      setIsStreaming(false)
      setAgentSteps([])
      setAgentRunComplete(false)
      setFixTarget(null)
      setGenerateProFile(false)
      setAttachedFile(null)
      setSelectedInputArtifacts([])
      setInputValue('')
      setSelectedKBIds(session?.knowledge_base_id ? [session.knowledge_base_id] : [])
    } catch (err) {
      if (streamRequestIdRef.current === requestId) {
        messageApi.error((err as Error).message || '会话加载失败')
      }
    } finally {
      if (streamRequestIdRef.current === requestId) setSessionsLoading(false)
    }
  }

  const handleRenameSession = async () => {
    if (!sessionId) {
      messageApi.warning('请先选择或开始一个会话')
      return
    }
    const current = sessions.find((item) => item.id === sessionId)
    const fallback = current?.title || messages.find((item) => item.role === 'user')?.content || `会话 ${sessionId}`
    const title = window.prompt('输入新的会话名称', fallback.slice(0, 200))?.trim()
    if (!title) return
    try {
      await api.renameSession(sessionId, { title })
      await refreshSessions()
      messageApi.success('会话已重命名')
    } catch (err) {
      messageApi.error((err as Error).message || '重命名失败')
    }
  }

  const handleDeleteSession = async () => {
    if (!sessionId) {
      messageApi.warning('请先选择一个会话')
      return
    }
    if (!window.confirm('确定删除当前会话？')) return
    try {
      await api.deleteSession(sessionId)
      handleNewSession()
      await refreshSessions()
      messageApi.success('会话已删除')
    } catch (err) {
      messageApi.error((err as Error).message || '删除失败')
    }
  }

  const handleFileUpload = async (file: File) => {
    const suffix = file.name.split('.').pop()?.toLowerCase() || ''
    const supported = ['pdf', 'md', 'markdown', 'txt', 'pro', 'idl']
    if (!supported.includes(suffix)) {
      messageApi.error('不支持的文件格式')
      return false
    }
    if (file.size > 10 * 1024 * 1024) {
      messageApi.error('文件大小不能超过 10MB')
      return false
    }
    setUploading(true)
    try {
      if (suffix === 'pdf') {
        const result = await api.uploadTempFile(file)
        setAttachedFile({ name: result.file_name, content: result.content })
      } else {
        const text = await file.text()
        setAttachedFile({ name: file.name, content: text })
      }
    } catch (err) {
      messageApi.error((err as Error).message || '文件解析失败')
    } finally {
      setUploading(false)
    }
    return false
  }

  const handleSubmit = () => {
    const question = inputValue.trim()
    if (!question) return
    if (isStreaming) return
    if (selectedKBIds.length === 0 && !attachedFile && !researchProjectId) {
      messageApi.warning('请至少选择一个知识库，或上传一个文件')
      return
    }
    if (chatMode !== 'agent' && selectedKBIds.length === 0 && researchProjectId) {
      messageApi.warning('研究项目上下文需要使用 Agent 模式；普通聊天请选择知识库')
      return
    }

    const userMessage: ChatMessage = {
      id: Date.now(),
      role: 'user',
      content: question + (attachedFile ? ` [附带文件: ${attachedFile.name}]` : ''),
      citations: [],
      artifacts: [],
      created_at: new Date().toISOString(),
    }
    setMessages((prev) => [...prev, userMessage])
    const requestId = ++streamRequestIdRef.current
    streamTerminalRef.current = false
    setIsStreaming(true)
    clearStreamingBuffer()
    setStreamError('')
    setAgentSteps([])
    setAgentRunComplete(false)
    stepIdRef.current = 0
    setInputValue('')
    if (inputRef.current) {
      inputRef.current.style.height = 'auto'
    }

    const controller = new AbortController()
    abortRef.current = controller

    let actualQuestion = question
    if (fixTarget) {
      actualQuestion = `请修复文件 ${fixTarget.fileName} 中的问题：${question}`
      setFixTarget(null)
    }
    const fileContent = attachedFile?.content || null
    setAttachedFile(null)

    if (chatMode === 'agent') {
      handleAgentStream(actualQuestion, controller.signal, fileContent, requestId)
    } else {
      handleSimpleStream(actualQuestion, controller.signal, fileContent, requestId)
    }
  }

  const finishStream = () => {
    clearStreamingBuffer()
    setIsStreaming(false)
    // The final answer is loaded from the persisted session immediately after
    // `done`. Keep tool trace/run cards for inspection, but do not render the
    // transient thinking/answer status as a second assistant response.
    setAgentSteps((prev) => prev.filter((step) => ['tool_call', 'tool_result', 'error'].includes(step.step)))
    abortRef.current = null
  }

  const loadCompletedSession = async (newSessionId: number, requestId: number, attachAgentTrace = false) => {
    if (streamRequestIdRef.current !== requestId) return
    setSessionId(newSessionId)
    try {
      const fullMessages = await api.listMessages(newSessionId)
      if (streamRequestIdRef.current !== requestId) return
      setMessages(fullMessages)
      setAgentRunComplete(attachAgentTrace)
      void refreshSessions()
    } catch (err) {
      if (streamRequestIdRef.current === requestId) {
        messageApi.error((err as Error).message || '会话消息加载失败')
      }
    } finally {
      if (streamRequestIdRef.current === requestId) finishStream()
    }
  }

  const handleStreamError = (errorMsg: string, requestId: number) => {
    if (streamTerminalRef.current || streamRequestIdRef.current !== requestId) return
    streamTerminalRef.current = true
    setAgentRunComplete(false)
    setStreamError(errorMsg)
    messageApi.error(errorMsg)
    finishStream()
  }

  const handleStreamException = (err: unknown, fallback: string, requestId: number) => {
    if (streamRequestIdRef.current !== requestId) return
    if (streamTerminalRef.current && (err as Error).name !== 'AbortError') return
    streamTerminalRef.current = true
    setAgentRunComplete(false)
    if ((err as Error).name !== 'AbortError') {
      const message = (err as Error).message || fallback
      setStreamError(message)
      messageApi.error(message)
    }
    finishStream()
  }

  const handleSimpleStream = async (question: string, signal: AbortSignal, fileContent: string | null, requestId: number) => {
    try {
      await api.askQuestionStream(
        {
          knowledge_base_ids: selectedKBIds.length > 0 ? selectedKBIds : undefined,
          question,
          session_id: sessionId,
          strategy: selectedRetrievalConfig?.strategy,
          top_k: selectedRetrievalConfig?.topK,
          generate_pro_file: generateProFile,
          attached_file_content: fileContent || undefined,
          input_artifact_ids: selectedInputArtifacts.map((artifact) => artifact.id),
        },
        {
          onToken: (content: string) => {
            appendStreamingText(content, requestId)
          },
          onDone: (newSessionId: number) => {
            if (streamTerminalRef.current || streamRequestIdRef.current !== requestId) return
            streamTerminalRef.current = true
            // 先移除临时流，再加载已落盘消息，避免同一答案短暂出现两次。
            // Agent 步骤保留在当前页面，方便用户在最终回答后继续查看运行追踪。
            finishStream()
            void loadCompletedSession(newSessionId, requestId, false)
          },
          onError: (errorMsg) => handleStreamError(errorMsg, requestId),
        },
        signal,
      )
    } catch (err) {
      handleStreamException(err, '请求失败', requestId)
    }
  }

  const handleAgentStream = async (question: string, signal: AbortSignal, fileContent: string | null, requestId: number) => {
    try {
      await api.agentStream(
        {
          knowledge_base_ids: selectedKBIds.length > 0 ? selectedKBIds : undefined,
          question,
          session_id: sessionId,
          strategy: selectedRetrievalConfig?.strategy,
          top_k: selectedRetrievalConfig?.topK,
          generate_pro_file: generateProFile,
          attached_file_content: fileContent || undefined,
          input_artifact_ids: selectedInputArtifacts.map((artifact) => artifact.id),
          research_project_id: researchProjectId,
          allow_external_research: allowExternalResearch,
          allow_research_execution: allowResearchExecution,
          allow_gee_fetch: allowGeeFetch,
        },
        {
          onStep: (event: AgentStreamEvent) => {
            if (streamTerminalRef.current || streamRequestIdRef.current !== requestId) return
            const stepEvent = event as AgentStepItem & { type: string }
            stepIdRef.current += 1
            setAgentSteps((prev) => [
              ...prev,
              {
                id: stepIdRef.current,
                step: stepEvent.step,
                content: stepEvent.content,
                tool: stepEvent.tool,
                args: stepEvent.args,
                output: stepEvent.output,
                metadata: stepEvent.metadata,
              },
            ])
          },
          onToken: (content: string) => {
            appendStreamingText(content, requestId)
          },
          onDone: (newSessionId: number) => {
            if (streamTerminalRef.current || streamRequestIdRef.current !== requestId) return
            streamTerminalRef.current = true
            // 保留 Agent 步骤，让研究运行卡片在最终回答落盘后仍可查看。
            finishStream()
            void loadCompletedSession(newSessionId, requestId, true)
          },
          onError: (errorMsg) => handleStreamError(errorMsg, requestId),
        },
        signal,
      )
    } catch (err) {
      handleStreamException(err, 'Agent 请求失败', requestId)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSubmit()
    }
  }

  const handleCancelStream = () => {
    streamRequestIdRef.current += 1
    streamTerminalRef.current = true
    abortRef.current?.abort()
    setAgentRunComplete(false)
    setStreamError('生成已停止，未保存完整回答。')
    finishStream()
    messageApi.info('已停止生成')
  }

  const downloadArtifact = async (artifact: ChatArtifact) => {
    try {
      await api.downloadChatArtifact(artifact.download_url, artifact.file_name)
    } catch (err) {
      messageApi.error((err as Error).message || '下载失败')
    }
  }

  const startFixMode = (artifact: ChatArtifact) => {
    setFixTarget({ artifactId: artifact.id, fileName: artifact.file_name })
    inputRef.current?.focus()
  }

  const useArtifactAsInput = (artifact: ChatArtifact) => {
    setSelectedInputArtifacts((prev) => {
      if (prev.some((item) => item.id === artifact.id)) return prev
      return [...prev, artifact]
    })
    messageApi.success('已加入 IDL 输入数据')
  }

  const removeInputArtifact = (artifactId: string) => {
    setSelectedInputArtifacts((prev) => prev.filter((item) => item.id !== artifactId))
  }

  const fetchGeeData = async (values: GeeFetchFormValues) => {
    if (isStreaming || fetchingGee) {
      messageApi.info('当前请求尚未结束，暂不能获取 GEE 数据。')
      return
    }
    const bbox = values.bbox.split(',').map((item) => Number(item.trim()))
    if (bbox.length !== 4 || bbox.some((value) => !Number.isFinite(value))) {
      messageApi.error('bbox 需要填写 4 个逗号分隔的数字')
      return
    }
    const bands = values.bands?.split(',').map((item) => item.trim()).filter(Boolean) ?? []
    const payload: GeeFetchRequest = {
      session_id: sessionId,
      dataset_id: values.dataset_id.trim(),
      start_date: values.start_date?.trim() || null,
      end_date: values.end_date?.trim() || null,
      bbox,
      bands,
      scale: values.scale ?? 30,
      crs: values.crs?.trim() || 'EPSG:4326',
      composite: values.composite ?? 'median',
      label: values.label?.trim() || null,
    }
    setFetchingGee(true)
    try {
      const response = await api.fetchGeeData(payload)
      setSessionId(response.session_id)
      setMessages((prev) => [...prev, response.message])
      setSelectedInputArtifacts((prev) => prev.some((item) => item.id === response.artifact.id) ? prev : [...prev, response.artifact])
      setGeeDrawerOpen(false)
      void refreshSessions()
      messageApi.success('GEE 数据已获取')
    } catch (err) {
      messageApi.error((err as Error).message || 'GEE 数据获取失败')
    } finally {
      setFetchingGee(false)
    }
  }

  const runArtifactWithIdl = async (artifact: ChatArtifact) => {
    if (!sessionId) {
      messageApi.warning('请先选择或创建会话。')
      return
    }
    setRunningArtifactId(artifact.id)
    try {
      const response = await api.runChatArtifactWithIdl(sessionId, artifact.id, {
        input_artifact_ids: selectedInputArtifacts.map((item) => item.id),
      })
      setMessages((prev) => [...prev, response.message])
      messageApi.success(response.timed_out ? 'IDL 运行已超时，日志已返回' : 'IDL 运行完成')
    } catch (err) {
      messageApi.error((err as Error).message || 'IDL 运行失败')
    } finally {
      setRunningArtifactId(null)
    }
  }

  const kbSelectOptions = useMemo(
    () => knowledgeBases.map((kb) => ({ label: `${kb.name} (${kb.document_count})`, value: kb.id })),
    [knowledgeBases],
  )

  const researchProjectOptions = useMemo(
    () => researchProjects.map((project) => ({
      label: `${project.name} · ${project.status}`,
      value: project.id,
    })),
    [researchProjects],
  )

  const sessionOptions = useMemo(
    () => sessions.map((item) => ({ label: sessionLabel(item), value: item.id })),
    [sessions],
  )

  const selectedRetrievalConfig = useMemo(() => {
    const selectedKnowledgeBases = selectedKBIds
      .map((id) => knowledgeBases.find((item) => item.id === id))
      .filter((item): item is KnowledgeBase => Boolean(item))
    if (!selectedKnowledgeBases.length) {
      return undefined
    }
    if (selectedKnowledgeBases.length === 1) {
      const knowledgeBase = selectedKnowledgeBases[0]
      return {
        strategy: knowledgeBase.default_retrieval_strategy,
        topK: knowledgeBase.default_top_k,
        rerank: knowledgeBase.default_rerank_enabled,
      }
    }
    const strategies = new Set(selectedKnowledgeBases.map((item) => item.default_retrieval_strategy))
    const strategy = strategies.size === 1 ? selectedKnowledgeBases[0].default_retrieval_strategy : 'hybrid_rrf_no_rerank'
    return {
      strategy,
      topK: selectedKnowledgeBases[0].default_top_k,
      rerank: strategy === 'hybrid_rrf' && selectedKnowledgeBases.some((item) => item.default_rerank_enabled),
      note: strategies.size === 1 ? 'multi-KB' : 'multi-KB fast default',
    }
  }, [knowledgeBases, selectedKBIds])

  const hasMessages = messages.length > 0 || isStreaming
  const contextLocked = isStreaming || sessionsLoading || fetchingGee

  return (
    <div className="chat-page" aria-busy={isStreaming || fetchingGee}>
      {contextHolder}

      {/* 顶部工具栏 */}
      <div className="chat-toolbar">
        <div className="chat-toolbar-left">
          <Select
            mode="multiple"
            allowClear
            placeholder="选择知识库"
            size="small"
            value={selectedKBIds}
            onChange={setSelectedKBIds}
            options={kbSelectOptions}
            maxTagCount={1}
            maxTagPlaceholder={(omitted) => `+ ${omitted.length}`}
            tagRender={({ label, closable, onClose }) => (
              <Tag className="chat-kb-tag" closable={closable} onClose={onClose}>
                {label}
              </Tag>
            )}
            className="chat-kb-select"
            disabled={contextLocked}
          />
          <Select
            allowClear
            size="small"
            placeholder="绑定研究项目"
            value={researchProjectId}
            options={researchProjectOptions}
            className="chat-research-select"
            onChange={(value) => {
              if (value !== researchProjectId) {
                handleNewSession()
                setAllowExternalResearch(false)
                setAllowResearchExecution(false)
                setAllowGeeFetch(false)
              }
              setResearchProjectId(value)
              if (!value) {
                setAllowExternalResearch(false)
                setAllowResearchExecution(false)
                setAllowGeeFetch(false)
              }
            }}
            disabled={contextLocked}
          />
          <Segmented
            size="small"
            value={chatMode}
            disabled={contextLocked}
            onChange={(val) => setChatMode(val as 'normal' | 'agent')}
            options={[
              { label: '普通', value: 'normal' },
              { label: 'Agent', value: 'agent' },
            ]}
          />
          <div className="chat-research-permissions" aria-label="研究 Agent 授权选项">
            <Tooltip title="仅将研究问题发送给公开文献元数据接口；不会发送项目影像或凭据">
              <Checkbox
                id="allow-external-research"
                aria-label="允许外部文献搜索"
                checked={allowExternalResearch}
                onChange={(event) => setAllowExternalResearch(event.target.checked)}
                disabled={!researchProjectId || chatMode !== 'agent' || isStreaming}
              >
                允许外部文献搜索
              </Checkbox>
            </Tooltip>
            <Tooltip title="允许 Agent 在确认后创建并排队 Python preview；不会执行 formal/IDL、修改公式或协议">
              <Checkbox
                id="allow-research-execution"
                aria-label="允许 Agent 预览执行"
                checked={allowResearchExecution}
                onChange={(event) => setAllowResearchExecution(event.target.checked)}
                disabled={!researchProjectId || chatMode !== 'agent' || isStreaming}
              >
                允许 Agent 预览执行
              </Checkbox>
            </Tooltip>
            <Tooltip title="仅允许 Agent 按 GEE 白名单获取数据并登记私有 DataAsset；不会冻结快照或运行实验">
              <Checkbox
                id="allow-gee-fetch"
                aria-label="允许 Agent 获取 GEE"
                checked={allowGeeFetch}
                onChange={(event) => setAllowGeeFetch(event.target.checked)}
                disabled={!researchProjectId || chatMode !== 'agent' || isStreaming}
              >
                允许 Agent 获取 GEE
              </Checkbox>
            </Tooltip>
          </div>
        </div>
        <div className="chat-toolbar-right">
          <Select
            allowClear
            size="small"
            placeholder="历史会话"
            value={sessionId ?? undefined}
            loading={sessionsLoading}
            options={sessionOptions}
            className="chat-session-select"
            disabled={contextLocked}
            onChange={(value) => {
              if (value) {
                void handleLoadSession(value)
              } else {
                handleNewSession()
              }
            }}
          />
          <Tooltip title="新会话">
            <Button size="small" type="text" icon={<PlusOutlined />} onClick={handleNewSession} disabled={fetchingGee || sessionsLoading} aria-label="新建会话" />
          </Tooltip>
          <Tooltip title="重命名会话">
            <Button size="small" type="text" icon={<EditOutlined />} onClick={handleRenameSession} disabled={!sessionId || contextLocked} aria-label="重命名会话" />
          </Tooltip>
          <Tooltip title="删除会话">
            <Button size="small" type="text" danger icon={<DeleteOutlined />} onClick={handleDeleteSession} disabled={!sessionId || contextLocked} aria-label="删除会话" />
          </Tooltip>
          <Tooltip title="获取 GEE 数据">
            <Button size="small" type="text" icon={<CloudDownloadOutlined />} onClick={() => setGeeDrawerOpen(true)} disabled={contextLocked} aria-label="获取 GEE 数据" />
          </Tooltip>
          <Tooltip title={generateProFile ? '已开启 .pro 文件生成' : '生成 .pro 文件'}>
            <Button
              size="small"
              type={generateProFile ? 'primary' : 'text'}
              icon={<FileTextOutlined />}
              onClick={() => setGenerateProFile(!generateProFile)}
              disabled={contextLocked}
              aria-label="生成 IDL pro 文件"
            />
          </Tooltip>
        </div>
      </div>

      <Drawer title="GEE 数据" open={geeDrawerOpen} onClose={() => setGeeDrawerOpen(false)} width="min(100vw, 480px)">
        <div className="drawer-intro">
          <InlineIllustration kind="map" size={40} />
          <div>
            <div className="drawer-intro-title">结构化获取 GEE 数据</div>
            <div className="drawer-intro-text">填写数据集、范围和波段，下载结果会作为 Chat artifact 保存。</div>
          </div>
        </div>
        <Form
          form={geeForm}
          layout="vertical"
          disabled={isStreaming || fetchingGee}
          initialValues={{
            dataset_id: 'CGIAR/SRTM90_V4',
            bbox: '116.30,39.85,116.45,39.98',
            bands: 'elevation',
            scale: 90,
            crs: 'EPSG:4326',
            composite: 'median',
          }}
          onFinish={fetchGeeData}
        >
          <Form.Item label="数据集" name="dataset_id" rules={[{ required: true, message: '请输入 GEE 数据集 ID' }]}>
            <Select
              showSearch
              options={[
                { label: 'SRTM elevation', value: 'CGIAR/SRTM90_V4' },
                { label: 'Sentinel-2 SR', value: 'COPERNICUS/S2_SR_HARMONIZED' },
                { label: 'Landsat 8 L2', value: 'LANDSAT/LC08/C02/T1_L2' },
              ]}
            />
          </Form.Item>
          <div className="grid-two">
            <Form.Item label="开始日期" name="start_date">
              <Input placeholder="YYYY-MM-DD" />
            </Form.Item>
            <Form.Item label="结束日期" name="end_date">
              <Input placeholder="YYYY-MM-DD" />
            </Form.Item>
          </div>
          <Form.Item label="bbox" name="bbox" rules={[{ required: true, message: '请输入 bbox' }]}>
            <Input placeholder="minLon,minLat,maxLon,maxLat" />
          </Form.Item>
          <div className="grid-two">
            <Form.Item label="bands" name="bands">
              <Input placeholder="B4,B3,B2" />
            </Form.Item>
            <Form.Item label="scale" name="scale">
              <InputNumber min={1} max={10000} style={{ width: '100%' }} />
            </Form.Item>
          </div>
          <div className="grid-two">
            <Form.Item label="CRS" name="crs">
              <Input placeholder="EPSG:4326" />
            </Form.Item>
            <Form.Item label="合成" name="composite">
              <Select
                options={[
                  { label: 'median', value: 'median' },
                  { label: 'mean', value: 'mean' },
                  { label: 'first', value: 'first' },
                ]}
              />
            </Form.Item>
          </div>
          <Form.Item label="文件标签" name="label">
            <Input placeholder="可选，例如 beijing_srtm" />
          </Form.Item>
          <Space className="form-actions">
            <Button type="primary" htmlType="submit" loading={fetchingGee}>获取数据</Button>
            <Button onClick={() => setGeeDrawerOpen(false)}>取消</Button>
          </Space>
        </Form>
      </Drawer>

      <KnowledgeStatusBar
        loading={documentsLoading}
        selectedCount={selectedKBIds.length}
        status={knowledgeStatus}
        retrievalConfig={selectedRetrievalConfig}
      />
      {researchContext ? (
        <ResearchContextBar
          project={researchContext.project}
          readiness={researchContext.readiness}
          sourceCount={researchContext.sources.length}
          assetCount={researchContext.assets.length}
          loading={researchContext.loading}
          error={researchContext.error}
          agentEnabled={chatMode === 'agent'}
          allowExternalResearch={allowExternalResearch}
          allowResearchExecution={allowResearchExecution}
          allowGeeFetch={allowGeeFetch}
        />
      ) : null}

      {/* 消息区域 */}
      <div className="chat-messages">
        {!hasMessages ? (
          <div className="chat-empty">
            <DisplayEmpty
              illustration="chat"
              title="开始提问"
              description="选择知识库后，可以直接询问 ENVI/IDL 文档、函数、代码片段或处理流程。"
            >
              <div className="chat-empty-hints">
                <button className="chat-hint-btn" onClick={() => setInputValue('ENVI 如何打开栅格数据？')}>
                  ENVI 如何打开栅格数据？
                </button>
                <button className="chat-hint-btn" onClick={() => setInputValue('帮我生成一个读取影像并打印尺寸的 .pro 示例')}>
                  帮我生成一个读取影像的 .pro 示例
                </button>
                <button className="chat-hint-btn" onClick={() => setInputValue('IDL 中 FILEPATH 函数怎么用？')}>
                  IDL 中 FILEPATH 函数怎么用？
                </button>
              </div>
            </DisplayEmpty>
          </div>
        ) : (
          <MessageList
            messages={messages}
            agentSteps={agentSteps}
            agentRunComplete={agentRunComplete}
            isStreaming={isStreaming}
            streamingContent={streamingContent}
            streamError={streamError}
            messagesEndRef={messagesEndRef}
            onDownloadArtifact={downloadArtifact}
            onStartFix={startFixMode}
            onRunArtifact={runArtifactWithIdl}
            onUseArtifactAsInput={useArtifactAsInput}
            runningArtifactId={runningArtifactId}
          />
        )}
      </div>

      {/* 底部输入区 */}
      <div className="chat-input-area">
        {fixTarget && (
          <div className="chat-fix-banner">
            <span>修复模式：{fixTarget.fileName}</span>
            <CloseCircleFilled onClick={() => setFixTarget(null)} style={{ cursor: 'pointer' }} />
          </div>
        )}
        {attachedFile && (
          <div className="chat-attached-file">
            <PaperClipOutlined />
            <span>{attachedFile.name}</span>
            <CloseCircleFilled onClick={() => setAttachedFile(null)} style={{ cursor: 'pointer', color: '#999' }} />
          </div>
        )}
        {selectedInputArtifacts.length > 0 && (
          <div className="chat-input-artifacts">
            <span>IDL 输入数据</span>
            {selectedInputArtifacts.map((artifact) => (
              <Tag key={artifact.id} closable onClose={() => removeInputArtifact(artifact.id)}>
                {artifact.file_name}
              </Tag>
            ))}
          </div>
        )}
        <div className="chat-input-row">
          <Upload
            beforeUpload={handleFileUpload}
            showUploadList={false}
            accept=".pdf,.md,.markdown,.txt,.pro,.idl"
            disabled={uploading || isStreaming || fetchingGee}
          >
            <Tooltip title="上传文件">
              <Button
                type="text"
                icon={<UploadOutlined />}
                loading={uploading}
                className="chat-input-action"
                aria-label="上传文件"
              />
            </Tooltip>
          </Upload>
          <textarea
            ref={inputRef}
            className="chat-input-textarea"
            id="chat-input"
            aria-label="聊天输入"
            rows={1}
            placeholder={
              fixTarget
                ? '描述需要修复的问题...'
                : '输入问题，Shift+Enter 换行...'
            }
            value={inputValue}
            onChange={(e) => {
              setInputValue(e.target.value)
              // Auto-resize
              e.target.style.height = 'auto'
              e.target.style.height = Math.min(e.target.scrollHeight, 160) + 'px'
            }}
            onKeyDown={handleKeyDown}
            disabled={isStreaming}
          />
          {isStreaming ? (
            <Button
              type="text"
              danger
              icon={<StopOutlined />}
              onClick={handleCancelStream}
              className="chat-input-action"
              aria-label="停止生成"
            />
          ) : (
            <Button
              type="primary"
              icon={<SendOutlined />}
              onClick={handleSubmit}
              disabled={!inputValue.trim()}
              className="chat-send-btn"
              aria-label="发送消息"
            />
          )}
        </div>
      </div>
    </div>
  )
}

function sessionLabel(session: ChatSession): string {
  const title = session.title?.trim() || `会话 ${session.id}`
  return `${title} · ${new Date(session.created_at).toLocaleString()}`
}
