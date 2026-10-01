import { Button, Collapse, Drawer, Tag } from 'antd'
import { CheckCircleOutlined, CloseCircleOutlined, ClockCircleOutlined, FileTextOutlined, PictureOutlined, PlayCircleOutlined, RobotOutlined, SettingOutlined, UserOutlined } from '@ant-design/icons'
import ReactMarkdown from 'react-markdown'
import rehypeKatex from 'rehype-katex'
import remarkMath from 'remark-math'
import { useEffect, useState } from 'react'
import type { RefObject } from 'react'

import 'katex/dist/katex.min.css'

import { api } from '../../api/client'
import type { ChatArtifact, ChatMessage, ChatRun, Citation, ResearchProject, ResearchProtocolReadiness, ResearchRun } from '../../api/types'
import { DisplayPlaceholder, FileTypeBadge, MetricSummary } from '../../components/DisplayPrimitives'
import { ResearchEvidenceBoundary } from '../../components/ResearchEvidenceBoundary'
import type { AgentRunMeta, AgentStepItem, ArtifactAction, AsyncArtifactAction, KnowledgeStatus } from './types'

export function KnowledgeStatusBar({
  loading,
  selectedCount,
  status,
  retrievalConfig,
  modelStatus,
}: {
  loading: boolean
  selectedCount: number
  status: KnowledgeStatus
  retrievalConfig?: { strategy: string; topK: number; rerank: boolean; note?: string }
  modelStatus?: { configured: boolean; provider_name: string; chat_model: string; message: string } | null
}) {
  return (
    <div className={`chat-kb-status ${status.ready === 0 && !loading ? 'chat-kb-status-warning' : ''}`}>
      {modelStatus ? (
        <span className={modelStatus.configured ? 'chat-model-status-ready' : 'chat-model-status-warning'}>
          模型：{modelProviderLabel(modelStatus.provider_name)} · {modelStatus.chat_model} · {modelStatus.configured ? '已配置' : '待配置'}
        </span>
      ) : null}
      {loading ? (
        <span>正在读取文档状态...</span>
      ) : selectedCount === 0 ? (
        <span>未选择知识库：Agent 可直接回答一般问题；普通聊天请上传文件或选择知识库。</span>
      ) : status.total === 0 ? (
        <span>当前知识库还没有文档，请先导入资料再提问。</span>
      ) : (
        <>
          <span>已就绪 {status.ready}</span>
          {status.active > 0 ? <span>索引中 {status.active}</span> : null}
          {status.stale > 0 ? <span>待更新 {status.stale}</span> : null}
          {status.failed > 0 ? <span>失败 {status.failed}</span> : null}
          {status.fallback > 0 ? <span>备用向量 {status.fallback} · 仅影响语义检索，当前仍可使用关键词与规则检索</span> : null}
          {retrievalConfig ? (
            <span>{retrievalStrategyLabel(retrievalConfig.strategy)} · 候选 {retrievalConfig.topK} · {retrievalConfig.rerank ? '已启用重排' : '未启用重排'}{retrievalConfig.note ? ` · ${retrievalConfig.note}` : ''}</span>
          ) : null}
          {status.ready === 0 ? <span>暂无已就绪文档，回答质量会受影响。</span> : null}
        </>
      )}
    </div>
  )
}

function retrievalStrategyLabel(strategy: string): string {
  const labels: Record<string, string> = {
    hybrid_rrf: '混合检索',
    hybrid_rrf_no_rerank: '混合检索（不重排）',
    fts: '关键词检索',
    vector_only: '语义检索',
  }
  return labels[strategy] || strategy.replace(/_/g, ' ')
}

function modelProviderLabel(provider: string): string {
  const labels: Record<string, string> = {
    gemini2api: 'Gemini2API',
    'openai-compatible': 'OpenAI 兼容服务',
    ollama: 'Ollama',
    lmstudio: 'LM Studio',
    'lm-studio': 'LM Studio',
    local: '本地模型服务',
  }
  return labels[provider.toLowerCase()] || provider
}

export function ResearchContextBar({
  project,
  readiness,
  sourceCount,
  assetCount,
  loading,
  error,
  agentEnabled,
  allowExternalResearch,
  allowResearchExecution,
  allowGeeFetch,
}: {
  project: ResearchProject
  readiness: ResearchProtocolReadiness | null
  sourceCount: number
  assetCount: number
  loading: boolean
  error: string
  agentEnabled: boolean
  allowExternalResearch: boolean
  allowResearchExecution: boolean
  allowGeeFetch: boolean
}) {
  return (
    <div className="chat-research-context">
      <div className="chat-research-context-main">
        <span className="chat-research-context-kicker">研究上下文</span>
        <strong>{project.name}</strong>
        <span className="chat-research-context-policy">private-local</span>
        {!agentEnabled ? <span className="chat-research-context-muted">切换到 Agent 后才会使用项目工具</span> : null}
      </div>
      {loading ? (
        <span className="chat-research-context-muted">正在读取协议与项目资料...</span>
      ) : error ? (
        <span className="chat-research-context-error">{error}</span>
      ) : (
        <div className="chat-research-context-side">
          <div className="chat-research-context-metrics">
            <span className={readiness?.ready ? 'is-ready' : 'is-warning'}>
              {readiness?.ready ? '协议已就绪' : `协议待补充${readiness ? ` · ${readiness.missing.length} 项` : ''}`}
            </span>
            <span>项目 RAG {sourceCount} 个</span>
            <span>数据资产 {assetCount} 个</span>
          </div>
          {agentEnabled ? (
            <div className="chat-research-consents" aria-label="本次 Agent 授权状态">
              <span className="chat-research-consents-label">本次授权</span>
              <span className={allowExternalResearch ? 'is-enabled' : ''}>文献{allowExternalResearch ? '已开' : '未开'}</span>
              <span className={allowResearchExecution ? 'is-enabled' : ''}>Preview{allowResearchExecution ? '已开' : '未开'}</span>
              <span className={allowGeeFetch ? 'is-enabled' : ''}>GEE{allowGeeFetch ? '已开' : '未开'}</span>
            </div>
          ) : null}
        </div>
      )}
    </div>
  )
}

