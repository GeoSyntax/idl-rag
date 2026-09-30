import { Alert, Card, Table } from 'antd'

import type { DashboardSummary } from '../../api/types'
import { DisplayEmpty, MetricSummary } from '../../components/DisplayPrimitives'

type DashboardPageProps = {
  summary?: DashboardSummary
  loading: boolean
}

type OverviewRow = {
  label: string
  value: number | string
  note?: string
}

export function DashboardPage({ summary, loading }: DashboardPageProps) {
  if (!summary && !loading) {
    return (
      <div className="page-stack dashboard-page">
        <DisplayEmpty illustration="report" title="暂无统计数据" description="导入知识库和文档后，这里会显示资料、索引和使用情况。" />
      </div>
    )
  }

  const corpusRows: OverviewRow[] = [
    { label: '知识库', value: summary?.knowledge_base_count ?? 0 },
    { label: '文档', value: summary?.document_count ?? 0 },
    { label: 'Chunk', value: summary?.chunk_count ?? 0 },
    { label: '平均 Chunk / 文档', value: formatNumber(summary?.avg_chunks_per_document), note: '用于评估切分粒度' },
    { label: 'Ready 文档', value: summary?.ready_document_count ?? 0 },
    { label: '失败文档', value: summary?.failed_document_count ?? 0 },
    { label: 'Fallback 文档', value: summary?.fallback_document_count ?? 0, note: '语义检索质量会下降' },
  ]

  const indexRows: OverviewRow[] = [
    { label: '文档队列', value: summary?.queued_document_count ?? 0 },
    { label: '处理中', value: summary?.processing_document_count ?? 0 },
    { label: '待重建', value: summary?.stale_document_count ?? 0 },
    { label: '失败任务', value: summary?.failed_index_job_count ?? 0 },
    { label: '平均索引耗时', value: formatSeconds(summary?.avg_index_job_seconds), note: '当前记录为完整索引任务耗时' },
    { label: 'Worker', value: summary?.worker_alive ? '正常' : '未运行', note: `模式：${summary?.worker_mode ?? 'embedded'}` },
  ]

  return (
    <div className="page-stack dashboard-page">
      {summary?.worker_last_error ? (
        <Alert type="error" message="索引 worker 最近发生错误" description={summary.worker_last_error} showIcon={false} />
      ) : null}
      {summary?.embedding_fallback_active ? (
        <Alert
          type="warning"
          message="当前存在 fallback embedding"
          description={summary.embedding_last_error ?? '部分文档或最近一次向量化使用了 hash fallback，语义检索质量会下降。'}
          showIcon={false}
        />
      ) : null}

      <Card className="section-card" loading={loading} title="资料状态">
        <OverviewTable rows={corpusRows} />
      </Card>

      <Card className="section-card" loading={loading} title="索引状态">
        <OverviewTable rows={indexRows} />
      </Card>

      <Card className="section-card" loading={loading} title="问答与评测">
        <MetricSummary
          items={[
            { label: '对话会话', value: summary?.chat_session_count ?? 0 },
            { label: '请求样本', value: summary?.chat_request_count ?? 0 },
            { label: '问答 P95', value: formatMs(summary?.chat_latency_p95_ms) },
            { label: '问答 P99', value: formatMs(summary?.chat_latency_p99_ms) },
            { label: '首 token P95', value: formatMs(summary?.chat_first_token_p95_ms) },
            { label: '首 token P99', value: formatMs(summary?.chat_first_token_p99_ms) },
            { label: '最近 Hit Rate', value: formatPercent(summary?.latest_eval_hit_rate) },
            { label: 'Eval Top K', value: summary?.latest_eval_top_k ?? '-' },
          ]}
        />
      </Card>

      <Card className="section-card" loading={loading} title="阶段耗时">
        <MetricSummary
          items={[
            { label: '检索 avg', value: formatMs(summary?.avg_retrieve_ms) },
            { label: 'Rerank avg', value: formatMs(summary?.avg_rerank_ms) },
            { label: 'LLM 首 token avg', value: formatMs(summary?.avg_llm_first_token_ms) },
            { label: '总耗时 avg', value: formatMs(summary?.avg_total_ms) },
            { label: '引用覆盖率', value: formatPercent(summary?.citation_coverage) },
            { label: '错误率', value: formatPercent(summary?.error_rate), tone: typeof summary?.error_rate === 'number' && summary.error_rate > 0.1 ? 'warning' : undefined },
          ]}
        />
      </Card>
    </div>
  )
}

function OverviewTable({ rows }: { rows: OverviewRow[] }) {
  return (
    <Table<OverviewRow>
      rowKey="label"
      size="small"
      pagination={false}
      dataSource={rows}
      scroll={{ x: 520 }}
      columns={[
        { title: '项目', dataIndex: 'label' },
        { title: '数值', dataIndex: 'value', width: 140, render: (value) => <strong>{value}</strong> },
        { title: '说明', dataIndex: 'note', render: (value) => value || '-' },
      ]}
    />
  )
}

function formatNumber(value: number | null | undefined): string {
  return typeof value === 'number' ? value.toFixed(2) : '-'
}

function formatSeconds(value: number | null | undefined): string {
  return typeof value === 'number' ? `${value.toFixed(2)}s` : '-'
}

function formatMs(value: number | null | undefined): string {
  return typeof value === 'number' ? `${value.toFixed(0)}ms` : '-'
}

function formatPercent(value: number | null | undefined): string {
  return typeof value === 'number' ? `${(value * 100).toFixed(1)}%` : '-'
}
