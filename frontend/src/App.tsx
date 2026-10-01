import { QueryClient, useQuery } from '@tanstack/react-query'
import { App as AntApp, Spin } from 'antd'
import { lazy, Suspense, useEffect, useState } from 'react'

import { api, clearAccessToken, hasStoredAccessToken, onUnauthorized, saveAccessToken } from './api/client'
import type { AuthUser, DocumentItem, ImportResult, KnowledgeBase, LoginResponse, SystemSettingsResponse } from './api/types'

// Keep the shell and auth path small. Research, Chat and the data-management
// pages are loaded only when the user opens them; this matters for the first
// visit on a school network where the JS bundle may be served slowly.
const AuthPage = lazy(() => import('./pages/Auth').then(({ AuthPage }) => ({ default: AuthPage })))
const AppLayout = lazy(() => import('./components/AppLayout').then(({ AppLayout }) => ({ default: AppLayout })))
const ChatPage = lazy(() => import('./pages/Chat').then(({ ChatPage }) => ({ default: ChatPage })))
const DashboardPage = lazy(() => import('./pages/Dashboard').then(({ DashboardPage }) => ({ default: DashboardPage })))
const DocumentsPage = lazy(() => import('./pages/Documents').then(({ DocumentsPage }) => ({ default: DocumentsPage })))
const KnowledgeBasesPage = lazy(() => import('./pages/KnowledgeBases').then(({ KnowledgeBasesPage }) => ({ default: KnowledgeBasesPage })))
const RetrievalLabPage = lazy(() => import('./pages/RetrievalLab').then(({ RetrievalLabPage }) => ({ default: RetrievalLabPage })))
const ResearchPage = lazy(() => import('./pages/Research').then(({ ResearchPage }) => ({ default: ResearchPage })))
const SettingsPage = lazy(() => import('./pages/Settings').then(({ SettingsPage }) => ({ default: SettingsPage })))
const UsersPage = lazy(() => import('./pages/Users').then(({ UsersPage }) => ({ default: UsersPage })))

type PageKey = 'dashboard' | 'research' | 'knowledge-bases' | 'documents' | 'chat' | 'retrieval-lab' | 'settings' | 'users'

const pageTitles: Record<PageKey, string> = {
  dashboard: '概览',
  research: '研究项目',
  'knowledge-bases': '知识库',
  documents: '文档',
  chat: '对话',
  'retrieval-lab': '检索测试',
  settings: '设置',
  users: '用户',
}

export const queryClient = new QueryClient()

