import { Button, Card, Drawer, Form, Input, InputNumber, Select, Table, Tag, message } from 'antd'
import { useMutation } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'

import { api } from '../../api/client'
import type { KnowledgeBase, RetrievalDebugCandidate, RetrievalDebugResponse } from '../../api/types'
import { DisplayEmpty, MetricSummary } from '../../components/DisplayPrimitives'

type RetrievalLabPageProps = {
  knowledgeBases: KnowledgeBase[]
  initialKnowledgeBaseId?: number
}

type RetrievalLabForm = {
  knowledge_base_ids?: number[]
  query?: string
  strategy?: string
  top_k?: number
}

const strategyOptions = [
  { label: 'Hybrid RRF · fast default', value: 'hybrid_rrf_no_rerank' },
  { label: 'Hybrid RRF + rerank', value: 'hybrid_rrf' },
  { label: 'FTS only', value: 'fts_only' },
  { label: 'Vector only · diagnostic', value: 'vector_only' },
  { label: 'Multi query · slow', value: 'multi_query' },
  { label: 'HyDE', value: 'hyde' },
  { label: 'Parent child', value: 'parent_child' },
  { label: 'Dependency GraphRAG', value: 'dependency_graphrag' },
  { label: 'Grep', value: 'grep' },
  { label: 'Symbol search', value: 'symbol_search' },
  { label: 'Read context', value: 'read_context' },
  { label: 'Find callers', value: 'find_callers' },
  { label: 'Find callees', value: 'find_callees' },
]

export function RetrievalLabPage({ knowledgeBases, initialKnowledgeBaseId }: RetrievalLabPageProps) {
  const [form] = Form.useForm<RetrievalLabForm>()
  const [messageApi, contextHolder] = message.useMessage()

  const debugMutation = useMutation({
    mutationFn: (values: RetrievalLabForm) => {
      const knowledgeBaseIds = values.knowledge_base_ids ?? []
      const query = values.query?.trim() ?? ''
      if (!knowledgeBaseIds.length) {
        throw new Error('请至少选择一个知识库')
      }
      if (!query) {
        throw new Error('请输入检索问题')
      }
      return api.retrieveDebug({
        knowledge_base_ids: knowledgeBaseIds,
        query,
        strategy: values.strategy,
        top_k: values.top_k,
      })
    },
    onError: (error: Error) => {
      messageApi.error(error.message || '检索测试失败')
    },
  })

  const knowledgeBaseOptions = knowledgeBases.map((kb) => ({
    label: `${kb.name} (${kb.document_count})`,
    value: kb.id,
  }))

  const initialKnowledgeBaseIds = useMemo(
    () => initialKnowledgeBaseId ? [initialKnowledgeBaseId] : knowledgeBases[0] ? [knowledgeBases[0].id] : [],
    [initialKnowledgeBaseId, knowledgeBases],
  )
  const initialKnowledgeBase = knowledgeBases.find((kb) => kb.id === initialKnowledgeBaseIds[0])

  useEffect(() => {
    if (initialKnowledgeBase) {
      form.setFieldsValue({
        knowledge_base_ids: [initialKnowledgeBase.id],
        strategy: initialKnowledgeBase.default_retrieval_strategy,
        top_k: initialKnowledgeBase.default_top_k,
      })
    }
  }, [form, initialKnowledgeBase])

  const result = debugMutation.data

  return (
    <div className="page-stack">
      {contextHolder}
      <Card title="检索测试" className="section-card">
        <Form
          form={form}
          layout="vertical"
          initialValues={{
            knowledge_base_ids: initialKnowledgeBaseIds,
            strategy: initialKnowledgeBase?.default_retrieval_strategy ?? 'hybrid_rrf_no_rerank',
            top_k: initialKnowledgeBase?.default_top_k ?? 8,
          }}
          onFinish={(values) => debugMutation.mutate(values)}
        >
          <div className="retrieval-policy-note">
            推荐默认使用 Hybrid RRF · fast default；Multi query 适合复杂跨库问题但较慢；Vector only 主要用于诊断 embedding 质量。
          </div>
          <div className="retrieval-lab-grid">
            <Form.Item name="knowledge_base_ids" label="知识库" rules={[{ required: true, message: '请选择知识库' }]}>
              <Select
                mode="multiple"
                allowClear
                placeholder="选择知识库"
                options={knowledgeBaseOptions}
                maxTagCount="responsive"
                onChange={(ids: number[]) => {
                  if (ids.length !== 1) {
                    return
                  }
                  const knowledgeBase = knowledgeBases.find((kb) => kb.id === ids[0])
                  if (knowledgeBase) {
                    form.setFieldsValue({
                      strategy: knowledgeBase.default_retrieval_strategy,
                      top_k: knowledgeBase.default_top_k,
                    })
                  }
                }}
              />
            </Form.Item>
            <Form.Item name="strategy" label="检索策略">
              <Select options={strategyOptions} />
            </Form.Item>
            <Form.Item name="top_k" label="Top K">
              <InputNumber min={1} max={20} style={{ width: '100%' }} />
            </Form.Item>
          </div>
          <Form.Item name="query" label="问题" rules={[{ required: true, message: '请输入检索问题' }]}>
            <Input.TextArea rows={3} placeholder="输入一个需要解释召回过程的问题" />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={debugMutation.isPending}>
            运行检索测试
          </Button>
        </Form>
      </Card>

      <Card title="候选结果" className="section-card">
        {!result ? (
          <DisplayEmpty
            compact
            illustration="retrieval"
            title="等待检索测试"
            description="运行一次检索测试后，这里会显示候选 chunk、来源、分数和调试信息。"
          />
        ) : (
          <RetrievalResult result={result} />
        )}
      </Card>
    </div>
  )
}

