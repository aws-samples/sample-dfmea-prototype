import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import StatusBadge from '../components/StatusBadge'
import { apiGet } from '../lib/api'

interface ReviewSummary {
  review_id: string
  assembly_name: string
  status: string
  created_at: string | null
  row_count: number | string | null
  material: string | null
  finding_count: number
  max_rpn: number | null
  gates: Record<'1' | '2' | '3' | '4', string | null>
}

interface ConfidenceDistribution {
  high: number
  medium: number
  low: number
  total: number
  average: number | null
}

interface Metrics {
  ap_counts: Record<'H' | 'M' | 'L', number>
  ap_trends: Record<'H' | 'M' | 'L', number>
  trend_basis: 'review_created_at'
  current_week_counts: Record<'H' | 'M' | 'L', number>
  previous_week_counts: Record<'H' | 'M' | 'L', number>
  total: number
  reviews_30d: number
  active_reviews: number
  complete_reviews: number
  agents_online: number | null
  confidence_distribution: ConfidenceDistribution
  reviews: ReviewSummary[]
}

interface TopFailureMode {
  failure_mode: string
  rpn: number
  count: number
  action_priority: 'H' | 'M' | 'L' | null
}

interface OntologyHealth {
  entities: number
  relations: number
  materials: number
  processes: number
  failure_mechs: number
  version: string | null
  recent_updates: string[]
  available: boolean
}

interface AgentActivityEvent {
  timestamp: string | null
  assembly_name: string
  event: string
  review_id: string
}

interface FindingsOverTimePoint {
  week: string
  week_start: string
  H: number
  M: number
  L: number
}

interface FindingsOverTimeResponse {
  basis: 'review_created_at'
  weeks: FindingsOverTimePoint[]
}

interface SeverityOccurrencePoint {
  sev_band: string
  [key: string]: number | string
}

interface SeverityOccurrenceResponse {
  matrix: SeverityOccurrencePoint[]
  occurrence_bands: string[]
}

interface HitlDecision {
  review_id: string
  assembly_name: string
  gate: string
  decision: 'APPROVED' | 'REJECTED'
  comment: string
  reviewer: string | null
  decision_timestamp: string | null
  record_updated_at: string | null
  finding: string | null
  agent_confidence: number | null
}

interface HitlResponse {
  decisions: HitlDecision[]
  total: number
}

const AP_COLORS: Record<'H' | 'M' | 'L', string> = {
  H: '#ef2b2d',
  M: '#f59e0b',
  L: '#16a34a',
}

const AP_LABELS: Record<'H' | 'M' | 'L', string> = {
  H: 'HIGH ACTION PRIORITY',
  M: 'MEDIUM ACTION PRIORITY',
  L: 'LOW ACTION PRIORITY',
}

const MATRIX_COLORS = ['#bbf7d0', '#fde68a', '#fca5a5', '#ef4444']

function formatTimestamp(value: string | null, includeDate = true) {
  if (!value) return '—'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return '—'
  return parsed.toLocaleString([], includeDate
    ? { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }
    : { hour: '2-digit', minute: '2-digit' })
}

function EmptyValue() {
  return <span className="text-gray-300">—</span>
}

function PanelHeader({ title, action }: { title: string; action?: React.ReactNode }) {
  return (
    <div className="mb-4 flex items-center justify-between gap-3">
      <h2 className="text-sm font-bold text-gray-950">{title}</h2>
      {action}
    </div>
  )
}

function LoadingBlock() {
  return <div className="flex min-h-24 items-center justify-center text-xs text-gray-400">Loading...</div>
}

function UnavailableBlock({ message }: { message: string }) {
  return <div className="flex min-h-24 items-center justify-center text-center text-xs text-gray-400">{message}</div>
}

function TrendLabel({ delta }: { delta: number }) {
  if (delta === 0) {
    return <span className="text-xs text-gray-500">No change vs prior submission week</span>
  }
  const positive = delta > 0
  return (
    <span className={positive ? 'text-xs font-medium text-red-600' : 'text-xs font-medium text-green-700'}>
      {positive ? '+' : ''}{delta} vs prior submission week
    </span>
  )
}

