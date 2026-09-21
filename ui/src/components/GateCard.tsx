// dfmea-prototype/ui/src/components/GateCard.tsx
import { useEffect, useState } from 'react'
import { apiPost } from '../lib/api'
import GateReviewDrawer from './GateReviewDrawer'

interface GateStatus {
  status: 'NOT_STARTED' | 'PENDING' | 'APPROVED' | 'REJECTED'
  comment: string
  pending: boolean
}

interface Review {
  review_id: string
  assembly_name: string
  file_key: string
  row_count?: number | string
  status: string
  created_at: string
}

interface GateCardProps {
  reviewId: string
  gateNumber: number
  gateLabel: string
  gateDescription: string
  gateData: GateStatus
  review: Review | null
  onDecision: () => void
}

const STATUS_STYLES: Record<string, string> = {
  NOT_STARTED: 'bg-gray-100 text-gray-500',
  PENDING: 'bg-yellow-100 text-yellow-800',
  APPROVED: 'bg-green-100 text-green-800',
  REJECTED: 'bg-red-100 text-red-800',
}

const REVIEW_STAGE_COPY: Record<string, string> = {
  CAD_RUNNING: 'CAD extraction is running. Gate 2 will appear when it is ready.',
  AGENTS_RUNNING: 'The four specialist agents are running in parallel. Gate 3 will appear when all finish.',
  SYNTHESIS_RUNNING: 'The analyst is consolidating findings and preparing Gate 4.',
  REPORT_GENERATING: 'The final PDF report is being generated.',
  COMPLETE: 'The review and final report are complete.',
}
export default function GateCard({
  reviewId,
  gateNumber,
  gateLabel,
  gateDescription,
  gateData,
  review,
  onDecision,
}: GateCardProps) {
  const [comment, setComment] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [submitted, setSubmitted] = useState(false)
  const [submittedAction, setSubmittedAction] = useState<'approve' | 'reject' | null>(null)
  const [drawerOpen, setDrawerOpen] = useState(false)

  const isDecided = gateData.status === 'APPROVED' || gateData.status === 'REJECTED'

  // The backend is authoritative. As soon as polling returns the persisted
  // decision, stop showing a local "running" state so two gates never appear
  // active at the same time.
  useEffect(() => {
    if (isDecided || gateData.status === 'NOT_STARTED') {
      setSubmitted(false)
      setSubmittedAction(null)
    }
  }, [gateData.status, isDecided])

  async function decide(action: 'approve' | 'reject') {
    setLoading(true)
    setError(null)
    try {
      await apiPost(`/reviews/${reviewId}/gate/${gateNumber}`, { action, comment })
      setSubmitted(true)
      setSubmittedAction(action)
      onDecision()
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Action failed')
    } finally {
      setLoading(false)
    }
  }

  const currentStage = review?.status ? REVIEW_STAGE_COPY[review.status] : undefined
  const showPanel = submitted || isDecided

  return (
    <div className="border rounded-lg p-4 mb-3">
      <div className="flex justify-between items-center mb-1">
        <h3 className="font-medium text-gray-800">
          Gate {gateNumber}: {gateLabel}
        </h3>
        <span className={`text-xs font-semibold px-2 py-1 rounded-full ${STATUS_STYLES[gateData.status] ?? ''}`}>
          {gateData.status.replace(/_/g, ' ')}
        </span>
      </div>

      {gateDescription && <p className="text-xs text-gray-400 mb-2">{gateDescription}</p>}

      {showPanel && (
        <div className="mt-2 overflow-hidden rounded-lg border border-gray-700">
          <div className="flex items-center gap-2 bg-gray-800 px-3 py-2">
            {submitted && !isDecided ? (
              <div className="h-3 w-3 animate-spin rounded-full border-2 border-gray-400 border-t-white" />
            ) : (
              <span className={`text-xs font-bold ${gateData.status === 'REJECTED' ? 'text-red-400' : 'text-green-400'}`}>●</span>
            )}
            <span className="text-xs font-mono text-gray-200">
              {submitted && !isDecided
                ? 'Recording decision and resuming the workflow…'
                : gateData.status === 'APPROVED'
                  ? 'Gate approved'
                  : 'Gate rejected — workflow halted'}
            </span>
          </div>
          <div className="space-y-1 bg-gray-900 p-3 text-xs font-mono">
            {submitted && !isDecided && (
              <p className={submittedAction === 'reject' ? 'text-red-400' : 'text-gray-300'}>
                {submittedAction === 'reject'
                  ? 'Submitting rejection…'
                  : 'Waiting for the next real workflow status…'}
              </p>
            )}
            {isDecided && (
              <>
                <p className={gateData.status === 'APPROVED' ? 'text-green-400' : 'text-red-400'}>
                  {gateData.status === 'APPROVED'
                    ? '✓ Decision saved. The workflow has moved forward.'
                    : '✗ Decision saved. The workflow has stopped.'}
                </p>
                {gateData.status === 'APPROVED' && currentStage && (
                  <p className="text-gray-300">{currentStage}</p>
                )}
                {gateData.comment && (
                  <p className="border-t border-gray-700 pt-1 text-gray-400">Comment: “{gateData.comment}”</p>
                )}
              </>
            )}
          </div>
        </div>
      )}

      {gateData.pending && !submitted && !isDecided && (
        <button
          onClick={() => setDrawerOpen(true)}
          className="mt-3 flex w-full items-center justify-center gap-2 rounded-lg border border-blue-300 bg-blue-50 py-2 text-sm font-medium text-blue-700 transition-colors hover:bg-blue-100"
        >
          <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
              d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
          </svg>
          Review data before deciding
        </button>
      )}

      <GateReviewDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        reviewId={reviewId}
        gateNumber={gateNumber}
        gateLabel={`Gate ${gateNumber}: ${gateLabel}`}
        review={review}
      />

      {gateData.pending && !submitted && !isDecided && (
        <>
          <textarea
            value={comment}
            onChange={(event) => setComment(event.target.value)}
            placeholder="Add a comment (optional)"
            rows={2}
            className="mt-3 mb-2 w-full rounded border px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
          <div className="flex justify-end gap-2">
            <button
              onClick={() => decide('approve')}
              disabled={loading}
              className="w-36 rounded bg-green-600 py-1.5 text-sm text-white hover:bg-green-700 disabled:opacity-50"
            >
              {loading ? 'Submitting…' : 'Approve'}
            </button>
            <button
              onClick={() => decide('reject')}
              disabled={loading}
              className="w-36 rounded bg-red-600 py-1.5 text-sm text-white hover:bg-red-700 disabled:opacity-50"
            >
              Reject
            </button>
          </div>
          {error && <p className="mt-1 text-sm text-red-600">{error}</p>}
        </>
      )}
    </div>
  )
}
