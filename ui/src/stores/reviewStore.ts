import { create } from 'zustand'
import { apiGet, apiPost, apiDelete } from '../lib/api'

export interface Review {
  review_id: string
  assembly_name: string
  file_key: string
  status: string
  row_count?: number
  created_at: string
  updated_at: string
}

interface ReviewState {
  reviews: Review[]
  current: Review | null
  loading: boolean
  error: string | null
  fetchReviews: () => Promise<void>
  fetchReview: (id: string) => Promise<void>
  createReview: (assemblyName: string, fileKey: string) => Promise<Review>
  deleteReview: (id: string) => Promise<void>
}

export const useReviewStore = create<ReviewState>((set) => ({
  reviews: [],
  current: null,
  loading: false,
  error: null,

  fetchReviews: async () => {
    set({ loading: true, error: null })
    try {
      const data = await apiGet<{ reviews: Review[] }>('/reviews')
      set({ reviews: data.reviews, loading: false })
    } catch (e) {
      set({ error: String(e), loading: false })
    }
  },

  fetchReview: async (id: string) => {
    // Show a loader whenever navigation targets a different review. Refreshes
    // for the currently displayed review remain silent.
    set((s) => ({ loading: s.current?.review_id !== id, error: null }))
    try {
      const review = await apiGet<Review>(`/reviews/${id}`)
      set({ current: review, loading: false })
    } catch (e) {
      set({ error: String(e), loading: false })
    }
  },

  createReview: async (assemblyName: string, fileKey: string) => {
    const review = await apiPost<Review>('/reviews', { assembly_name: assemblyName, file_key: fileKey })
    set((s) => ({ reviews: [review, ...s.reviews] }))
    return review
  },

  deleteReview: async (id: string) => {
    await apiDelete(`/reviews/${id}`)
    set((s) => ({ reviews: s.reviews.filter((r) => r.review_id !== id) }))
  },
}))