export function MessageList({
  messages,
  agentSteps,
  agentRunComplete,
  agentRunMeta,
  agentRunHistory,
  onRetryRun,
  retryingRunId,
  agentLiveStatus,
  agentElapsedMs,
  researchProjectId,
  isStreaming,
  streamingContent,
  streamError,
  streamId,
  retryQuestion,
  onRetry,
  canConfigureModel,
  onOpenSettings,
  messagesEndRef,
  onDownloadArtifact,
  onStartFix,
  onRunArtifact,
  onUseArtifactAsInput,
  runningArtifactId,
}: {
  messages: ChatMessage[]
  agentSteps: AgentStepItem[]
  agentRunComplete: boolean
  agentRunMeta: AgentRunMeta | null
  agentRunHistory: ChatRun[]
  onRetryRun: (run: ChatRun) => void
  retryingRunId: number | null
  agentLiveStatus: string
  agentElapsedMs: number
  researchProjectId?: number
  isStreaming: boolean
  streamingContent: string
  streamError: string
  streamId: string | null
  retryQuestion: string
  onRetry: () => void
  canConfigureModel: boolean
  onOpenSettings: () => void
  messagesEndRef: RefObject<HTMLDivElement>
  onDownloadArtifact: AsyncArtifactAction
  onStartFix: ArtifactAction
  onRunArtifact: AsyncArtifactAction
  onUseArtifactAsInput: ArtifactAction
  runningArtifactId: string | null
}) {
  // Thinking/answer are transport status events, not persisted assistant
  // messages. Rendering them as a second assistant bubble makes a completed
  // answer look duplicated. Attach the inspectable tool trace to the final
  // persisted answer; only an in-flight request gets a temporary trace bubble.
  const traceSteps = agentSteps.filter((step) => ['plan', 'tool_call', 'tool_result'].includes(step.step))
  const lastAssistantMessageId = agentRunComplete && !isStreaming
    ? [...messages].reverse().find((message) => message.role === 'assistant')?.id
    : undefined
  const traceAttachedToMessage = lastAssistantMessageId !== undefined && traceSteps.length > 0
  const showLiveStatus = isStreaming && !streamError
  const modelConfigurationError = /Gemini2API|API Key|模型配置|模型服务|可用的 Agent 模型/.test(streamError)
  return (
    <div className="chat-message-list">
      {messages.map((msg) => (
        <MessageBubble
          key={msg.role + msg.id}
          message={msg}
          agentSteps={msg.id === lastAssistantMessageId ? traceSteps : []}
          agentRunMeta={msg.id === lastAssistantMessageId ? agentRunMeta : null}
          agentRunComplete={msg.id === lastAssistantMessageId && agentRunComplete}
          onDownloadArtifact={onDownloadArtifact}
          onStartFix={onStartFix}
          onRunArtifact={onRunArtifact}
          onUseArtifactAsInput={onUseArtifactAsInput}
          runningArtifactId={runningArtifactId}
        />
      ))}
      {agentRunHistory.length > 0 ? (
        <AgentRunHistory runs={agentRunHistory} onRetryRun={onRetryRun} retryingRunId={retryingRunId} isStreaming={isStreaming} />
      ) : null}
      {(isStreaming || (agentSteps.length > 0 && !traceAttachedToMessage && !agentRunComplete) || Boolean(streamError)) && (
        <div className="chat-msg chat-msg-assistant">
          <div className="chat-avatar chat-avatar-assistant">
            <RobotOutlined />
          </div>
          <div className="chat-bubble chat-bubble-assistant">
            {showLiveStatus ? (
              <div className="chat-agent-live-status" role="status">
                <span aria-live="polite">{agentLiveStatus || 'Agent 正在处理请求…'}</span>
                <span className="chat-agent-live-elapsed" aria-hidden="true">已用时 {formatElapsed(agentElapsedMs)}</span>
              </div>
            ) : null}
            {traceSteps.length > 0 && <AgentStepList steps={traceSteps} />}
            {isStreaming ? (
              <div className="chat-streaming-text">
                {streamingContent}
                <span className="streaming-cursor">|</span>
              </div>
            ) : null}
            {streamError ? (
              <div className="chat-stream-error" role="status" aria-live="polite">
                <CloseCircleOutlined />
                <span className="chat-stream-error-message">{streamError}</span>
                {streamId ? <span className="chat-stream-error-id">流 {streamId}</span> : null}
                <div className="chat-stream-error-actions">
                  {modelConfigurationError ? (
                    <span className="chat-model-error-help">
                      {canConfigureModel
                        ? '请确认 Gemini2API 地址、模型和 API Key，保存后回到这里重试。'
                        : '模型配置由平台管理员维护；配置完成后可回到这里重试。'}
                    </span>
                  ) : null}
                  {canConfigureModel && modelConfigurationError ? (
                    <Button size="small" type="link" icon={<SettingOutlined />} onClick={onOpenSettings}>
                      打开模型设置
                    </Button>
                  ) : null}
                  {retryQuestion ? (
                    <Button size="small" type="link" onClick={onRetry} disabled={isStreaming}>
                      重试
                    </Button>
                  ) : null}
                </div>
              </div>
            ) : null}
          </div>
        </div>
      )}
      <div ref={messagesEndRef} />
      {researchProjectId && !isStreaming && !agentRunComplete && messages.length > 0 ? (
        <ResearchRunRecovery projectId={researchProjectId} />
      ) : null}
    </div>
  )
}

