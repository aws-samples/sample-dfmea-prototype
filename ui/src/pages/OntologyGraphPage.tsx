import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import PipelineStepper from '../components/PipelineStepper'
import { apiGet } from '../lib/api'
import type { Finding } from '../stores/findingsStore'
import type { Review } from '../stores/reviewStore'

interface GraphNode {
  id: string
  label: string
  type: string
  x?: number
  y?: number
  fx?: number
  fy?: number
}

interface GraphEdge {
  source: string | GraphNode
  target: string | GraphNode
  label: string
}

interface GraphData {
  nodes: GraphNode[]
  edges: Array<{ source: string; target: string; label: string }>
  available: boolean
}

interface GateSummary {
  pending: boolean
  status: string
  comment?: string
}

type FocusMode = 'overview' | 'trust' | 'gaps' | 'citations' | 'diff'
type ApFilter = 'all' | 'H' | 'M' | 'L'

const NODE_COLORS: Record<string, string> = {
  Component: '#ef4444',
  Material: '#f97316',
  Process: '#eab308',
  FailureMode: '#a855f7',
  Effect: '#3b82f6',
  DetectionMethod: '#14b8a6',
  CorrectiveAction: '#22c55e',
  Standard: '#94a3b8',
  Unknown: '#64748b',
}

const AGENT_LABELS: Record<string, string> = {
  failure_mode: 'Failure Mode',
  structural: 'Structural',
  regulatory: 'Regulatory',
  other: 'Schema',
  analyst: 'Analyst',
}

const FOCUS_OPTIONS: Array<{ id: FocusMode; label: string; number: number; disabled?: boolean }> = [
  { id: 'overview', label: 'Overview', number: 1 },
  { id: 'trust', label: 'Trust finding', number: 2 },
  { id: 'gaps', label: 'Gaps', number: 3 },
  { id: 'citations', label: 'Citations', number: 4 },
  { id: 'diff', label: 'Diff prior', number: 5, disabled: true },
]

const normalize = (value: string | undefined | null) =>
  (value ?? '').toLowerCase().replace(/[^a-z0-9]/g, '')

const hasCitation = (finding: Finding) => (finding.standard_citations?.length ?? 0) > 0

function graphEndpointId(value: GraphEdge['source']): string {
  return typeof value === 'string' ? value : value.id
}

function positionGraph(
  nodes: GraphNode[],
  edges: Array<{ source: string; target: string; label: string }>,
): GraphNode[] {
  if (nodes.length === 0) return []

  const degree = new Map<string, number>()
  edges.forEach((edge) => {
    degree.set(edge.source, (degree.get(edge.source) ?? 0) + 1)
    degree.set(edge.target, (degree.get(edge.target) ?? 0) + 1)
  })

  const root = [...nodes]
    .filter((node) => node.type === 'Component')
    .sort((left, right) => (degree.get(right.id) ?? 0) - (degree.get(left.id) ?? 0))[0]
    ?? [...nodes].sort((left, right) => (degree.get(right.id) ?? 0) - (degree.get(left.id) ?? 0))[0]

  const lowerTypes = new Set(['Process', 'DetectionMethod', 'CorrectiveAction', 'Effect'])
  const upper = nodes.filter((node) => node.id !== root.id && !lowerTypes.has(node.type))
  const lower = nodes.filter((node) => node.id !== root.id && lowerTypes.has(node.type))
  const positions = new Map<string, { x: number; y: number; fx: number; fy: number }>()

  const placeRows = (items: GraphNode[], startY: number, direction: -1 | 1) => {
    const maxColumns = 12
    items.forEach((node, index) => {
      const row = Math.floor(index / maxColumns)
      const rowStart = row * maxColumns
      const rowLength = Math.min(maxColumns, items.length - rowStart)
      const column = index - rowStart
      const x = (column - (rowLength - 1) / 2) * 74
      const y = startY + row * 72 * direction
      positions.set(node.id, { x, y, fx: x, fy: y })
    })
  }

  placeRows(upper, -125, -1)
  placeRows(lower, 125, 1)

  return nodes.map((node) => {
    if (node.id === root.id) return { ...node, x: 0, y: 0, fx: 0, fy: 0 }
    return { ...node, ...positions.get(node.id) }
  })
}