function KpiCard({
  title,
  value,
  accent,
  children,
}: {
  title: string
  value: number | string | null | undefined
  accent: string
  children?: React.ReactNode
}) {
  return (
    <div className="card relative min-h-32 overflow-hidden p-4">
      <p className="text-[11px] font-bold uppercase tracking-wide text-gray-600">{title}</p>
      <p className="mt-2 text-3xl font-bold leading-none text-gray-950">{value ?? '—'}</p>
      <div className="mt-3 min-h-5">{children}</div>
      <div className="absolute inset-x-4 bottom-3 h-1 overflow-hidden rounded-full bg-gray-100">
        <div className="h-full w-2/5 rounded-full" style={{ backgroundColor: accent }} />
      </div>
    </div>
  )
}

function GateStepper({ gates }: { gates: ReviewSummary['gates'] }) {
  return (
    <div className="flex items-center gap-1" aria-label="Four review gates">
      {(['1', '2', '3', '4'] as const).map((gate) => {
        const status = gates[gate]
        const classes = status === 'APPROVED'
          ? 'border-green-600 bg-green-600 text-white'
          : status === 'REJECTED'
            ? 'border-red-600 bg-red-600 text-white'
            : status === 'PENDING'
              ? 'border-amber-400 bg-amber-100 text-amber-800'
              : 'border-gray-200 bg-gray-100 text-gray-400'
        return (
          <span key={gate} className={`inline-flex h-6 w-6 items-center justify-center rounded border text-[10px] font-bold ${classes}`}>
            {gate}
          </span>
        )
      })}
    </div>
  )
}

function RpnBadge({ value }: { value: number | null }) {
  if (value === null) return <EmptyValue />
  const classes = value >= 200
    ? 'bg-red-50 text-red-700'
    : value >= 100
      ? 'bg-amber-50 text-amber-700'
      : 'bg-green-50 text-green-700'
  return <span className={`rounded px-2 py-1 text-xs font-bold ${classes}`}>{value}</span>
}

