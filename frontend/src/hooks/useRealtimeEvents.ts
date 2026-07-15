import { useEffect } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useNotificationStore } from '@/store/notifications'
import { playAlertSound } from '@/store/alertSound'
import type { RealtimeEvent } from '@/types/realtime'
import type { AlertSeverity } from '@/types/api'
import { useWebSocket } from './useWebSocket'

declare global {
  interface Window {
    electronAPI?: {
      showNotification: (title: string, body: string) => void
      setBadgeCount: (n: number) => void
      getServerUrl: () => Promise<string>
      setServerUrl: (url: string) => Promise<void>
      getAutoLaunch: () => Promise<boolean>
      setAutoLaunch: (enabled: boolean) => Promise<void>
      setKiosk: (enabled: boolean) => Promise<boolean>
      /** Opens a new, independent OS window showing Live Wall (optionally a
       * saved layout) — the renderer's own window.open() gets redirected to
       * the system browser by main.js's setWindowOpenHandler, so multi-window
       * / multi-monitor control-room use needs this explicit IPC instead. */
      openLiveWallWindow: (layoutId?: string) => Promise<void>
      /** Opens a new, independent OS window showing the live Attendance
       * monitor — same multi-monitor control-room rationale as
       * openLiveWallWindow above. */
      openAttendanceWindow: () => Promise<void>
      platform: string
      isElectron: boolean
    }
  }
}

export function useRealtimeEvents() {
  const { lastMessage } = useWebSocket()
  const queryClient = useQueryClient()
  const push = useNotificationStore((s) => s.push)
  const unreadCount = useNotificationStore((s) => s.unreadCount)

  // Keep tray badge in sync with unread count when running in Electron
  useEffect(() => {
    window.electronAPI?.setBadgeCount(unreadCount)
  }, [unreadCount])

  useEffect(() => {
    if (!lastMessage) return
    let event: RealtimeEvent
    try {
      event = JSON.parse(lastMessage) as RealtimeEvent
    } catch {
      return
    }

    switch (event.event_type) {
      case 'alert_created': {
        queryClient.invalidateQueries({ queryKey: ['alerts'] })
        queryClient.invalidateQueries({ queryKey: ['analytics-summary'] })
        queryClient.invalidateQueries({ queryKey: ['action-center'] })
        const title = (event.payload.title as string) ?? 'New alert'
        const severity = (event.payload.severity as AlertSeverity) ?? 'medium'
        push(severity, title)
        playAlertSound(severity)
        if (window.electronAPI && (severity === 'high' || severity === 'critical')) {
          window.electronAPI.showNotification('7th AI Vision — Alert', title)
        }
        break
      }
      case 'incident_created': {
        queryClient.invalidateQueries({ queryKey: ['incidents'] })
        const title = (event.payload.title as string) ?? 'New incident'
        push((event.payload.severity as AlertSeverity) ?? 'medium', title)
        if (window.electronAPI) {
          window.electronAPI.showNotification('7th AI Vision — Incident', title)
        }
        break
      }
      case 'camera_status_changed':
        queryClient.invalidateQueries({ queryKey: ['cameras'] })
        queryClient.invalidateQueries({ queryKey: ['action-center'] })
        break
      case 'attendance_status_changed':
        queryClient.invalidateQueries({ queryKey: ['attendance-live'] })
        queryClient.invalidateQueries({ queryKey: ['attendance-corrections'] })
        queryClient.invalidateQueries({ queryKey: ['action-center'] })
        break
      case 'roster_published':
        queryClient.invalidateQueries({ queryKey: ['shift-patterns'] })
        queryClient.invalidateQueries({ queryKey: ['roster-coverage'] })
        queryClient.invalidateQueries({ queryKey: ['my-shifts'] })
        break
      case 'violation_created':
        queryClient.invalidateQueries({ queryKey: ['violations'] })
        queryClient.invalidateQueries({ queryKey: ['violations-summary'] })
        break
      case 'leave_status_changed':
        queryClient.invalidateQueries({ queryKey: ['leave-requests'] })
        queryClient.invalidateQueries({ queryKey: ['leave-balances'] })
        break
    }
  }, [lastMessage, queryClient, push])
}
