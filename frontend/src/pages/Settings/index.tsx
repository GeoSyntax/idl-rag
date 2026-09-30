import { Alert, Button, Card, Drawer, Form, Input, InputNumber, Select, Space, Switch, Table, Tag, message } from 'antd'
import { useEffect, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'

import { api } from '../../api/client'
import type { EvaluationReport, KnowledgeBase, SystemSettingsPayload, SystemSettingsResponse } from '../../api/types'
import { DisplayEmpty, MetricSummary } from '../../components/DisplayPrimitives'

type CompareResult = {
  left: Record<string, unknown>
  right: Record<string, unknown>
  deltas: Record<string, Record<string, number>>
}

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
      form.setFieldsValue({ api_key: '', embedding_api_key: '', rerank_api_key: '', langsmith_api_key: '' })
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

  const embeddingTestMutation = useMutation({
    mutationFn: api.testEmbeddingConnection,
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
  const [compareMode, setCompareMode] = useState(false)
  const [compareIds, setCompareIds] = useState<number[]>([])
  const [compareResult, setCompareResult] = useState<CompareResult | null>(null)
  const [comparing, setComparing] = useState(false)

  const reportsQuery = useQuery({
    queryKey: ['evaluation-reports'],
    queryFn: api.listEvaluationReports,
  })

  const reportDetailQuery = useQuery({
    queryKey: ['evaluation-report', selectedReportId],
    queryFn: () => api.getEvaluationReport(selectedReportId as number),
    enabled: selectedReportId !== null,
  })

  const handleCompare = async () => {
    if (compareIds.length !== 2) return
    setComparing(true)
    try {
      const result = await api.compareEvaluationReports(compareIds[0], compareIds[1])
      setCompareResult(result)
    } catch (err) {
      messageApi.error((err as Error).message || '对比失败')
    } finally {
      setComparing(false)
    }
  }

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
            message="当前未配置模型 API Key，索引将使用 hash fallback embedding"
            description="默认 hybrid 会保护性切换到 FTS + 规则排序；vector_only 仅适合诊断，不代表真实语义检索质量。"
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

          <Card className="section-card" loading={loading} title="Embedding 配置（可与聊天服务分离）">
            <p className="auth-subtext" style={{ marginTop: 0, marginBottom: 16 }}>
              Gemini2API 只负责聊天时，可在这里接入本地 sentence-transformers 或其他 OpenAI-compatible embedding 服务。API Base URL 留空会复用聊天接口。
            </p>
            <div className="grid-two">
              <Form.Item label="Embedding API Base URL" name="embedding_api_base_url">
                <Input placeholder="留空复用聊天 API Base URL" />
              </Form.Item>
              <Form.Item label="Embedding 模型" name="embedding_model" rules={[{ required: true }]}>
                <Input placeholder="例如 bge-m3 / text-embedding-3-small" />
              </Form.Item>
              <Form.Item
                label="Embedding API Key"
                name="embedding_api_key"
                extra={settings?.has_embedding_api_key ? '已配置。留空则保留当前 Key。' : '留空则复用聊天 API Key。'}
              >
                <Input.Password placeholder={settings?.has_embedding_api_key ? '留空则保持当前 Key' : '留空复用聊天 API Key'} />
              </Form.Item>
            </div>
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
                embeddingTestMutation.mutate(values)
              }}
              loading={embeddingTestMutation.isPending}
            >
              测试 Embedding
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

          <Space style={{ marginBottom: 12 }}>
            <Button
              size="small"
              type={compareMode ? 'primary' : 'default'}
              onClick={() => { setCompareMode(!compareMode); setCompareIds([]); setCompareResult(null) }}
            >
              {compareMode ? '退出对比' : '报告对比'}
            </Button>
            {compareMode && compareIds.length === 2 && (
              <Button size="small" loading={comparing} onClick={handleCompare}>
                对比选中报告
              </Button>
            )}
            {compareMode && compareIds.length < 2 && (
              <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>请勾选 2 份报告</span>
            )}
          </Space>

          <Table<EvaluationReport>
            className="settings-report-table"
            rowKey="id"
            loading={reportsQuery.isLoading}
            dataSource={reports}
            pagination={{ pageSize: 6 }}
            scroll={{ x: 860 }}
            rowSelection={compareMode ? {
              selectedRowKeys: compareIds,
              onChange: (keys) => setCompareIds(keys as number[]),
            } : undefined}
            locale={{
              emptyText: (
                <DisplayEmpty
                  compact
                  illustration="report"
                  title="暂无评测报告"
                  description="运行本地评测或 LangSmith 评测后，报告会显示在这里。"
                />
              ),
            }}
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
      <CompareDrawer
        result={compareResult}
        open={compareResult !== null}
        onClose={() => setCompareResult(null)}
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
    <Drawer title="评测报告详情" open={open} onClose={onClose} width="min(100vw, 760px)">
      {loading || !report ? (
        <div className="status-text">正在读取报告...</div>
      ) : (
        <div className="settings-report-detail">
          <MetricSummary
            items={[
              { label: 'total', value: formatReportValue(summary.total ?? summary.total_cases ?? '-') },
              { label: 'top k', value: formatReportValue(summary.top_k ?? '-') },
              { label: 'pass rate', value: formatReportValue(summary.pass_rate ?? '-') },
              { label: 'hit rate', value: formatReportValue(summary.hit_rate ?? '-') },
              { label: 'rerank Δhit', value: formatDelta(summary.rerank_comparison, 'hit_rate_delta') },
              { label: 'latency', value: typeof summary.latency_ms === 'number' ? formatLatency(summary.latency_ms) : '-' },
            ]}
          />
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
              scroll={{ x: 360 }}
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
                scroll={{ x: 560 }}
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
        scroll={{ x: 560 }}
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

function formatDelta(source: unknown, key: string): string {
  if (!source || typeof source !== 'object' || Array.isArray(source)) {
    return '-'
  }
  const value = (source as Record<string, unknown>)[key]
  if (typeof value !== 'number') {
    return '-'
  }
  const sign = value > 0 ? '+' : ''
  return `${sign}${value.toFixed(4)}`
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

function CompareDrawer({
  result,
  open,
  onClose,
}: {
  result: CompareResult | null
  open: boolean
  onClose: () => void
}) {
  if (!result) return null
  const leftMeta = result.left as Record<string, string>
  const rightMeta = result.right as Record<string, string>
  const deltaRows = Object.entries(result.deltas).map(([metric, vals]) => ({
    metric,
    left: vals.left,
    right: vals.right,
    delta: vals.delta,
    direction: vals.delta > 0.0001 ? '↑' : vals.delta < -0.0001 ? '↓' : '—',
  }))

  return (
    <Drawer title="报告对比" open={open} onClose={onClose} width="min(100vw, 760px)">
      <div className="settings-report-detail">
        <MetricSummary
          items={[
            { label: '左（基准）', value: `#${leftMeta.id} · ${leftMeta.strategy || '-'}` },
            { label: '右（对比）', value: `#${rightMeta.id} · ${rightMeta.strategy || '-'}` },
          ]}
        />
        <Table
          size="small"
          pagination={false}
          rowKey="metric"
          dataSource={deltaRows}
          scroll={{ x: 500 }}
          columns={[
            { title: '指标', dataIndex: 'metric' },
            { title: '左', dataIndex: 'left', render: (v: number) => v.toFixed(4) },
            { title: '右', dataIndex: 'right', render: (v: number) => v.toFixed(4) },
            { title: 'Delta', dataIndex: 'delta', render: (v: number) => {
              const sign = v > 0 ? '+' : ''
              return `${sign}${v.toFixed(4)}`
            }},
            { title: '', dataIndex: 'direction', width: 40 },
          ]}
        />
      </div>
    </Drawer>
  )
}