function AgentRunHistory({
  runs,
  onRetryRun,
  retryingRunId,
  isStreaming,
}: {
  runs: ChatRun[]
  onRetryRun: (run: ChatRun) => void
  retryingRunId: number | null
  isStreaming: boolean
}) {
  return (
    <section className="chat-agent-history" aria-label="运行记录">
      <div className="chat-agent-history-title">运行记录</div>
      <div className="chat-agent-history-list">
        {runs.slice(0, 8).map((run) => (
          <div className="chat-agent-history-row" key={run.id}>
            <span className={`chat-agent-history-status is-${run.terminal_status}`}>
              {runStatusLabel(run.terminal_status)}
            </span>
            <span className="chat-agent-history-mode">{run.mode === 'agent-stream' ? 'Agent' : '普通'}</span>
            <span>{formatHistoryTime(run.created_at)}</span>
            <span>{formatDuration(String(run.total_ms))}</span>
            {run.mode === 'agent-stream' && run.retrieve_ms != null ? <span>检索 {formatDuration(String(run.retrieve_ms))}</span> : null}
            {run.mode === 'agent-stream' && run.llm_total_ms != null ? <span>模型 {formatDuration(String(run.llm_total_ms))}</span> : null}
            {run.mode === 'agent-stream' && run.llm_first_token_ms != null ? <span>首 token {formatDuration(String(run.llm_first_token_ms))}</span> : null}
            {run.agent_step_count > 0 ? <span>{run.agent_step_count} 步</span> : null}
            {run.error_message ? <span className="chat-agent-history-error">{run.error_message}</span> : null}
            {run.terminal_status === 'failed' || run.terminal_status === 'cancelled' ? (
              <Button
                size="small"
                type="link"
                loading={retryingRunId === run.id}
                disabled={retryingRunId !== null || isStreaming}
                onClick={() => onRetryRun(run)}
              >
                重试
              </Button>
            ) : null}
          </div>
        ))}
      </div>
    </section>
  )
}

function runStatusLabel(status: ChatRun['terminal_status']): string {
  if (status === 'completed') return '已完成'
  if (status === 'cancelled') return '已取消'
  if (status === 'failed') return '失败'
  return '未知'
}

function formatHistoryTime(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '时间未知' : date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

function MessageBubble({
  message,
  agentSteps = [],
  agentRunMeta = null,
  agentRunComplete = false,
  onDownloadArtifact,
  onStartFix,
  onRunArtifact,
  onUseArtifactAsInput,
  runningArtifactId,
}: {
  message: ChatMessage
  agentSteps?: AgentStepItem[]
  agentRunMeta?: AgentRunMeta | null
  agentRunComplete?: boolean
  onDownloadArtifact: AsyncArtifactAction
  onStartFix: ArtifactAction
  onRunArtifact: AsyncArtifactAction
  onUseArtifactAsInput: ArtifactAction
  runningArtifactId: string | null
}) {
  const isUser = message.role === 'user'
  const visibleCitations = message.citations
    .map((citation, index) => ({ citation, index }))
    .filter(({ index }) => new RegExp(`\\[${index + 1}\\]`).test(message.content))
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
            <MarkdownContent content={message.content} />
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
                onUseArtifactAsInput={onUseArtifactAsInput}
                running={runningArtifactId === artifact.id}
              />
            ))}
          </div>
        )}
        {visibleCitations.length > 0 && <CitationList citations={visibleCitations} />}
        {agentSteps.length > 0 && (
          <div className="chat-agent-trace-attached">
            <AgentStepList steps={agentSteps} completed={agentRunComplete} />
          </div>
        )}
        {agentRunMeta && <AgentRunMetaSummary meta={agentRunMeta} />}
      </div>
      {isUser && (
        <div className="chat-avatar chat-avatar-user">
          <UserOutlined />
        </div>
      )}
    </div>
  )
}

/**
 * Render model answers as safe Markdown with KaTeX math support.
 *
 * `react-markdown` does not enable raw HTML by default, so model output cannot
 * inject arbitrary markup into the chat surface. Links are deliberately kept
 * in the same tab: citations and private artifact links should remain inside
 * the authenticated app instead of silently opening a new context.
 */