function findingMatchesNode(finding: Finding, node: GraphNode): boolean {
  const nodeValue = normalize(node.label)
  if (nodeValue.length < 3) return false

  if (node.type === 'Component') {
    const component = normalize(finding.affected_component)
    return Boolean(component && (component.includes(nodeValue) || nodeValue.includes(component)))
  }

  if (node.type === 'Standard') {
    return Boolean(finding.standard_citations?.some((citation) => {
      const citationValue = normalize(citation)
      return citationValue.includes(nodeValue) || nodeValue.includes(citationValue)
    }))
  }

  if (node.type === 'FailureMode' || node.type === 'Effect') {
    const description = normalize(finding.description)
    return nodeValue.length >= 5 && description.includes(nodeValue)
  }

  return false
}

function ReviewPipeline({ status, gates }: { status: string; gates: Record<string, GateSummary> | null }) {
  return (
    <div className="border-y border-slate-700/80 bg-slate-900/95 px-5 py-2">
      <div className="mx-auto max-w-6xl">
        <PipelineStepper status={status} gates={gates} />
      </div>
    </div>
  )
}

export default function OntologyGraphPage() {
  const { reviewId: routeReviewId } = useParams<{ reviewId?: string }>()
  const navigate = useNavigate()

  const [reviews, setReviews] = useState<Review[]>([])
  const [review, setReview] = useState<Review | null>(null)
  const [findings, setFindings] = useState<Finding[]>([])
  const [gates, setGates] = useState<Record<string, GateSummary> | null>(null)
  const [graphData, setGraphData] = useState<{ nodes: GraphNode[]; links: GraphEdge[] }>({ nodes: [], links: [] })
  const [graphAvailable, setGraphAvailable] = useState(true)
  const [graphLoading, setGraphLoading] = useState(true)
  const [graphPaintReady, setGraphPaintReady] = useState(false)
  const [reviewLoading, setReviewLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [findingsError, setFindingsError] = useState(false)
  const [gatesError, setGatesError] = useState(false)
  const [selectedFindingId, setSelectedFindingId] = useState<string | null>(null)
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const [agentFilter, setAgentFilter] = useState('all')
  const [apFilter, setApFilter] = useState<ApFilter>('all')
  const [focusMode, setFocusMode] = useState<FocusMode>('overview')

  const selectedReviewId = routeReviewId ?? ''

  useEffect(() => {
    let cancelled = false
    apiGet<{ reviews: Review[] }>('/reviews')
      .then((data) => {
        if (cancelled) return
        const sortedReviews = [...data.reviews].sort(
          (left, right) => new Date(right.created_at).getTime() - new Date(left.created_at).getTime(),
        )
        setReviews(sortedReviews)
        if (!routeReviewId && sortedReviews.length > 0) {
          const defaultReview = sortedReviews.find((item) => item.status !== 'ARCHIVED') ?? sortedReviews[0]
          navigate(`/ontology/${defaultReview.review_id}`, { replace: true })
        }
      })
      .catch((cause) => {
        if (!cancelled) setError(`Could not load reviews: ${String(cause)}`)
      })
    return () => { cancelled = true }
  }, [navigate, routeReviewId])

  useEffect(() => {
    let cancelled = false
    setGraphLoading(true)
    setGraphAvailable(true)
    apiGet<GraphData>('/ontology/graph')
      .then((data) => {
        if (cancelled) return
        setGraphAvailable(data.available)
        setGraphData({ nodes: positionGraph(data.nodes, data.edges), links: data.edges })
      })
      .catch(() => {
        if (!cancelled) {
          setGraphAvailable(false)
          setGraphData({ nodes: [], links: [] })
        }
      })
      .finally(() => {
        if (!cancelled) setGraphLoading(false)
      })
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    if (graphLoading || reviewLoading || !graphAvailable || graphData.nodes.length === 0) {
      setGraphPaintReady(false)
      return
    }

    setGraphPaintReady(false)
    const paintTimer = window.setTimeout(() => setGraphPaintReady(true), 50)
    return () => window.clearTimeout(paintTimer)
  }, [graphAvailable, graphData.nodes.length, graphLoading, reviewLoading, selectedReviewId])

  useEffect(() => {
    if (!selectedReviewId) {
      setReview(null)
      setFindings([])
      setGates(null)
      return
    }

    let cancelled = false
    setReviewLoading(true)
    setError(null)
    setFindingsError(false)
    setGatesError(false)
    setReview(null)
    setFindings([])
    setGates(null)
    setSelectedFindingId(null)
    setSelectedNodeId(null)
    setFocusMode('overview')

    Promise.allSettled([
      apiGet<Review>(`/reviews/${selectedReviewId}`),
      apiGet<{ findings: Finding[] }>(`/reviews/${selectedReviewId}/findings`),
      apiGet<{ gates: Record<string, GateSummary> }>(`/reviews/${selectedReviewId}/gates`),
    ]).then(([reviewResult, findingsResult, gatesResult]) => {
      if (cancelled) return

      if (reviewResult.status === 'fulfilled') setReview(reviewResult.value)
      else setError('The selected review could not be loaded.')

      if (findingsResult.status === 'fulfilled') setFindings(findingsResult.value.findings)
      else setFindingsError(true)

      if (gatesResult.status === 'fulfilled') setGates(gatesResult.value.gates)
      else setGatesError(true)
    }).finally(() => {
      if (!cancelled) setReviewLoading(false)
    })

    return () => { cancelled = true }
  }, [selectedReviewId])

  const selectedFinding = useMemo(
    () => findings.find((finding) => finding.finding_id === selectedFindingId) ?? null,
    [findings, selectedFindingId],
  )

  const relatedNodeIds = useMemo(() => {
    const ids = new Set<string>()
    if (selectedFinding) {
      graphData.nodes.forEach((node) => {
        if (findingMatchesNode(selectedFinding, node)) ids.add(node.id)
      })
    }
    if (selectedNodeId) ids.add(selectedNodeId)
    return ids
  }, [graphData.nodes, selectedFinding, selectedNodeId])

  const agents = useMemo(() => [...new Set(findings.map((finding) => finding.agent))], [findings])

  const filteredFindings = useMemo(() => {
    const selectedNode = graphData.nodes.find((node) => node.id === selectedNodeId)
    return findings.filter((finding) => {
      if (agentFilter !== 'all' && finding.agent !== agentFilter) return false
      if (apFilter !== 'all' && finding.action_priority !== apFilter) return false
      if (focusMode === 'gaps' && !finding.finding_type?.toLowerCase().includes('gap')) return false
      if (focusMode === 'citations' && hasCitation(finding)) return false
      if (selectedNode && !findingMatchesNode(finding, selectedNode)) return false
      return true
    })
  }, [agentFilter, apFilter, findings, focusMode, graphData.nodes, selectedNodeId])

  useEffect(() => {
    if (selectedFindingId && !filteredFindings.some((finding) => finding.finding_id === selectedFindingId)) {
      setSelectedFindingId(null)
    }
  }, [filteredFindings, selectedFindingId])

  const mappedFindingCount = useMemo(
    () => findings.filter((finding) => graphData.nodes.some((node) => findingMatchesNode(finding, node))).length,
    [findings, graphData.nodes],
  )
  const citedFindingCount = useMemo(() => findings.filter(hasCitation).length, [findings])
  const mappingCoverage = findings.length > 0 ? Math.round((mappedFindingCount / findings.length) * 100) : 0
  const apCounts = useMemo(() => findings.reduce((counts, finding) => {
    counts[finding.action_priority] = (counts[finding.action_priority] ?? 0) + 1
    return counts
  }, {} as Record<string, number>), [findings])

  const activeGateNumber = useMemo(() => {
    if (!gates) return null
    const pending = Object.entries(gates)
      .filter(([, gate]) => gate.pending)
      .map(([number]) => Number(number))
      .sort((a, b) => b - a)
    if (pending.length > 0) return pending[0]
    const started = Object.entries(gates)
      .filter(([, gate]) => gate.status !== 'NOT_STARTED')
      .map(([number]) => Number(number))
      .sort((a, b) => b - a)
    return started[0] ?? null
  }, [gates])

  const activeGate = activeGateNumber && gates ? gates[String(activeGateNumber)] : null
  const selectedGraphNode = graphData.nodes.find((node) => node.id === selectedNodeId) ?? null
  const hasGraphSelection = Boolean(selectedFindingId || selectedNodeId)

  const resetView = useCallback(() => {
    setSelectedFindingId(null)
    setSelectedNodeId(null)
    setAgentFilter('all')
    setApFilter('all')
    setFocusMode('overview')
  }, [])

  const applyFocusMode = (mode: FocusMode) => {
    if (mode === 'diff') return
    setFocusMode(mode)
    setSelectedNodeId(null)
    if (mode === 'overview' || mode === 'gaps' || mode === 'citations') setSelectedFindingId(null)
    if (mode === 'trust' && !selectedFindingId && filteredFindings.length > 0) {
      setSelectedFindingId(filteredFindings[0].finding_id)
    }
  }

  return (
    <div className="flex h-[calc(100vh-3.5rem)] min-h-0 flex-col overflow-hidden bg-slate-950 text-slate-100">
      <section className="flex min-h-12 items-center gap-3 border-b border-slate-700 bg-slate-900 px-5 py-2">
        <Link to="/reviews" className="text-xs font-medium text-sky-400 hover:text-sky-300">← Reviews</Link>
        <span className="text-slate-600">/</span>
        <h1 className="min-w-0 truncate text-sm font-semibold text-white">
          {reviewLoading ? 'Loading review…' : review?.assembly_name ?? 'Knowledge Graph'}
        </h1>
        {activeGateNumber && (
          <span className="rounded-full border border-sky-500/50 bg-sky-500/10 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-sky-300">
            Gate {activeGateNumber}
          </span>
        )}
        <span className="hidden text-[10px] text-slate-500 xl:inline">Ontology · live Neptune view</span>
        <div className="ml-auto flex items-center gap-3">
          <span className="hidden text-[10px] text-slate-400 lg:inline">
            {findingsError
              ? 'Findings unavailable'
              : `${mappedFindingCount}/${findings.length} mapped · ${citedFindingCount}/${findings.length} cited`}
          </span>
          <select
            value={selectedReviewId}
            onChange={(event) => navigate(`/ontology/${event.target.value}`)}
            aria-label="Select review"
            className="max-w-64 rounded border border-slate-600 bg-slate-800 px-2 py-1 text-xs text-slate-100 outline-none focus:border-sky-400"
          >
            {reviews.length === 0 && <option value="">No reviews available</option>}
            {reviews.map((item) => (
              <option key={item.review_id} value={item.review_id}>{item.assembly_name}</option>
            ))}
          </select>
        </div>
      </section>

      <section className="flex min-h-10 items-center gap-1.5 border-b border-slate-700 bg-slate-800 px-5 py-1.5 text-[10px]">
        <span className="mr-1 font-semibold uppercase tracking-wider text-slate-500">Review focus</span>
        {FOCUS_OPTIONS.map((option) => (
          <button
            key={option.id}
            type="button"
            disabled={option.disabled}
            title={option.disabled ? 'Prior-review lineage is not available in the current API.' : undefined}
            onClick={() => applyFocusMode(option.id)}
            className={`rounded border px-2 py-1 transition-colors ${
              focusMode === option.id
                ? 'border-sky-400 bg-sky-500 text-white'
                : option.disabled
                  ? 'cursor-not-allowed border-slate-700 bg-slate-800 text-slate-600'
                  : 'border-slate-600 bg-slate-900/40 text-slate-300 hover:border-slate-500 hover:bg-slate-700'
            }`}
          >
            {option.number} · {option.label}
          </button>
        ))}
        {review && (
          <Link
            to={`/reviews/${review.review_id}`}
            className="rounded border border-slate-600 bg-slate-900/40 px-2 py-1 text-slate-300 hover:border-slate-500 hover:bg-slate-700"
          >
            6 · Gate controls
          </Link>
        )}
        {selectedGraphNode && (
          <button type="button" onClick={resetView} className="ml-auto text-sky-400 hover:text-sky-300">
            Clear node filter: {selectedGraphNode.label} ×
          </button>
        )}
      </section>

      <ReviewPipeline status={review?.status ?? ''} gates={gates} />

      {error && (
        <div className="border-b border-red-800 bg-red-950/70 px-5 py-2 text-xs text-red-300">{error}</div>
      )}

      <main className="grid h-0 min-h-0 flex-1 grid-cols-[minmax(0,1.5fr)_minmax(390px,1fr)] overflow-hidden">
        <section className="flex min-h-0 min-w-0 flex-col overflow-hidden border-r border-slate-700 bg-[#050914]">
          <div className="border-b border-slate-800 bg-slate-900/80 px-4 py-2">
            <div className="mb-1.5 flex items-center gap-3 text-[10px]">
              <span className="font-bold uppercase tracking-wider text-slate-300">Coverage graph</span>
              <span className="text-slate-500">Finding-to-node mapping</span>
              <span className="ml-auto rounded border border-sky-700 bg-sky-950/70 px-1.5 py-0.5 font-semibold text-sky-300">HTML graph</span>
              <span className="text-slate-400">{graphData.nodes.length} vertices · {graphData.links.length} edges</span>
              <button type="button" onClick={resetView} className="text-sky-400 hover:text-sky-300">Reset view</button>
            </div>
            <div className="flex items-center gap-3">
              <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-800">
                <div
                  className={`h-full rounded-full ${mappingCoverage === 100 ? 'bg-emerald-400' : mappingCoverage >= 70 ? 'bg-amber-400' : 'bg-red-400'}`}
                  style={{ width: `${mappingCoverage}%` }}
                />
              </div>
              <span className="w-28 text-right text-[10px] font-semibold text-slate-300">
                {mappingCoverage}% mapped
              </span>
            </div>
          </div>

          <div className="relative min-h-0 flex-1 overflow-hidden">
            {graphLoading && (
              <div className="absolute inset-0 z-10 flex items-center justify-center text-xs text-slate-400">Loading ontology graph…</div>
            )}
            {!graphLoading && !graphAvailable && (
              <div className="absolute inset-0 z-10 flex items-center justify-center text-xs text-slate-400">Neptune graph is not configured or is unreachable.</div>
            )}
            {!graphLoading && graphAvailable && graphData.nodes.length === 0 && (
              <div className="absolute inset-0 z-10 flex items-center justify-center text-xs text-slate-400">No ontology graph data is available.</div>
            )}
            {!graphLoading && graphPaintReady && graphAvailable && graphData.nodes.length > 0 && (() => {
                const nodesById = new Map(graphData.nodes.map((node) => [node.id, node]))
                const xValues = graphData.nodes.map((node) => node.x ?? 0)
                const yValues = graphData.nodes.map((node) => node.y ?? 0)
                const minX = Math.min(...xValues) - 70
                const maxX = Math.max(...xValues) + 70
                const minY = Math.min(...yValues) - 55
                const maxY = Math.max(...yValues) + 60

                return (
                  <svg
                    data-testid="ontology-graph-svg"
                    className="h-full min-h-[360px] w-full bg-[#050914]"
                    viewBox={`${minX} ${minY} ${Math.max(1, maxX - minX)} ${Math.max(1, maxY - minY)}`}
                    preserveAspectRatio="xMidYMid meet"
                    role="img"
                    aria-label={`Ontology graph with ${graphData.nodes.length} vertices and ${graphData.links.length} edges`}
                    onClick={() => {
                      setSelectedNodeId(null)
                      setSelectedFindingId(null)
                    }}
                  >
                    <defs>
                      <marker id="graph-arrow" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto" markerUnits="strokeWidth">
                        <path d="M0,0 L7,3.5 L0,7 Z" fill="#64748b" />
                      </marker>
                      <marker id="graph-arrow-active" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto" markerUnits="strokeWidth">
                        <path d="M0,0 L7,3.5 L0,7 Z" fill="#38bdf8" />
                      </marker>
                    </defs>

                    <g aria-label="Graph edges">
                      {graphData.links.map((link, index) => {
                        const sourceId = graphEndpointId(link.source)
                        const targetId = graphEndpointId(link.target)
                        const source = nodesById.get(sourceId)
                        const target = nodesById.get(targetId)
                        if (!source || !target) return null
                        const highlighted = relatedNodeIds.has(sourceId) && relatedNodeIds.has(targetId)
                        return (
                          <line
                            key={`${sourceId}-${targetId}-${link.label}-${index}`}
                            x1={source.x ?? 0}
                            y1={source.y ?? 0}
                            x2={target.x ?? 0}
                            y2={target.y ?? 0}
                            stroke={highlighted ? '#38bdf8' : hasGraphSelection ? '#162033' : '#475569'}
                            strokeWidth={highlighted ? 2 : 1}
                            opacity={hasGraphSelection && !highlighted ? 0.3 : 0.8}
                            markerEnd={highlighted ? 'url(#graph-arrow-active)' : 'url(#graph-arrow)'}
                            vectorEffect="non-scaling-stroke"
                          >
                            <title>{link.label}</title>
                          </line>
                        )
                      })}
                    </g>

                    <g aria-label="Graph vertices" display="none">
                      {graphData.nodes.map((node) => {
                        const x = node.x ?? 0
                        const y = node.y ?? 0
                        const color = NODE_COLORS[node.type] ?? NODE_COLORS.Unknown
                        const isRelated = relatedNodeIds.has(node.id)
                        const isSelected = selectedNodeId === node.id
                        const dimmed = hasGraphSelection && !isRelated && !isSelected
                        const label = node.label.length > 24 ? `${node.label.slice(0, 24)}…` : node.label
                        const hexPoints = Array.from({ length: 6 }, (_, index) => {
                          const angle = (Math.PI / 3) * index
                          return `${x + Math.cos(angle) * 9},${y + Math.sin(angle) * 9}`
                        }).join(' ')

                        return (
                          <g
                            key={node.id}
                            role="button"
                            tabIndex={0}
                            aria-label={`${node.label}, ${node.type}`}
                            className="cursor-pointer outline-none"
                            opacity={dimmed ? 0.18 : 1}
                            onClick={(event) => {
                              event.stopPropagation()
                              setSelectedNodeId(isSelected ? null : node.id)
                              setSelectedFindingId(null)
                            }}
                            onKeyDown={(event) => {
                              if (event.key !== 'Enter' && event.key !== ' ') return
                              event.preventDefault()
                              setSelectedNodeId(isSelected ? null : node.id)
                              setSelectedFindingId(null)
                            }}
                          >
                            <title>{`${node.label} · ${node.type}`}</title>
                            {(isSelected || isRelated) && (
                              <circle cx={x} cy={y} r="13" fill="none" stroke="#7dd3fc" strokeWidth="2" vectorEffect="non-scaling-stroke" />
                            )}
                            {node.type === 'Component' ? (
                              <rect x={x - 15} y={y - 8} width="30" height="16" rx="3" fill={color} />
                            ) : node.type === 'FailureMode' ? (
                              <polygon points={`${x},${y - 10} ${x + 10},${y} ${x},${y + 10} ${x - 10},${y}`} fill={color} />
                            ) : node.type === 'Standard' || node.type === 'Process' ? (
                              <polygon points={hexPoints} fill={color} />
                            ) : (
                              <circle cx={x} cy={y} r="7" fill={color} />
                            )}
                            <text
                              x={x}
                              y={y + 20}
                              textAnchor="middle"
                              dominantBaseline="middle"
                              fill="#e2e8f0"
                              fontSize="9"
                              fontWeight="600"
                              fontFamily="Inter, system-ui, sans-serif"
                              stroke="#050914"
                              strokeWidth="3"
                              paintOrder="stroke"
                            >
                              {label}
                            </text>
                          </g>
                        )
                      })}
                    </g>
                  </svg>
                )
              })()}
            {!graphLoading && graphPaintReady && graphAvailable && graphData.nodes.length > 0 && (() => {
              const xValues = graphData.nodes.map((node) => node.x ?? 0)
              const yValues = graphData.nodes.map((node) => node.y ?? 0)
              const minX = Math.min(...xValues)
              const maxX = Math.max(...xValues)
              const minY = Math.min(...yValues)
              const maxY = Math.max(...yValues)
              const xSpan = Math.max(1, maxX - minX)
              const ySpan = Math.max(1, maxY - minY)
              const horizontalPadding = 70
              const topPadding = 55
              const bottomPadding = 60
              const paddedXSpan = xSpan + horizontalPadding * 2
              const paddedYSpan = ySpan + topPadding + bottomPadding

              return (
                <div
                  data-testid="ontology-graph-html-nodes"
                  className="absolute inset-0 z-[2]"
                  aria-label={`Ontology graph nodes: ${graphData.nodes.length}`}
                >
                  <div className="pointer-events-none absolute left-3 top-3 rounded border border-slate-700 bg-slate-900/90 px-2 py-1 text-[9px] font-semibold text-slate-400">
                    Live ontology · {graphData.nodes.length} nodes
                  </div>
                  {graphData.nodes.map((node) => {
                    const isRelated = relatedNodeIds.has(node.id)
                    const isSelected = selectedNodeId === node.id
                    const isDimmed = hasGraphSelection && !isRelated && !isSelected
                    const left = ((((node.x ?? 0) - minX) + horizontalPadding) / paddedXSpan) * 100
                    const top = ((((node.y ?? 0) - minY) + topPadding) / paddedYSpan) * 100
                    const color = NODE_COLORS[node.type] ?? NODE_COLORS.Unknown

                    return (
                      <button
                        key={node.id}
                        type="button"
                        title={`${node.label} · ${node.type}`}
                        onClick={() => {
                          setSelectedNodeId(isSelected ? null : node.id)
                          setSelectedFindingId(null)
                        }}
                        className={`absolute flex -translate-x-1/2 -translate-y-1/2 flex-col items-center gap-1 rounded px-1.5 py-1 text-center transition-all ${
                          isSelected || isRelated
                            ? 'z-20 bg-sky-950/95 ring-2 ring-sky-300'
                            : 'z-10 bg-slate-900/90 hover:bg-slate-800 hover:ring-1 hover:ring-slate-500'
                        }`}
                        style={{ left: `${left}%`, top: `${top}%`, opacity: isDimmed ? 0.2 : 1 }}
                      >
                        <span
                          className={`${node.type === 'FailureMode' ? 'rotate-45' : node.type === 'Component' ? 'rounded-sm' : 'rounded-full'} h-3.5 w-3.5 border border-white/30 shadow-lg`}
                          style={{ backgroundColor: color }}
                        />
                        <span className="max-w-28 whitespace-nowrap text-[9px] font-semibold leading-none text-slate-100">
                          {node.label.length > 22 ? `${node.label.slice(0, 22)}…` : node.label}
                        </span>
                      </button>
                    )
                  })}
                </div>
              )
            })()}
          </div>

          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-slate-800 bg-slate-900/80 px-4 py-2 text-[9px] text-slate-400">
            <span className="font-semibold uppercase tracking-wider text-slate-500">Legend</span>
            {['Component', 'Material', 'FailureMode', 'Standard', 'Process'].map((type) => (
              <span key={type} className="flex items-center gap-1">
                <span className="h-2.5 w-2.5 rounded-sm" style={{ backgroundColor: NODE_COLORS[type] }} />
                {type}
              </span>
            ))}
            <span className="ml-auto">Click a finding to trace matching nodes · Click a node to filter findings</span>
          </div>
        </section>

        <aside className="flex min-h-0 min-w-0 flex-col overflow-hidden bg-slate-900">
          <div className="border-b border-slate-700 px-3 py-2">
            <div className="flex items-center gap-2">
              <h2 className="text-[11px] font-bold uppercase tracking-wider text-white">Findings ({filteredFindings.length})</h2>
              <span className="ml-auto text-[9px] text-slate-400">
                {apCounts.H ?? 0} high · {apCounts.M ?? 0} medium · {apCounts.L ?? 0} low
              </span>
            </div>
            <div className="mt-2 flex flex-wrap gap-1">
              <button
                type="button"
                onClick={() => setAgentFilter('all')}
                className={`rounded px-2 py-0.5 text-[9px] ${agentFilter === 'all' ? 'bg-sky-500 text-white' : 'bg-slate-800 text-slate-400 hover:text-white'}`}
              >
                All agents
              </button>
              {agents.map((agent) => (
                <button
                  key={agent}
                  type="button"
                  onClick={() => setAgentFilter(agent)}
                  className={`rounded px-2 py-0.5 text-[9px] ${agentFilter === agent ? 'bg-sky-500 text-white' : 'bg-slate-800 text-slate-400 hover:text-white'}`}
                >
                  {AGENT_LABELS[agent] ?? agent}
                </button>
              ))}
            </div>
            <div className="mt-1.5 flex gap-1">
              {(['all', 'H', 'M', 'L'] as ApFilter[]).map((ap) => (
                <button
                  key={ap}
                  type="button"
                  onClick={() => setApFilter(ap)}
                  className={`rounded border px-2 py-0.5 text-[9px] ${apFilter === ap ? 'border-slate-400 bg-slate-700 text-white' : 'border-slate-700 text-slate-500 hover:text-slate-300'}`}
                >
                  {ap === 'all' ? 'All priorities' : `AP ${ap}`}
                </button>
              ))}
            </div>
          </div>

          <div className="min-h-0 flex-1 space-y-1.5 overflow-y-auto p-2">
            {reviewLoading && <p className="p-4 text-center text-xs text-slate-500">Loading findings…</p>}
            {!reviewLoading && findingsError && (
              <div className="rounded border border-red-800 bg-red-950/50 p-4 text-center text-xs text-red-300">
                Findings could not be loaded. Readiness metrics are unavailable.
              </div>
            )}
            {!reviewLoading && !findingsError && filteredFindings.length === 0 && (
              <div className="rounded border border-dashed border-slate-700 p-5 text-center text-xs text-slate-500">
                No findings match the current graph and filters.
              </div>
            )}
            {filteredFindings.map((finding) => {
              const selected = finding.finding_id === selectedFindingId
              const cited = hasCitation(finding)
              const apClass = finding.action_priority === 'H'
                ? 'border-l-red-500'
                : finding.action_priority === 'M'
                  ? 'border-l-amber-400'
                  : 'border-l-emerald-500'
              const apBadge = finding.action_priority === 'H'
                ? 'bg-red-500 text-white'
                : finding.action_priority === 'M'
                  ? 'bg-amber-400 text-slate-950'
                  : 'bg-emerald-500 text-white'

              return (
                <button
                  key={finding.finding_id}
                  type="button"
                  onClick={() => {
                    setSelectedFindingId(selected ? null : finding.finding_id)
                    setSelectedNodeId(null)
                    setFocusMode('trust')
                  }}
                  className={`w-full border border-l-4 p-2.5 text-left transition-colors ${apClass} ${
                    selected
                      ? 'border-sky-400 bg-sky-950/60 ring-1 ring-sky-400/40'
                      : 'border-y-slate-700 border-r-slate-700 bg-slate-800/90 hover:bg-slate-800'
                  }`}
                >
                  <div className="mb-1 flex items-center gap-1.5">
                    <span className="font-mono text-[9px] font-bold text-slate-300">{finding.finding_id}</span>
                    <span className={`ml-auto rounded px-1.5 py-0.5 text-[8px] font-bold ${apBadge}`}>AP {finding.action_priority}</span>
                  </div>
                  <p className="line-clamp-2 text-[11px] font-semibold leading-4 text-slate-100">{finding.description}</p>
                  <p className="mt-1 truncate text-[9px] text-slate-400">
                    {finding.affected_component || 'Component not specified'} · {AGENT_LABELS[finding.agent] ?? finding.agent}
                  </p>
                  <div className="mt-1.5 flex items-center gap-2 text-[8px]">
                    {finding.severity != null && (
                      <span className="text-slate-500">S{finding.severity} · O{finding.occurrence ?? '—'} · D{finding.detection ?? '—'}</span>
                    )}
                    <span className={cited ? 'text-emerald-400' : 'text-amber-400'}>
                      {cited ? `✓ ${finding.standard_citations?.join(', ')}` : '△ Citation not recorded'}
                    </span>
                    {finding.confidence != null && (
                      <span className="ml-auto text-slate-500">{Math.round(finding.confidence * 100)}%</span>
                    )}
                  </div>
                  {selected && (
                    <div className="mt-2 border-t border-slate-700 pt-2 text-[9px] leading-4 text-slate-400">
                      Matching component and citation nodes are highlighted in the graph. This trace is inferred from existing labels because canonical finding paths are not yet available.
                    </div>
                  )}
                </button>
              )
            })}
          </div>

          <div className="border-t border-slate-700 bg-slate-950/70 p-3">
            <div className="rounded border border-slate-700 bg-slate-900 p-3">
              <div className="mb-2 flex items-center gap-2">
                <span className={`h-2 w-2 rounded-full ${activeGate?.pending ? 'bg-amber-400' : activeGate?.status === 'APPROVED' ? 'bg-emerald-400' : 'bg-slate-500'}`} />
                <h3 className="text-[10px] font-bold uppercase tracking-wider text-white">Review readiness</h3>
                <span className="ml-auto text-[9px] font-semibold text-slate-400">
                  {gatesError
                    ? 'Gate data unavailable'
                    : activeGateNumber
                      ? `Gate ${activeGateNumber} · ${activeGate?.status.replace(/_/g, ' ')}`
                      : 'No active gate'}
                </span>
              </div>
              <div className="grid grid-cols-3 gap-2 text-center">
                <div className="rounded bg-slate-800 px-2 py-1.5">
                  <p className="text-sm font-bold text-white">{findingsError ? '—' : `${mappingCoverage}%`}</p>
                  <p className="text-[8px] uppercase text-slate-500">Mapped</p>
                </div>
                <div className="rounded bg-slate-800 px-2 py-1.5">
                  <p className="text-sm font-bold text-white">{findingsError ? '—' : `${citedFindingCount}/${findings.length}`}</p>
                  <p className="text-[8px] uppercase text-slate-500">Cited</p>
                </div>
                <div className="rounded bg-slate-800 px-2 py-1.5">
                  <p className="text-sm font-bold text-white">{findingsError ? '—' : findings.length}</p>
                  <p className="text-[8px] uppercase text-slate-500">Findings</p>
                </div>
              </div>
              {review && (
                <Link
                  to={`/reviews/${review.review_id}`}
                  className="mt-2 block w-full rounded bg-sky-600 px-3 py-1.5 text-center text-[10px] font-semibold text-white hover:bg-sky-500"
                >
                  {activeGateNumber ? `Open Gate ${activeGateNumber} controls →` : 'Open review controls →'}
                </Link>
              )}
              <p className="mt-1.5 text-[8px] leading-3 text-slate-500">
                Mapping and citation counts use current labels and stored citations. They are not SHACL or template-coverage results.
              </p>
            </div>
          </div>
        </aside>
      </main>
    </div>
  )
}
