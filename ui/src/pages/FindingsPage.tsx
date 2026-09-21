import { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { useFindingsStore } from '../stores/findingsStore'
import ApBadge from '../components/ApBadge'
import SearchWidget from '../components/SearchWidget'

const AGENT_LABELS: Record<string, string> = {
  failure_mode: 'Failure Mode',
  structural: 'Structural',
  regulatory: 'Regulatory',
  other: 'Other',
  analyst: 'Analyst',
}

export default function FindingsPage() {
  const { reviewId } = useParams<{ reviewId: string }>()
  const { findings, loading, fetchFindings } = useFindingsStore()
  const [minAp, setMinAp] = useState<string>('')

  useEffect(() => {
    if (reviewId) fetchFindings(reviewId, minAp || undefined)
  }, [reviewId, minAp])

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <Link to={`/reviews/${reviewId}`} className="text-brand-600 text-sm hover:underline">
          ← Review
        </Link>
        <h1 className="text-2xl font-bold text-gray-900">Findings</h1>
      </div>

      {/* Filter */}
      <div className="flex items-center gap-3">
        <label className="text-sm text-gray-600">Min AP:</label>
        {['', 'H', 'M'].map((ap) => (
          <button
            key={ap}
            onClick={() => setMinAp(ap)}
            className={`px-3 py-1 rounded text-xs font-medium border transition-colors ${
              minAp === ap
                ? 'bg-brand-600 text-white border-brand-600'
                : 'bg-white text-gray-600 border-gray-300 hover:border-brand-400'
            }`}
          >
            {ap === '' ? 'All' : ap === 'H' ? 'High only' : 'Medium+'}
          </button>
        ))}
        <span className="text-xs text-gray-400 ml-auto">{findings.length} findings</span>
      </div>

      <SearchWidget />

      {loading && <p className="text-sm text-gray-400">Loading…</p>}

      <div className="space-y-3">
        {findings.length === 0 && !loading && (
          <p className="text-sm text-gray-400">No findings found for these filters.</p>
        )}
        {findings.map((f) => (
          <div key={f.finding_id} className="card p-4 space-y-1.5">
            <div className="flex items-start gap-3 justify-between">
              <div className="flex items-center gap-2 flex-wrap">
                <ApBadge ap={f.action_priority} />
                <span className="text-xs bg-gray-100 text-gray-600 px-2 py-0.5 rounded">
                  {AGENT_LABELS[f.agent] ?? f.agent}
                </span>
                <span className="text-xs text-gray-400">{f.finding_type.replace(/_/g, ' ')}</span>
              </div>
              <span className="text-xs text-gray-400 whitespace-nowrap">
                conf. {(f.confidence * 100).toFixed(0)}%
              </span>
            </div>
            <p className="font-medium text-sm text-gray-800">{f.affected_component}</p>
            <p className="text-sm text-gray-600">{f.description}</p>
            {f.severity != null && (
              <p className="text-xs text-gray-400">
                S={f.severity} O={f.occurrence} D={f.detection}
              </p>
            )}
            {f.standard_citations && f.standard_citations.length > 0 && (
              <p className="text-xs text-gray-500">
                Standards: {f.standard_citations.join(', ')}
              </p>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