export default function DashboardPage() {
  const [lastUpdated, setLastUpdated] = useState(new Date())
  const [metrics, setMetrics] = useState<Metrics | null>(null)
  const [topFailureModes, setTopFailureModes] = useState<TopFailureMode[] | null>(null)
  const [ontologyHealth, setOntologyHealth] = useState<OntologyHealth | null>(null)
  const [agentActivity, setAgentActivity] = useState<AgentActivityEvent[] | null>(null)
  const [findingsOverTime, setFindingsOverTime] = useState<FindingsOverTimeResponse | null>(null)
  const [severityMatrix, setSeverityMatrix] = useState<SeverityOccurrenceResponse | null>(null)
  const [hitl, setHitl] = useState<HitlResponse | null>(null)
  const [errors, setErrors] = useState<Record<string, boolean>>({})

  useEffect(() => {
    const load = async <T,>(key: string, path: string, setter: (value: T) => void) => {
      try {
        setter(await apiGet<T>(path))
      } catch {
        setErrors((current) => ({ ...current, [key]: true }))
      }
    }

    void Promise.all([
      load<Metrics>('metrics', '/admin/metrics', setMetrics),
      load<{ failure_modes: TopFailureMode[] }>('failureModes', '/admin/top-failure-modes', (data) => setTopFailureModes(data.failure_modes)),
      load<OntologyHealth>('ontology', '/ontology/health', setOntologyHealth),
      load<{ activity: AgentActivityEvent[] }>('activity', '/admin/agent-activity', (data) => setAgentActivity(data.activity)),
      load<FindingsOverTimeResponse>('timeline', '/admin/findings-over-time', setFindingsOverTime),
      load<SeverityOccurrenceResponse>('matrix', '/admin/severity-occurrence-matrix', setSeverityMatrix),
      load<HitlResponse>('hitl', '/admin/hitl-decisions', setHitl),
    ]).finally(() => setLastUpdated(new Date()))
  }, [])

  const pieData = useMemo(() => {
    if (!metrics) return []
    return (['H', 'M', 'L'] as const).map((key) => ({
      key,
      name: key === 'H' ? 'High' : key === 'M' ? 'Medium' : 'Low',
      value: metrics.ap_counts[key],
    }))
  }, [metrics])

  const confidencePercent = (count: number) => {
    const total = metrics?.confidence_distribution.total ?? 0
    return total ? Math.round((count / total) * 100) : 0
  }

  return (
    <div className="space-y-5 pb-12">
      <header className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-950">Dashboard</h1>
          <p className="mt-1 text-xs text-gray-500">
            Last updated: {lastUpdated.toLocaleString([], { month: 'short', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit' })}
            {' · '}{metrics?.active_reviews ?? '—'} active reviews
            {' · '}Ontology {ontologyHealth?.version ?? 'version unavailable'}
            {' · '}Agents online {metrics?.agents_online ?? '—'}
          </p>
        </div>
        <Link to="/upload" className="btn-primary">+ New Review</Link>
      </header>

      <section className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-5">
        {(['H', 'M', 'L'] as const).map((priority) => (
          <KpiCard
            key={priority}
            title={AP_LABELS[priority]}
            value={metrics?.ap_counts[priority]}
            accent={AP_COLORS[priority]}
          >
            {metrics && <TrendLabel delta={metrics.ap_trends[priority]} />}
          </KpiCard>
        ))}
        <KpiCard title="TOTAL FINDINGS" value={metrics?.total} accent="#1e3a5f">
          <span className="text-xs text-gray-500">
            across {metrics?.complete_reviews ?? '—'} completed reviews
          </span>
        </KpiCard>
        <KpiCard title="REVIEWS (30 DAYS)" value={metrics?.reviews_30d} accent="#7c3aed">
          <span className="text-xs text-gray-500">
            {metrics?.complete_reviews ?? '—'} complete · {metrics?.active_reviews ?? '—'} active
          </span>
        </KpiCard>
      </section>

      <section className="grid grid-cols-1 gap-5 xl:grid-cols-5">
        <div className="card overflow-hidden p-4 xl:col-span-3">
          <PanelHeader
            title="Recent Reviews"
            action={<Link to="/reviews" className="text-xs font-semibold text-gray-700 hover:text-blue-700">View history →</Link>}
          />
          {errors.metrics ? (
            <UnavailableBlock message="Review data unavailable" />
          ) : !metrics ? (
            <LoadingBlock />
          ) : metrics.reviews.length === 0 ? (
            <UnavailableBlock message="No reviews stored" />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[720px] text-xs">
                <thead>
                  <tr className="border-b-2 border-gray-700 text-left text-[10px] font-bold uppercase tracking-wide text-gray-600">
                    <th className="px-2 pb-2">Component</th>
                    <th className="px-2 pb-2">Material</th>
                    <th className="px-2 pb-2 text-center">Modes</th>
                    <th className="px-2 pb-2 text-center">Max RPN</th>
                    <th className="px-2 pb-2">Gates</th>
                    <th className="px-2 pb-2">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {metrics.reviews.map((review) => (
                    <tr key={review.review_id} className="border-b border-gray-200 align-middle hover:bg-gray-50">
                      <td className="px-2 py-3 font-semibold text-gray-900">
                        <Link to={`/reviews/${review.review_id}`} className="hover:text-blue-700 hover:underline">
                          {review.assembly_name || 'Unnamed review'}
                        </Link>
                        <div className="mt-0.5 font-normal text-gray-400">{review.row_count ?? '—'} DFMEA rows</div>
                      </td>
                      <td className="max-w-28 px-2 py-3 text-gray-600">{review.material || <EmptyValue />}</td>
                      <td className="px-2 py-3 text-center font-semibold">{review.finding_count}</td>
                      <td className="px-2 py-3 text-center"><RpnBadge value={review.max_rpn} /></td>
                      <td className="px-2 py-3"><GateStepper gates={review.gates} /></td>
                      <td className="px-2 py-3"><StatusBadge status={review.status} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <div className="card p-4 xl:col-span-2">
          <PanelHeader title="Findings by Action Priority" />
          {errors.metrics ? (
            <UnavailableBlock message="Action-priority data unavailable" />
          ) : !metrics ? (
            <LoadingBlock />
          ) : (
            <>
              <div className="grid grid-cols-1 items-center gap-3 sm:grid-cols-2">
                <ResponsiveContainer width="100%" height={220}>
                  <PieChart>
                    <Pie data={pieData} dataKey="value" nameKey="name" cx="50%" cy="50%" innerRadius={52} outerRadius={84} paddingAngle={2}>
                      {pieData.map((entry) => <Cell key={entry.key} fill={AP_COLORS[entry.key]} />)}
                    </Pie>
                    <Tooltip />
                  </PieChart>
                </ResponsiveContainer>
                <div className="space-y-4">
                  {pieData.map((entry) => {
                    const percentage = metrics.total ? Math.round((entry.value / metrics.total) * 100) : 0
                    return (
                      <div key={entry.key} className="flex items-start gap-2">
                        <span className="mt-1 h-2.5 w-2.5 rounded-full" style={{ backgroundColor: AP_COLORS[entry.key] }} />
                        <div>
                          <p className="text-xs font-semibold text-gray-800">{entry.name}</p>
                          <p className="text-xs text-gray-500">{entry.value} findings ({percentage}%)</p>
                        </div>
                      </div>
                    )
                  })}
                </div>
              </div>
              <div className="border-t border-gray-200 pt-4">
                <p className="text-xs font-semibold text-gray-500">Agent Confidence Distribution</p>
                <div className="mt-3 grid grid-cols-3 text-center">
                  {[
                    { label: 'HIGH (≥0.85)', count: metrics.confidence_distribution.high, color: '#16a34a' },
                    { label: 'MED (0.60-0.849)', count: metrics.confidence_distribution.medium, color: '#f59e0b' },
                    { label: 'LOW (<0.60)', count: metrics.confidence_distribution.low, color: '#ef4444' },
                  ].map((item) => (
                    <div key={item.label}>
                      <p className="text-2xl font-bold" style={{ color: item.color }}>{confidencePercent(item.count)}%</p>
                      <p className="mt-1 text-[10px] font-bold text-gray-600">{item.label}</p>
                      <p className="text-[10px] text-gray-400">{item.count} findings</p>
                    </div>
                  ))}
                </div>
              </div>
            </>
          )}
        </div>
      </section>

      <section className="grid grid-cols-1 gap-5 xl:grid-cols-3">
        <div className="card p-4">
          <PanelHeader title="Top Failure Modes (by RPN)" />
          {errors.failureModes ? (
            <UnavailableBlock message="Failure-mode data unavailable" />
          ) : !topFailureModes ? (
            <LoadingBlock />
          ) : topFailureModes.length === 0 ? (
            <UnavailableBlock message="No rated failure modes stored" />
          ) : (
            <ol className="space-y-3">
              {topFailureModes.map((mode, index) => {
                const maxRpn = Math.max(...topFailureModes.map((item) => item.rpn), 1)
                const color = mode.action_priority ? AP_COLORS[mode.action_priority] : '#9ca3af'
                return (
                  <li key={mode.failure_mode} className="grid grid-cols-[24px_1fr_48px] items-center gap-2">
                    <span className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-gray-100 text-[10px] font-bold text-gray-600">{index + 1}</span>
                    <div className="min-w-0">
                      <div className="flex items-center justify-between gap-2">
                        <span className="truncate text-xs font-medium text-gray-800" title={mode.failure_mode}>{mode.failure_mode}</span>
                        <span className="text-[10px] text-gray-400">{mode.count}x</span>
                      </div>
                      <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-gray-100">
                        <div className="h-full rounded-full" style={{ width: `${Math.round((mode.rpn / maxRpn) * 100)}%`, backgroundColor: color }} />
                      </div>
                    </div>
                    <span className="text-right text-xs font-bold text-gray-800">{mode.rpn}</span>
                  </li>
                )
              })}
            </ol>
          )}
        </div>

        <div className="card p-4">
          <PanelHeader title="Ontology Health" />
          {errors.ontology ? (
            <UnavailableBlock message="Ontology health unavailable" />
          ) : !ontologyHealth ? (
            <LoadingBlock />
          ) : !ontologyHealth.available ? (
            <UnavailableBlock message="Ontology data unavailable" />
          ) : (
            <>
              <div className="grid grid-cols-2 gap-x-8 gap-y-5">
                {[
                  ['ENTITIES', ontologyHealth.entities],
                  ['RELATIONS', ontologyHealth.relations],
                  ['MATERIALS', ontologyHealth.materials],
                  ['PROCESSES', ontologyHealth.processes],
                  ['FAILURE MECHS', ontologyHealth.failure_mechs],
                  ['VERSION', ontologyHealth.version],
                ].map(([label, value]) => (
                  <div key={String(label)}>
                    <p className="text-[10px] font-bold text-gray-500">{label}</p>
                    <p className="mt-1 text-xl font-bold text-gray-950">{value ?? <EmptyValue />}</p>
                  </div>
                ))}
              </div>
              <div className="mt-5 border-t border-gray-200 pt-4">
                <p className="text-xs font-semibold text-gray-500">Recent Ontology Updates</p>
                {ontologyHealth.recent_updates.length > 0 ? (
                  <ul className="mt-2 space-y-1">
                    {ontologyHealth.recent_updates.map((update) => <li key={update} className="text-xs text-gray-600">{update}</li>)}
                  </ul>
                ) : (
                  <p className="mt-2 text-xs text-gray-400">No update history captured.</p>
                )}
              </div>
            </>
          )}
        </div>

        <div className="card p-4">
          <PanelHeader title="Agent Activity Log" />
          {errors.activity ? (
            <UnavailableBlock message="Activity data unavailable" />
          ) : !agentActivity ? (
            <LoadingBlock />
          ) : agentActivity.length === 0 ? (
            <UnavailableBlock message="No persisted activity" />
          ) : (
            <>
              <div className="max-h-72 divide-y divide-gray-200 overflow-y-auto">
                {agentActivity.map((event) => (
                  <div key={`${event.review_id}-${event.timestamp}`} className="grid grid-cols-[52px_1fr] gap-2 py-2">
                    <span className="text-[10px] font-bold text-gray-700">{formatTimestamp(event.timestamp, false)}</span>
                    <div>
                      <p className="text-xs font-semibold text-gray-800">{event.assembly_name}</p>
                      <p className="text-xs text-gray-600">{event.event}</p>
                    </div>
                  </div>
                ))}
              </div>
              <p className="mt-3 border-t border-gray-100 pt-3 text-[10px] text-gray-400">Detailed per-agent events are not currently persisted.</p>
            </>
          )}
        </div>
      </section>

      <section className="grid grid-cols-1 gap-5 xl:grid-cols-2">
        <div className="card p-4">
          <PanelHeader title="Findings by Review Submission Week (Last 8 Weeks)" />
          {errors.timeline ? (
            <UnavailableBlock message="Weekly findings unavailable" />
          ) : !findingsOverTime ? (
            <LoadingBlock />
          ) : (
            <ResponsiveContainer width="100%" height={260}>
              <LineChart data={findingsOverTime.weeks} margin={{ top: 12, right: 16, left: -10, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" vertical={false} />
                <XAxis dataKey="week" tick={{ fontSize: 10, fill: '#64748b' }} />
                <YAxis allowDecimals={false} tick={{ fontSize: 10, fill: '#64748b' }} />
                <Tooltip />
                <Line type="monotone" dataKey="H" name="High AP" stroke={AP_COLORS.H} strokeWidth={2} dot={{ r: 3 }} />
                <Line type="monotone" dataKey="M" name="Medium AP" stroke={AP_COLORS.M} strokeWidth={2} dot={{ r: 3 }} />
                <Line type="monotone" dataKey="L" name="Low AP" stroke={AP_COLORS.L} strokeWidth={2} dot={{ r: 3 }} />
              </LineChart>
            </ResponsiveContainer>
          )}
        </div>

        <div className="card p-4">
          <PanelHeader title="Severity × Occurrence Matrix" />
          {errors.matrix ? (
            <UnavailableBlock message="Severity-occurrence data unavailable" />
          ) : !severityMatrix ? (
            <LoadingBlock />
          ) : (
            <ResponsiveContainer width="100%" height={260}>
              <BarChart data={severityMatrix.matrix} margin={{ top: 12, right: 16, left: -10, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" vertical={false} />
                <XAxis dataKey="sev_band" tick={{ fontSize: 10, fill: '#64748b' }} />
                <YAxis allowDecimals={false} tick={{ fontSize: 10, fill: '#64748b' }} />
                <Tooltip />
                {severityMatrix.occurrence_bands.map((band, index) => (
                  <Bar key={band} dataKey={band} name={band} stackId="occurrence" fill={MATRIX_COLORS[index % MATRIX_COLORS.length]} />
                ))}
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>
      </section>

      <section className="card overflow-hidden p-4">
        <PanelHeader
          title="Recent Human-in-the-Loop Decisions"
          action={<Link to="/hitl-decisions" className="text-xs font-semibold text-gray-700 hover:text-blue-700">View all {hitl?.total ?? '—'} →</Link>}
        />
        {errors.hitl ? (
          <UnavailableBlock message="HITL decisions unavailable" />
        ) : !hitl ? (
          <LoadingBlock />
        ) : hitl.decisions.length === 0 ? (
          <UnavailableBlock message="No decisions stored" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[1000px] text-xs">
              <thead>
                <tr className="border-b-2 border-gray-700 text-left text-[10px] font-bold uppercase tracking-wide text-gray-600">
                  <th className="px-2 pb-2">Timestamp</th>
                  <th className="px-2 pb-2">Component</th>
                  <th className="px-2 pb-2">Finding</th>
                  <th className="px-2 pb-2">Agent confidence</th>
                  <th className="px-2 pb-2">Reviewer</th>
                  <th className="px-2 pb-2">Gate</th>
                  <th className="px-2 pb-2">Decision</th>
                  <th className="px-2 pb-2">Comment</th>
                </tr>
              </thead>
              <tbody>
                {hitl.decisions.slice(0, 10).map((decision) => (
                  <tr key={`${decision.review_id}-${decision.gate}`} className="border-b border-gray-200 hover:bg-gray-50">
                    <td className="whitespace-nowrap px-2 py-3 text-gray-500">{formatTimestamp(decision.decision_timestamp)}</td>
                    <td className="max-w-44 px-2 py-3 font-semibold text-gray-800">{decision.assembly_name}</td>
                    <td className="px-2 py-3 text-gray-500">{decision.finding || <EmptyValue />}</td>
                    <td className="px-2 py-3 text-gray-500">{decision.agent_confidence ?? <EmptyValue />}</td>
                    <td className="px-2 py-3 text-gray-500">{decision.reviewer || <EmptyValue />}</td>
                    <td className="px-2 py-3 text-gray-600">Gate {decision.gate}</td>
                    <td className="px-2 py-3">
                      <span className={`rounded px-2 py-1 text-[10px] font-bold ${decision.decision === 'APPROVED' ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'}`}>
                        {decision.decision === 'APPROVED' ? 'Approved' : 'Rejected'}
                      </span>
                    </td>
                    <td className="max-w-64 truncate px-2 py-3 text-gray-500" title={decision.comment}>{decision.comment || <EmptyValue />}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  )
}
