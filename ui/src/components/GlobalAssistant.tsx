import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import {
  assistantApi,
  navigateFromAssistantAction,
  type AssistantConversation,
  type AssistantMessage,
  type AssistantRouteContext,
  type AssistantRouteId,
} from '../lib/assistant'

const DEFAULT_PROMPTS = [
  'How do I start a new DFMEA review?',
  'Explain the DFMEA review process.',
  'What does action priority mean?',
  'What can I do on this page?',
]

function getRouteContext(pathname: string): AssistantRouteContext {
  const findingsMatch = pathname.match(/^\/reviews\/([^/]+)\/findings\/?$/)
  if (findingsMatch) {
    return { route_id: 'REVIEW_FINDINGS', review_id: findingsMatch[1] }
  }

  const reviewMatch = pathname.match(/^\/reviews\/([^/]+)\/?$/)
  if (reviewMatch) {
    return { route_id: 'REVIEW_DETAIL', review_id: reviewMatch[1] }
  }

  const ontologyMatch = pathname.match(/^\/ontology(?:\/([^/]+))?\/?$/)
  if (ontologyMatch) {
    return ontologyMatch[1]
      ? { route_id: 'ONTOLOGY', review_id: ontologyMatch[1] }
      : { route_id: 'ONTOLOGY' }
  }

  const routeMap: Record<string, AssistantRouteId> = {
    '/dashboard': 'DASHBOARD',
    '/reviews': 'REVIEWS',
    '/upload': 'UPLOAD',
  }
  return { route_id: routeMap[pathname] ?? 'DASHBOARD' }
}

function getContextPrompts(context: AssistantRouteContext): string[] {
  switch (context.route_id) {
    case 'DASHBOARD':
      return [
        'Summarize the current review status.',
        'Which reviews need attention?',
        'Explain the dashboard metrics.',
        ...DEFAULT_PROMPTS,
      ]
    case 'REVIEWS':
      return [
        'Which reviews are still active?',
        'How do I interpret review status?',
        'How do I start a new review?',
        ...DEFAULT_PROMPTS,
      ]
    case 'REVIEW_DETAIL':
      return [
        'What stage is this review in?',
        'Which approvals are still pending?',
        'Open the findings for this review.',
        ...DEFAULT_PROMPTS,
      ]
    case 'REVIEW_FINDINGS':
      return [
        'Show the high-priority findings.',
        'Explain why a finding is High priority.',
        'What should I review next?',
        ...DEFAULT_PROMPTS,
      ]
    case 'UPLOAD':
      return [
        'What file format can I upload?',
        'How do I prepare a DFMEA file?',
        'What happens after upload?',
        ...DEFAULT_PROMPTS,
      ]
    case 'ONTOLOGY':
      return [
        'What does the knowledge graph show?',
        'How is ontology data used in a review?',
        'Explain the entity and relation counts.',
        ...DEFAULT_PROMPTS,
      ]
    default:
      return DEFAULT_PROMPTS
  }
}

function contextLabel(context: AssistantRouteContext): string {
  const labels: Record<AssistantRouteId, string> = {
    DASHBOARD: 'Dashboard',
    REVIEWS: 'Reviews',
    REVIEW_DETAIL: 'Review details',
    REVIEW_FINDINGS: 'Review findings',
    UPLOAD: 'Upload',
    ONTOLOGY: 'Knowledge graph',
  }
  return context.review_id
    ? `${labels[context.route_id]} · ${context.review_id.slice(0, 8)}`
    : labels[context.route_id]
}

function formatTime(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return new Intl.DateTimeFormat(undefined, {
    hour: 'numeric',
    minute: '2-digit',
  }).format(date)
}

