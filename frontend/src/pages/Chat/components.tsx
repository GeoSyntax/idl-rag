import { Button, Collapse, Drawer, Tag } from 'antd'
import { CheckCircleOutlined, CloseCircleOutlined, ClockCircleOutlined, FileTextOutlined, PictureOutlined, PlayCircleOutlined, RobotOutlined, UserOutlined } from '@ant-design/icons'
import { useEffect, useState } from 'react'
import type { RefObject } from 'react'

import { api } from '../../api/client'
import type { ChatArtifact, ChatMessage, Citation } from '../../api/types'
import type { AgentStepItem, ArtifactAction, AsyncArtifactAction, KnowledgeStatus } from './types'

export function KnowledgeStatusBar({
  loading,
  selectedCount,
  status,
  retrievalConfig,
}: {
  loading: boolean
  selectedCount: number
  status: KnowledgeStatus
  retrievalConfig?: { strategy: string; topK: number; rerank: boolean; note?: string }
}) {
  return (
    <div className={`chat-kb-status ${status.ready === 0 && !loading ? 'chat-kb-status-warning' : ''}`}>
      {loading ? (
        <span>正在读取文档状态...</span>
      ) : selectedCount === 0 ? (
        <span>未选择知识库，可上传文件后直接提问。</span>
      ) : status.total === 0 ? (
        <span>当前知识库还没有文档，请先导入资料再提问。</span>
      ) : (
        <>
          <span>ready {status.ready}</span>
          {status.active > 0 ? <span>索引中 {status.active}</span> : null}
          {status.stale > 0 ? <span>stale {status.stale}</span> : null}
          {status.failed > 0 ? <span>失败 {status.failed}</span> : null}
          {status.fallback > 0 ? <span>fallback embedding {status.fallback} · vector quality degraded</span> : null}
          {retrievalConfig ? (
            <span>{retrievalConfig.strategy} · top_k {retrievalConfig.topK} · {retrievalConfig.rerank ? 'rerank on' : 'rerank off'}{retrievalConfig.note ? ` · ${retrievalConfig.note}` : ''}</span>
          ) : null}
          {status.ready === 0 ? <span>暂无 ready 文档，回答质量会受影响。</span> : null}
        </>
      )}
    </div>
  )
}

export function MessageList({
  messages,
  agentSteps,
  isStreaming,
  streamingContent,
  messagesEndRef,
  onDownloadArtifact,
  onStartFix,
  onRunArtifact,
  runningArtifactId,
}: {
  messages: ChatMessage[]
  agentSteps: AgentStepItem[]
  isStreaming: boolean
  streamingContent: string
  messagesEndRef: RefObject<HTMLDivElement>
  onDownloadArtifact: ArtifactAction
  onStartFix: ArtifactAction
  onRunArtifact: AsyncArtifactAction
  runningArtifactId: string | null
}) {
  return (
    <div className="chat-message-list">
      {messages.map((msg) => (
        <MessageBubble
          key={msg.role + msg.id}
          message={msg}
          onDownloadArtifact={onDownloadArtifact}
          onStartFix={onStartFix}
          onRunArtifact={onRunArtifact}
          runningArtifactId={runningArtifactId}
        />
      ))}
      {agentSteps.length > 0 && !streamingContent ? (
        <div className="chat-msg chat-msg-assistant">
          <div className="chat-avatar chat-avatar-assistant">
            <RobotOutlined />
          </div>
          <div className="chat-bubble chat-bubble-assistant">
            <AgentStepList steps={agentSteps} />
          </div>
        </div>
      ) : null}
      {isStreaming && (
        <div className="chat-msg chat-msg-assistant">
          <div className="chat-avatar chat-avatar-assistant">
            <RobotOutlined />
          </div>
          <div className="chat-bubble chat-bubble-assistant">
            {agentSteps.length > 0 && <AgentStepList steps={agentSteps} />}
            <div className="chat-streaming-text">
              {streamingContent}
              <span className="streaming-cursor">|</span>
            </div>
          </div>
        </div>
      )}
      <div ref={messagesEndRef} />
    </div>
  )
}