function RetrievalResult({ result }: { result: RetrievalDebugResponse }) {
  const [selectedCandidate, setSelectedCandidate] = useState<RetrievalDebugCandidate | null>(null)

  return (
    <div className="retrieval-result-stack">
      <MetricSummary
        className="retrieval-trace-summary"
        items={[
          { label: 'query', value: result.query },
          { label: 'strategy', value: result.strategy },
          { label: 'candidates', value: result.candidate_count },
          ...(result.strategy === 'vector_only' ? [{ label: 'mode', value: 'diagnostic only', tone: 'warning' as const }] : []),
        ]}
      />
      <Table<RetrievalDebugCandidate>
        rowKey={(record) => `${record.rank}-${record.chunk_id}`}
        size="small"
        pagination={false}
        dataSource={result.candidates}
        scroll={{ x: 920 }}
        columns={[
          {
            title: 'Rank',
            dataIndex: 'rank',
            width: 72,
          },
          {
            title: '来源',
            render: (_, record) => (
              <div className="retrieval-source">
                <div className="document-name">{record.symbol_name || record.title || record.file_name}</div>
                <div className="document-path">{sourceMeta(record).join(' · ')}</div>
              </div>
            ),
          },
          {
            title: '分数',
            width: 180,
            render: (_, record) => <DebugScoreSummary record={record} />,
          },
          {
            title: '匹配',
            width: 160,
            render: (_, record) => (
              <div className="retrieval-tags">
                {record.source_strategy ? <Tag>{record.source_strategy}</Tag> : null}
                {record.match_type ? <Tag>{record.match_type}</Tag> : null}
              </div>
            ),
          },
          {
            title: '片段',
            render: (_, record) => <span className="retrieval-excerpt">{record.excerpt}</span>,
          },
          {
            title: '操作',
            width: 88,
            render: (_, record) => <Button size="small" onClick={() => setSelectedCandidate(record)}>详情</Button>,
          },
        ]}
      />
      <CandidateDrawer candidate={selectedCandidate} onClose={() => setSelectedCandidate(null)} />
    </div>
  )
}

