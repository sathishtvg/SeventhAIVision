import { useEffect, useRef, useState, useCallback } from 'react'
import { useAuthStore } from '@/store/auth'
import { useServerStore } from '@/store/server'

type WsStatus = 'connecting' | 'open' | 'closed'

export interface RealtimeEvent {
  // Backend publishes more event types than any one screen consumes (see
  // realtime.py's RealtimeEvent for the full set) — kept as a plain string
  // rather than an enumerated union so a handler can react to an event type
  // it doesn't explicitly need to be its own maintained constant here.
  event_type: string
  tenant_id: string
  payload: Record<string, unknown>
  occurred_at: string
}

export function useWebSocket(onEvent: (e: RealtimeEvent) => void) {
  const accessToken = useAuthStore((s) => s.accessToken)
  const serverUrl = useServerStore((s) => s.serverUrl)
  const [status, setStatus] = useState<WsStatus>('closed')
  const wsRef = useRef<WebSocket | null>(null)
  const retryRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const retryDelay = useRef(1000)
  const onEventRef = useRef(onEvent)
  onEventRef.current = onEvent

  const connect = useCallback(() => {
    if (!accessToken) return
    if (wsRef.current?.readyState === WebSocket.OPEN) return

    const wsBase = serverUrl.replace(/^http/, 'ws')
    setStatus('connecting')
    const ws = new WebSocket(`${wsBase}/ws/live?token=${accessToken}`)
    wsRef.current = ws

    ws.onopen = () => {
      setStatus('open')
      retryDelay.current = 1000
    }

    ws.onmessage = (e) => {
      try {
        const parsed = JSON.parse(e.data as string) as RealtimeEvent
        onEventRef.current(parsed)
      } catch {}
    }

    ws.onclose = () => {
      setStatus('closed')
      wsRef.current = null
      retryRef.current = setTimeout(() => {
        retryDelay.current = Math.min(retryDelay.current * 2, 30_000)
        connect()
      }, retryDelay.current)
    }

    ws.onerror = () => ws.close()
  }, [accessToken, serverUrl])

  useEffect(() => {
    connect()
    return () => {
      if (retryRef.current) clearTimeout(retryRef.current)
      wsRef.current?.close()
    }
  }, [connect])

  return status
}