function MarkdownContent({ content }: { content: string }) {
  return (
    <div className="chat-markdown">
      <ReactMarkdown
        remarkPlugins={[remarkMath]}
        rehypePlugins={[rehypeKatex]}
        components={{
          a: ({ node: _node, ...props }) => <a {...props} rel="noreferrer" />,
          code: ({ node: _node, className, children, ...props }) => (
            <code className={className} {...props}>{children}</code>
          ),
        }}
      >
        {content}
      </ReactMarkdown>
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
      <MetricSummary
        className="idl-run-grid"
        items={[
          { label: '退出码', value: result.exitCode, tone: ok ? 'success' : timedOut ? 'warning' : 'danger' },
          { label: '耗时', value: formatDuration(result.durationMs) },
          { label: '输出图片', value: artifactCount || Number(result.outputFiles) || 0 },
        ]}
      />
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

function getArtifactBadgeLabel(artifact: ChatArtifact): string {
  const fileName = artifact.file_name.toLowerCase()
  if (artifact.kind === 'chat_input') return 'ATT'
  if (artifact.kind === 'gee_data') return 'GEE'
  if (artifact.kind === 'gee_preview' || artifact.media_type.startsWith('image/')) return 'IMG'
  if (artifact.kind === 'idl_output') return 'OUT'
  if (artifact.kind === 'idl_log') return 'LOG'
  if (artifact.kind === 'pro' || fileName.endsWith('.pro')) return 'PRO'
  const suffix = fileName.split('.').pop()
  return suffix ? suffix.slice(0, 4).toUpperCase() : 'FILE'
}

function getArtifactValidationLabel(artifact: ChatArtifact): { label: string; status: 'passed' | 'failed' | 'unverified' } | null {
  const validation = artifact.metadata?.validation
  if (!validation || typeof validation !== 'object' || Array.isArray(validation)) return null
  const record = validation as Record<string, unknown>
  const status = record.validation_status
  if (status === 'passed') return { label: 'IDL 编译通过', status: 'passed' }
  if (status === 'failed') return { label: 'IDL 编译失败', status: 'failed' }
  return { label: '仅静态分析', status: 'unverified' }
}

function getArtifactValidationIssues(artifact: ChatArtifact): Array<{ line?: number; column?: number; message: string }> {
  const validation = artifact.metadata?.validation
  if (!validation || typeof validation !== 'object' || Array.isArray(validation)) return []
  const issues = (validation as Record<string, unknown>).validation_issues
  if (!Array.isArray(issues)) return []
  return issues
    .filter((item): item is Record<string, unknown> => Boolean(item && typeof item === 'object' && !Array.isArray(item)))
    .map((item) => ({
      line: typeof item.line === 'number' && Number.isFinite(item.line) ? item.line : undefined,
      column: typeof item.column === 'number' && Number.isFinite(item.column) ? item.column : undefined,
      message: String(item.message || '').trim(),
    }))
    .filter((item) => item.message)
    .slice(0, 20)
}

function ArtifactItem({
  artifact,
  onDownloadArtifact,
  onStartFix,
  onRunArtifact,
  onUseArtifactAsInput,
  running,
}: {
  artifact: ChatArtifact
  onDownloadArtifact: AsyncArtifactAction
  onStartFix: ArtifactAction
  onRunArtifact: AsyncArtifactAction
  onUseArtifactAsInput: ArtifactAction
  running: boolean
}) {
  const canRun = artifact.kind === 'pro' || artifact.file_name.toLowerCase().endsWith('.pro')
  const canUseAsInput = artifact.kind === 'gee_data'
  const isChatInput = artifact.kind === 'chat_input'
  const canPreview = artifact.previewable || artifact.media_type.startsWith('image/')
  const badgeLabel = getArtifactBadgeLabel(artifact)
  const validationLabel = getArtifactValidationLabel(artifact)
  const validationIssues = getArtifactValidationIssues(artifact)

  if (canPreview) {
    return <ArtifactImagePreview artifact={artifact} onDownloadArtifact={onDownloadArtifact} />
  }

  return (
    <div className="chat-artifact">
      <FileTypeBadge label={badgeLabel} />
      <FileTextOutlined />
      <span className="chat-artifact-name">{artifact.file_name}</span>
      <span className="chat-artifact-size">{formatBytes(artifact.size)}</span>
      {validationLabel ? (
        <span
          className={`chat-artifact-validation is-${validationLabel.status}`}
          title={typeof artifact.metadata?.validation === 'object'
            ? String((artifact.metadata?.validation as Record<string, unknown>).validation_notice || '')
            : undefined}
        >
          {validationLabel.label}
        </span>
      ) : null}
      {validationIssues.length > 0 && validationLabel?.status !== 'passed' ? (
        <details className="chat-artifact-validation-issues">
          <summary>查看 {validationIssues.length} 个编译问题</summary>
          <div className="chat-artifact-validation-issue-list">
            {validationIssues.map((issue, index) => (
              <div className="chat-artifact-validation-issue" key={`${issue.line || 'unknown'}-${issue.column || 'unknown'}-${index}`}>
                <span className="chat-artifact-validation-issue-location">
                  {issue.line ? `第 ${issue.line} 行${issue.column ? ` · 第 ${issue.column} 列` : ''}` : '位置未确定'}
                </span>
                <span className="chat-artifact-validation-issue-message">{issue.message}</span>
              </div>
            ))}
          </div>
        </details>
      ) : null}
      {isChatInput ? <span className="chat-artifact-input-note">已保存上下文，可用于历史重试</span> : null}
      {canRun ? (
        <Button size="small" type="link" icon={<PlayCircleOutlined />} loading={running} onClick={() => onRunArtifact(artifact)}>
          运行 IDL
        </Button>
      ) : null}
      {canUseAsInput ? (
        <Button size="small" type="link" onClick={() => onUseArtifactAsInput(artifact)}>
          作为 IDL 输入
        </Button>
      ) : null}
      {!isChatInput ? (
        <ArtifactDownloadButton artifact={artifact} onDownloadArtifact={onDownloadArtifact} />
      ) : null}
      {canRun ? (
        <Button size="small" type="link" onClick={() => onStartFix(artifact)}>
          修复
        </Button>
      ) : null}
    </div>
  )
}

function ArtifactDownloadButton({
  artifact,
  onDownloadArtifact,
}: {
  artifact: ChatArtifact
  onDownloadArtifact: AsyncArtifactAction
}) {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const handleDownload = async () => {
    setLoading(true)
    setError('')
    try {
      await onDownloadArtifact(artifact)
    } catch (err) {
      setError((err as Error).message || '下载失败，请重试。')
    } finally {
      setLoading(false)
    }
  }

  return (
    <>
      <Button size="small" type="link" loading={loading} onClick={() => void handleDownload()}>
        下载
      </Button>
      {error ? <span className="chat-artifact-download-error" role="alert">{error}</span> : null}
    </>
  )
}

function ArtifactImagePreview({
  artifact,
  onDownloadArtifact,
}: {
  artifact: ChatArtifact
  onDownloadArtifact: AsyncArtifactAction
}) {
  const [objectUrl, setObjectUrl] = useState('')
  const [previewOpen, setPreviewOpen] = useState(false)
  const [loadError, setLoadError] = useState('')
  const [loadAttempt, setLoadAttempt] = useState(0)

  useEffect(() => {
    let active = true
    let nextObjectUrl = ''
    setObjectUrl('')
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
  }, [artifact.download_url, loadAttempt])

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
      ) : loadError ? (
        <div className="chat-artifact-image-placeholder chat-artifact-image-load-error" role="alert">
          <span>{loadError}</span>
          <Button size="small" type="link" onClick={() => setLoadAttempt((attempt) => attempt + 1)}>
            重试加载
          </Button>
        </div>
      ) : (
        <DisplayPlaceholder kind="image" text={loadError || '正在加载图片...'} />
      )}
      <div className="chat-artifact-actions">
        <Button size="small" type="link" disabled={!objectUrl} onClick={() => setPreviewOpen(true)}>
          预览
        </Button>
        <ArtifactDownloadButton artifact={artifact} onDownloadArtifact={onDownloadArtifact} />
      </div>
      <Drawer title={artifact.file_name} open={previewOpen} onClose={() => setPreviewOpen(false)} width="min(100vw, 720px)">
        {objectUrl ? <img className="chat-artifact-image-full" src={objectUrl} alt={artifact.file_name} /> : null}
      </Drawer>
    </div>
  )
}