function MessageBubble({
  message,
  onDownloadArtifact,
  onStartFix,
  onRunArtifact,
  runningArtifactId,
}: {
  message: ChatMessage
  onDownloadArtifact: ArtifactAction
  onStartFix: ArtifactAction
  onRunArtifact: AsyncArtifactAction
  runningArtifactId: string | null
}) {
  const isUser = message.role === 'user'
  return (
    <div className={`chat-msg ${isUser ? 'chat-msg-user' : 'chat-msg-assistant'}`}>
      {!isUser && (
        <div className="chat-avatar chat-avatar-assistant">
          <RobotOutlined />
        </div>
      )}
      <div className={`chat-bubble ${isUser ? 'chat-bubble-user' : 'chat-bubble-assistant'}`}>
        <div className="chat-bubble-content">
          {parseIdlRunContent(message.content) ? (
            <IdlRunResult content={message.content} artifactCount={message.artifacts.length} />
          ) : (
            message.content
          )}
        </div>
        {message.artifacts.length > 0 && (
          <div className="chat-artifacts">
            {message.artifacts.map((artifact) => (
              <ArtifactItem
                key={artifact.id}
                artifact={artifact}
                onDownloadArtifact={onDownloadArtifact}
                onStartFix={onStartFix}
                onRunArtifact={onRunArtifact}
                running={runningArtifactId === artifact.id}
              />
            ))}
          </div>
        )}
        {message.citations.length > 0 && <CitationList citations={message.citations} />}
      </div>
      {isUser && (
        <div className="chat-avatar chat-avatar-user">
          <UserOutlined />
        </div>
      )}
    </div>
  )
}

type IdlRunView = {
  status: '成功' | '失败' | '超时'
  exitCode: string
  durationMs: string
  outputFiles: string
  stdout: string
  stderr: string
}

function IdlRunResult({ content, artifactCount }: { content: string; artifactCount: number }) {
  const result = parseIdlRunContent(content)
  if (!result) return <>{content}</>

  const ok = result.status === '成功'
  const timedOut = result.status === '超时'
  const logItems = []
  if (result.stdout) {
    logItems.push({ key: 'stdout', label: 'stdout', children: <pre className="idl-run-log">{result.stdout}</pre> })
  }
  if (result.stderr) {
    logItems.push({ key: 'stderr', label: 'stderr', children: <pre className="idl-run-log">{result.stderr}</pre> })
  }

  return (
    <div className="idl-run-card">
      <div className="idl-run-header">
        {ok ? <CheckCircleOutlined /> : timedOut ? <ClockCircleOutlined /> : <CloseCircleOutlined />}
        <span>IDL 运行{result.status}</span>
      </div>
      <div className="idl-run-grid">
        <div>
          <span>退出码</span>
          <strong>{result.exitCode}</strong>
        </div>
        <div>
          <span>耗时</span>
          <strong>{formatDuration(result.durationMs)}</strong>
        </div>
        <div>
          <span>输出图片</span>
          <strong>{artifactCount || Number(result.outputFiles) || 0}</strong>
        </div>
      </div>
      {logItems.length > 0 ? <Collapse className="idl-run-collapse" size="small" items={logItems} /> : null}
    </div>
  )
}

function parseIdlRunContent(content: string): IdlRunView | null {
  const statusMatch = content.match(/^IDL 运行(成功|失败|超时)。/)
  if (!statusMatch) return null

  return {
    status: statusMatch[1] as IdlRunView['status'],
    exitCode: content.match(/^exit_code: (.+)$/m)?.[1] ?? '-',
    durationMs: content.match(/^duration_ms: (.+)$/m)?.[1] ?? '-',
    outputFiles: content.match(/^output_files: (.+)$/m)?.[1] ?? '0',
    stdout: extractLogBlock(content, 'stdout'),
    stderr: extractLogBlock(content, 'stderr'),
  }
}

function extractLogBlock(content: string, label: 'stdout' | 'stderr'): string {
  const start = `${label}:\n` + '```text\n'
  const startIndex = content.indexOf(start)
  if (startIndex < 0) return ''
  const valueStart = startIndex + start.length
  const endIndex = content.indexOf('\n```', valueStart)
  if (endIndex < 0) return content.slice(valueStart).trim()
  return content.slice(valueStart, endIndex).trim()
}

function formatDuration(value: string): string {
  const ms = Number(value)
  if (!Number.isFinite(ms)) return value
  if (ms < 1000) return `${ms} ms`
  return `${(ms / 1000).toFixed(1)} s`
}

