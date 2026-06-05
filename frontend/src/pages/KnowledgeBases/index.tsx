import { Button, Card, Drawer, Form, Input, InputNumber, Popconfirm, Select, Space, Switch, Table, message } from 'antd'
import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'

import { api } from '../../api/client'
import type { KnowledgeBase } from '../../api/types'

type KnowledgeBasesPageProps = {
  items: KnowledgeBase[]
  loading: boolean
  selectedKnowledgeBaseId?: number
  onCreated: (item: KnowledgeBase) => void
  onDeleted: (id: number) => void
  onUpdated: (item: KnowledgeBase) => void
  onSelect: (id: number) => void
}

type KnowledgeBaseConfigForm = {
  name: string
  description?: string | null
  default_retrieval_strategy: string
  default_top_k: number
  default_rerank_enabled: boolean
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
]

export function KnowledgeBasesPage({
  items,
  loading,
  selectedKnowledgeBaseId,
  onCreated,
  onDeleted,
  onUpdated,
  onSelect,
}: KnowledgeBasesPageProps) {
  const [form] = Form.useForm<{ name: string; description?: string }>()
  const [configForm] = Form.useForm<KnowledgeBaseConfigForm>()
  const [configTarget, setConfigTarget] = useState<KnowledgeBase | null>(null)
  const [messageApi, contextHolder] = message.useMessage()

  const createMutation = useMutation({
    mutationFn: api.createKnowledgeBase,
    onSuccess: (value) => {
      onCreated(value)
      form.resetFields()
      messageApi.success('知识库已创建')
    },
    onError: (error: Error) => {
      messageApi.error(error.message)
    },
  })

  const deleteMutation = useMutation({
    mutationFn: api.deleteKnowledgeBase,
    onSuccess: (_, id) => {
      onDeleted(id)
      messageApi.success('知识库已删除')
    },
    onError: (error: Error) => {
      messageApi.error(error.message)
    },
  })

  const updateMutation = useMutation({
    mutationFn: (values: KnowledgeBaseConfigForm) => {
      if (!configTarget) {
        throw new Error('请先选择知识库')
      }
      return api.updateKnowledgeBase(configTarget.id, values)
    },
    onSuccess: (value) => {
      onUpdated(value)
      setConfigTarget(null)
      messageApi.success('知识库配置已保存')
    },
    onError: (error: Error) => {
      messageApi.error(error.message)
    },
  })

  const openConfig = (item: KnowledgeBase) => {
    setConfigTarget(item)
    configForm.setFieldsValue({
      name: item.name,
      description: item.description,
      default_retrieval_strategy: item.default_retrieval_strategy,
      default_top_k: item.default_top_k,
      default_rerank_enabled: item.default_rerank_enabled,
    })
  }

  return (
    <div className="page-stack">
      {contextHolder}
      <Card className="section-card" title="新建知识库">
        <Form form={form} layout="vertical" onFinish={(values) => createMutation.mutate(values)}>
          <div className="grid-two">
            <Form.Item label="名称" name="name" rules={[{ required: true, message: '请输入知识库名称' }]}>
              <Input placeholder="例如：ENVI/IDL 手册" />
            </Form.Item>
            <Form.Item label="说明" name="description">
              <Input placeholder="可选说明" />
            </Form.Item>
          </div>
          <Button type="primary" htmlType="submit" loading={createMutation.isPending}>
            创建知识库
          </Button>
        </Form>
      </Card>

      <Card className="section-card" title="知识库列表">
        <Table<KnowledgeBase>
          rowKey="id"
          loading={loading}
          dataSource={items}
          pagination={false}
          rowSelection={{
            type: 'radio',
            selectedRowKeys: selectedKnowledgeBaseId ? [selectedKnowledgeBaseId] : [],
            onChange: (keys) => {
              const selected = Number(keys[0])
              if (selected) {
                onSelect(selected)
              }
            },
          }}
          columns={[
            { title: '名称', dataIndex: 'name' },
            { title: '说明', dataIndex: 'description', render: (value: string | null) => value || '-' },
            { title: '文档数', dataIndex: 'document_count', width: 100 },
            {
              title: '默认检索',
              width: 220,
              render: (_, record) => (
                <div className="knowledge-config-summary">
                  <span>{record.default_retrieval_strategy}</span>
                  <span>top_k {record.default_top_k}</span>
                  <span>{record.default_rerank_enabled ? 'rerank on' : 'rerank off'}</span>
                </div>
              ),
            },
            {
              title: '操作',
              key: 'actions',
              width: 220,
              render: (_, record) => (
                <Space>
                  <Button onClick={() => onSelect(record.id)}>选择</Button>
                  <Button onClick={() => openConfig(record)}>配置</Button>
                  <Popconfirm title="确认删除该知识库？" onConfirm={() => deleteMutation.mutate(record.id)}>
                    <Button danger loading={deleteMutation.isPending}>删除</Button>
                  </Popconfirm>
                </Space>
              ),
            },
          ]}
        />
      </Card>

      <Drawer
        title={configTarget ? `知识库配置：${configTarget.name}` : '知识库配置'}
        open={Boolean(configTarget)}
        onClose={() => setConfigTarget(null)}
        width={420}
      >
        <Form form={configForm} layout="vertical" onFinish={(values) => updateMutation.mutate(values)}>
          <Form.Item label="名称" name="name" rules={[{ required: true, message: '请输入知识库名称' }]}>
            <Input />
          </Form.Item>
          <Form.Item label="说明" name="description">
            <Input />
          </Form.Item>
          <Form.Item label="默认检索策略" name="default_retrieval_strategy" rules={[{ required: true }]}>
            <Select options={strategyOptions} />
          </Form.Item>
          <Form.Item label="默认 Top K" name="default_top_k" rules={[{ required: true }]}>
            <InputNumber min={1} max={20} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item label="默认启用 rerank" name="default_rerank_enabled" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={updateMutation.isPending}>
            保存配置
          </Button>
        </Form>
      </Drawer>
    </div>
  )
}
