import { create } from 'zustand'

export type WsStatus = 'connecting' | 'open' | 'closed'

// Module-level singleton — ONE WebSocket connection for the whole app regardless
// of how many components call useWebSocket().
let _ws: WebSocket | null = null
let _retryTimer: ReturnType<typeof setTimeout> | null = null
let _retryDelay = 1_000
let _currentToken: string | null = null

interface WsState {
  status: WsStatus
  lastMessage: string | null
  connect: (token: string) => void
  disconnect: () => void
}

export const useWsStore = create<WsState>((set) => ({
  status: 'closed',
  lastMessage: null,

  connect(token) {
    // Already connected/connecting with this exact token — no-op
    if (
      _currentToken === token &&
      _ws &&
      (_ws.readyState === WebSocket.OPEN || _ws.readyState === WebSocket.CONNECTING)
    ) return

    _currentToken = token
    if (_retryTimer) { clearTimeout(_retryTimer); _retryTimer = null }
    if (_ws) { _ws.onclose = null; _ws.close(); _ws = null }
    _retryDelay = 1_000

    const dial = () => {
      // In Electron the page loads via app:// so window.location.host is '.'
      // which produces an invalid ws://./ws/live URL. Use the stored server URL
      // instead, converting http:// → ws:// and https:// → wss://.
      let url: string
      const electronUrl = (window as any).electronAPI?.serverUrl as string | undefined
      if (electronUrl) {
        const wsBase = electronUrl.replace(/^http(s?):\/\//, (_, s) => `ws${s}://`)
        url = `${wsBase}/ws/live?token=${_currentToken}`
      } else {
        const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
        url = `${proto}//${window.location.host}/ws/live?token=${_currentToken}`
      }
      set({ status: 'connecting' })
      _ws = new WebSocket(url)

      _ws.onopen = () => { set({ status: 'open' }); _retryDelay = 1_000 }
      _ws.onmessage = (e) => { set({ lastMessage: e.data as string }) }
      _ws.onclose = () => {
        _ws = null
        set({ status: 'closed' })
        if (_currentToken) {
          _retryTimer = setTimeout(() => {
            _retryDelay = Math.min(_retryDelay * 2, 30_000)
            if (_currentToken) dial()
          }, _retryDelay)
        }
      }
      _ws.onerror = () => _ws?.close()
    }

    dial()
  },

  disconnect() {
    _currentToken = null
    if (_retryTimer) { clearTimeout(_retryTimer); _retryTimer = null }
    if (_ws) { _ws.onclose = null; _ws.close(); _ws = null }
    set({ status: 'closed', lastMessage: null })
  },
}))