function ArtifactItem({
  artifact,
  onDownloadArtifact,
  onStartFix,
  onRunArtifact,
  running,
}: {
  artifact: ChatArtifact
  onDownloadArtifact: ArtifactAction
  onStartFix: ArtifactAction
  onRunArtifact: AsyncArtifactAction
  running: boolean
}) {
  const canRun = artifact.kind === 'pro' || artifact.file_name.toLowerCase().endsWith('.pro')
  const canPreview = artifact.previewable || artifact.media_type.startsWith('image/')

  if (canPreview) {
    return <ArtifactImagePreview artifact={artifact} onDownloadArtifact={onDownloadArtifact} />
  }

  return (
    <div className="chat-artifact">
      <FileTextOutlined />
      <span className="chat-artifact-name">{artifact.file_name}</span>
      <span className="chat-artifact-size">{formatBytes(artifact.size)}</span>
      {canRun ? (
        <Button size="small" type="link" icon={<PlayCircleOutlined />} loading={running} onClick={() => onRunArtifact(artifact)}>
          运行 IDL
        </Button>
      ) : null}
      <Button size="small" type="link" onClick={() => onDownloadArtifact(artifact)}>
        下载
      </Button>
      {canRun ? (
        <Button size="small" type="link" onClick={() => onStartFix(artifact)}>
          修复
        </Button>
      ) : null}
    </div>
  )
}

function ArtifactImagePreview({
  artifact,
  onDownloadArtifact,
}: {
  artifact: ChatArtifact
  onDownloadArtifact: ArtifactAction
}) {
  const [objectUrl, setObjectUrl] = useState('')
  const [previewOpen, setPreviewOpen] = useState(false)
  const [loadError, setLoadError] = useState('')

  useEffect(() => {
    let active = true
    let nextObjectUrl = ''
    setLoadError('')
    api.fetchChatArtifactBlob(artifact.download_url)
      .then((blob) => {
        if (!active) return
        nextObjectUrl = window.URL.createObjectURL(blob)
        setObjectUrl(nextObjectUrl)
      })
      .catch((err) => {
        if (active) setLoadError((err as Error).message || '图片加载失败')
      })
    return () => {
      active = false
      if (nextObjectUrl) window.URL.revokeObjectURL(nextObjectUrl)
    }
  }, [artifact.download_url])

  return (
    <div className="chat-artifact-image-card">
      <div className="chat-artifact-image-header">
        <PictureOutlined />
        <span className="chat-artifact-name">{artifact.file_name}</span>
        <span className="chat-artifact-size">{formatBytes(artifact.size)}</span>
      </div>
      {objectUrl ? (
        <button className="chat-artifact-image-button" type="button" onClick={() => setPreviewOpen(true)}>
          <img className="chat-artifact-image-thumb" src={objectUrl} alt={artifact.file_name} />
        </button>
      ) : (
        <div className="chat-artifact-image-placeholder">{loadError || '正在加载图片...'}</div>
      )}
      <div className="chat-artifact-actions">
        <Button size="small" type="link" disabled={!objectUrl} onClick={() => setPreviewOpen(true)}>
          预览
        </Button>
        <Button size="small" type="link" onClick={() => onDownloadArtifact(artifact)}>
          下载
        </Button>
      </div>
      <Drawer title={artifact.file_name} open={previewOpen} onClose={() => setPreviewOpen(false)} width={720}>
        {objectUrl ? <img className="chat-artifact-image-full" src={objectUrl} alt={artifact.file_name} /> : null}
      </Drawer>
    </div>
  )
}

function AgentStepList({ steps }: { steps: AgentStepItem[] }) {
  const items = steps.map((step) => ({
    key: String(step.id),
    label: getStepLabel(step),
    children: <StepContent step={step} />,
  }))
  return <Collapse items={items} size="small" className="chat-agent-collapse" defaultActiveKey={items.length ? [items[items.length - 1].key] : []} />
}

function getStepLabel(step: AgentStepItem): string {
  if (step.step === 'thinking') return '思考中...'
  if (step.step === 'tool_call') return `调用工具: ${step.tool}`
  if (step.step === 'tool_result') return `工具结果: ${step.tool}`
  if (step.step === 'answer') return '生成回答'
  if (step.step === 'error') return '出错'
  return step.step
}

function StepContent({ step }: { step: AgentStepItem }) {
  if (step.step === 'thinking' || step.step === 'answer' || step.step === 'error') {
    return <p style={{ margin: 0, whiteSpace: 'pre-wrap' }}>{step.content}</p>
  }
  if (step.step === 'tool_call') {
    return step.args ? <pre style={{ margin: 0, fontSize: 12, whiteSpace: 'pre-wrap' }}>{JSON.stringify(step.args, null, 2)}</pre> : null
  }
  if (step.step === 'tool_result') {
    return (
      <div>
        <Tag color="green" style={{ marginBottom: 4 }}>{step.tool}</Tag>
        <pre style={{ margin: 0, fontSize: 12, whiteSpace: 'pre-wrap', maxHeight: 200, overflow: 'auto' }}>{step.output}</pre>
      </div>
    )
  }
  return null
}

