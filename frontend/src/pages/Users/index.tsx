import { Button, Card, Form, Input, Modal, Table, message } from 'antd'
import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'

import { api } from '../../api/client'
import type { AuthUser } from '../../api/types'

type UsersPageProps = {
  items: AuthUser[]
  loading: boolean
  currentUserId: number
  onChanged: () => void
}

type ResetPasswordState = {
  user: AuthUser
  open: boolean
}

function formatDate(value: string): string {
  return new Date(value).toLocaleString()
}

export function UsersPage({ items, loading, currentUserId, onChanged }: UsersPageProps) {
  const [messageApi, contextHolder] = message.useMessage()
  const [resetForm] = Form.useForm<{ new_password: string }>()
  const [resetState, setResetState] = useState<ResetPasswordState | null>(null)

  const updateMutation = useMutation({
    mutationFn: ({ userId, payload }: { userId: number; payload: { role?: 'admin' | 'user'; is_active?: boolean } }) =>
      api.updateUser(userId, payload),
    onSuccess: () => {
      onChanged()
      messageApi.success('用户信息已更新')
    },
    onError: (error: Error) => {
      messageApi.error(error.message)
    },
  })

  const resetPasswordMutation = useMutation({
    mutationFn: ({ userId, newPassword }: { userId: number; newPassword: string }) =>
      api.resetUserPassword(userId, { new_password: newPassword }),
    onSuccess: () => {
      onChanged()
      setResetState(null)
      resetForm.resetFields()
      messageApi.success('密码已重置')
    },
    onError: (error: Error) => {
      messageApi.error(error.message)
    },
  })

  return (
    <div className="page-stack">
      {contextHolder}
      <Card className="section-card" title="用户列表">
        <Table<AuthUser>
          rowKey="id"
          loading={loading}
          dataSource={items}
          pagination={items.length > 10 ? { pageSize: 10 } : false}
          scroll={{ x: 820 }}
          columns={[
            { title: '用户名', dataIndex: 'username' },
            {
              title: '角色',
              dataIndex: 'role',
              width: 120,
              render: (value: AuthUser['role']) => (value === 'admin' ? '管理员' : '普通用户'),
            },
            {
              title: '状态',
              dataIndex: 'is_active',
              width: 120,
              render: (value: boolean) => (value ? '已启用' : '已禁用'),
            },
            {
              title: '创建时间',
              dataIndex: 'created_at',
              width: 200,
              render: (value: string) => formatDate(value),
            },
            {
              title: '操作',
              key: 'actions',
              width: 320,
              render: (_, record) => {
                const isCurrentUser = record.id === currentUserId
                return (
                  <div className="table-actions">
                    <Button
                      disabled={isCurrentUser}
                      loading={updateMutation.isPending && updateMutation.variables?.userId === record.id}
                      onClick={() =>
                        updateMutation.mutate({
                          userId: record.id,
                          payload: { role: record.role === 'admin' ? 'user' : 'admin' },
                        })
                      }
                    >
                      {record.role === 'admin' ? '设为普通用户' : '设为管理员'}
                    </Button>
                    <Button
                      disabled={isCurrentUser}
                      loading={updateMutation.isPending && updateMutation.variables?.userId === record.id}
                      onClick={() =>
                        updateMutation.mutate({
                          userId: record.id,
                          payload: { is_active: !record.is_active },
                        })
                      }
                    >
                      {record.is_active ? '禁用' : '启用'}
                    </Button>
                    <Button
                      onClick={() => {
                        resetForm.resetFields()
                        setResetState({ user: record, open: true })
                      }}
                    >
                      重置密码
                    </Button>
                  </div>
                )
              },
            },
          ]}
        />
      </Card>

      <Modal
        open={Boolean(resetState?.open)}
        title={resetState ? `重置密码：${resetState.user.username}` : '重置密码'}
        okText="确认"
        cancelText="取消"
        onCancel={() => {
          setResetState(null)
          resetForm.resetFields()
        }}
        onOk={async () => {
          const values = await resetForm.validateFields()
          if (!resetState) {
            return
          }
          resetPasswordMutation.mutate({ userId: resetState.user.id, newPassword: values.new_password })
        }}
        confirmLoading={resetPasswordMutation.isPending}
      >
        <Form form={resetForm} layout="vertical">
          <Form.Item
            label="新密码"
            name="new_password"
            rules={[
              { required: true, message: '请输入新密码' },
              { min: 6, message: '密码至少 6 位' },
            ]}
          >
            <Input.Password autoComplete="new-password" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
