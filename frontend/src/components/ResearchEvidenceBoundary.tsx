import type { ReactNode } from 'react'

export type ResearchEvidenceBoundaryProps = {
  executionMode?: string
  hasValidationMetrics?: boolean
  hasEvidencePackage?: boolean
  evidenceVerified?: boolean
  action?: ReactNode
  children?: ReactNode
}

/** Shared evidence-boundary copy for Chat and Research. */
export function ResearchEvidenceBoundary({
  executionMode,
  hasValidationMetrics = false,
  hasEvidencePackage = false,
  evidenceVerified = false,
  action,
  children,
}: ResearchEvidenceBoundaryProps) {
  if (executionMode === 'preview') {
    return (
      <div className="chat-research-run-boundary is-preview">
        <strong>探索性 Preview</strong>
        <span>阶段图和指标只用于方法探索，不等于正式实验或最终科学结论。</span>
        {!hasValidationMetrics ? (
          <span>当前没有可核验的 validation_metrics；请补充参考资产或样本验证设计后再比较结果。</span>
        ) : null}
        {children}
        {action ? <span className="chat-research-run-boundary-action">{action}</span> : null}
      </div>
    )
  }
  if (executionMode === 'formal') {
    return (
      <div className="chat-research-run-boundary is-formal">
        <strong>Formal 运行</strong>
        <span>仍需确认证据包完整且通过独立测试，才能用于正式报告。</span>
        {hasEvidencePackage ? (
          <span>{evidenceVerified ? 'Evidence Package 已校验。' : 'Evidence Package 尚未完成校验。'}</span>
        ) : (
          <span>当前运行尚未发现 Evidence Package。</span>
        )}
        {children}
        {action ? <span className="chat-research-run-boundary-action">{action}</span> : null}
      </div>
    )
  }
  return null
}
