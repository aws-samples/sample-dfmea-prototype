import { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { useReviewStore } from '../stores/reviewStore'
import StatusBadge from '../components/StatusBadge'
import GateCard from '../components/GateCard'
import { ReviewWebSocket } from '../lib/websocket'
import { apiGet } from '../lib/api'
import PipelineStepper from '../components/PipelineStepper'

interface GateStatus {
  status: 'NOT_STARTED' | 'PENDING' | 'APPROVED' | 'REJECTED'
  comment: string
  pending: boolean
}

const GATE_LABELS: Record<number, string> = {
  1: 'Intake Validation',
  2: 'Verification',
  3: 'Agent Analysis',
  4: 'Human Approval',
}

const GATE_DESCRIPTIONS: Record<number, string> = {
  1: 'Validates all required fields, S/O/D scores, and component structure in the uploaded file',
  2: 'Verifies assembly hierarchy and component connections',
  3: 'Parallel AI agents (failure mode, structural, regulatory) have all completed analysis',
  4: 'Final human sign-off before PDF report generation',
}

const PROGRESS_COPY: Record<string, { title: string; detail: string; waiting?: boolean }> = {
  SUBMITTED: {
    title: 'Upload received',
    detail: 'Reading and validating the JSON file. Gate 1 will appear automatically when intake is ready.',
  },
  INTAKE_COMPLETE: {
    title: 'Intake complete',
    detail: 'Preparing the Gate 1 validation request.',
  },
  GATE_1_PENDING: {
    title: 'Gate 1 is ready',
    detail: 'Review the normalized upload and approve Intake Validation to continue.',
    waiting: true,
  },
  CAD_RUNNING: {
    title: 'Building the review',
    detail: 'Gate 1 was approved. Component structure is being prepared for Gate 2.',
  },
  GATE_2_PENDING: {
    title: 'Gate 2 is ready',
    detail: 'Review the data and approve it to start the specialist agents.',
    waiting: true,
  },
  AGENTS_RUNNING: {
    title: 'Specialist agents are analyzing the review',
    detail: 'Failure-mode, structural, regulatory, and schema agents are running in parallel. This stage can take several minutes.',
  },
  GATE_3_PENDING: {
    title: 'Gate 3 is ready',
    detail: 'The specialist findings are ready for review and approval.',
    waiting: true,
  },
  SYNTHESIS_RUNNING: {
    title: 'Consolidating agent findings',
    detail: 'The analyst is deduplicating findings and preparing the final human approval package.',
  },
  HITL_PENDING: {
    title: 'Gate 4 is ready',
    detail: 'One final approval is required before generating the PDF report.',
    waiting: true,
  },
  HITL_APPROVED: {
    title: 'Final approval recorded',
    detail: 'The report-generation stage is starting.',
  },
  REPORT_GENERATING: {
    title: 'Generating the final report',
    detail: 'The approved findings are being converted into the final PDF.',
  },
}

export default function ReviewDetailPage() {
  const { reviewId } = useParams<{ reviewId: string }>()
  const { current, fetchReview, loading } = useReviewStore()
  const [reportDownloading, setReportDownloading] = useState(false)
  const [gates, setGates] = useState<Record<string, GateStatus> | null>(null)

  const fetchGates = async (id: string) => {
    try {
      const data = await apiGet<{ review_id: string; gates: Record<string, GateStatus> }>(`/reviews/${id}/gates`)
      setGates(data.gates)
    } catch {
      // Review details remain usable while a transient gate refresh fails.
    }
  }

  useEffect(() => {
    if (!reviewId) return
    let active = true
    setGates(null)

    const refresh = () => {
      if (!active) return
      void fetchReview(reviewId)
      void fetchGates(reviewId)
    }

    refresh()

    // WebSocket remains the fast path; polling guarantees progress even when
    // a connection is delayed or a status-only update is missed.
    const ws = new ReviewWebSocket(reviewId)
    ws.connect()
    const unsub = ws.onMessage((data) => {
      if (data && typeof data === 'object' && 'status' in data) refresh()
    })
    const pollTimer = window.setInterval(refresh, 3000)

    return () => {
      active = false
      window.clearInterval(pollTimer)
      unsub()
      ws.disconnect()
    }
  }, [reviewId, fetchReview])

  const handleDownloadReport = async () => {
    if (!reviewId) return
    setReportDownloading(true)
    try {
      const data = await apiGet<{ url: string }>(`/reviews/${reviewId}/report`)
      window.open(data.url, '_blank', 'noopener,noreferrer')
    } catch (e) {
      alert(`Could not fetch report: ${String(e)}`)
    } finally {
      setReportDownloading(false)
    }
  }

  if (loading || !current || current.review_id !== reviewId) {
    return (
      <div className="card flex min-h-64 flex-col items-center justify-center gap-4 p-8 text-center">
        <div className="h-12 w-12 animate-spin rounded-full border-4 border-blue-100 border-t-blue-600" aria-hidden="true" />
        <div>
          <p className="font-semibold text-gray-800">Preparing your review</p>
          <p className="mt-1 text-sm text-gray-500">The upload was received. Loading review progress and Gate 1 data…</p>
        </div>
      </div>
    )
  }

  const status = current.status
  const gateEntries = gates ? Object.entries(gates) : []
  const pendingGateNumber = gateEntries
    .filter(([, gate]) => gate.pending && gate.status !== 'APPROVED' && gate.status !== 'REJECTED')
    .map(([key]) => Number(key))
    .sort((left, right) => left - right)[0]
  const progress = pendingGateNumber
    ? PROGRESS_COPY[`GATE_${pendingGateNumber}_PENDING`]
    : PROGRESS_COPY[status]
  const visibleGates = gateEntries.filter(([key, gate]) => (
    gate.status === 'APPROVED' || gate.status === 'REJECTED' || Number(key) === pendingGateNumber
  ))

  const refreshAfterDecision = () => {
    if (!reviewId) return
    void fetchReview(reviewId)
    void fetchGates(reviewId)
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <Link to="/reviews" className="text-brand-600 text-sm hover:underline">
          ← Reviews
        </Link>
        <h1 className="text-2xl font-bold text-gray-900">{current.assembly_name}</h1>
        <StatusBadge status={status} />
      </div>

      {progress && (
        <div className={`card flex items-start gap-4 border-l-4 p-5 ${progress.waiting ? 'border-l-amber-400' : 'border-l-blue-500'}`}>
          {progress.waiting ? (
            <div className="mt-1 flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-amber-100 text-lg text-amber-700" aria-hidden="true">!</div>
          ) : (
            <div className="mt-1 h-9 w-9 shrink-0 animate-spin rounded-full border-4 border-blue-100 border-t-blue-600" aria-hidden="true" />
          )}
          <div>
            <p className="font-semibold text-gray-900">{progress.title}</p>
            <p className="mt-1 text-sm text-gray-600">{progress.detail}</p>
            <p className="mt-2 text-xs text-gray-400">This page refreshes automatically.</p>
          </div>
        </div>
      )}

      <div className="card p-5 grid grid-cols-2 sm:grid-cols-4 gap-4 text-sm">
        <div>
          <p className="text-gray-400 text-xs mb-1">Review ID</p>
          <p className="font-mono text-xs text-gray-600 truncate">{current.review_id}</p>
        </div>
        <div>
          <p className="text-gray-400 text-xs mb-1">Rows</p>
          <p className="font-semibold">{current.row_count ?? '—'}</p>
        </div>
        <div>
          <p className="text-gray-400 text-xs mb-1">Created</p>
          <p>{new Date(current.created_at).toLocaleString()}</p>
        </div>
        <div>
          <p className="text-gray-400 text-xs mb-1">Updated</p>
          <p>{new Date(current.updated_at).toLocaleString()}</p>
        </div>
      </div>

      <div className="card p-4">
        <PipelineStepper status={status} gates={gates} />
      </div>

      <div className="flex gap-3">
        <Link to={`/reviews/${reviewId}/findings`} className="btn-primary">
          View Findings
        </Link>
        <Link to={`/ontology/${reviewId}`} className="btn-secondary">
          Open Graph Review
        </Link>
        <button onClick={handleDownloadReport} disabled={reportDownloading} className="btn-secondary">
          {reportDownloading ? 'Fetching…' : 'Download Report'}
        </button>
      </div>

      {visibleGates.length > 0 && (
        <div className="card p-5 space-y-1">
          <h2 className="font-semibold text-gray-800 mb-3">HITL Gates</h2>
          {visibleGates.map(([key, gateData]) => {
            const gateNumber = Number(key)
            return (
              <GateCard
                key={key}
                reviewId={reviewId!}
                gateNumber={gateNumber}
                gateLabel={GATE_LABELS[gateNumber] ?? `Gate ${gateNumber}`}
                gateDescription={GATE_DESCRIPTIONS[gateNumber] ?? ''}
                gateData={gateData}
                review={current}
                onDecision={refreshAfterDecision}
              />
            )
          })}
        </div>
      )}
    </div>
  )
}
