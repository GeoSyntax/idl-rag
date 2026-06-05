import { Alert, Card, Empty, Table, Tag } from 'antd'

import type { DashboardSummary } from '../../api/types'

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
    return <Empty description="暂无统计数据" />
  }

  const corpusRows: OverviewRow[] = [
    { label: '知识库', value: summary?.knowledge_base_count ?? 0 },
    { label: '文档', value: summary?.document_count ?? 0 },
    { label: 'Ready 文档', value: summary?.ready_document_count ?? 0 },
    { label: '失败文档', value: summary?.failed_document_count ?? 0 },
    { label: 'Fallback 文档', value: summary?.fallback_document_count ?? 0, note: '语义检索质量会下降' },
  ]

  const indexRows: OverviewRow[] = [
    { label: '文档队列', value: summary?.queued_document_count ?? 0 },
    { label: '处理中', value: summary?.processing_document_count ?? 0 },
    { label: '待重建', value: summary?.stale_document_count ?? 0 },
    { label: '失败任务', value: summary?.failed_index_job_count ?? 0 },
    { label: 'Worker', value: summary?.worker_alive ? '正常' : '未运行' },
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

      <Card className="section-card" loading={loading} title="使用情况">
        <div className="dashboard-usage-row">
          <span>对话会话</span>
          <strong>{summary?.chat_session_count ?? 0}</strong>
          <Tag>{summary?.worker_alive ? 'worker 正常' : 'worker 未运行'}</Tag>
        </div>
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
      columns={[
        { title: '项目', dataIndex: 'label' },
        { title: '数值', dataIndex: 'value', width: 140, render: (value) => <strong>{value}</strong> },
        { title: '说明', dataIndex: 'note', render: (value) => value || '-' },
      ]}
    />
  )
}