function AgentStepList({ steps, completed = false }: { steps: AgentStepItem[]; completed?: boolean }) {
  // The backend step id is useful for tracing, but it is not guaranteed to be
  // unique while a stream is being reconciled with its persisted trace (for
  // example, a replay can contain the same server event twice).  React keys
  // must describe the rendered position, otherwise Ant Design Collapse can
  // reuse the wrong panel and make a tool result look duplicated.  Keep the
  // id in the key for debuggability and add the event index for uniqueness.
  const latestResearchSummaryIndex = [...steps]
    .map((step, index) => ({ step, index }))
    .reverse()
    .find(({ step }) => step.step === 'tool_result' && step.metadata?.research_run)?.index
  const items = steps.map((step, index) => ({
    key: `agent-step-${step.id}-${index}`,
    label: getStepLabel(step),
    children: <StepContent step={step} allSteps={steps} completed={completed} showResearchSummary={index === latestResearchSummaryIndex} />,
  }))
  return <Collapse items={items} size="small" className="chat-agent-collapse" defaultActiveKey={items.length ? [items[items.length - 1].key] : []} />
}

function AgentRunMetaSummary({ meta }: { meta: AgentRunMeta }) {
  const elapsed = typeof meta.serverElapsedMs === 'number' ? `${Math.round(meta.serverElapsedMs)}ms` : '-'
  const firstToken = typeof meta.firstTokenMs === 'number' ? `${Math.round(meta.firstTokenMs)}ms` : '-'
  const phase = meta.phaseTiming
  return (
    <div className="chat-agent-run-meta" aria-label="本次 Agent 运行信息">
      <span>本次 Agent</span>
      <span>服务端 {elapsed}</span>
      <span>端到端首 token {firstToken}</span>
      {typeof phase?.retrieve_ms === 'number' ? <span>检索 {Math.round(phase.retrieve_ms)}ms</span> : null}
      {typeof phase?.rerank_ms === 'number' ? <span>重排 {Math.round(phase.rerank_ms)}ms</span> : null}
      {typeof phase?.llm_total_ms === 'number' ? <span>模型 {Math.round(phase.llm_total_ms)}ms</span> : null}
      {meta.streamId ? <span className="chat-agent-stream-id" title={`流 ID：${meta.streamId}`}>流 {meta.streamId}</span> : null}
    </div>
  )
}

function formatElapsed(elapsedMs: number): string {
  const totalSeconds = Math.max(0, Math.floor(elapsedMs / 1000))
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  return minutes > 0 ? `${minutes}分${String(seconds).padStart(2, '0')}秒` : `${seconds}秒`
}

function getStepLabel(step: AgentStepItem): string {
  if (step.step === 'plan') return '任务计划'
  if (step.step === 'thinking') return '思考中...'
  if (step.step === 'tool_call') return `调用工具: ${step.tool}`
  if (step.step === 'tool_result') return `工具结果: ${step.tool}`
  if (step.step === 'answer') return '生成回答'
  if (step.step === 'error') return '出错'
  return step.step
}

type PlanViewItem = {
  id: string
  label: string
  requiresConsent: boolean
  authorized: boolean
}

type PlanExecutionState = 'planned' | 'running' | 'completed' | 'blocked'

