import { useEffect } from 'react'
import { Link } from 'react-router-dom'
import { useReviewStore } from '../stores/reviewStore'
import StatusBadge from '../components/StatusBadge'

export default function ReviewsPage() {
  const { reviews, loading, fetchReviews, deleteReview } = useReviewStore()

  useEffect(() => {
    fetchReviews()
  }, [])

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">Reviews</h1>
        <Link to="/upload" className="btn-primary">
          + Upload DFMEA
        </Link>
      </div>

      {loading && <p className="text-sm text-gray-400">Loading…</p>}

      <div className="card overflow-hidden">
        <table className="min-w-full divide-y divide-gray-200 text-sm">
          <thead className="bg-gray-50">
            <tr>
              {['Assembly', 'Status', 'Rows', 'Created', 'Actions'].map((h) => (
                <th
                  key={h}
                  className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider"
                >
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100 bg-white">
            {reviews.length === 0 && !loading && (
              <tr>
                <td colSpan={5} className="px-4 py-6 text-center text-gray-400">
                  No reviews found. Upload a DFMEA to get started.
                </td>
              </tr>
            )}
            {reviews.map((r) => (
              <tr key={r.review_id} className="hover:bg-gray-50">
                <td className="px-4 py-3 font-medium text-brand-700">
                  <Link to={`/reviews/${r.review_id}`} className="hover:underline">
                    {r.assembly_name}
                  </Link>
                </td>
                <td className="px-4 py-3">
                  <StatusBadge status={r.status} />
                </td>
                <td className="px-4 py-3 text-gray-600">{r.row_count ?? '—'}</td>
                <td className="px-4 py-3 text-gray-500">
                  {new Date(r.created_at).toLocaleDateString()}
                </td>
                <td className="px-4 py-3 flex gap-2">
                  <Link
                    to={`/reviews/${r.review_id}/findings`}
                    className="text-brand-600 hover:underline text-xs"
                  >
                    Findings
                  </Link>
                  <button
                    onClick={() => {
                      if (confirm('Archive this review?')) deleteReview(r.review_id)
                    }}
                    className="text-gray-400 hover:text-danger-600 text-xs"
                  >
                    Archive
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