export default function App() {
  const [activePage, setActivePage] = useState<PageKey>('dashboard')
  const [selectedKnowledgeBaseId, setSelectedKnowledgeBaseId] = useState<number>()
  const [initialResearchProjectId, setInitialResearchProjectId] = useState<number>()
  const [currentUser, setCurrentUser] = useState<AuthUser | null>(null)
  const [authLoading, setAuthLoading] = useState(true)

  const isAuthenticated = currentUser !== null
  const isAdmin = currentUser?.role === 'admin'

  useEffect(() => {
    let active = true

    const restoreSession = async () => {
      if (!hasStoredAccessToken()) {
        if (active) {
          setCurrentUser(null)
          setAuthLoading(false)
        }
        return
      }
      try {
        const user = await api.getCurrentUser()
        if (active) {
          setCurrentUser(user)
        }
      } catch {
        if (active) {
          setCurrentUser(null)
        }
      } finally {
        if (active) {
          setAuthLoading(false)
        }
      }
    }

    void restoreSession()
    const unsubscribe = onUnauthorized(() => {
      if (!active) {
        return
      }
      queryClient.clear()
      setCurrentUser(null)
      setSelectedKnowledgeBaseId(undefined)
      setInitialResearchProjectId(undefined)
      setActivePage('dashboard')
      setAuthLoading(false)
    })

    return () => {
      active = false
      unsubscribe()
    }
  }, [])

  useEffect(() => {
    if (!isAdmin && (activePage === 'settings' || activePage === 'users')) {
      setActivePage('dashboard')
    }
  }, [activePage, isAdmin])

  // Chat run cards can point back to the authoritative Research workspace
  // without coupling the lazy Chat chunk to AppLayout's local page state.
  useEffect(() => {
    const handleResearchNavigation = (event: Event) => {
      const detail = (event as CustomEvent<{ page?: string; projectId?: number }>).detail
      if (detail?.page !== 'research') return
      setInitialResearchProjectId(detail.projectId)
      setActivePage('research')
    }
    window.addEventListener('idl-rag:navigate', handleResearchNavigation)
    return () => window.removeEventListener('idl-rag:navigate', handleResearchNavigation)
  }, [])

  const dashboardQuery = useQuery({
    queryKey: ['dashboard-summary', currentUser?.id],
    queryFn: api.getDashboardSummary,
    enabled: isAuthenticated,
  })
  const settingsQuery = useQuery({
    queryKey: ['settings', currentUser?.id],
    queryFn: api.getSettings,
    enabled: isAdmin,
  })
  const usersQuery = useQuery({
    queryKey: ['users', currentUser?.id],
    queryFn: api.listUsers,
    enabled: isAdmin,
  })
  const knowledgeBasesQuery = useQuery({
    queryKey: ['knowledge-bases', currentUser?.id],
    queryFn: api.listKnowledgeBases,
    enabled: isAuthenticated,
  })
  const documentsQuery = useQuery({
    queryKey: ['documents', currentUser?.id, selectedKnowledgeBaseId],
    queryFn: () => api.listDocuments(selectedKnowledgeBaseId as number),
    enabled: isAuthenticated && Boolean(selectedKnowledgeBaseId),
    refetchInterval: (query) => {
      const documents = (query.state.data ?? []) as DocumentItem[]
      return documents.some((document) => document.status === 'queued' || document.status === 'processing') ? 1500 : false
    },
  })

  useEffect(() => {
    const items = knowledgeBasesQuery.data ?? []
    if (!items.length) {
      setSelectedKnowledgeBaseId(undefined)
      return
    }
    if (!selectedKnowledgeBaseId || !items.some((item) => item.id === selectedKnowledgeBaseId)) {
      setSelectedKnowledgeBaseId(items[0].id)
    }
  }, [knowledgeBasesQuery.data, selectedKnowledgeBaseId])

  const documents = selectedKnowledgeBaseId ? documentsQuery.data ?? [] : []

  const refreshSummary = () => {
    if (isAuthenticated) {
      void dashboardQuery.refetch()
    }
  }

  const refreshKnowledgeBases = async () => {
    const result = await knowledgeBasesQuery.refetch()
    return result.data ?? []
  }

  const refreshDocuments = () => {
    if (selectedKnowledgeBaseId) {
      void documentsQuery.refetch()
    }
  }

  const refreshUsers = () => {
    if (isAdmin) {
      void usersQuery.refetch()
    }
  }

  const handleAuthenticated = (value: LoginResponse) => {
    saveAccessToken(value.access_token)
    queryClient.clear()
    setCurrentUser(value.user)
    setSelectedKnowledgeBaseId(undefined)
    setInitialResearchProjectId(undefined)
    setActivePage('dashboard')
    setAuthLoading(false)
  }

  const handleLogout = () => {
    clearAccessToken(false)
    queryClient.clear()
    setCurrentUser(null)
    setSelectedKnowledgeBaseId(undefined)
    setInitialResearchProjectId(undefined)
    setActivePage('dashboard')
    setAuthLoading(false)
  }

  const handleSettingsSaved = (value: SystemSettingsResponse) => {
    queryClient.setQueryData(['settings', currentUser?.id], value)
    refreshDocuments()
    refreshSummary()
  }

  const handleKnowledgeBaseCreated = async (item: KnowledgeBase) => {
    setSelectedKnowledgeBaseId(item.id)
    await refreshKnowledgeBases()
    refreshSummary()
    setActivePage('documents')
  }

  const handleKnowledgeBaseDeleted = async (id: number) => {
    const items = await refreshKnowledgeBases()
    refreshSummary()
    if (selectedKnowledgeBaseId === id) {
      setSelectedKnowledgeBaseId(items[0]?.id)
    }
  }

  const handleImported = (_result: ImportResult) => {
    refreshDocuments()
    refreshSummary()
  }

  const handleDocumentsChanged = () => {
    refreshDocuments()
    refreshSummary()
  }

  const handleDocumentDeleted = (_documentId: number) => {
    refreshDocuments()
    refreshSummary()
  }

  if (authLoading) {
    return (
      <AntApp>
        <div className="app-loading">
          <Spin />
        </div>
      </AntApp>
    )
  }

  if (!currentUser) {
    return (
      <AntApp>
        <Suspense fallback={<div className="app-loading"><Spin /></div>}>
          <AuthPage onAuthenticated={handleAuthenticated} />
        </Suspense>
      </AntApp>
    )
  }

  const content = (() => {
    switch (activePage) {
      case 'dashboard':
        return <DashboardPage summary={dashboardQuery.data} loading={dashboardQuery.isLoading} />
      case 'research':
        return (
          <ResearchPage
            currentUserId={currentUser.id}
            initialProjectId={initialResearchProjectId}
            onOpenAgent={(projectId) => {
              setInitialResearchProjectId(projectId)
              setActivePage('chat')
            }}
          />
        )
      case 'knowledge-bases':
        return (
          <KnowledgeBasesPage
            items={knowledgeBasesQuery.data ?? []}
            loading={knowledgeBasesQuery.isLoading}
            selectedKnowledgeBaseId={selectedKnowledgeBaseId}
            onCreated={handleKnowledgeBaseCreated}
            onDeleted={handleKnowledgeBaseDeleted}
            onUpdated={() => void refreshKnowledgeBases()}
            onSelect={(id) => setSelectedKnowledgeBaseId(id)}
          />
        )
      case 'documents':
        return (
          <DocumentsPage
            knowledgeBaseId={selectedKnowledgeBaseId}
            documents={documents}
            loading={documentsQuery.isLoading}
            onImported={handleImported}
            onChanged={handleDocumentsChanged}
            onDeleted={handleDocumentDeleted}
          />
        )
      case 'chat':
        return (
          <ChatPage
            knowledgeBases={knowledgeBasesQuery.data ?? []}
            initialKnowledgeBaseId={selectedKnowledgeBaseId}
            initialResearchProjectId={initialResearchProjectId}
          />
        )
      case 'retrieval-lab':
        return <RetrievalLabPage knowledgeBases={knowledgeBasesQuery.data ?? []} initialKnowledgeBaseId={selectedKnowledgeBaseId} />
      case 'settings':
        return (
          <SettingsPage
            settings={settingsQuery.data}
            loading={settingsQuery.isLoading}
            knowledgeBases={knowledgeBasesQuery.data ?? []}
            onSaved={handleSettingsSaved}
          />
        )
      case 'users':
        return (
          <UsersPage
            items={usersQuery.data ?? []}
            loading={usersQuery.isLoading}
            currentUserId={currentUser.id}
            onChanged={refreshUsers}
          />
        )
      default:
        return <Spin />
    }
  })()

  return (
    <AntApp>
      <Suspense fallback={<div className="app-loading"><Spin /></div>}>
        <AppLayout
          title={pageTitles[activePage]}
          activeKey={activePage}
          onNavigate={(key) => setActivePage(key as PageKey)}
          onLogout={handleLogout}
          currentUser={currentUser}
          showUsers={isAdmin}
          showSettings={isAdmin}
        >
          {content}
        </AppLayout>
      </Suspense>
    </AntApp>
  )
}
