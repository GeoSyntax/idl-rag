import type { ChatArtifact } from '../../api/types'

export type AgentStepItem = {
  id: number
  step: string
  content?: string
  tool?: string
  args?: Record<string, string>
  output?: string
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
