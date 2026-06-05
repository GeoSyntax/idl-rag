import { Alert, Button, Card, Drawer, Form, Input, InputNumber, Select, Space, Switch, Table, Tag, message } from 'antd'
import { useEffect, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'

import { api } from '../../api/client'
import type { EvaluationReport, KnowledgeBase, SystemSettingsPayload, SystemSettingsResponse } from '../../api/types'

type SettingsPageProps = {
  settings?: SystemSettingsResponse
  loading: boolean
  knowledgeBases: KnowledgeBase[]
  onSaved: (value: SystemSettingsResponse) => void
}

type LocalEvaluationForm = {
  knowledge_base_id?: number
  categories?: string[]
  strategies?: string[]
  top_k?: number
  limit?: number
}

type LangSmithEvaluationForm = {
  knowledge_base_id?: number
  dataset?: string
  strategies?: string[]
  top_k?: number
}

const evaluationStrategyOptions = [
  { value: 'hybrid_rrf_no_rerank', label: 'Hybrid RRF · fast default' },
  { value: 'hybrid_rrf', label: 'Hybrid RRF + rerank' },
  { value: 'fts_only', label: 'FTS only' },
  { value: 'vector_only', label: 'Vector only · diagnostic' },
  { value: 'multi_query', label: 'Multi query · slow' },
  { value: 'hyde', label: 'HyDE' },
  { value: 'dependency_graphrag', label: 'Dependency GraphRAG' },
  { value: 'symbol_search', label: 'Symbol search' },
  { value: 'read_context', label: 'Read context' },
  { value: 'find_callers', label: 'Find callers' },
  { value: 'find_callees', label: 'Find callees' },
]

function reportTypeLabel(type: string): string {
  if (type === 'local') return '本地评测'
  if (type === 'langsmith_sync') return 'LangSmith 同步'
  if (type === 'langsmith_eval') return 'LangSmith 评测'
  return type
}

function reportSummary(report: EvaluationReport): string {
  const summary = report.summary_json
  if (report.report_type === 'local') {
    const total = typeof summary.total === 'number' ? summary.total : undefined
    const passed = typeof summary.passed === 'number' ? summary.passed : undefined
    return [total !== undefined ? `${total} cases` : undefined, passed !== undefined ? `${passed} passed` : undefined]
      .filter(Boolean)
      .join(' / ')
  }
  if (report.report_type === 'langsmith_sync') {
    const total = typeof summary.total_cases === 'number' ? summary.total_cases : undefined
    const created = typeof summary.created === 'number' ? summary.created : undefined
    const dryRun = summary.dry_run === true ? 'dry-run' : undefined
    return [total !== undefined ? `${total} cases` : undefined, created !== undefined ? `${created} created` : undefined, dryRun]
      .filter(Boolean)
      .join(' / ')
  }
  const count = typeof summary.report_count === 'number' ? summary.report_count : undefined
  return count !== undefined ? `${count} reports` : '-'
}

type MetricRow = {
  key: string
  value: unknown
}

type StrategyMetricRow = {
  name: string
  total?: number
  hit_rate?: number
  precision_at_k?: number
  recall_at_k?: number
  mrr?: number
  pass_rate?: number
  latency_ms?: number
}

export function SettingsPage({ settings, loading, knowledgeBases, onSaved }: SettingsPageProps) {
  const [form] = Form.useForm<SystemSettingsPayload>()
  const [localEvalForm] = Form.useForm<LocalEvaluationForm>()
  const [langSmithEvalForm] = Form.useForm<LangSmithEvaluationForm>()
  const [messageApi, contextHolder] = message.useMessage()

  useEffect(() => {
    if (settings) {
      form.setFieldsValue(settings)
    }
  }, [form, settings])

  const saveMutation = useMutation({
    mutationFn: api.updateSettings,
    onSuccess: (value) => {
      onSaved(value)
      form.setFieldsValue({ api_key: '', rerank_api_key: '', langsmith_api_key: '' })
      messageApi.success('设置已保存')
    },
    onError: (error: Error) => {
      messageApi.error(error.message)
    },
  })

  const testMutation = useMutation({
    mutationFn: api.testSettingsConnection,
    onSuccess: (value) => {
      if (value.ok) {
        messageApi.success(value.message)
      } else {
        messageApi.warning(value.message)
      }
    },
    onError: (error: Error) => {
      messageApi.error(error.message)
    },
  })

  const langSmithTestMutation = useMutation({
    mutationFn: api.testLangSmithConnection,
    onSuccess: (value) => {
      if (value.ok) {
        messageApi.success(value.message)
      } else {
        messageApi.warning(value.message)
      }
    },
    onError: (error: Error) => {
      messageApi.error(error.message)
    },
  })

  const [selectedReportId, setSelectedReportId] = useState<number | null>(null)

  const reportsQuery = useQuery({
    queryKey: ['evaluation-reports'],
    queryFn: api.listEvaluationReports,
  })

  const reportDetailQuery = useQuery({
    queryKey: ['evaluation-report', selectedReportId],
    queryFn: () => api.getEvaluationReport(selectedReportId as number),
    enabled: selectedReportId !== null,
  })

  useEffect(() => {
    const firstKnowledgeBaseId = knowledgeBases[0]?.id
    if (!firstKnowledgeBaseId) {
      return
    }
    localEvalForm.setFieldsValue({ knowledge_base_id: firstKnowledgeBaseId, top_k: 6, limit: 10 })
    langSmithEvalForm.setFieldsValue({ knowledge_base_id: firstKnowledgeBaseId, top_k: 6 })
  }, [knowledgeBases, langSmithEvalForm, localEvalForm])

  const localEvalMutation = useMutation({
    mutationFn: api.runLocalEvaluation,
    onSuccess: () => {
      messageApi.success('本地评测已完成')
      void reportsQuery.refetch()
    },
    onError: (error: Error) => {
      messageApi.error(error.message)
    },
  })

  const langSmithSyncMutation = useMutation({
    mutationFn: api.syncLangSmithDataset,
    onSuccess: () => {
      messageApi.success('LangSmith 数据集同步已记录')
      void reportsQuery.refetch()
    },
    onError: (error: Error) => {
      messageApi.error(error.message)
    },
  })

  const langSmithEvalMutation = useMutation({
    mutationFn: api.runLangSmithEvaluation,
    onSuccess: () => {
      messageApi.success('LangSmith 评测已完成')
      void reportsQuery.refetch()
    },
    onError: (error: Error) => {
      messageApi.error(error.message)
    },
  })

  const reports = reportsQuery.data ?? []

  return (
    <>
      {contextHolder}
      <div className="page-stack">
        <Alert
          message="修改嵌入模型后，已有 ready 文档会被标记为 stale，需要到文档页重建索引。"
          type="info"
          showIcon={false}
        />
        {settings && !settings.has_api_key ? (
          <Alert
            message="当前未配置模型 API Key，索引时会使用 hash fallback embedding；该模式仅适合临时测试，语义检索质量会下降。"
            type="warning"
            showIcon={false}
          />
        ) : null}
        <Form<SystemSettingsPayload>
          form={form}
          layout="vertical"
          onFinish={(values) => saveMutation.mutate(values)}
          initialValues={settings}
        >
          <Card className="section-card" loading={loading} title="模型配置">
            <div className="grid-two">
              <Form.Item label="Provider 名称" name="provider_name" rules={[{ required: true }]}>
                <Input />
              </Form.Item>
              <Form.Item label="API Base URL" name="api_base_url" rules={[{ required: true }]}>
                <Input />
              </Form.Item>
              <Form.Item label="聊天模型" name="chat_model" rules={[{ required: true }]}>
                <Input />
              </Form.Item>
              <Form.Item label="嵌入模型" name="embedding_model" rules={[{ required: true }]}>
                <Input />
              </Form.Item>
              <Form.Item
                label="API Key"
                name="api_key"
                extra={settings?.has_api_key ? '已配置。留空则保留当前 Key。' : '当前未配置 API Key。'}
              >
                <Input.Password placeholder={settings?.has_api_key ? '留空则保持当前 Key' : '请输入 API Key'} />
              </Form.Item>
              <Form.Item label="Temperature" name="temperature" rules={[{ required: true }]}>
                <InputNumber min={0} max={2} step={0.1} style={{ width: '100%' }} />
              </Form.Item>
            </div>
            <Form.Item label="系统提示词" name="system_prompt" rules={[{ required: true }]}>
              <Input.TextArea rows={5} />
            </Form.Item>
          </Card>

          <Card className="section-card" loading={loading} title="Rerank 配置（可选）">
            <p className="auth-subtext" style={{ marginTop: 0, marginBottom: 16 }}>
              留空则不启用 Rerank，系统使用 RRF + 启发式排序。配置后使用 cross-encoder 精排提升检索精度。
            </p>
            <div className="grid-two">
              <Form.Item label="Rerank API URL" name="rerank_api_url">
                <Input placeholder="https://api.cohere.com/v2/rerank" />
              </Form.Item>
              <Form.Item label="Rerank 模型" name="rerank_model">
                <Input placeholder="rerank-multilingual-v3.0（留空使用默认值）" />
              </Form.Item>
            </div>
            <Form.Item
              label="Rerank API Key"
              name="rerank_api_key"
              extra={settings?.has_rerank_api_key ? '已配置。留空则保留当前 Key。' : '当前未配置 Rerank API Key。'}
            >
              <Input.Password placeholder={settings?.has_rerank_api_key ? '留空则保持当前 Key' : '请输入 Rerank API Key'} />
            </Form.Item>
          </Card>

          <Card className="section-card" loading={loading} title="LangSmith 评估配置">
            <p className="auth-subtext" style={{ marginTop: 0, marginBottom: 16 }}>
              用于同步 Golden QA 数据集、运行多策略 RAG Experiment，并在 LangSmith 中查看 trace 与指标。
            </p>
            <div className="grid-two">
              <Form.Item label="启用 LangSmith" name="langsmith_enabled" valuePropName="checked">
                <Switch />
              </Form.Item>
              <Form.Item label="LangSmith Endpoint" name="langsmith_endpoint" rules={[{ required: true }]}>
                <Input placeholder="https://api.smith.langchain.com" />
              </Form.Item>
              <Form.Item label="Project" name="langsmith_project" rules={[{ required: true }]}>
                <Input placeholder="IDL-RAG" />
              </Form.Item>
              <Form.Item label="Dataset" name="langsmith_dataset" rules={[{ required: true }]}>
                <Input placeholder="idl-rag-golden-qa" />
              </Form.Item>
            </div>
            <Form.Item
              label="LangSmith API Key"
              name="langsmith_api_key"
              extra={settings?.has_langsmith_api_key ? '已配置。留空则保留当前 Key。' : '当前未配置 LangSmith API Key。'}
            >
              <Input.Password placeholder={settings?.has_langsmith_api_key ? '留空则保持当前 Key' : '请输入 LangSmith API Key'} />
            </Form.Item>
          </Card>

          <Space className="form-actions">
            <Button type="primary" htmlType="submit" loading={saveMutation.isPending}>
              保存设置
            </Button>
            <Button
              onClick={async () => {
                const values = await form.validateFields()
                testMutation.mutate(values)
              }}
              loading={testMutation.isPending}
            >
              测试模型连接
            </Button>
            <Button
              onClick={async () => {
                const values = await form.validateFields()
                langSmithTestMutation.mutate(values)
              }}
              loading={langSmithTestMutation.isPending}
            >
              测试 LangSmith
            </Button>
          </Space>
        </Form>

        <Card className="section-card" title="评测报告">
          <div className="settings-eval-grid">
            <Form<LocalEvaluationForm>
              form={localEvalForm}
              layout="vertical"
              onFinish={(values) => {
                if (!values.knowledge_base_id) return
                localEvalMutation.mutate({
                  knowledge_base_id: values.knowledge_base_id,
                  categories: values.categories?.length ? values.categories : undefined,
                  strategies: values.strategies?.length ? values.strategies : undefined,
                  top_k: values.top_k,
                  limit: values.limit || undefined,
                })
              }}
            >
              <Form.Item label="知识库" name="knowledge_base_id" rules={[{ required: true, message: '请选择知识库' }]}>
                <Select
                  options={knowledgeBases.map((item) => ({ value: item.id, label: item.name }))}
                  placeholder="选择知识库"
                />
              </Form.Item>
              <div className="grid-two">
                <Form.Item label="Top K" name="top_k">
                  <InputNumber min={1} max={12} style={{ width: '100%' }} />
                </Form.Item>
                <Form.Item label="Case 限制" name="limit">
                  <InputNumber min={1} max={100} style={{ width: '100%' }} />
                </Form.Item>
              </div>
              <Form.Item label="分类" name="categories">
                <Select
                  mode="tags"
                  placeholder="留空运行全部分类"
                  options={[
                    { value: 'fts_keyword', label: 'fts_keyword' },
                    { value: 'semantic', label: 'semantic' },
                    { value: 'hybrid', label: 'hybrid' },
                    { value: 'dependency', label: 'dependency' },
                  ]}
                />
              </Form.Item>
              <Form.Item label="策略" name="strategies">
                <Select
                  mode="multiple"
                  placeholder="留空对比 fast default 与 rerank"
                  options={evaluationStrategyOptions}
                />
              </Form.Item>
              <Button type="primary" htmlType="submit" loading={localEvalMutation.isPending} disabled={!knowledgeBases.length}>
                运行本地评测
              </Button>
            </Form>

            <Form<LangSmithEvaluationForm>
              form={langSmithEvalForm}
              layout="vertical"
              onFinish={(values) => {
                if (!values.knowledge_base_id) return
                langSmithEvalMutation.mutate({
                  knowledge_base_id: values.knowledge_base_id,
                  dataset: values.dataset || undefined,
                  strategies: values.strategies?.length ? values.strategies : undefined,
                  top_k: values.top_k,
                })
              }}
            >
              <Form.Item label="知识库" name="knowledge_base_id" rules={[{ required: true, message: '请选择知识库' }]}>
                <Select
                  options={knowledgeBases.map((item) => ({ value: item.id, label: item.name }))}
                  placeholder="选择知识库"
                />
              </Form.Item>
              <div className="grid-two">
                <Form.Item label="Dataset" name="dataset">
                  <Input placeholder={settings?.langsmith_dataset || '使用默认 Dataset'} />
                </Form.Item>
                <Form.Item label="Top K" name="top_k">
                  <InputNumber min={1} max={12} style={{ width: '100%' }} />
                </Form.Item>
              </div>
              <Form.Item label="策略" name="strategies">
                <Select
                  mode="multiple"
                  placeholder="留空使用默认策略"
                  options={evaluationStrategyOptions}
                />
              </Form.Item>
              <Space className="form-actions">
                <Button htmlType="submit" loading={langSmithEvalMutation.isPending} disabled={!knowledgeBases.length}>
                  运行 LangSmith 评测
                </Button>
                <Button
                  onClick={() => {
                    langSmithSyncMutation.mutate({
                      dataset: form.getFieldValue('langsmith_dataset') || undefined,
                      dry_run: true,
                    })
                  }}
                  loading={langSmithSyncMutation.isPending}
                >
                  Dry-run 同步数据集
                </Button>
              </Space>
            </Form>
          </div>

          <Table<EvaluationReport>
            className="settings-report-table"
            rowKey="id"
            loading={reportsQuery.isLoading}
            dataSource={reports}
            pagination={{ pageSize: 6 }}
            columns={[
              {
                title: '类型',
                dataIndex: 'report_type',
                render: (value: string) => reportTypeLabel(value),
              },
              {
                title: 'Dataset',
                dataIndex: 'dataset',
                render: (value: string | null) => value || '-',
              },
              {
                title: '策略',
                dataIndex: 'strategy',
                render: (value: string | null) => value || '-',
              },
              {
                title: '状态',
                dataIndex: 'status',
                render: (value: string) => <Tag>{value}</Tag>,
              },
              {
                title: '摘要',
                render: (_, record) => reportSummary(record),
              },
              {
                title: '时间',
                dataIndex: 'created_at',
                render: (value: string) => new Date(value).toLocaleString(),
              },
              {
                title: '操作',
                width: 88,
                render: (_, record) => <Button size="small" onClick={() => setSelectedReportId(record.id)}>查看</Button>,
              },
            ]}
          />
        </Card>
      </div>
      <ReportDetailDrawer
        report={reportDetailQuery.data ?? null}
        loading={reportDetailQuery.isLoading}
        open={selectedReportId !== null}
        onClose={() => setSelectedReportId(null)}
      />
    </>
  )
}

function ReportDetailDrawer({
  report,
  loading,
  open,
  onClose,
}: {
  report: EvaluationReport | null
  loading: boolean
  open: boolean
  onClose: () => void
}) {
  const summary = report?.summary_json ?? {}
  const reportJson = report?.report_json ?? {}
  const metricRows = metricEntries(summary)
  const strategyRows = strategyMetricRows(summary.by_strategy)
  const categoryRows = strategyMetricRows(summary.by_category ?? summary.by_category_strategy)
  const cases = Array.isArray(reportJson.reports) ? reportJson.reports.slice(0, 20) : []

  return (
    <Drawer title="评测报告详情" open={open} onClose={onClose} width={760}>
      {loading || !report ? (
        <div className="status-text">正在读取报告...</div>
      ) : (
        <div className="settings-report-detail">
          <div className="citation-detail-list">
            <div className="citation-detail-row">
              <span className="citation-detail-label">类型</span>
              <span className="citation-detail-value">{reportTypeLabel(report.report_type)}</span>
            </div>
            <div className="citation-detail-row">
              <span className="citation-detail-label">Dataset</span>
              <span className="citation-detail-value">{report.dataset || '-'}</span>
            </div>
            <div className="citation-detail-row">
              <span className="citation-detail-label">策略</span>
              <span className="citation-detail-value">{report.strategy || '-'}</span>
            </div>
            <div className="citation-detail-row">
              <span className="citation-detail-label">路径</span>
              <span className="citation-detail-value">{report.report_path || '-'}</span>
            </div>
          </div>

          <div>
            <div className="citation-detail-section-title">总体指标</div>
            <Table<MetricRow>
              size="small"
              pagination={false}
              rowKey="key"
              dataSource={metricRows}
              columns={[
                { title: 'Metric', dataIndex: 'key' },
                { title: 'Value', render: (_, row) => formatReportValue(row.value) },
              ]}
            />
          </div>

          {strategyRows.length > 0 ? <MetricTable title="策略对比" rows={strategyRows} /> : null}
          {categoryRows.length > 0 ? <MetricTable title="分类拆解" rows={categoryRows} /> : null}

          {cases.length > 0 ? (
            <div>
              <div className="citation-detail-section-title">Case 明细</div>
              <Table<Record<string, unknown>>
                size="small"
                pagination={false}
                rowKey={(row, index) => `${String(row.test_case_id ?? row.id ?? 'case')}-${index}`}
                dataSource={cases}
                columns={[
                  { title: 'Case', render: (_, row) => String(row.test_case_id ?? row.id ?? '-') },
                  { title: 'Category', render: (_, row) => String(row.category ?? '-') },
                  { title: 'Strategy', render: (_, row) => String(row.strategy ?? '-') },
                  { title: 'Passed', render: (_, row) => <Tag>{String(row.passed ?? '-')}</Tag> },
                ]}
              />
            </div>
          ) : null}

          <details>
            <summary className="retrieval-debug-summary">Raw report JSON</summary>
            <pre className="citation-detail-json">{JSON.stringify(reportJson, null, 2)}</pre>
          </details>
        </div>
      )}
    </Drawer>
  )
}

function MetricTable({ title, rows }: { title: string; rows: StrategyMetricRow[] }) {
  return (
    <div>
      <div className="citation-detail-section-title">{title}</div>
      <Table<StrategyMetricRow>
        size="small"
        pagination={false}
        rowKey="name"
        dataSource={rows}
        columns={[
          { title: 'Name', dataIndex: 'name' },
          { title: 'Total', dataIndex: 'total', width: 70 },
          { title: 'Hit', render: (_, row) => formatNumber(row.hit_rate), width: 80 },
          { title: 'Recall', render: (_, row) => formatNumber(row.recall_at_k), width: 80 },
          { title: 'MRR', render: (_, row) => formatNumber(row.mrr), width: 80 },
          { title: 'Latency', render: (_, row) => formatLatency(row.latency_ms), width: 100 },
        ]}
      />
    </div>
  )
}

function metricEntries(summary: Record<string, unknown>): MetricRow[] {
  return ['total', 'passed', 'pass_rate', 'hit_rate', 'precision_at_k', 'recall_at_k', 'mrr', 'answer_relevance', 'faithfulness', 'latency_ms']
    .filter((key) => summary[key] !== undefined)
    .map((key) => ({ key, value: summary[key] }))
}

function strategyMetricRows(value: unknown): StrategyMetricRow[] {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return []
  }
  return Object.entries(value as Record<string, Record<string, unknown>>).map(([name, row]) => ({
    name,
    total: asNumber(row.total),
    hit_rate: asNumber(row.hit_rate),
    precision_at_k: asNumber(row.precision_at_k),
    recall_at_k: asNumber(row.recall_at_k),
    mrr: asNumber(row.mrr),
    pass_rate: asNumber(row.pass_rate),
    latency_ms: asNumber(row.latency_ms),
  }))
}

function asNumber(value: unknown): number | undefined {
  return typeof value === 'number' ? value : undefined
}

function formatNumber(value: number | undefined): string {
  return typeof value === 'number' ? value.toFixed(3) : '-'
}

function formatLatency(value: number | undefined): string {
  return typeof value === 'number' ? `${value.toFixed(0)}ms` : '-'
}

function formatReportValue(value: unknown): string {
  if (typeof value === 'number') {
    return value.toFixed(4)
  }
  if (typeof value === 'string') {
    return value
  }
  if (typeof value === 'boolean') {
    return String(value)
  }
  return JSON.stringify(value) ?? '-'
}
