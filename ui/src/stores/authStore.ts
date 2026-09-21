import { create } from 'zustand'
import { fetchAuthSession } from 'aws-amplify/auth'

interface AuthState {
  token: string | null
  fetchToken: () => Promise<string>
}

export const useAuthStore = create<AuthState>((set) => ({
  token: null,
  fetchToken: async () => {
    const session = await fetchAuthSession()
    const token = session.tokens?.idToken?.toString() ?? ''
    set({ token })
    return token
  },
}))