function AssistantIcon({ close = false }: { close?: boolean }) {
  return close ? (
    <svg viewBox="0 0 24 24" aria-hidden="true" className="h-6 w-6" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M6 6l12 12M18 6L6 18" />
    </svg>
  ) : (
    <svg viewBox="0 0 24 24" aria-hidden="true" className="h-7 w-7" fill="none" stroke="currentColor" strokeWidth="1.8">
      <path d="M21 12a8 8 0 0 1-8 8H6l-3 2 1-4a9 9 0 1 1 17-6Z" />
      <path d="M8 12h.01M12 12h.01M16 12h.01" strokeLinecap="round" strokeWidth="2.5" />
    </svg>
  )
}

export default function GlobalAssistant() {
  const location = useLocation()
  const navigate = useNavigate()
  const routeContext = useMemo(
    () => getRouteContext(location.pathname),
    [location.pathname]
  )
  const prompts = useMemo(
    () => Array.from(new Set(getContextPrompts(routeContext))).slice(0, 6),
    [routeContext]
  )

  const [isOpen, setIsOpen] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [conversations, setConversations] = useState<AssistantConversation[]>([])
  const [activeConversation, setActiveConversation] = useState<AssistantConversation | null>(null)
  const [messages, setMessages] = useState<AssistantMessage[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [historyLoading, setHistoryLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const messagesEndRef = useRef<HTMLDivElement>(null)

  const loadConversations = useCallback(async () => {
    setHistoryLoading(true)
    try {
      const result = await assistantApi.listConversations()
      setConversations(result.conversations)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to load chat history.')
    } finally {
      setHistoryLoading(false)
    }
  }, [])

  useEffect(() => {
    if (isOpen) void loadConversations()
  }, [isOpen, loadConversations])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, loading])

  const startNewConversation = () => {
    setActiveConversation(null)
    setMessages([])
    setInput('')
    setError(null)
    setHistoryOpen(false)
  }

  const openConversation = async (conversation: AssistantConversation) => {
    setLoading(true)
    setError(null)
    try {
      const result = await assistantApi.listMessages(conversation.conversation_id)
      setActiveConversation(conversation)
      setMessages(result.messages)
      setHistoryOpen(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to open this conversation.')
    } finally {
      setLoading(false)
    }
  }

  const deleteConversation = async (conversationId: string) => {
    if (!window.confirm('Delete this conversation and its messages?')) return
    setHistoryLoading(true)
    setError(null)
    try {
      await assistantApi.deleteConversation(conversationId)
      setConversations((items) =>
        items.filter((item) => item.conversation_id !== conversationId)
      )
      if (activeConversation?.conversation_id === conversationId) {
        startNewConversation()
        setHistoryOpen(true)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to delete this conversation.')
    } finally {
      setHistoryLoading(false)
    }
  }

  const sendPrompt = async (prompt: string) => {
    const normalizedPrompt = prompt.trim()
    if (!normalizedPrompt || loading) return

    setLoading(true)
    setError(null)
    setInput('')

    const optimisticMessage: AssistantMessage = {
      message_id: `local-${Date.now()}`,
      conversation_id: activeConversation?.conversation_id ?? 'new',
      role: 'user',
      content: normalizedPrompt,
      created_at: new Date().toISOString(),
    }
    setMessages((items) => [...items, optimisticMessage])

    try {
      let conversation = activeConversation
      if (!conversation) {
        const created = await assistantApi.createConversation(
          normalizedPrompt.slice(0, 80),
          routeContext
        )
        conversation = created.conversation
        setActiveConversation(conversation)
      }

      const result = await assistantApi.sendMessage(
        conversation.conversation_id,
        normalizedPrompt,
        routeContext
      )
      setActiveConversation(result.conversation)
      setMessages((items) => [
        ...items.filter((item) => item.message_id !== optimisticMessage.message_id),
        result.user_message,
        result.assistant_message,
      ])
      await loadConversations()
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : 'The assistant could not answer this question.'
      )
    } finally {
      setLoading(false)
    }
  }

  const submit = (event: FormEvent) => {
    event.preventDefault()
    void sendPrompt(input)
  }

  return (
    <>
      {isOpen && (
        <section
          role="dialog"
          aria-modal="true"
          aria-label="DFMEA assistant"
          className="fixed inset-0 z-50 flex flex-col bg-white shadow-2xl sm:inset-auto sm:bottom-24 sm:right-6 sm:h-[38rem] sm:w-[28rem] sm:overflow-hidden sm:rounded-xl sm:border sm:border-gray-200"
        >
          <header className="flex items-center justify-between bg-brand-900 px-4 py-3 text-white">
            <div>
              <h2 className="font-semibold">DFMEA Assistant</h2>
              <p className="mt-0.5 text-xs text-blue-200">{contextLabel(routeContext)}</p>
            </div>
            <div className="flex items-center gap-1">
              <button
                type="button"
                onClick={() => setHistoryOpen((value) => !value)}
                className="rounded-md px-2 py-1.5 text-xs font-medium text-blue-100 hover:bg-brand-700"
                aria-label="View chat history"
              >
                History
              </button>
              <button
                type="button"
                onClick={startNewConversation}
                className="rounded-md px-2 py-1.5 text-xs font-medium text-blue-100 hover:bg-brand-700"
              >
                New
              </button>
              <button
                type="button"
                onClick={() => setIsOpen(false)}
                className="rounded-md p-1.5 text-blue-100 hover:bg-brand-700"
                aria-label="Close assistant"
              >
                <AssistantIcon close />
              </button>
            </div>
          </header>

          <div className="relative flex min-h-0 flex-1 flex-col">
            {historyOpen && (
              <aside className="absolute inset-0 z-10 overflow-y-auto bg-white p-4" aria-label="Chat history">
                <div className="mb-3 flex items-center justify-between">
                  <h3 className="font-semibold text-gray-900">Previous chats</h3>
                  <button type="button" onClick={() => setHistoryOpen(false)} className="text-sm text-brand-600 hover:text-brand-700">
                    Back to chat
                  </button>
                </div>
                {historyLoading && <p className="text-sm text-gray-500">Loading history...</p>}
                {!historyLoading && conversations.length === 0 && (
                  <p className="rounded-lg bg-gray-50 p-4 text-sm text-gray-500">No previous conversations yet.</p>
                )}
                <div className="space-y-2">
                  {conversations.map((conversation) => (
                    <div key={conversation.conversation_id} className="flex items-start gap-2 rounded-lg border border-gray-200 p-3 hover:bg-gray-50">
                      <button type="button" onClick={() => void openConversation(conversation)} className="min-w-0 flex-1 text-left">
                        <span className="block truncate text-sm font-medium text-gray-900">{conversation.title}</span>
                        <span className="mt-1 block text-xs text-gray-500">{new Date(conversation.updated_at).toLocaleString()}</span>
                      </button>
                      <button type="button" onClick={() => void deleteConversation(conversation.conversation_id)} className="text-xs text-danger-600 hover:text-danger-500" aria-label={`Delete ${conversation.title}`}>
                        Delete
                      </button>
                    </div>
                  ))}
                </div>
              </aside>
            )}

            <div className="flex-1 overflow-y-auto px-4 py-4" aria-live="polite">
              {messages.length === 0 && !loading && (
                <div>
                  <div className="rounded-lg bg-brand-50 p-4 text-sm text-gray-700">
                    Ask about the DFMEA process, this application, or the status of records you can access. Live status answers come from authoritative application data.
                  </div>
                  <h3 className="mb-2 mt-5 text-xs font-semibold uppercase tracking-wide text-gray-500">Try asking</h3>
                  <div className="grid gap-2">
                    {prompts.map((prompt) => (
                      <button key={prompt} type="button" onClick={() => void sendPrompt(prompt)} className="rounded-lg border border-gray-200 px-3 py-2 text-left text-sm text-gray-700 transition-colors hover:border-brand-500 hover:bg-brand-50">
                        {prompt}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              <div className="space-y-4">
                {messages.map((message) => (
                  <article key={message.message_id} className={message.role === 'user' ? 'ml-8' : 'mr-8'}>
                    <div className={message.role === 'user' ? 'rounded-lg bg-brand-600 px-3 py-2 text-sm text-white' : 'rounded-lg bg-gray-100 px-3 py-2 text-sm text-gray-800'}>
                      <p className="whitespace-pre-wrap">{message.content}</p>
                    </div>
                    <div className="mt-1 flex items-center gap-2 text-[11px] text-gray-400">
                      <span>{message.role === 'user' ? 'You' : 'Assistant'}</span>
                      <span>{formatTime(message.created_at)}</span>
                    </div>
                    {message.citations && message.citations.length > 0 && (
                      <div className="mt-2 rounded-md border border-gray-200 bg-white p-2">
                        <p className="text-[11px] font-semibold uppercase text-gray-500">Sources</p>
                        {message.citations.map((citation, index) => (
                          <p key={`${citation.label}-${index}`} className="mt-1 text-xs text-gray-600">
                            {citation.label}{citation.retrieved_at ? ` · retrieved ${new Date(citation.retrieved_at).toLocaleString()}` : ''}
                          </p>
                        ))}
                      </div>
                    )}
                    {message.navigation_actions?.map((action, index) => (
                      <button key={`${action.route_id}-${index}`} type="button" onClick={() => navigateFromAssistantAction(action, navigate)} className="btn-secondary mt-2 !px-3 !py-1.5 text-xs">
                        {action.label}
                      </button>
                    ))}
                    {message.suggested_prompts && message.suggested_prompts.length > 0 && (
                      <div className="mt-2 flex flex-wrap gap-1.5">
                        {message.suggested_prompts.map((prompt) => (
                          <button key={prompt} type="button" onClick={() => void sendPrompt(prompt)} className="rounded-full border border-brand-500 px-2.5 py-1 text-xs text-brand-700 hover:bg-brand-50">
                            {prompt}
                          </button>
                        ))}
                      </div>
                    )}
                  </article>
                ))}
                {loading && (
                  <div className="mr-8 rounded-lg bg-gray-100 px-3 py-2 text-sm text-gray-500">Assistant is working...</div>
                )}
                <div ref={messagesEndRef} />
              </div>
            </div>

            {error && (
              <div role="alert" className="mx-4 mb-2 rounded-md bg-danger-50 px-3 py-2 text-xs text-danger-600">
                {error}
              </div>
            )}

            <form onSubmit={submit} className="border-t border-gray-200 bg-white p-3">
              <label htmlFor="assistant-message" className="sr-only">Ask the DFMEA assistant</label>
              <div className="flex items-end gap-2">
                <textarea
                  id="assistant-message"
                  value={input}
                  onChange={(event) => setInput(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' && !event.shiftKey) {
                      event.preventDefault()
                      if (input.trim()) void sendPrompt(input)
                    }
                  }}
                  rows={2}
                  maxLength={4000}
                  placeholder="Ask about the process or current status..."
                  className="min-h-[2.75rem] flex-1 resize-none rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
                  disabled={loading}
                />
                <button type="submit" disabled={loading || !input.trim()} className="btn-primary !px-3 !py-2">
                  Send
                </button>
              </div>
              <p className="mt-1.5 text-[11px] text-gray-400">The assistant will not infer unavailable live data.</p>
            </form>
          </div>
        </section>
      )}

      <button
        type="button"
        onClick={() => setIsOpen((value) => !value)}
        className="fixed bottom-6 right-6 z-40 flex h-14 w-14 items-center justify-center rounded-full bg-brand-600 text-white shadow-lg transition-transform hover:scale-105 hover:bg-brand-700 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:ring-offset-2"
        aria-label={isOpen ? 'Close DFMEA assistant' : 'Open DFMEA assistant'}
        aria-expanded={isOpen}
      >
        <AssistantIcon close={isOpen} />
      </button>
    </>
  )
}