function StepContent({
  step,
  allSteps = [step],
  completed = false,
  showResearchSummary = false,
}: {
  step: AgentStepItem
  allSteps?: AgentStepItem[]
  completed?: boolean
  showResearchSummary?: boolean
}) {
  if (step.step === 'plan') {
    const items: PlanViewItem[] = Array.isArray(step.metadata?.items)
      ? step.metadata.items.flatMap((item) => {
        if (typeof item === 'string') return [{ id: item, label: item, requiresConsent: false, authorized: true }]
        if (!item || typeof item !== 'object') return []
        const raw = item as Record<string, unknown>
        if (typeof raw.label !== 'string') return []
        return [{
          id: typeof raw.id === 'string' ? raw.id : raw.label,
          label: raw.label,
          requiresConsent: raw.requires_consent === true,
          authorized: raw.authorized !== false,
        }]
      })
      : []
    return (
      <div className="chat-agent-plan">
        <p className="chat-agent-plan-intro">{step.content || 'Agent 将按以下计划处理本次请求。'}</p>
        {items.length > 0 ? (
          <ol>
            {items.map((item) => (
              <li key={item.id}>
                <span>{item.label}</span>
                <PlanStatusTag item={item} state={getPlanExecutionState(item.id, allSteps, completed)} />
              </li>
            ))}
          </ol>
        ) : null}
      </div>
    )
  }
  if (step.step === 'thinking' || step.step === 'answer' || step.step === 'error') {
    return <p style={{ margin: 0, whiteSpace: 'pre-wrap' }}>{step.content}</p>
  }
  if (step.step === 'tool_call') {
    if (step.args) {
      return <pre style={{ margin: 0, fontSize: 12, whiteSpace: 'pre-wrap' }}>{JSON.stringify(step.args, null, 2)}</pre>
    }
    return step.arg_keys?.length ? (
      <div className="chat-agent-step-note">已记录参数字段：{step.arg_keys.join('、')}</div>
    ) : null
  }
  if (step.step === 'tool_result') {
    const isResearchRun = Boolean(step.metadata?.research_run)
    return (
      <div>
        <Tag className="chat-agent-tool-tag" color="green" style={{ marginBottom: 4 }}>{step.tool}</Tag>
        {isResearchRun && showResearchSummary ? <ResearchRunSummary metadata={step.metadata ?? {}} /> : null}
        {isResearchRun && !showResearchSummary ? (
          <div className="chat-agent-step-note">运行摘要已更新，详情显示在最后一次运行查询中。</div>
        ) : null}
        {isResearchRun ? (
          <details className="chat-agent-tool-details">
            <summary>查看原始工具结果</summary>
            <pre>{step.output || '历史轨迹未保存原始工具输出；请在本次运行中展开查看。'}</pre>
          </details>
        ) : (
          <pre className="chat-agent-tool-output">{step.output || (
            typeof step.output_length === 'number'
              ? `历史轨迹仅保存结果摘要（${step.output_length} 字符），未保存原始内容。`
              : '历史轨迹未保存原始工具输出。'
          )}</pre>
        )}
      </div>
    )
  }
  return null
}

function PlanStatusTag({ item, state }: { item: PlanViewItem; state: PlanExecutionState }) {
  if (state === 'completed') return <Tag color="green">已完成</Tag>
  if (state === 'running') return <Tag color="blue">执行中</Tag>
  if (state === 'blocked') return <Tag color="red">被拒绝</Tag>
  if (item.requiresConsent && !item.authorized) return <Tag color="orange">需授权</Tag>
  if (item.requiresConsent) return <Tag color="green">已授权</Tag>
  return <Tag color="blue">待执行</Tag>
}

const PLAN_TOOL_MAP: Record<string, string[]> = {
  project_context: ['research_project_context'],
  project_sources: ['research_rag_search', 'research_protocol_readiness', 'research_data_catalog', 'research_run_summary'],
  knowledge_retrieval: ['kb_search'],
  input_context: ['read_artifact'],
  code_review: ['read_artifact', 'kb_search', 'grep_search', 'symbol_search', 'analyze_code', 'fix_code', 'lint_code'],
  code_analysis: ['kb_search', 'grep_search', 'symbol_search', 'read_context', 'find_callers', 'find_callees', 'analyze_code', 'lint_code'],
  external_literature: ['public_literature_search', 'research_literature_search'],
  gee_fetch: ['research_fetch_gee_asset'],
  python_preview: ['research_create_preview_experiment', 'research_queue_preview'],
}

function getPlanExecutionState(itemId: string, steps: AgentStepItem[], completed: boolean): PlanExecutionState {
  if (itemId === 'evidence_answer' && completed) return 'completed'
  const tools = PLAN_TOOL_MAP[itemId] || []
  if (tools.length === 0) return 'planned'
  const calls = steps.filter((step) => step.step === 'tool_call' && step.tool && tools.includes(step.tool))
  const results = steps.filter((step) => step.step === 'tool_result' && step.tool && tools.includes(step.tool))
  const latestResult = results[results.length - 1]
  if (latestResult) {
    return latestResult.output?.startsWith('工具调用被拒绝') ? 'blocked' : 'completed'
  }
  return calls.length > 0 ? 'running' : 'planned'
}

type ResearchRunOutputCardData = {
  file_name: string
  kind?: string
  size?: number
  previewable?: boolean
}

type ResearchRunCardData = {
  run_id: number
  experiment_id: number
  experiment_name?: string
  status: string
  execution_mode?: string
  parameters?: Record<string, unknown>
  validation_plan?: Record<string, unknown>
  visualization_contract?: string[]
  formula?: {
    id?: number
    name?: string
    version?: number
    status?: string
    operation?: string
  }
  data_snapshot?: {
    id?: number
    name?: string
    snapshot_hash?: string
    asset_count?: number
  }
  input_assets?: Array<{ id?: number; name?: string; asset_kind?: string; source_type?: string }>
  output_count?: number
  validation_metrics?: Record<string, unknown>
  outputs?: ResearchRunOutputCardData[]
}

