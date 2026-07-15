import { useEffect } from 'react'
import { useAuthStore } from '@/store/auth'
import { useWsStore } from '@/store/websocket'

export type { WsStatus } from '@/store/websocket'

interface UseWebSocketResult {
  status: import('@/store/websocket').WsStatus
  lastMessage: string | null
}

export function useWebSocket(): UseWebSocketResult {
  const accessToken = useAuthStore((s) => s.accessToken)
  const connect = useWsStore((s) => s.connect)
  const disconnect = useWsStore((s) => s.disconnect)
  const status = useWsStore((s) => s.status)
  const lastMessage = useWsStore((s) => s.lastMessage)

  useEffect(() => {
    if (accessToken) connect(accessToken)
    else disconnect()
  }, [accessToken, connect, disconnect])

  return { status, lastMessage }
}
