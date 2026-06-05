import { Alert, Button, Card, Checkbox, Drawer, Form, Input, Select, Table, Tag, Upload, message } from 'antd'
import type { UploadFile } from 'antd'
import { UploadOutlined } from '@ant-design/icons'
import type { RcFile } from 'antd/es/upload/interface'
import { useMemo, useState } from 'react'
import { useMutation } from '@tanstack/react-query'

import { api } from '../../api/client'
import type { DocumentChunk, DocumentItem, ImportResult } from '../../api/types'
import { DisplayEmpty, MetricSummary } from '../../components/DisplayPrimitives'

type DocumentsPageProps = {
  knowledgeBaseId?: number
  documents: DocumentItem[]
  loading: boolean
  onImported: (result: ImportResult) => void
  onChanged: () => void
  onDeleted: (documentId: number) => void
}

const statusColorMap: Record<string, string> = {
  queued: 'default',
  processing: 'gold',
  ready: 'green',
  failed: 'red',
  stale: 'orange',
}

const maxUploadFiles = 20
const maxUploadFileMb = 50
const supportedExtensions = ['.pdf', '.md', '.markdown', '.txt', '.pro', '.idl']

function formatDate(value: string | null): string {
  return value ? new Date(value).toLocaleString() : '-'
}

export function DocumentsPage({
  knowledgeBaseId,
  documents,
  loading,
  onImported,
  onChanged,
  onDeleted,
}: DocumentsPageProps) {
  const [form] = Form.useForm<{ path: string; recursive: boolean }>()
  const [fileList, setFileList] = useState<RcFile[]>([])
  const [chunkDocument, setChunkDocument] = useState<DocumentItem | null>(null)
  const [messageApi, contextHolder] = message.useMessage()

  const documentSummary = useMemo(() => {
    return documents.reduce(
      (summary, document) => {
        if (document.status === 'ready') {
          summary.ready += 1
        } else if (document.status === 'queued' || document.status === 'processing') {
          summary.active += 1
        } else if (document.status === 'failed') {
          summary.failed += 1
        } else if (document.status === 'stale') {
          summary.stale += 1
        }
        return summary
      },
      { ready: 0, active: 0, failed: 0, stale: 0 },
    )
  }, [documents])

  const importPathMutation = useMutation({
    mutationFn: (values: { path: string; recursive: boolean }) => {
      if (!knowledgeBaseId) {
        throw new Error('请先选择知识库')
      }
      return api.importPath(knowledgeBaseId, values)
    },
    onSuccess: (result) => {
      onImported(result)
      if (result.imported.length > 0) {
        messageApi.success(`已加入 ${result.imported.length} 个文档到索引队列`)
      }
      if (result.skipped.length > 0) {
        messageApi.warning(`跳过 ${result.skipped.length} 个重复或不支持的文件`)
      }
    },
    onError: (error: Error) => {
      messageApi.error(error.message)
    },
  })

  const uploadMutation = useMutation({
    mutationFn: () => {
      if (!knowledgeBaseId) {
        throw new Error('请先选择知识库')
      }
      return api.uploadDocuments(knowledgeBaseId, fileList)
    },
    onSuccess: (result) => {
      onImported(result)
      setFileList([])
      if (result.imported.length > 0) {
        messageApi.success(`已加入 ${result.imported.length} 个上传文件到索引队列`)
      }
      if (result.skipped.length > 0) {
        messageApi.warning(`跳过 ${result.skipped.length} 个重复或不支持的文件`)
      }
    },
    onError: (error: Error) => {
      messageApi.error(error.message)
    },
  })

  const retryMutation = useMutation({
    mutationFn: (documentId: number) => {
      if (!knowledgeBaseId) {
        throw new Error('请先选择知识库')
      }
      return api.retryDocument(knowledgeBaseId, documentId)
    },
    onSuccess: () => {
      onChanged()
      messageApi.success('文档已重新加入索引队列')
    },
    onError: (error: Error) => {
      messageApi.error(error.message)
    },
  })

  const reindexMutation = useMutation({
    mutationFn: (documentId: number) => {
      if (!knowledgeBaseId) {
        throw new Error('请先选择知识库')
      }
      return api.reindexDocument(knowledgeBaseId, documentId)
    },
    onSuccess: () => {
      onChanged()
      messageApi.success('文档已加入重建索引队列')
    },
    onError: (error: Error) => {
      messageApi.error(error.message)
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (documentId: number) => {
      if (!knowledgeBaseId) {
        throw new Error('请先选择知识库')
      }
      return api.deleteDocument(knowledgeBaseId, documentId)
    },
    onSuccess: (_, documentId) => {
      onDeleted(documentId)
      messageApi.success('文档已删除')
    },
    onError: (error: Error) => {
      messageApi.error(error.message)
    },
  })

  const chunksMutation = useMutation({
    mutationFn: (documentId: number) => {
      if (!knowledgeBaseId) {
        throw new Error('请先选择知识库')
      }
      return api.listDocumentChunks(knowledgeBaseId, documentId)
    },
    onError: (error: Error) => {
      messageApi.error(error.message || '片段加载失败')
    },
  })

  const openChunkViewer = (document: DocumentItem) => {
    setChunkDocument(document)
    chunksMutation.mutate(document.id)
  }

  const closeChunkViewer = () => {
    setChunkDocument(null)
    chunksMutation.reset()
  }

  const title = useMemo(() => {
    return knowledgeBaseId ? `当前知识库 ID：${knowledgeBaseId}` : '请先在知识库页选择一个知识库'
  }, [knowledgeBaseId])

  const beforeUpload = (file: RcFile) => {
    const extension = `.${file.name.split('.').pop()?.toLowerCase() ?? ''}`
    if (!supportedExtensions.includes(extension)) {
      messageApi.warning(`不支持的文件类型：${file.name}`)
      return false
    }
    if (file.size > maxUploadFileMb * 1024 * 1024) {
      messageApi.warning(`单个文件不能超过 ${maxUploadFileMb} MB：${file.name}`)
      return false
    }
    if (fileList.length >= maxUploadFiles) {
      messageApi.warning(`单次最多上传 ${maxUploadFiles} 个文件`)
      return false
    }
    setFileList((current) => [...current, file])
    return false
  }

  return (
    <div className="page-stack">
      {contextHolder}
      <Card
        className="section-card"
        title="导入资料"
        extra={<span className="status-text">{title}</span>}
      >
        <div className="content-stack">
          <Alert
            type="info"
            message="路径导入读取的是服务器本机路径，不是浏览器所在电脑的路径。未配置服务器导入根目录时，请使用上传文件。"
            showIcon={false}
          />
          <div className="document-import-summary">
            <MetricSummary
              items={[
                { label: '支持格式', value: supportedExtensions.join(' ') },
                { label: '单次文件', value: maxUploadFiles },
                { label: '单文件上限', value: `${maxUploadFileMb} MB` },
              ]}
            />
          </div>
          <Form
            form={form}
            layout="vertical"
            initialValues={{ recursive: true }}
            onFinish={(values) => importPathMutation.mutate(values)}
          >
            <div className="grid-two">
              <Form.Item label="本地目录或文件路径" name="path" rules={[{ required: true }]}>
                <Input placeholder="例如：E:/docs/idl-manual" disabled={!knowledgeBaseId} />
              </Form.Item>
              <Form.Item label="选项" name="recursive" valuePropName="checked">
                <Checkbox disabled={!knowledgeBaseId}>递归导入子目录</Checkbox>
              </Form.Item>
            </div>
            <Button type="primary" htmlType="submit" disabled={!knowledgeBaseId} loading={importPathMutation.isPending}>
              导入路径
            </Button>
          </Form>

          <div className="content-stack content-stack-inline">
            <Upload
              multiple
              beforeUpload={beforeUpload}
              onRemove={(file) => {
                setFileList((current) => current.filter((item) => item.uid !== file.uid))
              }}
              fileList={fileList.map<UploadFile>((file) => ({
                uid: file.uid,
                name: file.name,
                status: 'done',
              }))}
            >
              <Button icon={<UploadOutlined />} disabled={!knowledgeBaseId}>
                选择文件
              </Button>
            </Upload>
            <Button
              onClick={() => uploadMutation.mutate()}
              disabled={!knowledgeBaseId || fileList.length === 0}
              loading={uploadMutation.isPending}
            >
              上传文件
            </Button>
          </div>
        </div>
      </Card>

      <Card
        className="section-card"
        title="文档列表"
        extra={
          <MetricSummary
            className="document-toolbar"
            items={[
              { label: 'ready', value: documentSummary.ready, tone: 'success' },
              { label: '处理中', value: documentSummary.active },
              { label: 'failed', value: documentSummary.failed, tone: documentSummary.failed > 0 ? 'danger' : 'default' },
              { label: 'stale', value: documentSummary.stale, tone: documentSummary.stale > 0 ? 'warning' : 'default' },
            ]}
          />
        }
      >
        <Table<DocumentItem>
          rowKey="id"
          loading={loading}
          dataSource={documents}
          pagination={documents.length > 10 ? { pageSize: 10 } : false}
          scroll={{ x: 860 }}
          locale={{
            emptyText: (
              <DisplayEmpty
                compact
                illustration="documents"
                title="暂无文档"
                description="导入路径或上传文件后，文档会加入索引队列。"
              />
            ),
          }}
          columns={[
            {
              title: '文件',
              render: (_, record) => (
                <div className="document-primary">
                  <div className="document-name">{record.file_name}</div>
                  <div className="document-path">{record.file_path}</div>
                </div>
              ),
            },
            {
              title: '状态',
              dataIndex: 'status',
              width: 150,
              render: (value: string, record) => (
                <div className="document-status-block">
                  <Tag color={statusColorMap[value] ?? 'default'}>{value}</Tag>
                  {record.error_message ? <div className="document-error">{record.error_message}</div> : null}
                </div>
              ),
            },
            {
              title: '索引信息',
              width: 280,
              render: (_, record) => (
                <div className="document-meta">
                  <div>Chunk：{record.chunk_count}</div>
                  <div>重试：{record.retry_count}</div>
                  <div>解析：{record.parser_version ?? '-'}</div>
                  <div>切块：{record.chunker_version ?? '-'}</div>
                  <div>
                    向量：
                    {record.embedding_model ? `${record.embedding_model} / ${record.embedding_dimensions ?? '-'} 维` : '-'}
                    {record.embedding_is_fallback ? <Tag color="orange">fallback</Tag> : null}
                  </div>
                  <div>索引表：{record.index_table ?? '-'}</div>
                  <div>最近索引：{formatDate(record.last_indexed_at)}</div>
                </div>
              ),
            },
            {
              title: '操作',
              key: 'actions',
              width: 220,
              render: (_, record) => (
                <div className="table-actions">
                  {record.status === 'failed' ? (
                    <Button
                      loading={retryMutation.isPending && retryMutation.variables === record.id}
                      onClick={() => retryMutation.mutate(record.id)}
                    >
                      重试
                    </Button>
                  ) : null}
                  {record.chunk_count > 0 ? (
                    <Button
                      loading={chunksMutation.isPending && chunksMutation.variables === record.id}
                      onClick={() => openChunkViewer(record)}
                    >
                      查看片段
                    </Button>
                  ) : null}
                  {record.status === 'ready' || record.status === 'stale' ? (
                    <Button
                      loading={reindexMutation.isPending && reindexMutation.variables === record.id}
                      onClick={() => reindexMutation.mutate(record.id)}
                    >
                      重建索引
                    </Button>
                  ) : null}
                  <Button
                    danger
                    loading={deleteMutation.isPending && deleteMutation.variables === record.id}
                    onClick={() => deleteMutation.mutate(record.id)}
                  >
                    删除
                  </Button>
                </div>
              ),
            },
          ]}
        />
      </Card>

      <ChunkViewerDrawer
        document={chunkDocument}
        chunks={chunksMutation.data ?? []}
        loading={chunksMutation.isPending}
        onClose={closeChunkViewer}
      />
    </div>
  )
}

function ChunkViewerDrawer({
  document,
  chunks,
  loading,
  onClose,
}: {
  document: DocumentItem | null
  chunks: DocumentChunk[]
  loading: boolean
  onClose: () => void
}) {
  const [chunkKindFilter, setChunkKindFilter] = useState<string | undefined>()
  const [symbolFilter, setSymbolFilter] = useState<string | undefined>()

  const chunkKindOptions = useMemo(() => {
    return Array.from(new Set(chunks.map(getChunkKind).filter(Boolean) as string[])).map((value) => ({ label: value, value }))
  }, [chunks])

  const symbolOptions = useMemo(() => {
    return Array.from(new Set(chunks.map((chunk) => chunk.symbol_name).filter(Boolean) as string[])).map((value) => ({
      label: value,
      value,
    }))
  }, [chunks])

  const filteredChunks = useMemo(() => {
    return chunks.filter((chunk) => {
      if (chunkKindFilter && getChunkKind(chunk) !== chunkKindFilter) {
        return false
      }
      if (symbolFilter && chunk.symbol_name !== symbolFilter) {
        return false
      }
      return true
    })
  }, [chunks, chunkKindFilter, symbolFilter])

  const handleClose = () => {
    setChunkKindFilter(undefined)
    setSymbolFilter(undefined)
    onClose()
  }

  return (
    <Drawer
      title={document ? `文档片段：${document.file_name}` : '文档片段'}
      open={Boolean(document)}
      onClose={handleClose}
      width="min(100vw, 820px)"
    >
      <div className="chunk-viewer-stack">
        {document ? (
          <div className="document-meta">
            <div>状态：{document.status}</div>
            <div>Chunk：{document.chunk_count}</div>
            <div>解析：{document.parser_version ?? '-'}</div>
            <div>切块：{document.chunker_version ?? '-'}</div>
            <div>
              向量：{document.embedding_model ? `${document.embedding_model} / ${document.embedding_dimensions ?? '-'} 维` : '-'}
              {document.embedding_is_fallback ? ' / fallback' : ''}
            </div>
            <div>索引表：{document.index_table ?? '-'}</div>
            <div>最近索引：{formatDate(document.last_indexed_at)}</div>
          </div>
        ) : null}
        <div className="chunk-filter-bar">
          <Select
            allowClear
            placeholder="按 chunk 类型筛选"
            value={chunkKindFilter}
            options={chunkKindOptions}
            onChange={setChunkKindFilter}
            style={{ minWidth: 180 }}
          />
          <Select
            allowClear
            showSearch
            placeholder="按符号筛选"
            value={symbolFilter}
            options={symbolOptions}
            onChange={setSymbolFilter}
            style={{ minWidth: 220 }}
          />
          <span className="status-text">{filteredChunks.length} / {chunks.length} chunks</span>
        </div>
        <Table<DocumentChunk>
          rowKey="id"
          loading={loading}
          size="small"
          pagination={filteredChunks.length > 8 ? { pageSize: 8 } : false}
          dataSource={filteredChunks}
          scroll={{ x: 760 }}
          columns={[
            {
              title: '#',
              dataIndex: 'chunk_index',
              width: 64,
            },
            {
              title: '类型',
              width: 150,
              render: (_, chunk) => (
                <div className="retrieval-tags">
                  {getChunkKind(chunk) ? <Tag>{getChunkKind(chunk)}</Tag> : null}
                  {getSymbolKind(chunk) ? <Tag>{getSymbolKind(chunk)}</Tag> : null}
                </div>
              ),
            },
            {
              title: '符号',
              width: 170,
              render: (_, chunk) => chunk.symbol_name || '-',
            },
            {
              title: '行号',
              width: 110,
              render: (_, chunk) => getLineRange(chunk) || '-',
            },
            {
              title: '片段',
              render: (_, chunk) => <ChunkSummary chunk={chunk} document={document} />,
            },
          ]}
        />
      </div>
    </Drawer>
  )
}

function ChunkSummary({ chunk, document }: { chunk: DocumentChunk; document: DocumentItem | null }) {
  const metadata = chunk.metadata ?? {}
  const details = [
    ['Chunk ID', chunk.id],
    ['标题', chunk.title || '-'],
    ['章节', chunk.section || '-'],
    ['符号', chunk.symbol_name || '-'],
    ['Chunk 类型', getChunkKind(chunk) || '-'],
    ['符号类型', getSymbolKind(chunk) || '-'],
    ['行号', getLineRange(chunk) || '-'],
    ['解析器', metadataValue(metadata.parser_version) || document?.parser_version || '-'],
    ['Chunker', metadataValue(metadata.chunker_version) || document?.chunker_version || '-'],
    ['Embedding', document?.embedding_model ? `${document.embedding_model} / ${document.embedding_dimensions ?? '-'} 维` : '-'],
    ['索引状态', document?.status === 'ready' && document.index_table ? '已索引' : document?.status || '-'],
  ]

  return (
    <div className="chunk-summary">
      <div className="chunk-summary-title">{chunk.title || chunk.symbol_name || `Chunk ${chunk.chunk_index}`}</div>
      <div className="chunk-detail-grid">
        {details.map(([label, value]) => (
          <div key={label} className="chunk-detail-row">
            <span>{label}</span>
            <strong>{value}</strong>
          </div>
        ))}
      </div>
      <pre className="chunk-summary-content">{chunk.content}</pre>
      {Object.keys(metadata).length > 0 ? (
        <details className="chunk-summary-details">
          <summary>metadata</summary>
          <pre className="chunk-summary-json">{JSON.stringify(metadata, null, 2)}</pre>
        </details>
      ) : null}
    </div>
  )
}

function getChunkKind(chunk: DocumentChunk): string | undefined {
  const value = chunk.metadata?.chunk_kind
  return typeof value === 'string' && value ? value : undefined
}

function getSymbolKind(chunk: DocumentChunk): string | undefined {
  const value = chunk.metadata?.symbol_kind
  return typeof value === 'string' && value ? value : undefined
}

function getLineRange(chunk: DocumentChunk): string | undefined {
  const startLine = typeof chunk.metadata?.start_line === 'number' ? chunk.metadata.start_line : undefined
  const endLine = typeof chunk.metadata?.end_line === 'number' ? chunk.metadata.end_line : undefined
  return startLine ? `L${startLine}${endLine && endLine !== startLine ? `-L${endLine}` : ''}` : undefined
}

function metadataValue(value: unknown): string | undefined {
  return typeof value === 'string' && value ? value : undefined
}
