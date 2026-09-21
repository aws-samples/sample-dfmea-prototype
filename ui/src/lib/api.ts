import { fetchAuthSession } from 'aws-amplify/auth'
import { getConfig } from '../aws-config'

async function getToken(): Promise<string> {
  const session = await fetchAuthSession()
  return session.tokens?.idToken?.toString() ?? ''
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const token = await getToken()
  const url = `${getConfig().restApiUrl.replace(/\/$/, '')}${path}`
  const res = await fetch(url, {
    method,
    headers: {
      'Content-Type': 'application/json',
      Authorization: token,
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) {
    const err = await res.text()
    throw new Error(`API ${method} ${path} → ${res.status}: ${err}`)
  }
  return res.json() as Promise<T>
}

export const apiGet = <T>(path: string) => request<T>('GET', path)
export const apiPost = <T>(path: string, body: unknown) => request<T>('POST', path, body)
export const apiDelete = <T>(path: string) => request<T>('DELETE', path)
