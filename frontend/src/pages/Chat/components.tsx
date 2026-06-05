import { Button, Collapse, Drawer, Tag } from 'antd'
import { FileTextOutlined, RobotOutlined, UserOutlined } from '@ant-design/icons'
import { useState } from 'react'
import type { RefObject } from 'react'

import type { ChatMessage, Citation } from '../../api/types'
import type { AgentStepItem, ArtifactAction, KnowledgeStatus } from './types'

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
}: {
  messages: ChatMessage[]
  agentSteps: AgentStepItem[]
  isStreaming: boolean
  streamingContent: string
  messagesEndRef: RefObject<HTMLDivElement>
  onDownloadArtifact: ArtifactAction
  onStartFix: ArtifactAction
}) {
  return (
    <div className="chat-message-list">
      {messages.map((msg) => (
        <MessageBubble
          key={msg.role + msg.id}
          message={msg}
          onDownloadArtifact={onDownloadArtifact}
          onStartFix={onStartFix}
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
}: {
  message: ChatMessage
  onDownloadArtifact: ArtifactAction
  onStartFix: ArtifactAction
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
        <div className="chat-bubble-content">{message.content}</div>
        {message.artifacts.length > 0 && (
          <div className="chat-artifacts">
            {message.artifacts.map((artifact) => (
              <div key={artifact.id} className="chat-artifact">
                <FileTextOutlined />
                <span className="chat-artifact-name">{artifact.file_name}</span>
                <span className="chat-artifact-size">{formatBytes(artifact.size)}</span>
                <Button size="small" type="link" onClick={() => onDownloadArtifact(artifact)}>
                  下载
                </Button>
                <Button size="small" type="link" onClick={() => onStartFix(artifact)}>
                  修复
                </Button>
              </div>
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