function CitationList({ citations }: { citations: Citation[] }) {
  const [selectedCitation, setSelectedCitation] = useState<Citation | null>(null)

  return (
    <div className="chat-citations">
      <div className="chat-citations-label">参考来源</div>
      <div className="chat-citation-grid">
        {citations.map((c, i) => {
          const meta = citationMeta(c)
          return (
            <button key={`${c.chunk_id}-${i}`} className="chat-citation-card" onClick={() => setSelectedCitation(c)} type="button">
              <span className="chat-citation-num">[{i + 1}]</span>
              <span className="chat-citation-title">{c.symbol_name || c.title || c.file_name}</span>
              {meta.length > 0 && <span className="chat-citation-meta">{meta.join(' · ')}</span>}
              <span className="chat-citation-excerpt">{c.excerpt.slice(0, 80)}...</span>
            </button>
          )
        })}
      </div>
      <CitationDrawer citation={selectedCitation} onClose={() => setSelectedCitation(null)} />
    </div>
  )
}

function CitationDrawer({ citation, onClose }: { citation: Citation | null; onClose: () => void }) {
  if (!citation) {
    return null
  }

  const metadata = citation.metadata ?? {}
  const scoreKeys = ['fts_score', 'vector_score', 'rrf_score', 'combined_score', 'fused_score', 'rerank_score', 'source_ranks']
  const scoreMetadata = Object.fromEntries(scoreKeys.filter((key) => metadata[key] !== undefined).map((key) => [key, metadata[key]]))
  const otherMetadata = Object.fromEntries(Object.entries(metadata).filter(([key]) => !scoreKeys.includes(key)))
  const details = [
    ['知识库', citation.knowledge_base_name || citation.knowledge_base_id || '-'],
    ['文件', citation.file_name],
    ['路径', citation.file_path],
    ['标题', citation.title || '-'],
    ['章节', citation.section || '-'],
    ['符号', citation.symbol_name || '-'],
    ['Chunk 类型', citation.chunk_kind || '-'],
    ['检索策略', citation.source_strategy || '-'],
    ['匹配类型', citation.match_type || '-'],
    ['行号', citationLineRange(citation) || '-'],
    ['分数', typeof citation.score === 'number' ? citation.score.toFixed(4) : '-'],
  ]

  return (
    <Drawer title="来源详情" open={Boolean(citation)} onClose={onClose} width={560}>
      <div className="citation-detail-stack">
        <div className="citation-detail-list">
          {details.map(([label, value]) => (
            <div key={label} className="citation-detail-row">
              <span className="citation-detail-label">{label}</span>
              <span className="citation-detail-value">{value}</span>
            </div>
          ))}
        </div>
        <CitationMetadataSection title="检索分数" metadata={scoreMetadata} />
        <div>
          <div className="citation-detail-section-title">引用片段</div>
          <pre className="citation-detail-excerpt">{citation.excerpt}</pre>
        </div>
        <CitationMetadataSection title="Metadata" metadata={otherMetadata} />
      </div>
    </Drawer>
  )
}

function CitationMetadataSection({ title, metadata }: { title: string; metadata: Record<string, unknown> }) {
  const entries = Object.entries(metadata).filter(([, value]) => value !== undefined && value !== null && value !== '')
  if (!entries.length) {
    return null
  }
  return (
    <div>
      <div className="citation-detail-section-title">{title}</div>
      <div className="citation-detail-list">
        {entries.map(([key, value]) => (
          <div key={key} className="citation-detail-row">
            <span className="citation-detail-label">{key}</span>
            <span className="citation-detail-value">{formatMetadataValue(value)}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

function citationMeta(citation: Citation): string[] {
  return [
    citation.knowledge_base_name,
    citation.chunk_kind || undefined,
    citation.source_strategy || undefined,
    citationLineRange(citation),
    typeof citation.score === 'number' ? `score ${citation.score.toFixed(3)}` : undefined,
  ].filter(Boolean) as string[]
}

function citationLineRange(citation: Citation): string | undefined {
  if (!citation.line_start) {
    return undefined
  }
  return `L${citation.line_start}${citation.line_end && citation.line_end !== citation.line_start ? `-L${citation.line_end}` : ''}`
}

function formatMetadataValue(value: unknown): string {
  if (typeof value === 'number') {
    return value.toFixed(4)
  }
  if (typeof value === 'string') {
    return value
  }
  return JSON.stringify(value) ?? String(value)
}

function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / (1024 * 1024)).toFixed(1)} MB`
}
