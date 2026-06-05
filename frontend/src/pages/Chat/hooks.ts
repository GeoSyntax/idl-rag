import { useEffect, useMemo, useState } from 'react'

import { api } from '../../api/client'
import type { DocumentItem } from '../../api/types'
import type { KnowledgeStatus } from './types'

const EMPTY_STATUS: KnowledgeStatus = {
  ready: 0,
  stale: 0,
  failed: 0,
  active: 0,
  fallback: 0,
  total: 0,
}

export function useKnowledgeStatus(selectedKBIds: number[], onError: (message: string) => void) {
  const [documents, setDocuments] = useState<DocumentItem[]>([])
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    let active = true
    if (!selectedKBIds.length) {
      setDocuments([])
      return
    }
    setLoading(true)
    Promise.all(selectedKBIds.map((id) => api.listDocuments(id)))
      .then((groups) => {
        if (active) {
          setDocuments(groups.flat())
        }
      })
      .catch((err: Error) => {
        if (active) {
          onError(err.message || '文档状态加载失败')
        }
      })
      .finally(() => {
        if (active) {
          setLoading(false)
        }
      })
    return () => {
      active = false
    }
  }, [selectedKBIds, onError])

  const status = useMemo(() => {
    if (!documents.length) {
      return EMPTY_STATUS
    }
    const ready = documents.filter((item) => item.status === 'ready').length
    const stale = documents.filter((item) => item.status === 'stale').length
    const failed = documents.filter((item) => item.status === 'failed').length
    const active = documents.filter((item) => item.status === 'queued' || item.status === 'processing').length
    const fallback = documents.filter((item) => item.embedding_is_fallback).length
    return { ready, stale, failed, active, fallback, total: documents.length }
  }, [documents])

  return { loading, status }
}
