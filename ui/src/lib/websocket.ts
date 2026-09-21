import { getConfig } from '../aws-config'

type MessageHandler = (data: unknown) => void

export class ReviewWebSocket {
  private ws: WebSocket | null = null
  private handlers: Set<MessageHandler> = new Set()
  private reviewId: string
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private maxReconnects = 5
  private reconnects = 0

  constructor(reviewId: string) {
    this.reviewId = reviewId
  }

  async connect(): Promise<void> {
    const wsUrl = getConfig().wsApiUrl
    const url = `${wsUrl}?review_id=${this.reviewId}`
    this.ws = new WebSocket(url)

    this.ws.onopen = () => {
      this.reconnects = 0
    }

    this.ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data as string)
        this.handlers.forEach((h) => h(data))
      } catch {
        // ignore malformed messages
      }
    }

    this.ws.onclose = () => {
      if (this.reconnects < this.maxReconnects) {
        this.reconnects++
        this.reconnectTimer = setTimeout(() => this.connect(), 2000 * this.reconnects)
      }
    }

    this.ws.onerror = () => {
      this.ws?.close()
    }
  }

  onMessage(handler: MessageHandler): () => void {
    this.handlers.add(handler)
    return () => this.handlers.delete(handler)
  }

  disconnect(): void {
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer)
    this.ws?.close()
    this.ws = null
  }
}
