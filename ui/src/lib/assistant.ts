import type { NavigateFunction } from 'react-router-dom'
import { apiDelete, apiGet, apiPost } from './api'

export type AssistantRouteId =
  | 'DASHBOARD'
  | 'REVIEWS'
  | 'REVIEW_DETAIL'
  | 'REVIEW_FINDINGS'
  | 'UPLOAD'
  | 'ONTOLOGY'

export interface AssistantRouteContext {
  route_id: AssistantRouteId
  review_id?: string
}

export interface AssistantCitation {
  source_type: 'APPLICATION_HELP' | 'REGULATORY' | 'FAILURE_MODE' | 'LIVE_DATA'
  label: string
  source_id?: string
  retrieved_at?: string
}

export interface AssistantNavigationAction {
  type: 'OPEN_ROUTE'
  route_id: AssistantRouteId
  parameters?: {
    review_id?: string
  }
  label: string
}

export interface AssistantMessage {
  message_id: string
  conversation_id: string
  role: 'user' | 'assistant'
  content: string
  created_at: string
  citations?: AssistantCitation[]
  suggested_prompts?: string[]
  navigation_actions?: AssistantNavigationAction[]
}

export interface AssistantConversation {
  conversation_id: string
  title: string
  created_at: string
  updated_at: string
  route_context?: AssistantRouteContext
}

interface ConversationListResponse {
  conversations: AssistantConversation[]
}

interface MessageListResponse {
  messages: AssistantMessage[]
}

interface CreateConversationResponse {
  conversation: AssistantConversation
}

interface SendMessageResponse {
  user_message: AssistantMessage
  assistant_message: AssistantMessage
  conversation: AssistantConversation
}

export const assistantApi = {
  listConversations: () =>
    apiGet<ConversationListResponse>('/assistant/conversations'),

  createConversation: (title: string, routeContext: AssistantRouteContext) =>
    apiPost<CreateConversationResponse>('/assistant/conversations', {
      title,
      route_context: routeContext,
    }),

  listMessages: (conversationId: string) =>
    apiGet<MessageListResponse>(
      `/assistant/conversations/${encodeURIComponent(conversationId)}/messages`
    ),

  sendMessage: (
    conversationId: string,
    prompt: string,
    routeContext: AssistantRouteContext
  ) =>
    apiPost<SendMessageResponse>(
      `/assistant/conversations/${encodeURIComponent(conversationId)}/messages`,
      { prompt, route_context: routeContext }
    ),

  deleteConversation: (conversationId: string) =>
    apiDelete<{ conversation_id: string; deleted: boolean }>(
      `/assistant/conversations/${encodeURIComponent(conversationId)}`
    ),
}

const SAFE_REVIEW_ID = /^[A-Za-z0-9-]{1,128}$/

export function resolveNavigationPath(
  action: AssistantNavigationAction
): string | null {
  if (action.type !== 'OPEN_ROUTE') return null

  const reviewId = action.parameters?.review_id
  switch (action.route_id) {
    case 'DASHBOARD':
      return '/dashboard'
    case 'REVIEWS':
      return '/reviews'
    case 'UPLOAD':
      return '/upload'
    case 'ONTOLOGY':
      return '/ontology'
    case 'REVIEW_DETAIL':
      return reviewId && SAFE_REVIEW_ID.test(reviewId)
        ? `/reviews/${encodeURIComponent(reviewId)}`
        : null
    case 'REVIEW_FINDINGS':
      return reviewId && SAFE_REVIEW_ID.test(reviewId)
        ? `/reviews/${encodeURIComponent(reviewId)}/findings`
        : null
    default:
      return null
  }
}

export function navigateFromAssistantAction(
  action: AssistantNavigationAction,
  navigate: NavigateFunction
): boolean {
  const path = resolveNavigationPath(action)
  if (!path) return false
  navigate(path)
  return true
}
