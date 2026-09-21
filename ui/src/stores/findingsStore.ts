import { create } from 'zustand'
import { apiGet } from '../lib/api'

export interface Finding {
  review_id: string
  finding_id: string
  agent: string
  finding_type: string
  affected_component: string
  description: string
  action_priority: 'H' | 'M' | 'L'
  severity?: number
  occurrence?: number
  detection?: number
  confidence: number
  standard_citations?: string[]
  created_at: string
}

interface FindingsState {
  findings: Finding[]
  loading: boolean
  error: string | null
  fetchFindings: (reviewId: string, minAp?: string) => Promise<void>
  clearFindings: () => void
}

export const useFindingsStore = create<FindingsState>((set) => ({
  findings: [],
  loading: false,
  error: null,

  fetchFindings: async (reviewId: string, minAp?: string) => {
    set({ loading: true, error: null })
    try {
      const qs = minAp ? `?min_ap=${minAp}` : ''
      const data = await apiGet<{ findings: Finding[] }>(`/reviews/${reviewId}/findings${qs}`)
      set({ findings: data.findings, loading: false })
    } catch (e) {
      set({ error: String(e), loading: false })
    }
  },

  clearFindings: () => set({ findings: [] }),
}))
