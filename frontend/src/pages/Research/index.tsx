import { useMutation, useQuery } from '@tanstack/react-query'
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Divider,
  Form,
  Input,
  InputNumber,
  Select,
  Space,
  Spin,
  Table,
  Tabs,
  Tag,
  Timeline,
  message,
} from 'antd'
import { MessageOutlined } from '@ant-design/icons'
import { useEffect, useMemo, useRef, useState } from 'react'

import { api } from '../../api/client'
import type {
  EvidenceCard,
  FormulaSpec,
  ResearchDataAsset,
  ResearchDataSnapshot,
  ResearchExperiment,
  ResearchLiteratureCandidate,
  ResearchLiteratureNetworkResponse,
  ResearchLiteratureSearchResponse,
  ResearchStacCandidate,
  ResearchStacSearchResponse,
  ResearchKnowledgeSource,
  ResearchProject,
  ResearchProtocolRevision,
  ResearchProtocolEvidenceMapDraft,
  ResearchProtocolReadiness,
  ResearchRun,
  ResearchRagSearchResponse,
  ResearchValidationSample,
  ResearchProjectMember,
} from '../../api/types'
import { DisplayEmpty, FileTypeBadge, MetricSummary } from '../../components/DisplayPrimitives'
import { ResearchEvidenceBoundary } from '../../components/ResearchEvidenceBoundary'

type ProjectFormValues = {
  name: string
  description?: string
  entry_mode: 'template' | 'open'
}

type ProtocolFormValues = {
  description?: string
  protocol_json: string
}

type SnapshotFormValues = {
  name: string
  description?: string
  asset_ids: number[]
}

type EvidenceFormValues = {
  title: string
  status: EvidenceCard['status']
  source_type: EvidenceCard['source_type']
  source_url?: string
  doi?: string
  applicability?: string
  limitations?: string
}

type FormulaFormValues = {
  name: string
  version: number
  status: FormulaSpec['status']
  evidence_card_ids: number[]
  spec_json: string
}

type ExperimentFormValues = {
  name: string
  formula_spec_id: number
  data_snapshot_id: number
  runner_type: ResearchExperiment['runner_type']
  execution_mode: ResearchExperiment['execution_mode']
  parameters_json: string
  validation_plan_json: string
  visualization_contract: string[]
}

type ResearchGeeFormValues = {
  dataset_id: string
  start_date?: string
  end_date?: string
  bbox: string
  bands: string
  scale: number
  crs: string
  composite: 'median' | 'mean' | 'first'
  label?: string
}

type ValidationSampleFormValues = Omit<ResearchValidationSample, 'id' | 'project_id' | 'created_at' | 'metadata'>

type ValidationSampleImportFormValues = {
  data_snapshot_id: number
  source_asset_id?: number
}

type ProjectMemberFormValues = {
  username: string
}

type ResearchRagSourceFormValues = {
  knowledge_base_id: number
  category: ResearchKnowledgeSource['category']
}

// Ant Design places aria-required on the Select wrapper when a Form.Item uses
// `{ required: true }`; that wrapper is a div, so the attribute is invalid.
// Keep required validation without polluting the accessibility tree.
const requiredValueRule = (message: string) => ({
  validator: (_rule: unknown, value: unknown) => {
    const hasValue = Array.isArray(value) ? value.length > 0 : value !== undefined && value !== null && value !== ''
    return hasValue ? Promise.resolve() : Promise.reject(new Error(message))
  },
})

const requiredLabel = (label: string) => (
  <span>
    {label}<span className="research-required-mark" aria-hidden="true"> *</span>
  </span>
)

const DEFAULT_FORMULA_SPEC = JSON.stringify(
  {
    operation: 'normalized_difference_threshold',
    input_asset_id: 0,
    inputs: { green: { band: 1 }, swir1: { band: 2 } },
    parameters: { threshold: 0.0 },
  },
  null,
  2,
)

const SAFE_BAND_MATH_FORMULA_SPEC = JSON.stringify(
  {
    operation: 'safe_band_math_threshold',
    input_asset_id: 0,
    inputs: {
      green: { band: 1, scale: 1.0, offset: 0.0 },
      swir1: { band: 2, scale: 1.0, offset: 0.0 },
    },
    expression: 'clip((green - swir1) / (green + swir1) + bias, -1, 1)',
    parameters: { bias: 0.0, threshold: 0.0 },
    water_condition: '>=',
  },
  null,
  2,
)

const DEFAULT_VALIDATION_PLAN = JSON.stringify(
  {
    split: 'spatiotemporal-holdout',
    metrics: ['f1', 'iou', 'area_difference'],
  },
  null,
  2,
)

function parseObjectJson(value: string, label: string): Record<string, unknown> {
  try {
    const parsed: unknown = JSON.parse(value || '{}')
    if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') {
      throw new Error('不是对象')
    }
    return parsed as Record<string, unknown>
  } catch {
    throw new Error(`${label}必须是有效的 JSON 对象。`)
  }
}

function parseBoundingBox(value: string): number[] {
  const bbox = value.split(',').map((item) => Number(item.trim()))
  if (bbox.length !== 4 || bbox.some((item) => !Number.isFinite(item))) {
    throw new Error('GEE 范围必须是四个以英文逗号分隔的数值：minLon,minLat,maxLon,maxLat。')
  }
  return bbox
}

function parseBandList(value: string): string[] {
  return value.split(',').map((item) => item.trim()).filter(Boolean)
}

