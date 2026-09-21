import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { apiGet } from '../lib/api'

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

function formatTimestamp(value: string | null) {
  if (!value) return '—'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return '—'
  return parsed.toLocaleString([], {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export default function HitlDecisionsPage() {
  const [data, setData] = useState<HitlResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let active = true
    apiGet<HitlResponse>('/admin/hitl-decisions')
      .then((response) => {
        if (active) setData(response)
      })
      .catch((err: unknown) => {
        if (active) {
          setError(err instanceof Error ? err.message : 'Unable to load HITL decisions.')
        }
      })
    return () => {
      active = false
    }
  }, [])

  const counts = useMemo(() => {
    const decisions = data?.decisions ?? []
    return {
      approved: decisions.filter((item) => item.decision === 'APPROVED').length,
      rejected: decisions.filter((item) => item.decision === 'REJECTED').length,
    }
  }, [data])

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Human-in-the-Loop Decisions</h1>
          <p className="mt-1 text-sm text-gray-500">
            Review approval and rejection decisions across all DFMEA gates.
          </p>
        </div>
        <Link to="/dashboard" className="text-sm font-semibold text-brand-700 hover:underline">
          ← Back to dashboard
        </Link>
      </div>

      <section className="grid grid-cols-1 gap-4 sm:grid-cols-3" aria-label="Decision summary">
        <div className="card p-4">
          <p className="text-xs font-bold uppercase tracking-wide text-gray-500">Total decisions</p>
          <p className="mt-2 text-3xl font-bold text-gray-950">{data?.total ?? '—'}</p>
        </div>
        <div className="card p-4">
          <p className="text-xs font-bold uppercase tracking-wide text-gray-500">Approved</p>
          <p className="mt-2 text-3xl font-bold text-green-700">{data ? counts.approved : '—'}</p>
        </div>
        <div className="card p-4">
          <p className="text-xs font-bold uppercase tracking-wide text-gray-500">Rejected</p>
          <p className="mt-2 text-3xl font-bold text-red-700">{data ? counts.rejected : '—'}</p>
        </div>
      </section>

      <section className="card overflow-hidden">
        {error ? (
          <div className="px-5 py-10 text-center text-sm text-red-600" role="alert">
            HITL decisions could not be loaded. Please refresh and try again.
          </div>
        ) : !data ? (
          <div className="px-5 py-10 text-center text-sm text-gray-400">Loading decisions...</div>
        ) : data.decisions.length === 0 ? (
          <div className="px-5 py-10 text-center text-sm text-gray-400">No decisions stored.</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[1050px] text-xs">
              <thead className="bg-gray-50">
                <tr className="border-b-2 border-gray-700 text-left text-[10px] font-bold uppercase tracking-wide text-gray-600">
                  <th className="px-4 py-3">Timestamp</th>
                  <th className="px-4 py-3">Component</th>
                  <th className="px-4 py-3">Finding</th>
                  <th className="px-4 py-3">Agent confidence</th>
                  <th className="px-4 py-3">Reviewer</th>
                  <th className="px-4 py-3">Gate</th>
                  <th className="px-4 py-3">Decision</th>
                  <th className="px-4 py-3">Comment</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 bg-white">
                {data.decisions.map((decision) => (
                  <tr
                    key={`${decision.review_id}-${decision.gate}`}
                    className="align-top hover:bg-gray-50"
                  >
                    <td className="whitespace-nowrap px-4 py-3 text-gray-500">
                      {formatTimestamp(decision.decision_timestamp || decision.record_updated_at)}
                    </td>
                    <td className="max-w-52 px-4 py-3 font-semibold text-brand-700">
                      <Link to={`/reviews/${encodeURIComponent(decision.review_id)}`} className="hover:underline">
                        {decision.assembly_name || decision.review_id}
                      </Link>
                    </td>
                    <td className="max-w-56 px-4 py-3 text-gray-500">{decision.finding || '—'}</td>
                    <td className="px-4 py-3 text-gray-500">{decision.agent_confidence ?? '—'}</td>
                    <td className="px-4 py-3 text-gray-500">{decision.reviewer || '—'}</td>
                    <td className="whitespace-nowrap px-4 py-3 text-gray-600">Gate {decision.gate}</td>
                    <td className="px-4 py-3">
                      <span className={`rounded px-2 py-1 text-[10px] font-bold ${decision.decision === 'APPROVED' ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'}`}>
                        {decision.decision === 'APPROVED' ? 'Approved' : 'Rejected'}
                      </span>
                    </td>
                    <td className="max-w-80 whitespace-normal px-4 py-3 text-gray-500">
                      {decision.comment || '—'}
                    </td>
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