function DebugScoreSummary({ record }: { record: RetrievalDebugCandidate }) {
  const scores = asRecord(record.debug.scores)
  const entries = Object.entries(scores).filter(([, value]) => typeof value === 'number')
  if (!entries.length) {
    return <span>{typeof record.score === 'number' ? record.score.toFixed(4) : '-'}</span>
  }
  return (
    <div className="retrieval-score-list">
      {entries.slice(0, 4).map(([key, value]) => (
        <div key={key} className="retrieval-score-row">
          <span>{key}</span>
          <strong>{formatValue(value)}</strong>
        </div>
      ))}
    </div>
  )
}

function CandidateDrawer({ candidate, onClose }: { candidate: RetrievalDebugCandidate | null; onClose: () => void }) {
  if (!candidate) {
    return null
  }

  const details = [
    ['知识库', candidate.knowledge_base_name || candidate.knowledge_base_id || '-'],
    ['文件', candidate.file_name],
    ['路径', candidate.file_path],
    ['标题', candidate.title || '-'],
    ['章节', candidate.section || '-'],
    ['符号', candidate.symbol_name || '-'],
    ['Chunk 类型', candidate.chunk_kind || '-'],
    ['检索策略', candidate.source_strategy || '-'],
    ['匹配类型', candidate.match_type || '-'],
    ['行号', lineRange(candidate) || '-'],
    ['分数', typeof candidate.score === 'number' ? candidate.score.toFixed(4) : '-'],
  ]

  return (
    <Drawer title="候选详情" open={Boolean(candidate)} onClose={onClose} width="min(100vw, 620px)">
      <div className="citation-detail-stack">
        <div className="citation-detail-list">
          {details.map(([label, value]) => (
            <div key={label} className="citation-detail-row">
              <span className="citation-detail-label">{label}</span>
              <span className="citation-detail-value">{value}</span>
            </div>
          ))}
        </div>
        <DebugSection title="检索分数" value={candidate.debug.scores} />
        <DebugSection title="匹配信息" value={candidate.debug.match} />
        <DebugSection title="融合信息" value={candidate.debug.fusion} />
        <div>
          <div className="citation-detail-section-title">引用片段</div>
          <pre className="citation-detail-excerpt">{candidate.excerpt}</pre>
        </div>
        {candidate.metadata && Object.keys(candidate.metadata).length > 0 ? (
          <div>
            <div className="citation-detail-section-title">Metadata</div>
            <pre className="citation-detail-json">{JSON.stringify(candidate.metadata, null, 2)}</pre>
          </div>
        ) : null}
        <details>
          <summary className="retrieval-debug-summary">Raw debug</summary>
          <pre className="citation-detail-json">{JSON.stringify(candidate.debug, null, 2)}</pre>
        </details>
      </div>
    </Drawer>
  )
}

function DebugSection({ title, value }: { title: string; value: unknown }) {
  const record = asRecord(value)
  const entries = Object.entries(record).filter(([, item]) => item !== undefined && item !== null && item !== '')
  if (!entries.length) {
    return null
  }
  return (
    <div>
      <div className="citation-detail-section-title">{title}</div>
      <div className="citation-detail-list">
        {entries.map(([key, item]) => (
          <div key={key} className="citation-detail-row">
            <span className="citation-detail-label">{key}</span>
            <span className="citation-detail-value">{formatValue(item)}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

function sourceMeta(record: RetrievalDebugCandidate): string[] {
  return [
    record.knowledge_base_name,
    record.file_name,
    record.chunk_kind || undefined,
    lineRange(record),
  ].filter(Boolean) as string[]
}

function lineRange(record: { line_start?: number | null; line_end?: number | null }): string | undefined {
  if (!record.line_start) {
    return undefined
  }
  return `L${record.line_start}${record.line_end && record.line_end !== record.line_start ? `-L${record.line_end}` : ''}`
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

function formatValue(value: unknown): string {
  if (typeof value === 'number') {
    return value.toFixed(4)
  }
  if (typeof value === 'string') {
    return value
  }
  return JSON.stringify(value) ?? String(value)
}
