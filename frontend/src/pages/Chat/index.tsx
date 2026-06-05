import { Button, Select, Segmented, Tag, Upload, message, Tooltip } from 'antd'
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
} from '@ant-design/icons'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { api } from '../../api/client'
import type { AgentStreamEvent, ChatArtifact, ChatMessage, ChatSession, KnowledgeBase } from '../../api/types'
import { KnowledgeStatusBar, MessageList } from './components'
import { useKnowledgeStatus } from './hooks'
import type { AgentStepItem, AttachedFile } from './types'

type ChatPageProps = {
  knowledgeBases: KnowledgeBase[]
  initialKnowledgeBaseId?: number
}

export function ChatPage({ knowledgeBases, initialKnowledgeBaseId }: ChatPageProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [sessionId, setSessionId] = useState<number | null>(null)
  const [sessions, setSessions] = useState<ChatSession[]>([])
  const [sessionsLoading, setSessionsLoading] = useState(false)
  const [messageApi, contextHolder] = message.useMessage()

  const [isStreaming, setIsStreaming] = useState(false)
  const [streamingContent, setStreamingContent] = useState('')
  const [agentSteps, setAgentSteps] = useState<AgentStepItem[]>([])
  const [chatMode, setChatMode] = useState<'normal' | 'agent'>('normal')
  const [fixTarget, setFixTarget] = useState<{ artifactId: string; fileName: string } | null>(null)
  const [generateProFile, setGenerateProFile] = useState(false)

  const [selectedKBIds, setSelectedKBIds] = useState<number[]>([])
  const [attachedFile, setAttachedFile] = useState<AttachedFile | null>(null)
  const [uploading, setUploading] = useState(false)
  const [inputValue, setInputValue] = useState('')

  const abortRef = useRef<AbortController | null>(null)
  const messagesEndRef = useRef<HTMLDivElement | null>(null)
  const inputRef = useRef<HTMLTextAreaElement | null>(null)
  const stepIdRef = useRef(0)
  const restoringSessionRef = useRef(false)

  const showError = useCallback((text: string) => {
    messageApi.error(text)
  }, [messageApi])

  const { loading: documentsLoading, status: knowledgeStatus } = useKnowledgeStatus(selectedKBIds, showError)

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
  }, [])

  useEffect(() => {
    if (initialKnowledgeBaseId && knowledgeBases.some((kb) => kb.id === initialKnowledgeBaseId)) {
      setSelectedKBIds([initialKnowledgeBaseId])
    } else if (knowledgeBases.length > 0 && selectedKBIds.length === 0) {
      setSelectedKBIds([knowledgeBases[0].id])
    }
  }, [initialKnowledgeBaseId, knowledgeBases])

  useEffect(() => {
    if (restoringSessionRef.current) {
      restoringSessionRef.current = false
      return
    }
    setMessages([])
    setSessionId(null)
    setStreamingContent('')
    setIsStreaming(false)
    setAgentSteps([])
    setFixTarget(null)
    setGenerateProFile(false)
    setInputValue('')
    setAttachedFile(null)
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
    abortRef.current?.abort()
    setMessages([])
    setSessionId(null)
    setStreamingContent('')
    setIsStreaming(false)
    setAgentSteps([])
    setFixTarget(null)
    setGenerateProFile(false)
    setInputValue('')
    setAttachedFile(null)
  }

  const handleLoadSession = async (targetSessionId: number) => {
    const session = sessions.find((item) => item.id === targetSessionId)
    setSessionsLoading(true)
    try {
      const fullMessages = await api.listMessages(targetSessionId)
      restoringSessionRef.current = true
      setSessionId(targetSessionId)
      setMessages(fullMessages)
      setStreamingContent('')
      setIsStreaming(false)
      setAgentSteps([])
      setFixTarget(null)
      setGenerateProFile(false)
      setAttachedFile(null)
      setInputValue('')
      setSelectedKBIds(session?.knowledge_base_id ? [session.knowledge_base_id] : [])
    } catch (err) {
      messageApi.error((err as Error).message || '会话加载失败')
    } finally {
      setSessionsLoading(false)
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
    if (selectedKBIds.length === 0 && !attachedFile) {
      messageApi.warning('请至少选择一个知识库，或上传一个文件')
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
    setIsStreaming(true)
    setStreamingContent('')
    setAgentSteps([])
    stepIdRef.current = 0
    setInputValue('')

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
      handleAgentStream(actualQuestion, controller.signal, fileContent)
    } else {
      handleSimpleStream(actualQuestion, controller.signal, fileContent)
    }
  }

  const finishStream = () => {
    setStreamingContent('')
    setIsStreaming(false)
    abortRef.current = null
  }

  const loadCompletedSession = async (newSessionId: number) => {
    setSessionId(newSessionId)
    try {
      const fullMessages = await api.listMessages(newSessionId)
      setMessages(fullMessages)
      void refreshSessions()
    } catch (err) {
      messageApi.error((err as Error).message || '会话消息加载失败')
    } finally {
      finishStream()
    }
  }

  const handleStreamError = (errorMsg: string) => {
    messageApi.error(errorMsg)
    finishStream()
  }

  const handleStreamException = (err: unknown, fallback: string) => {
    if ((err as Error).name !== 'AbortError') {
      messageApi.error((err as Error).message || fallback)
    }
    finishStream()
  }

  const handleSimpleStream = async (question: string, signal: AbortSignal, fileContent: string | null) => {
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
        },
        {
          onToken: (content: string) => setStreamingContent((prev) => prev + content),
          onDone: (newSessionId: number) => {
            void loadCompletedSession(newSessionId)
          },
          onError: handleStreamError,
        },
        signal,
      )
    } catch (err) {
      handleStreamException(err, '请求失败')
    }
  }

  const handleAgentStream = async (question: string, signal: AbortSignal, fileContent: string | null) => {
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
        },
        {
          onStep: (event: AgentStreamEvent) => {
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
              },
            ])
          },
          onToken: (content: string) => setStreamingContent((prev) => prev + content),
          onDone: (newSessionId: number) => {
            void loadCompletedSession(newSessionId)
          },
          onError: handleStreamError,
        },
        signal,
      )
    } catch (err) {
      handleStreamException(err, 'Agent 请求失败')
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSubmit()
    }
  }

  const handleCancelStream = () => {
    abortRef.current?.abort()
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

  const kbSelectOptions = useMemo(
    () => knowledgeBases.map((kb) => ({ label: `${kb.name} (${kb.document_count})`, value: kb.id })),
    [knowledgeBases],
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

  return (
    <div className="chat-page">
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
          />
          <Segmented
            size="small"
            value={chatMode}
            onChange={(val) => setChatMode(val as 'normal' | 'agent')}
            options={[
              { label: '普通', value: 'normal' },
              { label: 'Agent', value: 'agent' },
            ]}
          />
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
            onChange={(value) => {
              if (value) {
                void handleLoadSession(value)
              } else {
                handleNewSession()
              }
            }}
          />
          <Tooltip title="新会话">
            <Button size="small" type="text" icon={<PlusOutlined />} onClick={handleNewSession} />
          </Tooltip>
          <Tooltip title="重命名会话">
            <Button size="small" type="text" icon={<EditOutlined />} onClick={handleRenameSession} disabled={!sessionId} />
          </Tooltip>
          <Tooltip title="删除会话">
            <Button size="small" type="text" danger icon={<DeleteOutlined />} onClick={handleDeleteSession} disabled={!sessionId} />
          </Tooltip>
          <Tooltip title={generateProFile ? '已开启 .pro 文件生成' : '生成 .pro 文件'}>
            <Button
              size="small"
              type={generateProFile ? 'primary' : 'text'}
              icon={<FileTextOutlined />}
              onClick={() => setGenerateProFile(!generateProFile)}
            />
          </Tooltip>
        </div>
      </div>

      <KnowledgeStatusBar
        loading={documentsLoading}
        selectedCount={selectedKBIds.length}
        status={knowledgeStatus}
        retrievalConfig={selectedRetrievalConfig}
      />

      {/* 消息区域 */}
      <div className="chat-messages">
        {!hasMessages ? (
          <div className="chat-empty">
            <div className="chat-empty-title">开始提问</div>
            <div className="chat-empty-text">选择知识库后，可以直接询问 ENVI/IDL 文档、函数、代码片段或处理流程。</div>
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
          </div>
        ) : (
          <MessageList
            messages={messages}
            agentSteps={agentSteps}
            isStreaming={isStreaming}
            streamingContent={streamingContent}
            messagesEndRef={messagesEndRef}
            onDownloadArtifact={downloadArtifact}
            onStartFix={startFixMode}
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
        <div className="chat-input-row">
          <Upload
            beforeUpload={handleFileUpload}
            showUploadList={false}
            accept=".pdf,.md,.markdown,.txt,.pro,.idl"
            disabled={uploading}
          >
            <Tooltip title="上传文件">
              <Button
                type="text"
                icon={<UploadOutlined />}
                loading={uploading}
                className="chat-input-action"
              />
            </Tooltip>
          </Upload>
          <textarea
            ref={inputRef}
            className="chat-input-textarea"
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
            />
          ) : (
            <Button
              type="primary"
              icon={<SendOutlined />}
              onClick={handleSubmit}
              disabled={!inputValue.trim()}
              className="chat-send-btn"
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