function ResearchRunSummary({ metadata, projectWide = false }: { metadata: Record<string, unknown>; projectWide?: boolean }) {
  const projectId = typeof metadata.project_id === 'number' ? metadata.project_id : Number(metadata.project_id)
  const rawRuns = Array.isArray(metadata.runs) ? metadata.runs : []
  const runs = rawRuns.filter((value): value is ResearchRunCardData => {
    if (!value || typeof value !== 'object') return false
    const item = value as Record<string, unknown>
    return Number.isFinite(Number(item.run_id)) && Number.isFinite(Number(item.experiment_id)) && typeof item.status === 'string'
  })
  const activeRunKey = runs
    .filter((run) => run.status === 'queued' || run.status === 'running')
    .map((run) => `${run.experiment_id}:${run.run_id}`)
    .join(',')
  const [liveRuns, setLiveRuns] = useState<ResearchRunCardData[] | null>(null)

  useEffect(() => {
    if (projectId <= 0 || !activeRunKey) {
      setLiveRuns(null)
      return undefined
    }

    let active = true
    let timer: number | undefined
    const baseByRun = new Map(runs.map((run) => [run.run_id, run]))
    const experimentIds = [...new Set(runs
      .filter((run) => run.status === 'queued' || run.status === 'running')
      .map((run) => run.experiment_id))]

    const refresh = async () => {
      try {
        const responses = projectWide
          ? [await api.listResearchProjectRuns(projectId)]
          : await Promise.all(experimentIds.map((experimentId) => api.listResearchRuns(projectId, experimentId)))
        if (!active) return
        const refreshed = responses
          .flatMap((items) => items)
          .filter((item) => baseByRun.has(item.id))
          .map((item) => mergeResearchRunCard(baseByRun.get(item.id), item))
        if (!refreshed.length) return
        setLiveRuns(refreshed)
        if (refreshed.some((run) => run.status === 'queued' || run.status === 'running')) {
          timer = window.setTimeout(() => void refresh(), 5000)
        }
      } catch {
        // The original tool result remains visible when a transient poll fails.
        if (active) timer = window.setTimeout(() => void refresh(), 10000)
      }
    }

    void refresh()
    return () => {
      active = false
      if (timer !== undefined) window.clearTimeout(timer)
    }
  }, [activeRunKey, projectId, projectWide])

  if (!runs.length) {
    return <div className="chat-research-run-empty">尚未有可展示的运行记录；queued 任务完成后可再次查询阶段产物。</div>
  }

  const displayRuns = liveRuns || runs

  return (
    <div className="chat-research-run-summary">
      <div className="chat-research-run-title">
        研究运行追踪
        {activeRunKey ? <span className="chat-research-run-refreshing">自动刷新中</span> : null}
      </div>
      {displayRuns.map((run) => (
        <div className="chat-research-run-card" key={`${run.experiment_id}-${run.run_id}`}>
          <div className="chat-research-run-meta">
            <strong>Run #{run.run_id}</strong>
            <Tag color={runStatusColor(run.status)}>{run.status}</Tag>
            <span>{run.execution_mode || 'preview'}</span>
            <span>{run.experiment_name || `实验 #${run.experiment_id}`}</span>
            <span>{run.output_count ?? run.outputs?.length ?? 0} 个产物</span>
          </div>
          {run.execution_mode === 'preview' ? (
            <ResearchEvidenceBoundary
              executionMode="preview"
              hasValidationMetrics={Boolean(run.validation_metrics && Object.keys(run.validation_metrics).length > 0)}
              action={
                !run.validation_metrics || Object.keys(run.validation_metrics).length === 0 ? (
                  <Button size="small" type="link" onClick={() => openResearchWorkspace(projectId)}>
                    去研究页补验证
                  </Button>
                ) : null
              }
            />
          ) : run.execution_mode === 'formal' ? (
            <ResearchEvidenceBoundary
              executionMode="formal"
              hasEvidencePackage={Boolean(run.outputs?.some((output) => output.kind === 'research_evidence_package'))}
              action={
                <Button size="small" type="link" onClick={() => openResearchWorkspace(projectId)}>
                  去研究页核验
                </Button>
              }
            />
          ) : null}
          {(run.formula || run.parameters || run.data_snapshot || run.input_assets?.length) ? (
            <details className="chat-research-run-provenance">
              <summary>查看公式、参数与输入</summary>
              <div className="chat-research-run-provenance-grid">
                {run.formula ? (
                  <span><b>公式</b>：{run.formula.name || '-'}{run.formula.operation ? ` · ${run.formula.operation}` : ''}</span>
                ) : null}
                {run.parameters && Object.keys(run.parameters).length > 0 ? (
                  <span><b>参数</b>：{formatRunParams(run.parameters)}</span>
                ) : <span><b>参数</b>：默认</span>}
                {run.data_snapshot ? (
                  <span><b>快照</b>：{run.data_snapshot.name || `#${run.data_snapshot.id}`} · {run.data_snapshot.asset_count ?? 0} 个资产</span>
                ) : null}
                {run.input_assets?.length ? (
                  <span><b>输入</b>：{run.input_assets.map((asset) => asset.name || `#${asset.id}`).join('、')}</span>
                ) : null}
              </div>
            </details>
          ) : null}
          {run.validation_metrics && Object.keys(run.validation_metrics).length > 0 ? (
            <div className="chat-research-run-metrics">
              {Object.entries(run.validation_metrics).slice(0, 8).map(([key, value]) => (
                <span key={key}>{key}: <strong>{formatRunMetric(value)}</strong></span>
              ))}
            </div>
          ) : null}
          {projectId > 0 && run.outputs?.length ? (
            <>
              <div className="chat-research-run-outputs">
                {run.outputs.filter((output) => output.previewable).map((output) => (
                <ResearchRunOutputPreview
                  key={`${run.run_id}-${output.file_name}`}
                  projectId={projectId}
                  experimentId={run.experiment_id}
                  runId={run.run_id}
                  output={output}
                />
                ))}
              </div>
              {run.outputs.some((output) => !output.previewable) ? (
                <div className="chat-research-run-other-outputs">
                  其余产物：{run.outputs.filter((output) => !output.previewable).map((output) => output.file_name).join('、')}
                </div>
              ) : null}
            </>
          ) : null}
        </div>
      ))}
    </div>
  )
}

