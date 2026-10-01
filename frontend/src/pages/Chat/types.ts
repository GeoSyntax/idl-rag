import type { ChatArtifact } from '../../api/types'

export type AgentStepItem = {
  id: number
  step: string
  content?: string
  tool?: string
  args?: Record<string, unknown>
  arg_keys?: string[]
  output?: string
  output_length?: number
  output_digest?: string
  metadata?: Record<string, unknown>
}

export type AgentRunMeta = {
  streamId: string | null
  serverElapsedMs: number | null
  firstTokenMs: number | null
}

export type AttachedFile = {
  name: string
  content: string
}

export type KnowledgeStatus = {
  ready: number
  stale: number
  failed: number
  active: number
  fallback: number
  total: number
}

export type ArtifactAction = (artifact: ChatArtifact) => void
export type AsyncArtifactAction = (artifact: ChatArtifact) => void | Promise<void>
