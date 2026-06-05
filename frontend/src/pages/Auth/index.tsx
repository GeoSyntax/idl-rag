import { Button, Card, Form, Input, Tabs, message } from 'antd'
import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'

import { api } from '../../api/client'
import type { LoginResponse } from '../../api/types'

type AuthPageProps = {
  onAuthenticated: (value: LoginResponse) => void
}

type LoginFormValues = {
  username: string
  password: string
}

type RegisterFormValues = {
  username: string
  password: string
  confirm_password: string
}

export function AuthPage({ onAuthenticated }: AuthPageProps) {
  const [activeTab, setActiveTab] = useState<'login' | 'register'>('login')
  const [loginForm] = Form.useForm<LoginFormValues>()
  const [registerForm] = Form.useForm<RegisterFormValues>()
  const [messageApi, contextHolder] = message.useMessage()

  const loginMutation = useMutation({
    mutationFn: api.login,
    onSuccess: (value) => {
      onAuthenticated(value)
      loginForm.resetFields()
      messageApi.success('登录成功')
    },
    onError: (error: Error) => {
      messageApi.error(error.message)
    },
  })

  const registerMutation = useMutation({
    mutationFn: (values: RegisterFormValues) =>
      api.register({
        username: values.username,
        password: values.password,
      }),
    onSuccess: (value) => {
      onAuthenticated(value)
      registerForm.resetFields()
      messageApi.success('注册成功')
    },
    onError: (error: Error) => {
      messageApi.error(error.message)
    },
  })

  return (
    <div className="auth-shell">
      {contextHolder}
      <Card className="section-card auth-card" title="登录或注册">
        <div className="auth-subtext">系统为完全私有模式，登录后只能访问自己的知识库和对话数据。</div>
        <Tabs
          activeKey={activeTab}
          onChange={(key) => setActiveTab(key as 'login' | 'register')}
          items={[
            {
              key: 'login',
              label: '登录',
              children: (
                <Form<LoginFormValues>
                  form={loginForm}
                  layout="vertical"
                  onFinish={(values) => loginMutation.mutate(values)}
                >
                  <Form.Item label="用户名" name="username" rules={[{ required: true, message: '请输入用户名' }]}>
                    <Input autoComplete="username" />
                  </Form.Item>
                  <Form.Item label="密码" name="password" rules={[{ required: true, message: '请输入密码' }]}>
                    <Input.Password autoComplete="current-password" />
                  </Form.Item>
                  <Button type="primary" htmlType="submit" loading={loginMutation.isPending}>
                    登录
                  </Button>
                </Form>
              ),
            },
            {
              key: 'register',
              label: '注册',
              children: (
                <Form<RegisterFormValues>
                  form={registerForm}
                  layout="vertical"
                  onFinish={(values) => registerMutation.mutate(values)}
                >
                  <Form.Item label="用户名" name="username" rules={[{ required: true, message: '请输入用户名' }]}>
                    <Input autoComplete="username" />
                  </Form.Item>
                  <Form.Item label="密码" name="password" rules={[{ required: true, message: '请输入密码' }]}>
                    <Input.Password autoComplete="new-password" />
                  </Form.Item>
                  <Form.Item
                    label="确认密码"
                    name="confirm_password"
                    dependencies={['password']}
                    rules={[
                      { required: true, message: '请再次输入密码' },
                      ({ getFieldValue }) => ({
                        validator(_, value) {
                          if (!value || getFieldValue('password') === value) {
                            return Promise.resolve()
                          }
                          return Promise.reject(new Error('两次输入的密码不一致'))
                        },
                      }),
                    ]}
                  >
                    <Input.Password autoComplete="new-password" />
                  </Form.Item>
                  <Button type="primary" htmlType="submit" loading={registerMutation.isPending}>
                    注册并登录
                  </Button>
                </Form>
              ),
            },
          ]}
        />
      </Card>
    </div>
  )
}