export function ResearchRunRecovery({ projectId }: { projectId: number }) {
  const [runs, setRuns] = useState<ResearchRun[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let active = true
    setLoading(true)
    api.listResearchProjectRuns(projectId)
      .then((items) => {
        if (active) setRuns(items)
      })
      .catch(() => {
        if (active) setRuns([])
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [projectId])

  if (loading || runs.length === 0) return null
  return (
    <div className="chat-research-run-recovery">
      <div className="chat-research-run-recovery-label">已恢复的研究运行</div>
      <ResearchRunSummary metadata={researchRunsToMetadata(projectId, runs)} projectWide />
    </div>
  )
}

function openResearchWorkspace(projectId: number) {
  if (!Number.isFinite(projectId) || projectId <= 0) return
  window.dispatchEvent(new CustomEvent('idl-rag:navigate', {
    detail: { page: 'research', projectId },
  }))
}

function researchRunsToMetadata(projectId: number, runs: ResearchRun[]): Record<string, unknown> {
  return {
    project_id: projectId,
    runs: runs.map((run) => {
      const manifest = run.manifest || {}
      const metrics = manifest.validation_metrics || manifest.metrics
      return {
        run_id: run.id,
        experiment_id: run.experiment_id,
        status: run.status,
        execution_mode: String(manifest.execution_mode || 'preview'),
        output_count: run.outputs.length,
        outputs: run.outputs.map((output) => ({
          file_name: output.file_name,
          kind: output.kind,
          size: output.size,
          previewable: /\.(png|jpe?g|webp)$/i.test(output.file_name),
        })),
        validation_metrics: metrics && typeof metrics === 'object' ? metrics : undefined,
      }
    }),
  }
}

function mergeResearchRunCard(base: ResearchRunCardData | undefined, item: ResearchRun): ResearchRunCardData {
  const manifest = item.manifest || {}
  const metrics = manifest.validation_metrics || manifest.metrics
  return {
    ...(base || { run_id: item.id, experiment_id: item.experiment_id, status: item.status }),
    run_id: item.id,
    experiment_id: item.experiment_id,
    status: item.status,
    output_count: item.outputs.length,
    outputs: item.outputs.map((output) => ({
      file_name: output.file_name,
      kind: output.kind,
      size: output.size,
      previewable: /\.(png|jpe?g|webp)$/i.test(output.file_name),
    })),
    validation_metrics: metrics && typeof metrics === 'object' ? metrics as Record<string, unknown> : base?.validation_metrics,
  }
}

function formatRunMetric(value: unknown): string {
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(4)
  if (typeof value === 'string') return value
  return JSON.stringify(value) ?? String(value)
}

function formatRunParams(parameters: Record<string, unknown>): string {
  return Object.entries(parameters)
    .slice(0, 8)
    .map(([key, value]) => `${key}=${formatRunMetric(value)}`)
    .join(' · ')
}

function runStatusColor(status: string): string {
  if (status === 'completed') return 'green'
  if (status === 'failed' || status === 'cancelled' || status === 'unavailable') return 'red'
  if (status === 'running') return 'blue'
  return 'gold'
}

function ResearchRunOutputPreview({
  projectId,
  experimentId,
  runId,
  output,
}: {
  projectId: number
  experimentId: number
  runId: number
  output: ResearchRunOutputCardData
}) {
  const [objectUrl, setObjectUrl] = useState('')
  const [loadError, setLoadError] = useState('')
  const previewable = Boolean(output.previewable)

  useEffect(() => {
    if (!previewable) return undefined
    let active = true
    let nextObjectUrl = ''
    api.fetchResearchRunOutputBlob(projectId, experimentId, runId, output.file_name)
      .then((blob) => {
        if (!active) return
        nextObjectUrl = window.URL.createObjectURL(blob)
        setObjectUrl(nextObjectUrl)
      })
      .catch((err) => {
        if (active) setLoadError((err as Error).message || '阶段图加载失败')
      })
    return () => {
      active = false
      if (nextObjectUrl) window.URL.revokeObjectURL(nextObjectUrl)
    }
  }, [experimentId, output.file_name, previewable, projectId, runId])

  return (
    <div className="chat-research-run-output">
      <div className="chat-research-run-output-name" title={output.file_name}>
        {output.kind || 'output'} · {output.file_name}
      </div>
      {previewable ? (
        objectUrl ? <img src={objectUrl} alt={output.file_name} /> : <span>{loadError || '正在加载阶段图...'}</span>
      ) : (
        <span>可在研究页下载或查看</span>
      )}
    </div>
  )
}

function CitationList({ citations }: { citations: Array<{ citation: Citation; index: number }> }) {
  const [selectedCitation, setSelectedCitation] = useState<Citation | null>(null)

  return (
    <div className="chat-citations">
      <div className="chat-citations-label">参考来源</div>
      <div className="chat-citation-grid">
        {citations.map(({ citation: c, index }, i) => {
          const meta = citationMeta(c)
          return (
            <button key={`${c.chunk_id}-${i}`} className="chat-citation-card" onClick={() => setSelectedCitation(c)} type="button">
              <span className="chat-citation-num">[{index + 1}]</span>
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
    <Drawer title="来源详情" open={Boolean(citation)} onClose={onClose} width="min(100vw, 560px)">
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
