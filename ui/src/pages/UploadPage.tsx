import { useState, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { apiGet } from '../lib/api'
import { useReviewStore } from '../stores/reviewStore'

export default function UploadPage() {
  const navigate = useNavigate()
  const { createReview } = useReviewStore()
  const [assemblyName, setAssemblyName] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [uploading, setUploading] = useState(false)
  const [progress, setProgress] = useState(0)
  const [error, setError] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!file || !assemblyName.trim()) {
      setError('Assembly name and file are required.')
      return
    }
    setUploading(true)
    setError('')
    setProgress(0)
    try {
      // Get a presigned PUT URL from the API
      const { url, key } = await apiGet<{ url: string; key: string }>(
        `/upload-url?filename=${encodeURIComponent(file.name)}`
      )
      setProgress(20)

      // Upload directly to S3 — no Amplify Storage needed
      const uploadRes = await fetch(url, {
        method: 'PUT',
        body: file,
        headers: { 'Content-Type': file.type || 'application/octet-stream' },
      })
      if (!uploadRes.ok) throw new Error(`S3 upload failed: ${uploadRes.status}`)
      setProgress(80)

      const review = await createReview(assemblyName, key)
      setProgress(100)
      navigate(`/reviews/${review.review_id}`)
    } catch (e) {
      setError(String(e))
    } finally {
      setUploading(false)
    }
  }

  return (
    <div className="max-w-xl space-y-6">
      <h1 className="text-2xl font-bold text-gray-900">Upload DFMEA</h1>

      <form onSubmit={handleSubmit} className="card p-6 space-y-5">
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Assembly Name</label>
          <input
            type="text"
            value={assemblyName}
            onChange={(e) => setAssemblyName(e.target.value)}
            placeholder="e.g. B-Pillar Assembly"
            className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
            required
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">DFMEA File</label>
          <div
            onClick={() => inputRef.current?.click()}
            className="border-2 border-dashed border-gray-300 rounded-lg p-6 cursor-pointer hover:border-brand-400 transition-colors text-center"
          >
            {file ? (
              <p className="text-sm text-gray-700 font-medium">{file.name}</p>
            ) : (
              <>
                <p className="text-sm text-gray-500">Click to select or drag & drop</p>
                <p className="text-xs text-gray-400 mt-1">Excel (.xlsx) or JSON</p>
              </>
            )}
          </div>
          <input
            ref={inputRef}
            type="file"
            accept=".xlsx,.xls,.json"
            className="hidden"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
        </div>

        {uploading && (
          <div>
            <div className="h-2 bg-gray-200 rounded-full overflow-hidden">
              <div
                className="h-2 bg-brand-500 rounded-full transition-all"
                style={{ width: `${progress}%` }}
              />
            </div>
            <p className="text-xs text-gray-400 mt-1 text-right">{progress}%</p>
          </div>
        )}

        {error && <p className="text-sm text-danger-600">{error}</p>}

        <button type="submit" className="btn-primary w-full justify-center" disabled={uploading}>
          {uploading ? 'Uploading…' : 'Upload & Start Review'}
        </button>
      </form>

      <div className="card p-4 text-sm text-gray-500 space-y-1">
        <p className="font-medium text-gray-700">What happens next?</p>
        <ol className="list-decimal ml-4 space-y-0.5">
          <li>File is securely uploaded to S3</li>
          <li>S0: CAD drawing components are extracted</li>
          <li>S1: DFMEA rows are parsed and normalised</li>
          <li>S3: Four specialist agents analyse in parallel</li>
          <li>S4: Analyst agent synthesises all findings</li>
          <li>Gate 4: Human review and approval</li>
          <li>S6: Final report is generated</li>
        </ol>
      </div>
    </div>
  )
}
