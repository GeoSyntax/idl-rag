import {
  DatabaseOutlined,
  ExperimentOutlined,
  FileTextOutlined,
  MenuFoldOutlined,
  MenuOutlined,
  MenuUnfoldOutlined,
  MessageOutlined,
  SearchOutlined,
  SettingOutlined,
  TeamOutlined,
} from '@ant-design/icons'
import { Button, Drawer, Layout, Menu, Tooltip } from 'antd'
import { useCallback, useEffect, useState } from 'react'
import type { ReactNode } from 'react'

import type { AuthUser } from '../api/types'

const { Header, Content, Sider } = Layout

const SIDER_COLLAPSED_KEY = 'idl-rag-sider-collapsed'

type AppLayoutProps = {
  title: string
  activeKey: string
  onNavigate: (key: string) => void
  onLogout: () => void
  currentUser: AuthUser
  showUsers: boolean
  showSettings: boolean
  children: ReactNode
}

export function AppLayout({
  title,
  activeKey,
  onNavigate,
  onLogout,
  currentUser,
  showUsers,
  showSettings,
  children,
}: AppLayoutProps) {
  const [collapsed, setCollapsed] = useState(() => {
    try {
      return localStorage.getItem(SIDER_COLLAPSED_KEY) === 'true'
    } catch {
      return false
    }
  })
  const [mobileNavOpen, setMobileNavOpen] = useState(false)

  useEffect(() => {
    try {
      localStorage.setItem(SIDER_COLLAPSED_KEY, String(collapsed))
    } catch {
      // ignore
    }
  }, [collapsed])

  const toggleCollapsed = useCallback(() => {
    setCollapsed((prev) => !prev)
  }, [])

  const menuItems = [
    { key: 'dashboard', icon: <DatabaseOutlined />, label: '概览' },
    { key: 'research', icon: <ExperimentOutlined />, label: '研究项目' },
    { key: 'knowledge-bases', icon: <DatabaseOutlined />, label: '知识库' },
    { key: 'documents', icon: <FileTextOutlined />, label: '文档' },
    { key: 'chat', icon: <MessageOutlined />, label: '对话' },
    { key: 'retrieval-lab', icon: <SearchOutlined />, label: '检索测试' },
    ...(showUsers ? [{ key: 'users', icon: <TeamOutlined />, label: '用户' }] : []),
    ...(showSettings ? [{ key: 'settings', icon: <SettingOutlined />, label: '设置' }] : []),
  ]

  const handleNavigate = useCallback((key: string) => {
    onNavigate(key)
    setMobileNavOpen(false)
  }, [onNavigate])

  return (
    <Layout className="app-shell">
      <Sider
        width={240}
        collapsedWidth={56}
        collapsed={collapsed}
        className="app-sidebar"
        collapsible
        trigger={null}
      >
        <div className="app-sidebar-inner">
          <div className="app-brand">
            {collapsed ? (
              <div className="app-brand-collapsed">IR</div>
            ) : (
              <>
                <h1 className="app-brand-title">IDL RAG Panel</h1>
                <p className="app-brand-subtitle">ENVI/IDL 知识问答</p>
              </>
            )}
          </div>
          <Menu
            className="app-menu"
            mode="inline"
            inlineCollapsed={collapsed}
            selectedKeys={[activeKey]}
            items={menuItems}
            onClick={({ key }) => handleNavigate(key)}
          />
          <div className="app-sidebar-footer">
            <Tooltip title={collapsed ? '展开侧边栏' : '收起侧边栏'} placement="right">
              <Button
                type="text"
                className="app-sidebar-toggle"
                icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
                onClick={toggleCollapsed}
              />
            </Tooltip>
          </div>
        </div>
      </Sider>
      <Layout className="app-content">
        <Header className="app-header">
          <div className="app-header-bar">
            <div className="app-title-row">
              <Button
                type="text"
                className="app-mobile-nav-trigger"
                icon={<MenuOutlined />}
                onClick={() => setMobileNavOpen(true)}
              />
              <h2 className="app-title">{title}</h2>
            </div>
            <div className="app-user-panel">
              <div>
                <div className="app-user-name">{currentUser.username}</div>
                <div className="app-user-meta">{currentUser.role === 'admin' ? '管理员' : '普通用户'}</div>
              </div>
              <Button onClick={onLogout}>退出登录</Button>
            </div>
          </div>
        </Header>
        <Content className="app-body">{children}</Content>
      </Layout>
      <Drawer
        title="导航"
        placement="left"
        open={mobileNavOpen}
        onClose={() => setMobileNavOpen(false)}
        width="min(320px, 86vw)"
        className="app-mobile-nav"
      >
        <Menu
          className="app-menu"
          mode="inline"
          selectedKeys={[activeKey]}
          items={menuItems}
          onClick={({ key }) => handleNavigate(key)}
        />
      </Drawer>
    </Layout>
  )
}