function formatDate(value: string | null | undefined): string {
  if (!value) return '-'
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

function statusColor(status: string): 'green' | 'gold' | 'red' | 'default' {
  if (status === 'completed' || status === 'verified' || status === 'frozen') return 'green'
  if (status === 'failed' || status === 'unavailable') return 'red'
  if (status === 'cancelled') return 'default'
  if (status === 'candidate' || status === 'running' || status === 'queued') return 'gold'
  return 'default'
}

function timelineColor(status: ResearchRun['status']): 'green' | 'red' | 'gray' | 'blue' {
  if (status === 'completed') return 'green'
  if (status === 'failed' || status === 'unavailable') return 'red'
  if (status === 'cancelled') return 'gray'
  return 'blue'
}

function validationMetricsFromRun(run: ResearchRun): Record<string, number> | null {
  const validation = run.manifest.validation
  if (!validation || typeof validation !== 'object' || Array.isArray(validation)) return null
  const metrics = (validation as Record<string, unknown>).metrics
  if (!metrics || typeof metrics !== 'object' || Array.isArray(metrics)) return null
  const entries = Object.entries(metrics as Record<string, unknown>).filter(
    (entry): entry is [string, number] => typeof entry[1] === 'number' && Number.isFinite(entry[1]),
  )
  return entries.length ? Object.fromEntries(entries) : null
}

function validationIntervalsFromRun(run: ResearchRun): Record<string, { lower: number; upper: number }> | null {
  const validation = run.manifest.validation
  if (!validation || typeof validation !== 'object' || Array.isArray(validation)) return null
  const validationRecord = validation as Record<string, unknown>
  const pointSamples = validationRecord.point_samples
  const pointSampleMetrics = pointSamples && typeof pointSamples === 'object' && !Array.isArray(pointSamples)
    ? (pointSamples as Record<string, unknown>).metrics
    : null
  const intervals = validationRecord.confidence_intervals
    ?? (pointSampleMetrics && typeof pointSampleMetrics === 'object' && !Array.isArray(pointSampleMetrics)
      ? (pointSampleMetrics as Record<string, unknown>).confidence_intervals
      : null)
  if (!intervals || typeof intervals !== 'object' || Array.isArray(intervals)) return null
  const result: Record<string, { lower: number; upper: number }> = {}
  for (const key of ['overall_accuracy', 'precision', 'recall', 'iou']) {
    const interval = (intervals as Record<string, unknown>)[key]
    if (!interval || typeof interval !== 'object' || Array.isArray(interval)) continue
    const lower = (interval as Record<string, unknown>).lower
    const upper = (interval as Record<string, unknown>).upper
    if (typeof lower === 'number' && typeof upper === 'number') result[key] = { lower, upper }
  }
  return Object.keys(result).length ? result : null
}

function validationWeightingFromRun(run: ResearchRun): Record<string, unknown> | null {
  const validation = run.manifest.validation
  if (!validation || typeof validation !== 'object' || Array.isArray(validation)) return null
  const pointSamples = (validation as Record<string, unknown>).point_samples
  if (!pointSamples || typeof pointSamples !== 'object' || Array.isArray(pointSamples)) return null
  const selection = (pointSamples as Record<string, unknown>).selection
  if (!selection || typeof selection !== 'object' || Array.isArray(selection)) return null
  const weighting = (selection as Record<string, unknown>).weighting
  if (!weighting || typeof weighting !== 'object' || Array.isArray(weighting)) return null
  return (weighting as Record<string, unknown>).enabled === true ? weighting as Record<string, unknown> : null
}

function validationAreaAdjustmentFromRun(run: ResearchRun): { selection: Record<string, unknown>; metrics: Record<string, unknown> } | null {
  const validation = run.manifest.validation
  if (!validation || typeof validation !== 'object' || Array.isArray(validation)) return null
  const pointSamples = (validation as Record<string, unknown>).point_samples
  if (!pointSamples || typeof pointSamples !== 'object' || Array.isArray(pointSamples)) return null
  const pointRecord = pointSamples as Record<string, unknown>
  const selection = pointRecord.selection
  const metrics = pointRecord.metrics
  if (!selection || typeof selection !== 'object' || Array.isArray(selection)) return null
  if (!metrics || typeof metrics !== 'object' || Array.isArray(metrics)) return null
  const areaAdjustment = (selection as Record<string, unknown>).area_adjustment
  const areaMetrics = (metrics as Record<string, unknown>).area_adjusted
  if (!areaAdjustment || typeof areaAdjustment !== 'object' || Array.isArray(areaAdjustment)) return null
  if (!areaMetrics || typeof areaMetrics !== 'object' || Array.isArray(areaMetrics)) return null
  return { selection: areaAdjustment as Record<string, unknown>, metrics: areaMetrics as Record<string, unknown> }
}

function parameterSweepFromRun(run: ResearchRun): Record<string, unknown> | null {
  if (run.manifest.run_kind !== 'parameter_sweep') return null
  const sweep = run.manifest.sweep
  if (!sweep || typeof sweep !== 'object' || Array.isArray(sweep)) return null
  return sweep as Record<string, unknown>
}

function RunOutputPreview({ projectId, experimentId, run, executionMode }: { projectId: number; experimentId: number; run: ResearchRun; executionMode?: ResearchExperiment['execution_mode'] }) {
  const [urls, setUrls] = useState<Record<string, string>>({})
  const [imageErrors, setImageErrors] = useState<Record<string, string>>({})
  const [imageRetryKey, setImageRetryKey] = useState(0)
  const [downloadingFileName, setDownloadingFileName] = useState('')
  const [downloadError, setDownloadError] = useState('')
  const verifyPackage = useMutation({
    mutationFn: () => api.verifyResearchRun(projectId, experimentId, run.id),
    onError: (error: Error) => message.error(error.message),
  })
  const evidencePackage = useMemo(
    () => run.outputs.find((output) => output.kind === 'research_evidence_package'),
    [run.outputs],
  )
  const imageOutputs = useMemo(
    () => run.outputs.filter((output) => output.file_name.toLowerCase().endsWith('.png')),
    [run.outputs],
  )
  const downloadableOutputs = useMemo(
    () => run.outputs.filter((output) => !output.file_name.toLowerCase().endsWith('.png') && !['run_manifest', 'research_evidence_package'].includes(output.kind)),
    [run.outputs],
  )
  const rasterMetadata = useMemo(() => {
    const value = run.manifest.raster_metadata
    return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null
  }, [run.manifest])
  const snapshotMetadata = useMemo(() => {
    const value = run.manifest.data_snapshot
    return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null
  }, [run.manifest])
  const formulaMetadata = useMemo(() => {
    const value = run.manifest.formula_spec
    return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null
  }, [run.manifest])
  const displayMetadata = (value: unknown): string => {
    if (value === null || value === undefined || value === '') return '-'
    if (Array.isArray(value)) return value.join(', ')
    if (typeof value === 'object') return JSON.stringify(value)
    return String(value)
  }

  useEffect(() => {
    let active = true
    const createdUrls: string[] = []
    const load = async () => {
      const results = await Promise.all(imageOutputs.map(async (output) => {
        try {
          const blob = await api.fetchResearchRunOutputBlob(projectId, experimentId, run.id, output.file_name)
          const url = URL.createObjectURL(blob)
          createdUrls.push(url)
          return { fileName: output.file_name, url, error: '' }
        } catch (error) {
          return {
            fileName: output.file_name,
            url: '',
            error: error instanceof Error ? error.message : '阶段图加载失败，请重试。',
          }
        }
      }))
      if (active) {
        setUrls(Object.fromEntries(results.filter((item) => item.url).map((item) => [item.fileName, item.url])))
        setImageErrors(Object.fromEntries(results.filter((item) => item.error).map((item) => [item.fileName, item.error])))
      }
    }
    setImageErrors({})
    if (imageOutputs.length) {
      void load()
    } else {
      setUrls({})
    }
    return () => {
      active = false
      createdUrls.forEach((url) => URL.revokeObjectURL(url))
    }
  }, [experimentId, imageOutputs, imageRetryKey, projectId, run.id])

  const downloadOutput = async (fileName: string) => {
    if (downloadingFileName) return
    setDownloadingFileName(fileName)
    setDownloadError('')
    try {
      const blob = await api.fetchResearchRunOutputBlob(projectId, experimentId, run.id, fileName)
      const url = URL.createObjectURL(blob)
      const link = window.document.createElement('a')
      link.href = url
      link.download = fileName
      window.document.body.appendChild(link)
      link.click()
      link.remove()
      URL.revokeObjectURL(url)
    } catch (error) {
      setDownloadError(error instanceof Error ? error.message : '获取运行产物失败，请稍后重试。')
    } finally {
      setDownloadingFileName('')
    }
  }

  return (
    <div className="research-run-products">
      <ResearchEvidenceBoundary
        executionMode={executionMode}
        hasValidationMetrics={Boolean(validationMetricsFromRun(run))}
        hasEvidencePackage={Boolean(evidencePackage)}
        evidenceVerified={verifyPackage.data?.verified === true}
      />
      {evidencePackage ? (
        <Alert
          type="success"
          showIcon
          message="正式运行的 Research Evidence Package 已生成"
          description={
            <Space wrap>
              <span>包含协议、快照引用、公式证据、环境、运行记录和生成图件；不包含私有原始数据。</span>
              <Button size="small" loading={downloadingFileName === evidencePackage.file_name} onClick={() => void downloadOutput(evidencePackage.file_name)}>
                下载证据包 ZIP
              </Button>
              <Button size="small" loading={verifyPackage.isPending} onClick={() => verifyPackage.mutate()}>
                校验证据包
              </Button>
            </Space>
          }
        />
      ) : null}
      {verifyPackage.data ? (
        <Alert
          type={verifyPackage.data.verified ? 'success' : 'error'}
          showIcon
          message={verifyPackage.data.notice}
          description={
            verifyPackage.data.verified
              ? `已校验 ${verifyPackage.data.checked_file_count} 个文件和 ${verifyPackage.data.output_count} 个生成产物。`
              : verifyPackage.data.issues.join(' ')
          }
        />
      ) : null}
      {downloadError ? (
        <Alert
          type="error"
          showIcon
          message="运行产物下载失败"
          description={downloadError}
          closable
          onClose={() => setDownloadError('')}
        />
      ) : null}
      {rasterMetadata || snapshotMetadata || formulaMetadata ? (
        <Descriptions
          className="research-output-metadata"
          size="small"
          bordered
          column={{ xs: 1, sm: 2, lg: 4 }}
          title="影像证据元数据"
          items={[
            { key: 'crs', label: 'CRS', children: displayMetadata(rasterMetadata?.crs) },
            { key: 'shape', label: '栅格尺寸', children: rasterMetadata ? `${displayMetadata(rasterMetadata.width)} × ${displayMetadata(rasterMetadata.height)}` : '-' },
            { key: 'nodata', label: 'NoData', children: displayMetadata(rasterMetadata?.nodata ?? rasterMetadata?.input_nodata) },
            { key: 'valid', label: '有效像元', children: displayMetadata(rasterMetadata?.valid_pixel_count) },
            { key: 'water', label: '分类像元', children: displayMetadata(rasterMetadata?.water_pixel_count) },
            { key: 'threshold', label: '阈值', children: displayMetadata(rasterMetadata?.threshold) },
            { key: 'operation', label: '公式操作', children: displayMetadata(formulaMetadata?.operation) },
            { key: 'snapshot', label: '快照指纹', children: snapshotMetadata?.snapshot_hash ? `${String(snapshotMetadata.snapshot_hash).slice(0, 16)}…` : '-' },
          ]}
        />
      ) : null}
      {imageOutputs.length ? (
        <div className="research-output-grid">
          {imageOutputs.map((output) => (
            <figure key={output.file_name} className="research-output-figure">
              {urls[output.file_name] ? (
                <img src={urls[output.file_name]} alt={`${output.kind} · ${output.file_name}`} />
              ) : imageErrors[output.file_name] ? (
                <div className="research-output-image-error" role="alert">
                  <span>{imageErrors[output.file_name]}</span>
                  <Button type="link" size="small" onClick={() => setImageRetryKey((value) => value + 1)}>
                    重试加载
                  </Button>
                </div>
              ) : <Spin size="small" />}
              <figcaption>
                <strong>{output.kind}</strong>
                <span>{output.file_name}</span>
                <Button type="link" size="small" loading={downloadingFileName === output.file_name} onClick={() => void downloadOutput(output.file_name)}>
                  下载图件
                </Button>
              </figcaption>
            </figure>
          ))}
        </div>
      ) : <DisplayEmpty compact illustration="image" title="本次运行没有可预览图像" />}
      {downloadableOutputs.length ? (
        <Alert
          type="info"
          showIcon
          message="可下载的数值与文本产物"
          description={
            <Space wrap>
              {downloadableOutputs.map((output) => (
                <Button key={output.file_name} size="small" loading={downloadingFileName === output.file_name} onClick={() => void downloadOutput(output.file_name)}>
                  下载 {output.kind}
                </Button>
              ))}
            </Space>
          }
        />
      ) : null}
    </div>
  )
}

export function ResearchPage({ currentUserId, initialProjectId, onOpenAgent }: { currentUserId: number; initialProjectId?: number; onOpenAgent?: (projectId: number) => void }) {
  const [messageApi, contextHolder] = message.useMessage()
  const [projectForm] = Form.useForm<ProjectFormValues>()
  const [protocolForm] = Form.useForm<ProtocolFormValues>()
  const [snapshotForm] = Form.useForm<SnapshotFormValues>()
  const [evidenceForm] = Form.useForm<EvidenceFormValues>()
  const [formulaForm] = Form.useForm<FormulaFormValues>()
  const [experimentForm] = Form.useForm<ExperimentFormValues>()
  const [geeForm] = Form.useForm<ResearchGeeFormValues>()
  const [validationSampleForm] = Form.useForm<ValidationSampleFormValues>()
  const [validationSampleImportForm] = Form.useForm<ValidationSampleImportFormValues>()
  const [projectMemberForm] = Form.useForm<ProjectMemberFormValues>()
  const [researchRagSourceForm] = Form.useForm<ResearchRagSourceFormValues>()
  const [selectedProjectId, setSelectedProjectId] = useState<number>()
  const [selectedExperimentId, setSelectedExperimentId] = useState<number>()
  const initialProjectAppliedRef = useRef<number>()
  const [assetFile, setAssetFile] = useState<File | null>(null)
  const [idlScriptFile, setIdlScriptFile] = useState<File | null>(null)
  const [validationSampleCsvFile, setValidationSampleCsvFile] = useState<File | null>(null)
  const [assetKind, setAssetKind] = useState<ResearchDataAsset['asset_kind']>('raster')
  const [stackAssetIds, setStackAssetIds] = useState<number[]>([])
  const [stackReferenceAssetId, setStackReferenceAssetId] = useState<number>()
  const [stackName, setStackName] = useState('aligned_raster_stack')
  const [stackBandNames, setStackBandNames] = useState('')
  const [stackResampling, setStackResampling] = useState<'nearest' | 'bilinear' | 'cubic'>('bilinear')
  const [protocolDraftQuestion, setProtocolDraftQuestion] = useState('')
  const [literatureQuery, setLiteratureQuery] = useState('')
  const [literatureProvider, setLiteratureProvider] = useState<ResearchLiteratureSearchResponse['provider']>('crossref')
  const [literatureSearchResult, setLiteratureSearchResult] = useState<ResearchLiteratureSearchResponse | null>(null)
  const [literatureNetworkResult, setLiteratureNetworkResult] = useState<ResearchLiteratureNetworkResponse | null>(null)
  const [literatureNetworkRequest, setLiteratureNetworkRequest] = useState<{
    candidate: ResearchLiteratureCandidate
    relation: 'citations' | 'references'
  } | null>(null)
  const [literatureRagKnowledgeBaseId, setLiteratureRagKnowledgeBaseId] = useState<number>()
  const [stacSearchResult, setStacSearchResult] = useState<ResearchStacSearchResponse | null>(null)
  const [stacProvider, setStacProvider] = useState<ResearchStacSearchResponse['provider']>('planetary_computer')
  const [stacCollections, setStacCollections] = useState('sentinel-2-l2a')
  const [stacBbox, setStacBbox] = useState('115.7,28.8,115.8,28.9')
  const [stacStart, setStacStart] = useState('2024-10-01')
  const [stacEnd, setStacEnd] = useState('2024-10-31')
  const [stacCloudMax, setStacCloudMax] = useState(20)
  const [researchRagQuery, setResearchRagQuery] = useState('')
  const [researchRagCategory, setResearchRagCategory] = useState<ResearchRagSearchResponse['category']>('all')
  const [researchRagResult, setResearchRagResult] = useState<ResearchRagSearchResponse | null>(null)
  const [protocolEvidenceMapDraft, setProtocolEvidenceMapDraft] = useState<ResearchProtocolEvidenceMapDraft | null>(null)
  const [protocolReadiness, setProtocolReadiness] = useState<ResearchProtocolReadiness | null>(null)
  const [sweepCandidatesJson, setSweepCandidatesJson] = useState('[\n  { "name": "baseline", "parameters": { "threshold": 0.0 } },\n  { "name": "candidate", "parameters": { "threshold": 0.2 } }\n]')
  const [sweepEvaluationSplit, setSweepEvaluationSplit] = useState<'development' | 'model_selection'>('development')
  const [sweepRankingMetric, setSweepRankingMetric] = useState<'overall_accuracy' | 'precision' | 'recall' | 'f1' | 'iou'>('f1')
  const [reproducibilityReferenceRunId, setReproducibilityReferenceRunId] = useState<number>()

  const projectsQuery = useQuery({ queryKey: ['research-projects'], queryFn: api.listResearchProjects })
  const selectedProject = (projectsQuery.data ?? []).find((project) => project.id === selectedProjectId)
  const assetsQuery = useQuery({
    queryKey: ['research-assets', selectedProjectId],
    queryFn: () => api.listResearchDataAssets(selectedProjectId as number),
    enabled: Boolean(selectedProjectId),
  })
  const snapshotsQuery = useQuery({
    queryKey: ['research-snapshots', selectedProjectId],
    queryFn: () => api.listResearchDataSnapshots(selectedProjectId as number),
    enabled: Boolean(selectedProjectId),
  })
  const evidenceQuery = useQuery({
    queryKey: ['research-evidence', selectedProjectId],
    queryFn: () => api.listEvidenceCards(selectedProjectId as number),
    enabled: Boolean(selectedProjectId),
  })
  const formulasQuery = useQuery({
    queryKey: ['research-formulas', selectedProjectId],
    queryFn: () => api.listFormulaSpecs(selectedProjectId as number),
    enabled: Boolean(selectedProjectId),
  })
  const experimentsQuery = useQuery({
    queryKey: ['research-experiments', selectedProjectId],
    queryFn: () => api.listResearchExperiments(selectedProjectId as number),
    enabled: Boolean(selectedProjectId),
  })
  const protocolRevisionsQuery = useQuery({
    queryKey: ['research-protocol-revisions', selectedProjectId],
    queryFn: () => api.listResearchProtocolRevisions(selectedProjectId as number),
    enabled: Boolean(selectedProjectId),
  })
  const runsQuery = useQuery({
    queryKey: ['research-runs', selectedProjectId, selectedExperimentId],
    queryFn: () => api.listResearchRuns(selectedProjectId as number, selectedExperimentId as number),
    enabled: Boolean(selectedProjectId && selectedExperimentId),
  })
  const validationSamplesQuery = useQuery({
    queryKey: ['research-validation-samples', selectedProjectId],
    queryFn: () => api.listResearchValidationSamples(selectedProjectId as number),
    enabled: Boolean(selectedProjectId),
  })
  const projectMembersQuery = useQuery({
    queryKey: ['research-project-members', selectedProjectId],
    queryFn: () => api.listResearchProjectMembers(selectedProjectId as number),
    enabled: Boolean(selectedProjectId),
  })
  const knowledgeBasesQuery = useQuery({ queryKey: ['knowledge-bases-for-research'], queryFn: api.listKnowledgeBases })
  const researchRagSourcesQuery = useQuery({
    queryKey: ['research-rag-sources', selectedProjectId],
    queryFn: () => api.listResearchRagSources(selectedProjectId as number),
    enabled: Boolean(selectedProjectId),
  })

  useEffect(() => {
    const projects = projectsQuery.data ?? []
    if (!projects.length) {
      setSelectedProjectId(undefined)
      return
    }
    if (initialProjectId && initialProjectAppliedRef.current !== initialProjectId && projects.some((project) => project.id === initialProjectId)) {
      setSelectedProjectId(initialProjectId)
      initialProjectAppliedRef.current = initialProjectId
    } else if (!selectedProjectId || !projects.some((project) => project.id === selectedProjectId)) {
      setSelectedProjectId(projects[0].id)
    }
  }, [initialProjectId, projectsQuery.data, selectedProjectId])

  useEffect(() => {
    setSelectedExperimentId(undefined)
    setLiteratureSearchResult(null)
    setLiteratureNetworkResult(null)
    setLiteratureNetworkRequest(null)
    setResearchRagResult(null)
    setProtocolEvidenceMapDraft(null)
    setProtocolReadiness(null)
    setIdlScriptFile(null)
  }, [selectedProjectId])

  useEffect(() => {
    if (!selectedProject) return
    protocolForm.setFieldsValue({
      description: selectedProject.description ?? undefined,
      protocol_json: JSON.stringify(selectedProject.protocol, null, 2),
    })
  }, [protocolForm, selectedProject])

  const createProject = useMutation({
    mutationFn: api.createResearchProject,
    onSuccess: async (project) => {
      projectForm.resetFields()
      setSelectedProjectId(project.id)
      await projectsQuery.refetch()
      messageApi.success('研究项目已创建')
    },
    onError: (error: Error) => messageApi.error(error.message),
  })
  const updateProject = useMutation({
    mutationFn: (values: ProtocolFormValues) => {
      if (!selectedProject) throw new Error('请先选择研究项目')
      return api.updateResearchProject(selectedProject.id, {
        description: values.description,
        protocol: parseObjectJson(values.protocol_json, '研究协议'),
      })
    },
    onSuccess: async () => {
      await Promise.all([projectsQuery.refetch(), protocolRevisionsQuery.refetch()])
      messageApi.success('研究协议已保存')
    },
    onError: (error: Error) => messageApi.error(error.message),
  })
  const draftProtocol = useMutation({
    mutationFn: () => {
      if (!selectedProject) throw new Error('请先选择研究项目')
      return api.draftResearchProtocol(selectedProject.id, protocolDraftQuestion.trim())
    },
    onSuccess: (result) => {
      setProtocolEvidenceMapDraft(null)
      protocolForm.setFieldsValue({ protocol_json: JSON.stringify(result.protocol, null, 2) })
      messageApi.success(result.notice)
    },
    onError: (error: Error) => messageApi.error(error.message),
  })
  const draftProtocolWithEvidenceMap = useMutation({
    mutationFn: () => {
      if (!selectedProject) throw new Error('请先选择研究项目')
      return api.draftResearchProtocolEvidenceMap(selectedProject.id, protocolDraftQuestion.trim())
    },
    onSuccess: (result) => {
      setProtocolEvidenceMapDraft(result)
      protocolForm.setFieldsValue({ protocol_json: JSON.stringify(result.protocol, null, 2) })
      messageApi.success(result.notice)
    },
    onError: (error: Error) => messageApi.error(error.message),
  })
  const checkProtocolReadiness = useMutation({
    mutationFn: () => {
      if (!selectedProject) throw new Error('请先选择研究项目')
      return api.getResearchProtocolReadiness(selectedProject.id)
    },
    onSuccess: (result) => {
      setProtocolReadiness(result)
      messageApi.success(result.notice)
    },
    onError: (error: Error) => messageApi.error(error.message),
  })
  const uploadAsset = useMutation({
    mutationFn: ({ file, assetKind }: { file: File; assetKind: ResearchDataAsset['asset_kind'] }) => {
      if (!selectedProject) throw new Error('请先选择研究项目')
      return api.uploadResearchDataAsset(selectedProject.id, file, assetKind)
    },
    onSuccess: async () => {
      setAssetFile(null)
      await assetsQuery.refetch()
      messageApi.success('数据资产已上传并登记')
    },
    onError: (error: Error) => messageApi.error(error.message),
  })
  const uploadIdlScript = useMutation({
    mutationFn: (file: File) => {
      if (!selectedProject) throw new Error('请先选择研究项目')
      return api.uploadResearchIdlScript(selectedProject.id, file)
    },
    onSuccess: async (asset) => {
      setIdlScriptFile(null)
      await assetsQuery.refetch()
      messageApi.success(`IDL 脚本已登记为项目资产 #${asset.id}，创建实验时在参数 JSON 中引用它`)
    },
    onError: (error: Error) => messageApi.error(error.message),
  })
  const stackAssets = useMutation({
    mutationFn: () => {
      if (!selectedProject) throw new Error('请先选择研究项目')
      if (stackAssetIds.length < 2) throw new Error('请至少选择两个 raster 资产')
      return api.stackResearchRasterAssets(selectedProject.id, {
        name: stackName.trim() || 'aligned_raster_stack',
        asset_ids: stackAssetIds,
        reference_asset_id: stackReferenceAssetId ?? stackAssetIds[0],
        band_names: stackBandNames.split(',').map((value) => value.trim()).filter(Boolean),
        resampling: stackResampling,
      })
    },
    onSuccess: async (asset) => {
      setStackAssetIds([])
      setStackReferenceAssetId(undefined)
      setStackBandNames('')
      await assetsQuery.refetch()
      messageApi.success(`已生成对齐栅格 #${asset.id}，可用于 PythonRunner 多波段公式`)
    },
    onError: (error: Error) => messageApi.error(error.message),
  })
  const handleStackAssetIdsChange = (ids: number[]) => {
    setStackAssetIds(ids)
    if (!ids.length || (stackReferenceAssetId !== undefined && !ids.includes(stackReferenceAssetId))) {
      setStackReferenceAssetId(ids[0])
    }
  }
  const fetchGeeAsset = useMutation({
    mutationFn: (values: ResearchGeeFormValues) => {
      if (!selectedProject) throw new Error('请先选择研究项目')
      return api.fetchResearchGeeAsset(selectedProject.id, {
        dataset_id: values.dataset_id.trim(),
        start_date: values.start_date?.trim() || undefined,
        end_date: values.end_date?.trim() || undefined,
        bbox: parseBoundingBox(values.bbox),
        bands: parseBandList(values.bands),
        scale: values.scale,
        crs: values.crs.trim(),
        composite: values.composite,
        label: values.label?.trim() || undefined,
      })
    },
    onSuccess: async (result) => {
      await assetsQuery.refetch()
      messageApi.success(result.notice)
    },
    onError: (error: Error) => messageApi.error(error.message),
  })
  const createSnapshot = useMutation({
    mutationFn: (values: SnapshotFormValues) => {
      if (!selectedProject) throw new Error('请先选择研究项目')
      return api.createResearchDataSnapshot(selectedProject.id, values)
    },
    onSuccess: async () => {
      snapshotForm.resetFields()
      await snapshotsQuery.refetch()
      messageApi.success('数据快照已冻结')
    },
    onError: (error: Error) => messageApi.error(error.message),
  })
  const createValidationSample = useMutation({
    mutationFn: (values: ValidationSampleFormValues) => {
      if (!selectedProject) throw new Error('请先选择研究项目')
      return api.createResearchValidationSample(selectedProject.id, values)
    },
    onSuccess: async () => {
      validationSampleForm.resetFields()
      await validationSamplesQuery.refetch()
      messageApi.success('验证样本已登记并保留分层与溯源信息')
    },
    onError: (error: Error) => messageApi.error(error.message),
  })
  const importValidationSamples = useMutation({
    mutationFn: (values: ValidationSampleImportFormValues) => {
      if (!selectedProject) throw new Error('请先选择研究项目')
      if (!validationSampleCsvFile) throw new Error('请选择 UTF-8 编码的验证样本 CSV 文件')
      return api.importResearchValidationSamples(
        selectedProject.id,
        validationSampleCsvFile,
        values.data_snapshot_id,
        values.source_asset_id,
      )
    },
    onSuccess: async (result) => {
      validationSampleImportForm.resetFields()
      setValidationSampleCsvFile(null)
      await validationSamplesQuery.refetch()
      messageApi.success(`已原子导入 ${result.imported_count} 条验证样本`)
    },
    onError: (error: Error) => messageApi.error(error.message),
  })
  const addProjectMember = useMutation({
    mutationFn: (values: ProjectMemberFormValues) => {
      if (!selectedProject) throw new Error('请先选择研究项目')
      return api.addResearchProjectMember(selectedProject.id, values.username.trim())
    },
    onSuccess: async () => {
      projectMemberForm.resetFields()
      await projectMembersQuery.refetch()
      messageApi.success('协作成员已加入项目')
    },
    onError: (error: Error) => messageApi.error(error.message),
  })
  const removeProjectMember = useMutation({
    mutationFn: (memberId: number) => {
      if (!selectedProject) throw new Error('请先选择研究项目')
      return api.removeResearchProjectMember(selectedProject.id, memberId)
    },
    onSuccess: async () => {
      await projectMembersQuery.refetch()
      messageApi.success('协作成员已移出项目')
    },
    onError: (error: Error) => messageApi.error(error.message),
  })
  const addResearchRagSource = useMutation({
    mutationFn: (values: ResearchRagSourceFormValues) => {
      if (!selectedProject) throw new Error('请先选择研究项目')
      return api.addResearchRagSource(selectedProject.id, values)
    },
    onSuccess: async () => {
      researchRagSourceForm.resetFields()
      researchRagSourceForm.setFieldsValue({ category: 'method' })
      await researchRagSourcesQuery.refetch()
      messageApi.success('文本知识库已显式绑定到当前项目')
    },
    onError: (error: Error) => messageApi.error(error.message),
  })
  const searchResearchRag = useMutation({
    mutationFn: () => {
      if (!selectedProject) throw new Error('请先选择研究项目')
      return api.searchResearchRag(selectedProject.id, researchRagQuery, researchRagCategory)
    },
    onSuccess: (result) => {
      setResearchRagResult(result)
      messageApi.success(`项目 RAG 返回 ${result.citations.length} 条引用`)
    },
    onError: (error: Error) => messageApi.error(error.message),
  })
  const createEvidence = useMutation({
    mutationFn: (values: EvidenceFormValues) => {
      if (!selectedProject) throw new Error('请先选择研究项目')
      return api.createEvidenceCard(selectedProject.id, values)
    },
    onSuccess: async () => {
      evidenceForm.resetFields()
      evidenceForm.setFieldsValue({ status: 'candidate', source_type: 'paper' })
      await evidenceQuery.refetch()
      messageApi.success('证据卡已保存')
    },
    onError: (error: Error) => messageApi.error(error.message),
  })
  const searchLiterature = useMutation({
    mutationFn: () => {
      if (!selectedProject) throw new Error('请先选择研究项目')
      return api.searchResearchLiterature(selectedProject.id, literatureQuery, 8, literatureProvider)
    },
    onSuccess: (result) => {
      setLiteratureSearchResult(result)
      setLiteratureNetworkResult(null)
      setLiteratureNetworkRequest(null)
      messageApi.success(`已找到 ${result.candidates.length} 条候选文献元数据`)
    },
    onError: (error: Error) => messageApi.error(error.message),
  })
  const importLiteratureCandidate = useMutation({
    mutationFn: (candidate: ResearchLiteratureCandidate) => {
      if (!selectedProject || !literatureSearchResult) throw new Error('请先完成当前项目的文献搜索')
      return api.importResearchLiteratureCandidate(selectedProject.id, literatureSearchResult.audit_id, candidate)
    },
    onSuccess: async () => {
      await evidenceQuery.refetch()
      messageApi.success('已保存为候选 EvidenceCard；请阅读原文后再标记为 verified。')
    },
    onError: (error: Error) => messageApi.error(error.message),
  })
  const importLiteratureToRag = useMutation({
    mutationFn: (candidate: ResearchLiteratureCandidate) => {
      if (!selectedProject || !literatureSearchResult || !literatureRagKnowledgeBaseId) {
        throw new Error('请先绑定并选择一个 method 知识库')
      }
      return api.importResearchLiteratureToRag(selectedProject.id, {
        audit_id: literatureSearchResult.audit_id,
        candidate,
        knowledge_base_id: literatureRagKnowledgeBaseId,
        category: 'method',
      })
    },
    onSuccess: async (result) => {
      await researchRagSourcesQuery.refetch()
      messageApi.success(result.notice)
    },
    onError: (error: Error) => messageApi.error(error.message),
  })
  const expandLiteratureNetwork = useMutation({
    mutationFn: ({ candidate, relation, offset = 0 }: { candidate: ResearchLiteratureCandidate; relation: 'citations' | 'references'; offset?: number }) => {
      if (!selectedProject || !literatureSearchResult) throw new Error('请先完成当前项目的文献搜索')
      return api.expandResearchLiteratureNetwork(selectedProject.id, {
        audit_id: literatureSearchResult.audit_id,
        candidate,
        relation,
        limit: 10,
        offset,
      })
    },
    onSuccess: (result, variables) => {
      setLiteratureNetworkResult(result)
      setLiteratureNetworkRequest({ candidate: variables.candidate, relation: variables.relation })
      messageApi.success(`已获取 ${result.candidates.length} 条${result.relation === 'references' ? '参考文献' : '被引用'}候选`)
    },
    onError: (error: Error) => messageApi.error(error.message),
  })
  const searchStac = useMutation({
    mutationFn: () => {
      if (!selectedProject) throw new Error('请先选择研究项目')
      return api.searchResearchStac(selectedProject.id, {
        provider: stacProvider,
        collections: parseBandList(stacCollections),
        bbox: parseBoundingBox(stacBbox),
        datetime_start: stacStart || undefined,
        datetime_end: stacEnd || undefined,
        cloud_cover_max: stacCloudMax,
        limit: 10,
      })
    },
    onSuccess: (result) => {
      setStacSearchResult(result)
      messageApi.success(`已找到 ${result.candidates.length} 个公开 STAC 候选`) 
    },
    onError: (error: Error) => messageApi.error(error.message),
  })
  const importStacReference = useMutation({
    mutationFn: ({ candidate, assetKey }: { candidate: ResearchStacCandidate; assetKey: string }) => {
      if (!selectedProject || !stacSearchResult) throw new Error('请先完成当前项目的 STAC 搜索')
      return api.importResearchStacReference(selectedProject.id, stacSearchResult.audit_id, candidate, assetKey)
    },
    onSuccess: async () => {
      await assetsQuery.refetch()
      messageApi.success('已登记远端 reference；下载为私有 research://assets/ 后才能运行')
    },
    onError: (error: Error) => messageApi.error(error.message),
  })
  const downloadStacAsset = useMutation({
    mutationFn: ({ candidate, assetKey }: { candidate: ResearchStacCandidate; assetKey: string }) => {
      if (!selectedProject || !stacSearchResult) throw new Error('请先完成当前项目的 STAC 搜索')
      return api.downloadResearchStacAsset(selectedProject.id, stacSearchResult.audit_id, candidate, assetKey)
    },
    onSuccess: async () => {
      await assetsQuery.refetch()
      messageApi.success('已下载、校验并保存为私有 GeoTIFF；现在可以创建 DataSnapshot')
    },
    onError: (error: Error) => messageApi.error(error.message),
  })
  const createFormula = useMutation({
    mutationFn: (values: FormulaFormValues) => {
      if (!selectedProject) throw new Error('请先选择研究项目')
      return api.createFormulaSpec(selectedProject.id, {
        name: values.name,
        version: values.version,
        status: values.status,
        evidence_card_ids: values.evidence_card_ids ?? [],
        spec: parseObjectJson(values.spec_json, 'FormulaSpec'),
      })
    },
    onSuccess: async () => {
      formulaForm.resetFields()
      formulaForm.setFieldsValue({ version: 1, status: 'draft', spec_json: DEFAULT_FORMULA_SPEC, evidence_card_ids: [] })
      await formulasQuery.refetch()
      messageApi.success('公式规格已保存')
    },
    onError: (error: Error) => messageApi.error(error.message),
  })
  const createExperiment = useMutation({
    mutationFn: (values: ExperimentFormValues) => {
      if (!selectedProject) throw new Error('请先选择研究项目')
      return api.createResearchExperiment(selectedProject.id, {
        name: values.name,
        formula_spec_id: values.formula_spec_id,
        data_snapshot_id: values.data_snapshot_id,
        runner_type: values.runner_type,
        execution_mode: values.execution_mode,
        parameters: parseObjectJson(values.parameters_json, '参数'),
        validation_plan: parseObjectJson(values.validation_plan_json, '验证方案'),
        visualization_contract: values.visualization_contract ?? [],
      })
    },
    onSuccess: async (experiment) => {
      experimentForm.resetFields()
      experimentForm.setFieldsValue({
        runner_type: 'python',
        execution_mode: 'preview',
        parameters_json: '{}',
        validation_plan_json: DEFAULT_VALIDATION_PLAN,
        visualization_contract: ['input', 'index', 'water_mask'],
      })
      setSelectedExperimentId(experiment.id)
      await experimentsQuery.refetch()
      messageApi.success('实验计划已创建')
    },
    onError: (error: Error) => messageApi.error(error.message),
  })
  const startRun = useMutation({
    mutationFn: (mode: 'sync' | 'queue') => {
      if (!selectedProjectId || !selectedExperimentId) throw new Error('请先选择实验')
      return api.startResearchRun(selectedProjectId, selectedExperimentId, mode)
    },
    onSuccess: async (run) => {
      await Promise.all([runsQuery.refetch(), experimentsQuery.refetch()])
      messageApi.success(run.status === 'queued' ? '实验已加入后台队列；页面会自动刷新运行状态' : '实验运行已完成，产物已保存')
    },
    onError: (error: Error) => messageApi.error(error.message),
  })
  const startSweep = useMutation({
    mutationFn: (mode: 'sync' | 'queue') => {
      if (!selectedProjectId || !selectedExperimentId) throw new Error('请先选择 preview 实验')
      let candidates: unknown
      try {
        candidates = JSON.parse(sweepCandidatesJson)
      } catch {
        throw new Error('候选参数必须是有效的 JSON 数组。')
      }
      if (!Array.isArray(candidates)) throw new Error('候选参数必须是 JSON 数组。')
      return api.startResearchParameterSweep(selectedProjectId, selectedExperimentId, {
        candidates: candidates as Array<{ name: string; parameters: Record<string, unknown> }>,
        evaluation_split: sweepEvaluationSplit,
        ranking_metric: sweepRankingMetric,
      }, mode)
    },
    onSuccess: async (run) => {
      await Promise.all([runsQuery.refetch(), experimentsQuery.refetch()])
      messageApi.success(
        run.status === 'queued'
          ? '候选参数实验已加入后台队列；页面会自动刷新运行状态'
          : run.status === 'completed'
            ? '候选参数实验已完成，排名和图件已保存'
            : '候选参数实验已保存，但有候选失败；请查看运行清单',
      )
    },
    onError: (error: Error) => messageApi.error(error.message),
  })
  const cancelRun = useMutation({
    mutationFn: () => {
      const run = runsQuery.data?.[0]
      if (!selectedProjectId || !selectedExperimentId || !run) throw new Error('请先选择运行记录')
      return api.cancelResearchRun(selectedProjectId, selectedExperimentId, run.id)
    },
    onSuccess: async (run) => {
      await Promise.all([runsQuery.refetch(), experimentsQuery.refetch()])
      messageApi.success(run.status === 'running' ? '已提交取消请求，当前步骤结束后会停止' : '排队运行已取消')
    },
    onError: (error: Error) => messageApi.error(error.message),
  })
  const retryRun = useMutation({
    mutationFn: () => {
      const run = runsQuery.data?.[0]
      if (!selectedProjectId || !selectedExperimentId || !run) throw new Error('请先选择运行记录')
      return api.retryResearchRun(selectedProjectId, selectedExperimentId, run.id, 'queue')
    },
    onSuccess: async () => {
      await Promise.all([runsQuery.refetch(), experimentsQuery.refetch()])
      messageApi.success('已创建独立重试运行，并加入后台队列')
    },
    onError: (error: Error) => messageApi.error(error.message),
  })
  const compareReproducibility = useMutation({
    mutationFn: () => {
      const targetRun = runsQuery.data?.[0]
      if (!selectedProjectId || !selectedExperimentId || !targetRun || !reproducibilityReferenceRunId) {
        throw new Error('请选择目标运行和一个已完成的基准运行')
      }
      return api.compareResearchRun(selectedProjectId, selectedExperimentId, targetRun.id, {
        reference_run_id: reproducibilityReferenceRunId,
        absolute_tolerance: 1e-6,
        relative_tolerance: 1e-6,
      })
    },
    onSuccess: (result) => {
      if (result.matched) messageApi.success('两次正式运行在给定容差内一致')
      else messageApi.warning('重跑一致性校验未通过，请查看差异明细')
    },
    onError: (error: Error) => messageApi.error(error.message),
  })
  const compareFormalRuns = useMutation({
    mutationFn: () => {
      const targetRun = runsQuery.data?.[0]
      if (!selectedProjectId || !selectedExperimentId || !targetRun || !reproducibilityReferenceRunId) {
        throw new Error('请选择目标运行和一个已完成的基线运行')
      }
      return api.compareFormalResearchRuns(selectedProjectId, selectedExperimentId, targetRun.id, reproducibilityReferenceRunId)
    },
    onSuccess: (result) => {
      if (result.comparable) messageApi.success('已生成基线与候选的描述性指标差异')
      else messageApi.warning('基线与候选不可直接比较，请查看差异原因')
    },
    onError: (error: Error) => messageApi.error(error.message),
  })

  const assets = assetsQuery.data ?? []
  const snapshots = snapshotsQuery.data ?? []
  const validationSamples = validationSamplesQuery.data ?? []
  const evidenceCards = evidenceQuery.data ?? []
  const formulas = formulasQuery.data ?? []
  const experiments = experimentsQuery.data ?? []
  const selectedExperiment = experiments.find((experiment) => experiment.id === selectedExperimentId)
  const runs = runsQuery.data ?? []
  const visibleRun = runs[0]
  const completedReferenceRuns = runs.filter((run) => run.status === 'completed' && run.id !== visibleRun?.id)
  const validationMetrics = visibleRun ? validationMetricsFromRun(visibleRun) : null
  const validationIntervals = visibleRun ? validationIntervalsFromRun(visibleRun) : null
  const validationWeighting = visibleRun ? validationWeightingFromRun(visibleRun) : null
  const validationAreaAdjustment = visibleRun ? validationAreaAdjustmentFromRun(visibleRun) : null
  const parameterSweep = visibleRun ? parameterSweepFromRun(visibleRun) : null
  const projectMembers = projectMembersQuery.data ?? []
  const researchRagSources = researchRagSourcesQuery.data ?? []
  const ownKnowledgeBases = knowledgeBasesQuery.data ?? []
  const isProjectOwner = selectedProject?.owner_user_id === currentUserId

  useEffect(() => {
    if (!completedReferenceRuns.length) {
      setReproducibilityReferenceRunId(undefined)
      return
    }
    if (!reproducibilityReferenceRunId || !completedReferenceRuns.some((run) => run.id === reproducibilityReferenceRunId)) {
      setReproducibilityReferenceRunId(completedReferenceRuns[0].id)
    }
  }, [completedReferenceRuns, reproducibilityReferenceRunId])

  useEffect(() => {
    if (!selectedExperimentId || !runs.some((run) => run.status === 'queued' || run.status === 'running')) return
    const timer = window.setInterval(() => void runsQuery.refetch(), 1500)
    return () => window.clearInterval(timer)
  }, [runs, runsQuery.refetch, selectedExperimentId])

  return (
    <div className="page-stack research-page">
      {contextHolder}
      {projectsQuery.isError ? (
        <Alert
          type="error"
          showIcon
          message="研究项目暂时无法加载"
          description={projectsQuery.error instanceof Error ? projectsQuery.error.message : '请检查后端连接后重试。'}
          action={<Button size="small" onClick={() => void projectsQuery.refetch()} loading={projectsQuery.isFetching}>重新加载</Button>}
        />
      ) : null}
      <Card className="section-card" title="新建研究项目">
        <Form
          form={projectForm}
          layout="vertical"
          initialValues={{ entry_mode: 'open' }}
          onFinish={(values) => {
            if (!values.entry_mode) {
              messageApi.error('请选择入口模式')
              return
            }
            createProject.mutate({ ...values, protocol: {} })
          }}
        >
          <div className="grid-two">
            <Form.Item label="项目名称" name="name" rules={[{ required: true, message: '请输入项目名称' }]}>
              <Input placeholder="例如：鄱阳湖多源水体制图" />
            </Form.Item>
            <Form.Item label="入口模式" name="entry_mode">
              <Select options={[{ value: 'open', label: '开放研究' }, { value: 'template', label: '研究模板' }]} />
            </Form.Item>
          </div>
          <Form.Item label="说明" name="description">
            <Input placeholder="可选：研究目标、课程或课题说明" />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={createProject.isPending}>
            创建项目
          </Button>
        </Form>
      </Card>

      <Card className="section-card" title="研究项目">
        <Table<ResearchProject>
          rowKey="id"
          size="middle"
          loading={projectsQuery.isLoading}
          dataSource={projectsQuery.data ?? []}
          pagination={false}
          scroll={{ x: 720 }}
          locale={{ emptyText: <DisplayEmpty compact illustration="map" title="还没有研究项目" description="从开放研究或研究模板开始创建。" /> }}
          rowSelection={{
            type: 'radio',
            columnTitle: '选择',
            // Ant Design's CheckboxProps type omits native aria attributes, but
            // Table forwards this object to the actual input element at runtime.
            getCheckboxProps: (record) => ({ 'aria-label': `选择项目 ${record.name}` } as never),
            selectedRowKeys: selectedProjectId ? [selectedProjectId] : [],
            onChange: (keys) => setSelectedProjectId(Number(keys[0]) || undefined),
          }}
          columns={[
            { title: '名称', dataIndex: 'name', width: 240 },
            { title: '入口', dataIndex: 'entry_mode', width: 120, render: (value) => (value === 'open' ? '开放研究' : '研究模板') },
            { title: '状态', dataIndex: 'status', width: 120, render: (value) => <Tag color={statusColor(value)}>{value}</Tag> },
            { title: '数据策略', dataIndex: 'egress_policy', width: 150, render: (value) => <FileTypeBadge label={value} /> },
            { title: '更新于', dataIndex: 'updated_at', width: 180, render: formatDate },
          ]}
        />
      </Card>

      {selectedProject ? (
        <Card
          className="section-card"
          title={selectedProject.name}
          extra={(
            <Space wrap>
              <Tag>{selectedProject.entry_mode === 'open' ? '开放研究' : '研究模板'}</Tag>
              <FileTypeBadge label="private-local" />
              {onOpenAgent ? (
                <Button size="small" icon={<MessageOutlined />} onClick={() => onOpenAgent(selectedProject.id)}>
                  在 Agent 中打开
                </Button>
              ) : null}
            </Space>
          )}
        >
          <Descriptions
            size="small"
            column={{ xs: 1, sm: 2, lg: 4 }}
            items={[
              { key: 'owner', label: '所有者', children: `用户 #${selectedProject.owner_user_id}` },
              { key: 'assets', label: '数据资产', children: assets.length },
              { key: 'snapshots', label: '冻结快照', children: snapshots.length },
              { key: 'experiments', label: '实验计划', children: experiments.length },
            ]}
          />
        </Card>
      ) : null}

      {selectedProject ? (
        <Tabs
          className="research-tabs"
          items={[
            {
              key: 'collaboration',
              label: (
                <span className="research-tab-label">
                  <span className="research-tab-label-full">协作成员{projectMembers.length ? ` · ${projectMembers.length}` : ''}</span>
                  <span className="research-tab-label-short">协作{projectMembers.length ? ` · ${projectMembers.length}` : ''}</span>
                </span>
              ),
              children: (
                <Card className="section-card" title="低摩擦项目协作">
                  <Alert
                    type="info"
                    showIcon
                    message="成员没有教师/学生角色分级：加入后可共同处理项目的资料、证据、公式、实验和运行结果。"
                    description="项目仍保持 private-local。只有创建项目的所有者可以按用户名增加或移出成员，这是为了避免内部资料被默认扩散，而不是给日常工作增加审批流程。"
                  />
                  {isProjectOwner ? (
                    <Form
                      form={projectMemberForm}
                      layout="inline"
                      className="research-member-form"
                      onFinish={(values) => addProjectMember.mutate(values)}
                    >
                      <Form.Item name="username" rules={[{ required: true, message: '请输入已注册用户名' }]}>
                        <Input aria-label="协作成员用户名" placeholder="输入已注册用户名" />
                      </Form.Item>
                      <Button type="primary" htmlType="submit" loading={addProjectMember.isPending}>添加成员</Button>
                    </Form>
                  ) : (
                    <Alert
                      className="research-member-notice"
                      type="success"
                      showIcon
                      message="你已加入此共享项目，可直接共同完成研究工作流。"
                    />
                  )}
                  <Table<ResearchProjectMember>
                    className="research-inline-table"
                    rowKey="id"
                    size="small"
                    loading={projectMembersQuery.isLoading}
                    dataSource={projectMembers}
                    pagination={false}
                    locale={{ emptyText: '尚未邀请协作成员。' }}
                    columns={[
                      { title: '用户名', dataIndex: 'username' },
                      { title: '加入时间', dataIndex: 'created_at', width: 190, render: formatDate },
                      ...(isProjectOwner ? [{
                        title: '操作',
                        key: 'action',
                        width: 110,
                        render: (_value: unknown, member: ResearchProjectMember) => (
                          <Button danger size="small" loading={removeProjectMember.isPending} onClick={() => removeProjectMember.mutate(member.id)}>
                            移出
                          </Button>
                        ),
                      }] : []),
                    ]}
                  />
                </Card>
              ),
            },
            {
              key: 'rag',
              label: <span className="research-tab-label"><span className="research-tab-label-full">项目 RAG</span><span className="research-tab-label-short">RAG</span></span>,
              children: (
                <div className="research-tab-stack">
                  <Card className="section-card" title="RAG 分层边界">
                    <Alert
                      type="info"
                      showIcon
                      message="论文/方法、IDL 代码和 Python 代码可以作为项目文本 RAG；遥感数据仅作为 Data Catalog。"
                      description="只有成员主动绑定的文本知识库会被当前项目检索。原始 GeoTIFF、矢量、样本表和像元不会被嵌入或发送给 RAG；请在“数据与快照”中查看和管理这些私有数据资产。"
                    />
                  </Card>
                  <Card className="section-card" title="绑定我的文本知识库">
                    <Form
                      form={researchRagSourceForm}
                      layout="inline"
                      initialValues={{ category: 'method' }}
                      className="research-member-form"
                      onFinish={(values) => addResearchRagSource.mutate(values)}
                    >
                      <Form.Item name="knowledge_base_id" rules={[requiredValueRule('请选择自己的知识库')]}>
                        <Select
                          aria-label="要绑定的知识库"
                          placeholder="选择自己拥有的知识库"
                          style={{ minWidth: 250 }}
                          options={ownKnowledgeBases.map((knowledgeBase) => ({
                            value: knowledgeBase.id,
                            label: `${knowledgeBase.name} · ${knowledgeBase.document_count} 份文档`,
                          }))}
                        />
                      </Form.Item>
                      <Form.Item name="category" rules={[requiredValueRule('请选择 RAG 分类')]}>
                        <Select
                          aria-label="RAG 分类"
                          style={{ minWidth: 150 }}
                          options={[
                            { value: 'method', label: '论文 / 方法' },
                            { value: 'idl_code', label: 'IDL 代码' },
                            { value: 'python_code', label: 'Python 代码' },
                          ]}
                        />
                      </Form.Item>
                      <Button type="primary" htmlType="submit" loading={addResearchRagSource.isPending}>绑定到项目</Button>
                    </Form>
                    <Table<ResearchKnowledgeSource>
                      className="research-inline-table"
                      rowKey="id"
                      size="small"
                      dataSource={researchRagSources}
                      loading={researchRagSourcesQuery.isLoading}
                      pagination={false}
                      scroll={{ x: 720 }}
                      locale={{ emptyText: '尚未绑定项目文本 RAG。' }}
                      columns={[
                        { title: '分类', dataIndex: 'category', width: 130 },
                        { title: '知识库', dataIndex: 'knowledge_base_name' },
                        { title: '文档数', dataIndex: 'document_count', width: 100 },
                        { title: '绑定时间', dataIndex: 'created_at', width: 190, render: formatDate },
                      ]}
                    />
                  </Card>
                  <Card className="section-card" title="检索已绑定的项目资料">
                    <Space wrap className="research-literature-search">
                      <Select
                        aria-label="检索分类"
                        value={researchRagCategory}
                        onChange={setResearchRagCategory}
                        options={[
                          { value: 'all', label: '全部文本 RAG' },
                          { value: 'method', label: '论文 / 方法' },
                          { value: 'idl_code', label: 'IDL 代码' },
                          { value: 'python_code', label: 'Python 代码' },
                        ]}
                        style={{ minWidth: 160 }}
                      />
                      <Input.Search
                        value={researchRagQuery}
                        onChange={(event) => setResearchRagQuery(event.target.value)}
                        onSearch={() => searchResearchRag.mutate()}
                        loading={searchResearchRag.isPending}
                        placeholder="例如：MNDWI 阈值，或 ENVI 栅格读取"
                        enterButton="检索项目 RAG"
                      />
                    </Space>
                    {researchRagResult ? (
                      <>
                        <Alert className="research-member-notice" type="success" showIcon message={researchRagResult.notice} />
                        <Table
                          className="research-inline-table"
                          rowKey="chunk_id"
                          size="small"
                          dataSource={researchRagResult.citations}
                          pagination={false}
                          scroll={{ x: 860 }}
                          locale={{ emptyText: '没有检索到可引用的片段。' }}
                          columns={[
                            { title: '来源', dataIndex: 'file_name', width: 210 },
                            { title: '知识库', dataIndex: 'knowledge_base_name', width: 180 },
                            { title: '片段', dataIndex: 'excerpt', render: (value) => <span className="research-citation-excerpt">{value}</span> },
                            { title: '分数', dataIndex: 'score', width: 90, render: (value) => typeof value === 'number' ? value.toFixed(3) : '-' },
                          ]}
                        />
                      </>
                    ) : null}
                  </Card>
                </div>
              ),
            },
            {
              key: 'protocol',
              label: <span className="research-tab-label"><span className="research-tab-label-full">研究协议</span><span className="research-tab-label-short">协议</span></span>,
              // The protocol form is populated as soon as a project is selected.
              // Keep this tab mounted so the form instance is connected before
              // that effect runs (and avoid a misleading React console warning).
              forceRender: true,
              children: (
                <Card className="section-card" title="研究问题与协议">
                  <Alert
                    type="info"
                    showIcon
                    message="开放研究：先写研究问题，再生成可编辑协议草案"
                    description="此步骤只在本地把你的问题整理为研究区、数据、方法、验证、图件和结论边界的结构化草案；不会发送项目资料到 RAG、外部检索或模型服务。生成后请人工补全并点击“保存协议”。"
                  />
                  <Input.TextArea
                    aria-label="研究问题"
                    value={protocolDraftQuestion}
                    onChange={(event) => setProtocolDraftQuestion(event.target.value)}
                    rows={3}
                    placeholder="例如：比较 Sentinel-1、Sentinel-2 及可解释融合方法在 2018–2025 年鄱阳湖丰水、枯水和云遮挡情景下的水体制图稳定性。"
                  />
                  {protocolDraftQuestion.trim().length < 8 ? <p className="research-form-hint research-protocol-hint">输入至少 8 个字符后才能生成协议草案。</p> : null}
                  <Button
                    className="research-protocol-draft-button"
                    onClick={() => draftProtocol.mutate()}
                    disabled={protocolDraftQuestion.trim().length < 8}
                    loading={draftProtocol.isPending}
                  >
                    生成本地协议草案
                  </Button>
                  <Button
                    className="research-protocol-draft-button"
                    onClick={() => draftProtocolWithEvidenceMap.mutate()}
                    disabled={protocolDraftQuestion.trim().length < 8}
                    loading={draftProtocolWithEvidenceMap.isPending}
                  >
                    使用项目 RAG 生成带引用草案
                  </Button>
                  <Button
                    className="research-protocol-draft-button"
                    onClick={() => checkProtocolReadiness.mutate()}
                    loading={checkProtocolReadiness.isPending}
                  >
                    检查已保存协议就绪度
                  </Button>
                  {protocolReadiness ? (
                    <Alert
                      className="research-member-notice"
                      type={protocolReadiness.ready ? 'success' : 'warning'}
                      showIcon
                      message={protocolReadiness.ready ? '协议已通过研究设计就绪检查' : '协议尚未满足研究设计就绪条件'}
                      description={
                        protocolReadiness.ready ? protocolReadiness.notice : (
                          <ul className="research-readiness-list">
                            <li>{protocolReadiness.notice}</li>
                            {protocolReadiness.missing.map((item) => <li key={item.code}><code>{item.path}</code>：{item.message}</li>)}
                          </ul>
                        )
                      }
                    />
                  ) : null}
                  {protocolEvidenceMapDraft ? (
                    <>
                      <Alert
                        className="research-member-notice"
                        type="success"
                        showIcon
                        message={`已附入 ${protocolEvidenceMapDraft.evidence_map.length} 条待人工核验的项目 RAG 引用`}
                        description="这些片段只作为方法计划中的可追溯线索：尚未保存协议、尚未创建 EvidenceCard，更不等同于方法或结论已被核验。"
                      />
                      <Table
                        className="research-inline-table"
                        rowKey={(citation) => `${citation.citation.knowledge_base_id}-${citation.citation.chunk_id}`}
                        size="small"
                        dataSource={protocolEvidenceMapDraft.evidence_map}
                        pagination={false}
                        locale={{ emptyText: '当前已绑定 RAG 没有返回可引用片段。' }}
                        columns={[
                          { title: '来源', dataIndex: ['citation', 'file_name'], width: 190 },
                          { title: '知识库', dataIndex: ['citation', 'knowledge_base_name'], width: 170 },
                          { title: '片段', dataIndex: ['citation', 'excerpt'], render: (value) => <span className="research-citation-excerpt">{value}</span> },
                          { title: '状态', dataIndex: 'review_status', width: 110, render: (value) => <Tag color="gold">{value}</Tag> },
                        ]}
                      />
                    </>
                  ) : null}
                  <Form form={protocolForm} layout="vertical" onFinish={(values) => updateProject.mutate(values)}>
                    <Form.Item label="项目说明" name="description">
                      <Input.TextArea rows={3} />
                    </Form.Item>
                    <Form.Item label="研究协议 JSON" name="protocol_json" rules={[{ required: true, message: '请输入研究协议' }]}>
                      <Input.TextArea className="research-json-input" rows={12} spellCheck={false} />
                    </Form.Item>
                    <Button type="primary" htmlType="submit" loading={updateProject.isPending}>保存协议</Button>
                  </Form>
                  <Table<ResearchProtocolRevision>
                    className="research-inline-table"
                    rowKey="id"
                    size="small"
                    loading={protocolRevisionsQuery.isLoading}
                    dataSource={protocolRevisionsQuery.data ?? []}
                    pagination={false}
                    locale={{ emptyText: '尚未保存可追溯的协议版本。' }}
                    columns={[
                      { title: '版本', dataIndex: 'version', width: 80, render: (value) => `v${value}` },
                      { title: '协议指纹', dataIndex: 'protocol_hash', render: (value: string) => <span title={value}>{value.slice(0, 16)}…</span> },
                      { title: '保存者', dataIndex: 'saved_by_user_id', width: 100, render: (value) => `#${value}` },
                      { title: '保存时间', dataIndex: 'created_at', width: 180, render: formatDate },
                    ]}
                  />
                </Card>
              ),
            },
            {
              key: 'data',
              label: <span className="research-tab-label"><span className="research-tab-label-full">数据与快照</span><span className="research-tab-label-short">数据</span></span>,
              children: (
                <div className="research-tab-stack">
                  <Card className="section-card" title="上传私有研究数据">
                    <Alert
                      type="info"
                      showIcon
                      message="上传文件保存在当前项目的私有研究资产仓，默认不会发送给外部检索或模型服务。"
                    />
                    <div className="research-upload-row">
                      <input
                        type="file"
                        aria-label="私有研究数据文件"
                        accept=".tif,.tiff,.geojson,.json,.csv"
                        onChange={(event) => setAssetFile(event.target.files?.[0] ?? null)}
                      />
                      <Select
                        aria-label="资料类型"
                        options={[
                          { value: 'raster', label: '栅格影像' },
                          { value: 'vector', label: '矢量数据' },
                          { value: 'roi', label: '研究区 ROI' },
                          { value: 'table', label: '样本表' },
                          { value: 'reference', label: '参考数据' },
                          { value: 'derived', label: '派生结果（例如本地 IDL 输出）' },
                        ]}
                        onChange={setAssetKind}
                        value={assetKind}
                      />
                      <Button
                        onClick={() => {
                          if (!assetFile) {
                            messageApi.error('请选择 GeoTIFF、GeoJSON、JSON 或 CSV 文件。')
                            return
                          }
                          uploadAsset.mutate({ file: assetFile, assetKind })
                        }}
                        loading={uploadAsset.isPending}
                      >
                        上传私有资料
                      </Button>
                    </div>
                    <Divider />
                    <Alert
                      type="warning"
                      showIcon
                      message="项目级 IDLRunner（可选）"
                      description="仅上传受许可的 .pro 源文件。它不会进入数据快照；创建 IDL 实验时必须在参数 JSON 中显式填写 idl_script_asset_id。没有本机 IDL/ENVI 许可时运行会明确显示 unavailable，不影响 PythonRunner。"
                    />
                    <div className="research-upload-row" style={{ marginTop: 12 }}>
                      <input
                        type="file"
                        aria-label="项目 IDL 脚本文件"
                        accept=".pro"
                        onChange={(event) => setIdlScriptFile(event.target.files?.[0] ?? null)}
                      />
                      <Button
                        onClick={() => {
                          if (!idlScriptFile) {
                            messageApi.error('请选择 .pro IDL 脚本')
                            return
                          }
                          uploadIdlScript.mutate(idlScriptFile)
                        }}
                        loading={uploadIdlScript.isPending}
                      >
                        上传项目 IDL 脚本
                      </Button>
                    </div>
                  </Card>
                  <Card className="section-card" title="从 Google Earth Engine 受控获取">
                    <Alert
                      type="info"
                      showIcon
                      message="仅在后端已启用 GEE 且数据集位于白名单时可用"
                      description="该操作会把所填的公开数据集、时间、范围、波段、尺度和合成条件发送到 GEE；结果下载后立刻保存到当前项目的私有资产仓，不会发送已有私有影像或样本。"
                    />
                    <Form
                      form={geeForm}
                      layout="vertical"
                      initialValues={{
                        dataset_id: 'COPERNICUS/S2_SR_HARMONIZED',
                        bbox: '115.7,28.8,115.8,28.9',
                        bands: 'B3,B11',
                        scale: 10,
                        crs: 'EPSG:4326',
                        composite: 'median',
                        label: 'gee_research_asset',
                      }}
                      onFinish={(values) => fetchGeeAsset.mutate(values)}
                    >
                      <div className="grid-two">
                        <Form.Item label="数据集 ID" name="dataset_id" rules={[{ required: true }]}><Input /></Form.Item>
                        <Form.Item label="输出名称" name="label"><Input /></Form.Item>
                      </div>
                      <div className="grid-two">
                        <Form.Item label="开始日期（YYYY-MM-DD）" name="start_date"><Input /></Form.Item>
                        <Form.Item label="结束日期（YYYY-MM-DD）" name="end_date"><Input /></Form.Item>
                      </div>
                      <div className="grid-two">
                        <Form.Item label="范围 minLon,minLat,maxLon,maxLat" name="bbox" rules={[{ required: true }]}><Input /></Form.Item>
                        <Form.Item label="波段（英文逗号分隔）" name="bands"><Input /></Form.Item>
                      </div>
                      <div className="grid-two">
                        <Form.Item label="尺度（米）" name="scale" rules={[{ required: true }]}><InputNumber min={1} max={10000} style={{ width: '100%' }} /></Form.Item>
                        <Form.Item label={requiredLabel('合成方式')} name="composite" rules={[requiredValueRule('请选择合成方式')]}>
                          <Select options={['median', 'mean', 'first'].map((value) => ({ value, label: value }))} />
                        </Form.Item>
                      </div>
                      <Form.Item label="CRS" name="crs" rules={[{ required: true }]}><Input /></Form.Item>
                      <Button type="primary" htmlType="submit" loading={fetchGeeAsset.isPending}>获取并登记 GEE 资产</Button>
                    </Form>
                  </Card>
                  <Card className="section-card" title="公开 STAC 候选（仅元数据与远端引用）">
                    <Alert
                      type="info"
                      showIcon
                      message="不会上传当前项目的影像、ROI、样本或运行产物"
                      description="查询只发送集合、范围、日期、云量和数量到白名单公共 STAC 端点。导入前请核对许可与场景；导入只登记远端 reference，不下载像元，也不能直接交给 PythonRunner。"
                    />
                    <Space wrap className="research-upload-row">
                      <Select
                        aria-label="STAC 提供方"
                        value={stacProvider}
                        onChange={setStacProvider}
                        options={[{ value: 'planetary_computer', label: 'Microsoft Planetary Computer' }, { value: 'earth_search', label: 'Earth Search' }]}
                        style={{ width: 220 }}
                      />
                      <Input value={stacCollections} onChange={(event) => setStacCollections(event.target.value)} placeholder="collection（逗号分隔）" style={{ width: 220 }} />
                      <Input value={stacBbox} onChange={(event) => setStacBbox(event.target.value)} placeholder="minLon,minLat,maxLon,maxLat" style={{ width: 220 }} />
                      <Input value={stacStart} onChange={(event) => setStacStart(event.target.value)} placeholder="开始日期" style={{ width: 130 }} />
                      <Input value={stacEnd} onChange={(event) => setStacEnd(event.target.value)} placeholder="结束日期" style={{ width: 130 }} />
                      <InputNumber
                        min={0}
                        max={100}
                        value={stacCloudMax}
                        onChange={(value) => setStacCloudMax(value ?? 20)}
                        suffix="%云量"
                        aria-label="最大云量"
                      />
                      <Button type="primary" onClick={() => searchStac.mutate()} loading={searchStac.isPending}>搜索公开场景</Button>
                    </Space>
                    {stacSearchResult ? (
                      <Table<ResearchStacCandidate>
                        className="research-inline-table"
                        rowKey={(candidate) => candidate.external_id}
                        size="small"
                        dataSource={stacSearchResult.candidates}
                        pagination={false}
                        scroll={{ x: 760 }}
                        columns={[
                          { title: '场景', dataIndex: 'external_id', width: 220 },
                          { title: '集合', dataIndex: 'collection', width: 150 },
                          { title: '时间', dataIndex: 'datetime', width: 180, render: formatDate },
                          { title: '云量', dataIndex: 'cloud_cover', width: 90, render: (value) => value == null ? '-' : `${value.toFixed(2)}%` },
                          {
                            title: '资产引用',
                            render: (_, candidate) => Object.entries(candidate.assets).map(([assetKey]) => (
                              <Space key={assetKey} size={4}>
                                <Button size="small" onClick={() => importStacReference.mutate({ candidate, assetKey })} loading={importStacReference.isPending}>
                                  登记引用 {assetKey}
                                </Button>
                                <Button size="small" type="primary" onClick={() => downloadStacAsset.mutate({ candidate, assetKey })} loading={downloadStacAsset.isPending}>
                                  下载私有 GeoTIFF
                                </Button>
                              </Space>
                            )),
                          },
                        ]}
                      />
                    ) : null}
                  </Card>
                  <Card className="section-card" title="数据资产">
                    <Alert
                      type="info"
                      showIcon
                      message="多波段栅格对齐"
                      description="选择同一项目内已落盘的 raster 资产，系统会以第一个资产为参考网格，重投影/重采样后生成新的私有多波段 GeoTIFF。远端 reference、跨项目资产和非栅格资产不会被直接运行。"
                      style={{ marginBottom: 16 }}
                    />
                    <Space wrap className="research-upload-row" style={{ marginBottom: 16 }}>
                      <Input value={stackName} onChange={(event) => setStackName(event.target.value)} placeholder="输出名称" style={{ width: 220 }} />
                      <Select
                        mode="multiple"
                        aria-label="待对齐的栅格资产"
                        value={stackAssetIds}
                        onChange={handleStackAssetIdsChange}
                        options={assets.filter((asset) => asset.asset_kind === 'raster').map((asset) => ({ value: asset.id, label: `#${asset.id} ${asset.name}` }))}
                        placeholder="选择至少两个 raster"
                        style={{ minWidth: 300 }}
                        maxTagCount="responsive"
                      />
                      <Select
                        allowClear
                        aria-label="参考网格资产"
                        value={stackReferenceAssetId}
                        onChange={setStackReferenceAssetId}
                        options={assets.filter((asset) => stackAssetIds.includes(asset.id)).map((asset) => ({ value: asset.id, label: `参考网格：#${asset.id} ${asset.name}` }))}
                        placeholder="参考网格"
                        style={{ minWidth: 180 }}
                      />
                      <Input value={stackBandNames} onChange={(event) => setStackBandNames(event.target.value)} placeholder="波段名（逗号分隔，可选）" style={{ width: 230 }} />
                      <Select
                        aria-label="重采样方法"
                        value={stackResampling}
                        onChange={setStackResampling}
                        options={[{ value: 'bilinear', label: '双线性' }, { value: 'nearest', label: '最近邻' }, { value: 'cubic', label: '三次卷积' }]}
                        style={{ width: 120 }}
                      />
                      <Button type="primary" onClick={() => stackAssets.mutate()} loading={stackAssets.isPending} disabled={stackAssetIds.length < 2}>
                        生成对齐多波段栅格
                      </Button>
                    </Space>
                    <Table<ResearchDataAsset>
                      rowKey="id"
                      size="small"
                      loading={assetsQuery.isLoading}
                      dataSource={assets}
                      pagination={false}
                      scroll={{ x: 760 }}
                      columns={[
                        { title: 'ID', dataIndex: 'id', width: 80 },
                        { title: '名称', dataIndex: 'name' },
                        { title: '类型', dataIndex: 'asset_kind', width: 110 },
                        { title: '来源', dataIndex: 'source_type', width: 100 },
                        { title: '校验', dataIndex: 'sha256', width: 140, render: (value) => value ? `${value.slice(0, 12)}…` : '-' },
                        { title: '时间', dataIndex: 'created_at', width: 180, render: formatDate },
                      ]}
                    />
                  </Card>
                  <Card className="section-card" title="冻结数据快照">
                    <Form form={snapshotForm} layout="vertical" onFinish={(values) => createSnapshot.mutate(values)}>
                      <div className="grid-two">
                        <Form.Item label="快照名称" name="name" rules={[{ required: true, message: '请输入快照名称' }]}>
                          <Input placeholder="例如：2023 丰水期多源快照" />
                        </Form.Item>
                        <Form.Item label={requiredLabel('资产')} name="asset_ids" rules={[requiredValueRule('至少选择一个资产')]}>
                          <Select mode="multiple" options={assets.map((asset) => ({ value: asset.id, label: `#${asset.id} ${asset.name}` }))} />
                        </Form.Item>
                      </div>
                      <Form.Item label="说明" name="description"><Input /></Form.Item>
                      <Button htmlType="submit" type="primary" loading={createSnapshot.isPending}>冻结快照</Button>
                    </Form>
                    <Table<ResearchDataSnapshot>
                      className="research-inline-table"
                      rowKey="id"
                      size="small"
                      dataSource={snapshots}
                      pagination={false}
                      columns={[
                        { title: '名称', dataIndex: 'name' },
                        { title: '资产数', dataIndex: 'asset_ids', width: 100, render: (value: number[]) => value.length },
                        { title: '指纹', dataIndex: 'snapshot_hash', width: 160, render: (value) => `${value.slice(0, 12)}…` },
                        { title: '冻结时间', dataIndex: 'frozen_at', width: 180, render: formatDate },
                      ]}
                    />
                  </Card>
                  <Card className="section-card" title="验证样本与独立划分">
                    <Alert
                      type="info"
                      showIcon
                      message="样本质量与独立性必须可审查"
                      description="在正式研究中，请将独立测试样本与开发/模型选择样本分开，并填写空间块、时间分层、标注人、置信度和来源。若要计算点样本指标，必须把样本绑定到实验所选冻结快照；系统只会按实验验证方案中指定的划分、置信度和冲突规则取样，标签不会参与公式计算。"
                    />
                    <Form
                      form={validationSampleImportForm}
                      layout="vertical"
                      onFinish={(values) => importValidationSamples.mutate(values)}
                    >
                      <div className="grid-two">
                        <Form.Item label={requiredLabel('绑定的冻结快照')} name="data_snapshot_id" rules={[requiredValueRule('批量样本必须绑定冻结快照')]}>
                          <Select options={snapshots.map((snapshot) => ({ value: snapshot.id, label: `#${snapshot.id} ${snapshot.name}` }))} />
                        </Form.Item>
                        <Form.Item label="默认来源资产（可选）" name="source_asset_id">
                          <Select allowClear options={assets.map((asset) => ({ value: asset.id, label: `#${asset.id} ${asset.name}` }))} />
                        </Form.Item>
                      </div>
                      <Form.Item label="独立样本 CSV（UTF-8，最多 10,000 行 / 10 MiB）" required>
                        <Input
                          type="file"
                          aria-label="独立验证样本 CSV 文件"
                          accept=".csv,text/csv"
                          onChange={(event) => setValidationSampleCsvFile(event.target.files?.[0] ?? null)}
                        />
                      </Form.Item>
                      <p className="research-form-hint">必需表头：longitude、latitude、label、observed_at、annotator、confidence、split、spatial_block、temporal_stratum、source_note；可选 source_asset_id、conflict_status、metadata_json。CSV 仅解析为当前项目的私有样本记录，不会进入 RAG 或外部检索。</p>
                      <Button type="primary" htmlType="submit" disabled={!validationSampleCsvFile} loading={importValidationSamples.isPending}>批量导入验证样本</Button>
                    </Form>
                    <Form
                      form={validationSampleForm}
                      layout="vertical"
                      initialValues={{
                        label: 1,
                        confidence: 0.9,
                        split: 'independent_test',
                        conflict_status: 'none',
                        observed_at: new Date().toISOString().slice(0, 16),
                      }}
                      onFinish={(values) => createValidationSample.mutate(values)}
                    >
                      <div className="grid-two">
                        <Form.Item label="经度" name="longitude" rules={[{ required: true }]}><InputNumber min={-180} max={180} style={{ width: '100%' }} /></Form.Item>
                        <Form.Item label="纬度" name="latitude" rules={[{ required: true }]}><InputNumber min={-90} max={90} style={{ width: '100%' }} /></Form.Item>
                      </div>
                      <div className="grid-two">
                        <Form.Item label={requiredLabel('类别标签')} name="label" rules={[requiredValueRule('请选择类别标签')]}><Select options={[{ value: 1, label: '1 · 水体' }, { value: 0, label: '0 · 非水体' }]} /></Form.Item>
                        <Form.Item label="置信度（0–1）" name="confidence" rules={[{ required: true }]}><InputNumber min={0} max={1} step={0.05} style={{ width: '100%' }} /></Form.Item>
                      </div>
                      <div className="grid-two">
                        <Form.Item label="观察时间" name="observed_at" rules={[{ required: true }]}><Input type="datetime-local" /></Form.Item>
                        <Form.Item label="标注人" name="annotator" rules={[{ required: true }]}><Input /></Form.Item>
                      </div>
                      <div className="grid-two">
                        <Form.Item label={requiredLabel('数据划分')} name="split" rules={[requiredValueRule('请选择数据划分')]}><Select options={['development', 'model_selection', 'independent_test'].map((value) => ({ value, label: value }))} /></Form.Item>
                        <Form.Item label={requiredLabel('冲突状态')} name="conflict_status" rules={[requiredValueRule('请选择冲突状态')]}><Select options={['none', 'flagged', 'resolved'].map((value) => ({ value, label: value }))} /></Form.Item>
                      </div>
                      <div className="grid-two">
                        <Form.Item label="空间块" name="spatial_block" rules={[{ required: true }]}><Input placeholder="例如：block-east-03" /></Form.Item>
                        <Form.Item label="时间分层" name="temporal_stratum" rules={[{ required: true }]}><Input placeholder="例如：2024-wet-season" /></Form.Item>
                      </div>
                      <div className="grid-two">
                        <Form.Item label="关联快照" name="data_snapshot_id"><Select allowClear options={snapshots.map((snapshot) => ({ value: snapshot.id, label: `#${snapshot.id} ${snapshot.name}` }))} /></Form.Item>
                        <Form.Item label="来源资产" name="source_asset_id"><Select allowClear options={assets.map((asset) => ({ value: asset.id, label: `#${asset.id} ${asset.name}` }))} /></Form.Item>
                      </div>
                      <Form.Item label="来源与判读说明" name="source_note" rules={[{ required: true }]}><Input.TextArea rows={2} /></Form.Item>
                      <Button type="primary" htmlType="submit" loading={createValidationSample.isPending}>登记验证样本</Button>
                    </Form>
                    <Table<ResearchValidationSample>
                      className="research-inline-table"
                      rowKey="id"
                      size="small"
                      dataSource={validationSamples}
                      pagination={false}
                      scroll={{ x: 920 }}
                      columns={[
                        { title: '标签', dataIndex: 'label', width: 80, render: (value) => value === 1 ? '水体' : '非水体' },
                        { title: '划分', dataIndex: 'split', width: 130 },
                        { title: '空间块', dataIndex: 'spatial_block', width: 130 },
                        { title: '时间分层', dataIndex: 'temporal_stratum', width: 150 },
                        { title: '置信度', dataIndex: 'confidence', width: 90, render: (value) => value.toFixed(2) },
                        { title: '标注人', dataIndex: 'annotator', width: 120 },
                        { title: '冲突', dataIndex: 'conflict_status', width: 100, render: (value) => <Tag color={statusColor(value)}>{value}</Tag> },
                        { title: '时间', dataIndex: 'observed_at', width: 180, render: formatDate },
                      ]}
                    />
                  </Card>
                </div>
              ),
            },
            {
              key: 'method',
              label: <span className="research-tab-label"><span className="research-tab-label-full">证据与公式</span><span className="research-tab-label-short">证据</span></span>,
              children: (
                <div className="research-tab-stack research-method-grid">
                  <Card className="section-card" title="EvidenceCard">
                    <Alert
                      type="info"
                      showIcon
                      message="外部文献搜索助手"
                      description="仅发送您输入的关键词至 Crossref、OpenAlex 或 Semantic Scholar 公开元数据接口；不会发送当前项目的影像、ROI、快照或运行产物。搜索结果先是候选，不会自动进入本地 RAG 或成为已核验方法依据。明确选择目标 method 知识库后，可把公开元数据和摘要排队写入 RAG；系统不会自动下载付费全文，全文请在核对许可后手动上传。"
                    />
                    <Space.Compact block className="research-literature-search">
                      <Select
                        aria-label="外部论文元数据提供方"
                        value={literatureProvider}
                        onChange={setLiteratureProvider}
                        options={[
                          { value: 'crossref', label: 'Crossref' },
                          { value: 'openalex', label: 'OpenAlex' },
                          { value: 'semantic_scholar', label: 'Semantic Scholar' },
                        ]}
                        style={{ width: 140 }}
                      />
                      <Input.Search
                        value={literatureQuery}
                        onChange={(event) => setLiteratureQuery(event.target.value)}
                        placeholder="例如：Sentinel-1 Sentinel-2 seasonal water mapping"
                        enterButton="搜索论文元数据"
                        loading={searchLiterature.isPending}
                        onSearch={() => searchLiterature.mutate()}
                      />
                    </Space.Compact>
                    <Space wrap style={{ margin: '12px 0' }}>
                      <span>导入摘要到论文 RAG：</span>
                      <Select
                        allowClear
                        value={literatureRagKnowledgeBaseId}
                        onChange={setLiteratureRagKnowledgeBaseId}
                        placeholder="先绑定 method 知识库"
                        style={{ minWidth: 260 }}
                        options={(researchRagSourcesQuery.data ?? [])
                          .filter((source) => source.category === 'method')
                          .map((source) => ({ value: source.knowledge_base_id, label: source.knowledge_base_name }))}
                      />
                    </Space>
                    {literatureSearchResult ? (
                      <>
                        <p className="research-search-notice">{literatureSearchResult.notice}</p>
                        <Table<ResearchLiteratureCandidate>
                          className="research-inline-table"
                          rowKey={(candidate) => candidate.external_id}
                          size="small"
                          dataSource={literatureSearchResult.candidates}
                          pagination={false}
                          scroll={{ x: 620 }}
                          columns={[
                            {
                              title: '候选来源',
                              dataIndex: 'title',
                              render: (title, candidate) => (
                                <div className="research-candidate-title">
                                  <strong>{title}</strong>
                                  <span>{candidate.authors.join(', ') || '作者未提供'} · {candidate.container_title || '来源未提供'} · {candidate.published_year ?? '-'}</span>
                                </div>
                              ),
                            },
                            {
                              title: '链接',
                              width: 82,
                              render: (_, candidate) => candidate.source_url ? (
                                <a href={candidate.source_url} target="_blank" rel="noreferrer">原始记录</a>
                              ) : '-',
                            },
                            {
                              title: '操作',
                              width: 390,
                              render: (_, candidate) => (
                                <Space size={4} wrap>
                                  <Button
                                    size="small"
                                    onClick={() => importLiteratureCandidate.mutate(candidate)}
                                    loading={importLiteratureCandidate.isPending}
                                  >
                                    保存为候选证据
                                  </Button>
                                  <Button
                                    size="small"
                                    type="primary"
                                    disabled={!literatureRagKnowledgeBaseId || !candidate.abstract}
                                    title={!candidate.abstract ? '该候选没有公开摘要，请手动上传全文' : undefined}
                                    onClick={() => importLiteratureToRag.mutate(candidate)}
                                    loading={importLiteratureToRag.isPending}
                                  >
                                    导入摘要到 RAG
                                  </Button>
                                  {candidate.provider === 'semantic_scholar' ? (
                                    <>
                                      <Button
                                        size="small"
                                        onClick={() => expandLiteratureNetwork.mutate({ candidate, relation: 'references' })}
                                        loading={expandLiteratureNetwork.isPending}
                                      >
                                        参考文献
                                      </Button>
                                      <Button
                                        size="small"
                                        onClick={() => expandLiteratureNetwork.mutate({ candidate, relation: 'citations' })}
                                        loading={expandLiteratureNetwork.isPending}
                                      >
                                        被引用
                                      </Button>
                                    </>
                                  ) : null}
                                </Space>
                              ),
                            },
                          ]}
                        />
                        {literatureNetworkResult ? (
                          <Card size="small" title={literatureNetworkResult.relation === 'references' ? '参考文献扩展' : '被引用扩展'} style={{ marginTop: 12 }}>
                            <Alert
                              type="info"
                              showIcon
                              message={literatureNetworkResult.notice}
                              action={literatureNetworkResult.next_offset !== null && literatureNetworkRequest ? (
                                <Button
                                  size="small"
                                  onClick={() => expandLiteratureNetwork.mutate({
                                    candidate: literatureNetworkRequest.candidate,
                                    relation: literatureNetworkRequest.relation,
                                    offset: literatureNetworkResult.next_offset ?? 0,
                                  })}
                                  loading={expandLiteratureNetwork.isPending}
                                >
                                  下一页
                                </Button>
                              ) : undefined}
                            />
                            <p className="research-search-notice">
                              当前页 offset={literatureNetworkResult.offset}；结果审计 #{literatureNetworkResult.audit_id}，来源审计 #{literatureNetworkResult.source_audit_id}
                            </p>
                            <Table<ResearchLiteratureCandidate>
                              className="research-inline-table"
                              rowKey={(candidate) => candidate.external_id}
                              size="small"
                              dataSource={literatureNetworkResult.candidates}
                              pagination={false}
                              scroll={{ x: 620 }}
                              locale={{ emptyText: '该页没有可用的公开关系元数据。' }}
                              columns={[
                                {
                                  title: '关联候选',
                                  dataIndex: 'title',
                                  render: (title, candidate) => (
                                    <div className="research-candidate-title">
                                      <strong>{title}</strong>
                                      <span>{candidate.authors.join(', ') || '作者未提供'} · {candidate.container_title || '来源未提供'} · {candidate.published_year ?? '-'}</span>
                                    </div>
                                  ),
                                },
                                { title: 'DOI', dataIndex: 'doi', width: 180, render: (value) => value || '-' },
                                {
                                  title: '链接',
                                  width: 82,
                                  render: (_, candidate) => candidate.source_url ? <a href={candidate.source_url} target="_blank" rel="noreferrer">原始记录</a> : '-',
                                },
                              ]}
                            />
                          </Card>
                        ) : null}
                      </>
                    ) : null}
                    <Form form={evidenceForm} layout="vertical" initialValues={{ status: 'candidate', source_type: 'paper' }} onFinish={(values) => createEvidence.mutate(values)}>
                      <Form.Item label="标题" name="title" rules={[{ required: true, message: '请输入来源标题' }]}><Input /></Form.Item>
                      <div className="grid-two">
                        <Form.Item label={requiredLabel('状态')} name="status" rules={[requiredValueRule('请选择证据状态')]}>
                          <Select options={['candidate', 'verified', 'imported', 'experiment_pinned'].map((value) => ({ value, label: value }))} />
                        </Form.Item>
                        <Form.Item label={requiredLabel('来源类型')} name="source_type" rules={[requiredValueRule('请选择来源类型')]}>
                          <Select options={['paper', 'official_document', 'code', 'dataset', 'web'].map((value) => ({ value, label: value }))} />
                        </Form.Item>
                      </div>
                      <Form.Item label="DOI" name="doi"><Input /></Form.Item>
                      <Form.Item label="来源链接" name="source_url"><Input /></Form.Item>
                      <Form.Item label="适用范围" name="applicability"><Input.TextArea rows={2} /></Form.Item>
                      <Form.Item label="局限" name="limitations"><Input.TextArea rows={2} /></Form.Item>
                      <Button type="primary" htmlType="submit" loading={createEvidence.isPending}>保存证据卡</Button>
                    </Form>
                    <Table<EvidenceCard>
                      className="research-inline-table"
                      rowKey="id"
                      size="small"
                      dataSource={evidenceCards}
                      pagination={false}
                      columns={[
                        { title: '标题', dataIndex: 'title' },
                        { title: '状态', dataIndex: 'status', width: 130, render: (value) => <Tag color={statusColor(value)}>{value}</Tag> },
                        { title: '来源', width: 120, render: (_, record) => record.doi || record.source_url || '-' },
                      ]}
                    />
                  </Card>
                  <Card className="section-card" title="FormulaSpec">
                    <Alert
                      type="info"
                      showIcon
                      message="可用安全的声明式波段数学测试反射率或指数候选"
                      description={
                        <Space direction="vertical" size={4}>
                          <span>仅能引用当前 GeoTIFF 中此处声明的波段和有限数值参数；白名单为 <code>abs</code>、<code>sqrt</code>、<code>log</code>、<code>exp</code>、<code>clip</code>、<code>minimum</code>、<code>maximum</code> 及基本四则/幂运算。</span>
                          <span>不能执行 Python、访问属性或下标、调用未知函数，也不能读取验证标签。运行会保存特征图、水体图、预览、参数和波段变换；候选进入正式实验前仍须关联已核验的 EvidenceCard 并冻结。</span>
                          <Button size="small" onClick={() => formulaForm.setFieldValue('spec_json', SAFE_BAND_MATH_FORMULA_SPEC)}>载入双波段指数示例</Button>
                        </Space>
                      }
                    />
                    <Form
                      form={formulaForm}
                      layout="vertical"
                      initialValues={{ version: 1, status: 'draft', evidence_card_ids: [], spec_json: DEFAULT_FORMULA_SPEC }}
                      onFinish={(values) => createFormula.mutate(values)}
                    >
                      <div className="grid-two">
                        <Form.Item label="名称" name="name" rules={[{ required: true, message: '请输入公式名称' }]}><Input placeholder="例如：MNDWI 基线" /></Form.Item>
                        <Form.Item label="版本" name="version" rules={[{ required: true }]}><InputNumber min={1} style={{ width: '100%' }} /></Form.Item>
                      </div>
                      <div className="grid-two">
                        <Form.Item label={requiredLabel('状态')} name="status" rules={[requiredValueRule('请选择公式状态')]}>
                          <Select options={['draft', 'candidate', 'frozen'].map((value) => ({ value, label: value }))} />
                        </Form.Item>
                        <Form.Item label="关联证据卡" name="evidence_card_ids">
                          <Select mode="multiple" options={evidenceCards.map((card) => ({ value: card.id, label: `#${card.id} ${card.title}` }))} />
                        </Form.Item>
                      </div>
                      <Form.Item label="FormulaSpec JSON" name="spec_json" rules={[{ required: true }]}>
                        <Input.TextArea className="research-json-input" rows={12} spellCheck={false} />
                      </Form.Item>
                      <Button type="primary" htmlType="submit" loading={createFormula.isPending}>保存公式规格</Button>
                    </Form>
                    <Table<FormulaSpec>
                      className="research-inline-table"
                      rowKey="id"
                      size="small"
                      dataSource={formulas}
                      pagination={false}
                      columns={[
                        { title: '名称', dataIndex: 'name' },
                        { title: '版本', dataIndex: 'version', width: 80 },
                        { title: '状态', dataIndex: 'status', width: 110, render: (value) => <Tag color={statusColor(value)}>{value}</Tag> },
                        { title: '证据', dataIndex: 'evidence_card_ids', width: 90, render: (value: number[]) => value.length },
                      ]}
                    />
                  </Card>
                </div>
              ),
            },
            {
              key: 'experiments',
              label: <span className="research-tab-label"><span className="research-tab-label-full">实验与影像证据</span><span className="research-tab-label-short">实验</span></span>,
              children: (
                <div className="research-tab-stack">
                  <Card className="section-card" title="创建实验计划">
                    <Form
                      form={experimentForm}
                      layout="vertical"
                      initialValues={{
                        runner_type: 'python',
                        execution_mode: 'preview',
                        parameters_json: '{}',
                        validation_plan_json: DEFAULT_VALIDATION_PLAN,
                        visualization_contract: ['input', 'index', 'water_mask'],
                      }}
                      onFinish={(values) => createExperiment.mutate(values)}
                    >
                      <div className="grid-two">
                        <Form.Item label="实验名称" name="name" rules={[{ required: true, message: '请输入实验名称' }]}><Input /></Form.Item>
                        <Form.Item label={requiredLabel('执行器')} name="runner_type" rules={[requiredValueRule('请选择执行器')]}>
                          <Select options={[{ value: 'python', label: 'PythonRunner（默认）' }, { value: 'idl', label: 'IDLRunner（本机受许可节点）' }]} />
                        </Form.Item>
                      </div>
                      <div className="grid-two">
                        <Form.Item label={requiredLabel('冻结公式')} name="formula_spec_id" rules={[requiredValueRule('请选择公式规格')]}>
                          <Select options={formulas.map((formula) => ({ value: formula.id, label: `#${formula.id} ${formula.name} v${formula.version} · ${formula.status}` }))} />
                        </Form.Item>
                        <Form.Item label={requiredLabel('数据快照')} name="data_snapshot_id" rules={[requiredValueRule('请选择快照')]}>
                          <Select options={snapshots.map((snapshot) => ({ value: snapshot.id, label: `#${snapshot.id} ${snapshot.name}` }))} />
                        </Form.Item>
                      </div>
                      <div className="grid-two">
                        <Form.Item label={requiredLabel('运行模式')} name="execution_mode" rules={[requiredValueRule('请选择运行模式')]}>
                          <Select options={[{ value: 'preview', label: '预览' }, { value: 'formal', label: '正式可复现实验' }]} />
                        </Form.Item>
                        <Form.Item label={requiredLabel('可视化证据')} name="visualization_contract" rules={[requiredValueRule('至少声明一项图件证据')]}>
                          <Select mode="tags" options={['input', 'cloud_mask', 'index', 'water_mask', 'uncertainty', 'validation_error'].map((value) => ({ value, label: value }))} />
                        </Form.Item>
                      </div>
                      <Alert
                        type="info"
                        showIcon
                        message="正式可复现实验必须登记独立参考栅格或独立点样本"
                        description={`正式模式还需先保存至少 8 个字符的研究问题；创建时会冻结一份项目协议并在下方显示其指纹。验证方案必须设置 "split":"spatiotemporal-holdout"，并满足其一：参考栅格加入 "reference_asset_id": <资产 ID>（本快照内、同网格、0/1 GeoTIFF）；或点样本加入 "sample_validation": {"split":"independent_test","min_confidence":0.8,"require_unconflicted":true,"min_sample_count":100,"min_spatial_blocks":10,"min_temporal_strata":3,"min_samples_per_spatial_block":10,"min_samples_per_temporal_stratum":20}。后五个数值是研究协议下限示例，须按真实样本设计调整；运行器会拒绝总体或任一分层未达标的结果。预览模式可以先不具备验证资料。当前可选 reference 资产：${assets.filter((asset) => asset.asset_kind === 'reference').map((asset) => `#${asset.id} ${asset.name}`).join('；') || '尚未上传 reference 类型资产'}`}
                      />
                      <Alert
                        type="info"
                        showIcon
                        message="可选：将本地 IDL GeoTIFF 与本次 Python 结果做数值对照"
                        description={'先在“数据与快照”上传本地 IDL 输出，资产类型选 derived；再在参数 JSON 声明：{"idl_comparison":{"idl_output_asset_id":<derived 资产 ID>,"comparison_mode":"classification","python_output_file":"water_mask.tif","absolute_tolerance":0}}。分类会输出一致率、F1、IoU、NoData 差异与差异图；连续变量可用 comparison_mode:"continuous"、例如 python_output_file:"normalized_difference.tif" 和非负容差，输出 MAE/RMSE/容差通过率。IDL 输出只是对照，不会被当作参考真值；CRS、尺寸或仿射变换不一致将拒绝运行。'}
                      />
                      <Alert
                        type="info"
                        showIcon
                        message="项目级 IDLRunner 参数"
                        description={'选择 IDLRunner 后，参数 JSON 必须包含 {"idl_script_asset_id": <项目 IDL 脚本资产 ID>}；可选 {"idl_entrypoint":"procedure_name","idl_prediction_output_file":"water_mask.tif"}。预测输出必须是受限输出目录中的 GeoTIFF；若验证方案声明参考栅格或独立点样本，IDL 结果会复用同一验证、误差图和证据包契约。脚本会通过 IDLRAG_INPUT_DIR、IDLRAG_OUTPUT_DIR 和 IDLRAG_INPUT_MANIFEST 获取输入/输出约定，运行结果会进入同一 Run manifest 和证据包。'}
                      />
                      <div className="grid-two">
                        <Form.Item label="参数 JSON" name="parameters_json" rules={[{ required: true }]}><Input.TextArea className="research-json-input" rows={7} spellCheck={false} /></Form.Item>
                        <Form.Item label="验证方案 JSON" name="validation_plan_json" rules={[{ required: true }]}><Input.TextArea className="research-json-input" rows={7} spellCheck={false} /></Form.Item>
                      </div>
                      <Button type="primary" htmlType="submit" loading={createExperiment.isPending}>创建实验</Button>
                    </Form>
                  </Card>
                  <Card className="section-card" title="实验计划">
                    <Table<ResearchExperiment>
                      rowKey="id"
                      size="small"
                      dataSource={experiments}
                      pagination={false}
                      scroll={{ x: 760 }}
                      rowSelection={{
                        type: 'radio',
                        columnTitle: '选择',
                        getCheckboxProps: (record) => ({ 'aria-label': `选择实验 ${record.name}` } as never),
                        selectedRowKeys: selectedExperimentId ? [selectedExperimentId] : [],
                        onChange: (keys) => setSelectedExperimentId(Number(keys[0]) || undefined),
                      }}
                      columns={[
                        { title: '名称', dataIndex: 'name' },
                        { title: '执行器', dataIndex: 'runner_type', width: 110 },
                        { title: '模式', dataIndex: 'execution_mode', width: 100 },
                        { title: '状态', dataIndex: 'status', width: 120, render: (value) => <Tag color={statusColor(value)}>{value}</Tag> },
                        { title: '协议指纹', dataIndex: 'project_protocol_hash', width: 130, render: (value: string) => <span title={value}>{value ? `${value.slice(0, 12)}…` : '-'}</span> },
                        { title: '创建时间', dataIndex: 'created_at', width: 180, render: formatDate },
                      ]}
                    />
                    <Space className="research-run-actions">
                      <Button type="primary" disabled={!selectedExperimentId} loading={startRun.isPending} onClick={() => startRun.mutate('sync')}>
                        立即运行
                      </Button>
                      <Button disabled={!selectedExperimentId} loading={startRun.isPending} onClick={() => startRun.mutate('queue')}>
                        加入后台队列
                      </Button>
                      <Button
                        danger
                        disabled={!visibleRun || !['queued', 'running'].includes(visibleRun.status)}
                        loading={cancelRun.isPending}
                        onClick={() => cancelRun.mutate()}
                      >
                        {visibleRun?.status === 'running' ? '请求取消' : '取消排队'}
                      </Button>
                      <Button
                        disabled={!visibleRun || !['completed', 'failed', 'cancelled', 'unavailable'].includes(visibleRun.status)}
                        loading={retryRun.isPending}
                        onClick={() => retryRun.mutate()}
                      >
                        重新排队
                      </Button>
                      <Button disabled={!selectedExperimentId} onClick={() => void runsQuery.refetch()}>刷新运行记录</Button>
                      <Select
                        aria-label="选择重跑基准运行"
                        style={{ minWidth: 220 }}
                        placeholder="选择重跑基准运行"
                        value={reproducibilityReferenceRunId}
                        onChange={setReproducibilityReferenceRunId}
                        options={completedReferenceRuns.map((run) => ({
                          value: run.id,
                          label: `基准 ${run.run_token.slice(0, 12)} · ${formatDate(run.finished_at)}`,
                        }))}
                        disabled={!visibleRun || visibleRun.status !== 'completed' || selectedExperiment?.execution_mode !== 'formal'}
                      />
                      <Button
                        loading={compareReproducibility.isPending}
                        disabled={!visibleRun || visibleRun.status !== 'completed' || selectedExperiment?.execution_mode !== 'formal' || !reproducibilityReferenceRunId}
                        onClick={() => compareReproducibility.mutate()}
                      >
                        比较重跑一致性
                      </Button>
                      <Button
                        loading={compareFormalRuns.isPending}
                        disabled={!visibleRun || visibleRun.status !== 'completed' || selectedExperiment?.execution_mode !== 'formal' || !reproducibilityReferenceRunId}
                        onClick={() => compareFormalRuns.mutate()}
                      >
                        比较基线/候选指标
                      </Button>
                    </Space>
                    <Alert
                      className="research-sweep-notice"
                      type="info"
                      showIcon
                      message="科研候选参数实验（仅开发/模型选择样本）"
                      description="用于比较修改后的公式或阈值，不读取 independent_test，不自动冻结公式，也不替代正式实验结论。"
                    />
                    <div className="research-sweep-controls">
                      <Select
                        aria-label="候选参数评估数据划分"
                        value={sweepEvaluationSplit}
                        onChange={setSweepEvaluationSplit}
                        options={[
                          { value: 'development', label: 'development 开发样本' },
                          { value: 'model_selection', label: 'model_selection 模型选择样本' },
                        ]}
                        disabled={!selectedExperimentId || selectedExperiment?.execution_mode !== 'preview'}
                      />
                      <Select
                        aria-label="候选参数排序指标"
                        value={sweepRankingMetric}
                        onChange={setSweepRankingMetric}
                        options={[
                          { value: 'f1', label: '按 F1 排序' },
                          { value: 'iou', label: '按 IoU 排序' },
                          { value: 'overall_accuracy', label: '按 OA 排序' },
                          { value: 'precision', label: '按 Precision 排序' },
                          { value: 'recall', label: '按 Recall 排序' },
                        ]}
                        disabled={!selectedExperimentId || selectedExperiment?.execution_mode !== 'preview'}
                      />
                      <Button
                        type="primary"
                        ghost
                        loading={startSweep.isPending}
                        disabled={!selectedExperimentId || selectedExperiment?.execution_mode !== 'preview'}
                        onClick={() => startSweep.mutate('sync')}
                      >
                        立即比较候选参数
                      </Button>
                      <Button
                        loading={startSweep.isPending}
                        disabled={!selectedExperimentId || selectedExperiment?.execution_mode !== 'preview'}
                        onClick={() => startSweep.mutate('queue')}
                      >
                        加入候选实验队列
                      </Button>
                    </div>
                    <Input.TextArea
                      aria-label="候选参数 JSON"
                      className="research-json-input"
                      rows={4}
                      value={sweepCandidatesJson}
                      onChange={(event) => setSweepCandidatesJson(event.target.value)}
                      spellCheck={false}
                      disabled={!selectedExperimentId || selectedExperiment?.execution_mode !== 'preview'}
                    />
                  </Card>
                  <Card className="section-card" title="运行与影像证据">
                    {runsQuery.isError ? (
                      <Alert
                        type="error"
                        showIcon
                        message="运行记录暂时无法加载"
                        description={runsQuery.error instanceof Error ? runsQuery.error.message : '请检查项目权限或后端连接后重试。'}
                        action={<Button size="small" onClick={() => void runsQuery.refetch()} loading={runsQuery.isFetching}>重新加载</Button>}
                        style={{ marginBottom: 12 }}
                      />
                    ) : null}
                    {selectedExperimentId ? (
                      <>
                        <Table<ResearchRun>
                          rowKey="id"
                          size="small"
                          loading={runsQuery.isLoading}
                          dataSource={runs}
                          pagination={false}
                          columns={[
                            { title: '运行', dataIndex: 'run_token', render: (value) => value.slice(0, 12) },
                            { title: '状态', dataIndex: 'status', width: 130, render: (value) => <Tag color={statusColor(value)}>{value}</Tag> },
                            { title: '产物数', dataIndex: 'outputs', width: 100, render: (value: ResearchRun['outputs']) => value.length },
                            { title: '结束时间', dataIndex: 'finished_at', width: 180, render: formatDate },
                          ]}
                        />
                        {runs.length > 0 ? (
                          <div className="research-run-timeline-block">
                            <div className="research-run-section-title">运行时间线</div>
                            <Timeline
                              className="research-run-timeline"
                              items={[...runs]
                                .sort((left, right) => Date.parse(right.created_at) - Date.parse(left.created_at))
                                .map((run) => {
                                  const metrics = validationMetricsFromRun(run)
                                  return {
                                    color: timelineColor(run.status),
                                    children: (
                                      <div className="research-run-timeline-item">
                                        <div className="research-run-timeline-heading">
                                          <strong>Run #{run.id}</strong>
                                          <Tag color={statusColor(run.status)}>{run.status}</Tag>
                                          <span>{run.runner_type} · {run.run_token.slice(0, 12)}</span>
                                        </div>
                                        <div className="research-run-timeline-meta">
                                          <span>创建 {formatDate(run.created_at)}</span>
                                          <span>{run.outputs.length} 个产物</span>
                                          {metrics?.f1 !== undefined ? <span>F1 {metrics.f1.toFixed(3)}</span> : null}
                                          {metrics?.iou !== undefined ? <span>IoU {metrics.iou.toFixed(3)}</span> : null}
                                        </div>
                                        {run.error_message ? (
                                          <div className="research-run-timeline-error">{run.error_message}</div>
                                        ) : null}
                                      </div>
                                    ),
                                  }
                                })}
                            />
                          </div>
                        ) : null}
                        {visibleRun ? (
                          <div className="research-run-detail">
                            {visibleRun.error_message ? <Alert type="error" showIcon message={visibleRun.error_message} /> : null}
                            <MetricSummary
                              items={[
                                { label: '运行器', value: visibleRun.runner_type },
                                { label: '状态', value: visibleRun.status, tone: visibleRun.status === 'completed' ? 'success' : 'warning' },
                                { label: '产物', value: visibleRun.outputs.length },
                                { label: '开始', value: formatDate(visibleRun.started_at) },
                              ]}
                            />
                            {validationMetrics ? (
                              <MetricSummary
                                items={[
                                  { label: 'OA', value: validationMetrics.overall_accuracy?.toFixed(3) ?? '-' },
                                  { label: 'Precision', value: validationMetrics.precision?.toFixed(3) ?? '-' },
                                  { label: 'Recall', value: validationMetrics.recall?.toFixed(3) ?? '-' },
                                  { label: 'F1', value: validationMetrics.f1?.toFixed(3) ?? '-', tone: 'success' },
                                  { label: 'IoU', value: validationMetrics.iou?.toFixed(3) ?? '-', tone: 'success' },
                                ]}
                              />
                            ) : null}
                            {validationWeighting ? (
                              <Alert
                                type="info"
                                showIcon
                                message={`本次点样本指标使用 metadata.${String(validationWeighting.metadata_key)} 权重；总权重 ${String(validationWeighting.total_weight)}，有效样本量 ${Number(validationWeighting.effective_sample_size).toFixed(2)}。`}
                              />
                            ) : null}
                            {validationAreaAdjustment ? (
                              <>
                                <Alert
                                  type="info"
                                  showIcon
                                  message={`本次点样本同时输出分层面积调整精度（${String(validationAreaAdjustment.selection.area_unit)}）`}
                                  description={`覆盖 ${String(validationAreaAdjustment.selection.strata_count)} 个分层，总面积 ${String(validationAreaAdjustment.selection.total_area)}；设计型置信区间仍需依据真实抽样设计计算。`}
                                />
                                <MetricSummary
                                  items={[
                                    { label: '面积调整 OA', value: typeof validationAreaAdjustment.metrics.metrics === 'object' && validationAreaAdjustment.metrics.metrics ? Number((validationAreaAdjustment.metrics.metrics as Record<string, unknown>).overall_accuracy).toFixed(3) : '-' },
                                    { label: '面积调整 F1', value: typeof validationAreaAdjustment.metrics.metrics === 'object' && validationAreaAdjustment.metrics.metrics ? Number((validationAreaAdjustment.metrics.metrics as Record<string, unknown>).f1).toFixed(3) : '-', tone: 'success' },
                                    { label: '参考正类面积', value: String(validationAreaAdjustment.metrics.reference_positive_area ?? '-') },
                                    { label: '预测正类面积', value: String(validationAreaAdjustment.metrics.predicted_positive_area ?? '-') },
                                    { label: '面积绝对误差', value: String(validationAreaAdjustment.metrics.absolute_area_error ?? '-') },
                                  ]}
                                />
                              </>
                            ) : null}
                            {parameterSweep ? (
                              <>
                                <Alert
                                  type="info"
                                  showIcon
                                  message={`候选参数实验：仅 ${String(parameterSweep.evaluation_split)} 样本，按 ${String(parameterSweep.ranking_metric)} 排序`}
                                  description={String(parameterSweep.selection_notice ?? '排序仅用于探索，不会自动冻结公式或替代独立测试。')}
                                />
                                <Table
                                  size="small"
                                  pagination={false}
                                  rowKey="rank"
                                  dataSource={Array.isArray(parameterSweep.ranking) ? parameterSweep.ranking as Array<Record<string, unknown>> : []}
                                  columns={[
                                    { title: '排名', dataIndex: 'rank', width: 70 },
                                    { title: '候选', dataIndex: 'name' },
                                    { title: '分数', dataIndex: 'score', render: (value: unknown) => typeof value === 'number' ? value.toFixed(4) : '-' },
                                  ]}
                                />
                              </>
                            ) : null}
                            {compareReproducibility.data ? (
                              <Alert
                                type={compareReproducibility.data.matched ? 'success' : 'error'}
                                showIcon
                                message={compareReproducibility.data.notice}
                                description={
                                  compareReproducibility.data.matched
                                    ? `已比较 ${compareReproducibility.data.compared_output_count} 个产物；绝对容差 ${compareReproducibility.data.absolute_tolerance}，相对容差 ${compareReproducibility.data.relative_tolerance}。`
                                    : compareReproducibility.data.issues.join(' ')
                                }
                              />
                            ) : null}
                            {compareFormalRuns.data ? (
                              <>
                                <Alert
                                  type={compareFormalRuns.data.comparable ? 'info' : 'error'}
                                  showIcon
                                  message={compareFormalRuns.data.notice}
                                  description={
                                    compareFormalRuns.data.comparable
                                      ? `已比较 ${compareFormalRuns.data.compared_metric_count} 个指标；表中 delta = 候选 − 基线，不代表统计显著性。`
                                      : compareFormalRuns.data.issues.join(' ')
                                  }
                                />
                                {compareFormalRuns.data.comparable ? (
                                  <Table
                                    size="small"
                                    pagination={false}
                                    rowKey={(row) => `${row.scope}-${row.metric}`}
                                    dataSource={compareFormalRuns.data.metrics}
                                    columns={[
                                      { title: '范围', dataIndex: 'scope' },
                                      { title: '指标', dataIndex: 'metric' },
                                      { title: '基线', dataIndex: 'baseline', render: (value: number) => value.toFixed(4) },
                                      { title: '候选', dataIndex: 'candidate', render: (value: number) => value.toFixed(4) },
                                      { title: 'delta', dataIndex: 'delta', render: (value: number) => value >= 0 ? `+${value.toFixed(4)}` : value.toFixed(4) },
                                    ]}
                                  />
                                ) : null}
                              </>
                            ) : null}
                            {validationIntervals ? (
                              <>
                                <Alert
                                  type="info"
                                  showIcon
                                  message="95% 区间采用未加权 Wilson 方法；不代表复杂抽样设计下的面积或空间方差区间。"
                                />
                                <MetricSummary
                                  items={[
                                    { label: 'OA 95% 区间', value: validationIntervals.overall_accuracy ? `[${validationIntervals.overall_accuracy.lower.toFixed(3)}, ${validationIntervals.overall_accuracy.upper.toFixed(3)}]` : '-' },
                                    { label: 'Precision 95% 区间', value: validationIntervals.precision ? `[${validationIntervals.precision.lower.toFixed(3)}, ${validationIntervals.precision.upper.toFixed(3)}]` : '-' },
                                    { label: 'Recall 95% 区间', value: validationIntervals.recall ? `[${validationIntervals.recall.lower.toFixed(3)}, ${validationIntervals.recall.upper.toFixed(3)}]` : '-' },
                                    { label: 'IoU 95% 区间', value: validationIntervals.iou ? `[${validationIntervals.iou.lower.toFixed(3)}, ${validationIntervals.iou.upper.toFixed(3)}]` : '-' },
                                  ]}
                                />
                              </>
                            ) : null}
                            <RunOutputPreview projectId={selectedProject.id} experimentId={selectedExperimentId} run={visibleRun} executionMode={selectedExperiment?.execution_mode} />
                          </div>
                        ) : <DisplayEmpty compact illustration="terminal" title="尚未运行所选实验" />}
                      </>
                    ) : <DisplayEmpty compact illustration="terminal" title="请选择一个实验" description="运行后会在这里展示阶段影像和产物记录。" />}
                  </Card>
                </div>
              ),
            },
          ]}
        />
      ) : null}
    </div>
  )
}
