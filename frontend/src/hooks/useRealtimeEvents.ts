import { useEffect } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useNotificationStore } from '@/store/notifications'
import { playAlertSound, announceAlert } from '@/store/alertSound'
import { PRODUCT_NAME } from '@/lib/brand'
import type { RealtimeEvent } from '@/types/realtime'
import type { AlertSeverity } from '@/types/api'
import { useWebSocket } from './useWebSocket'
import { useVisitorEntryStore } from '@/store/visitorEntry'
import type { VisitorEntryPrompt } from '@/api/vms'

/** Read off the store rather than via a hook: this is called from inside the
 * message handler, not during render. */
const enqueueVisitorEntry = (p: VisitorEntryPrompt) =>
  useVisitorEntryStore.getState().enqueue(p)

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
      /** Opens a new, independent OS window at the given app-relative route
       * (e.g. "/live?layout=abc123", "/attendance", "/command-centre",
       * "/action-center") — the renderer's own window.open() gets
       * redirected to the system browser by main.js's setWindowOpenHandler,
       * so multi-window / multi-monitor control-room use needs this
       * explicit IPC instead. Secondary windows are auto-spread across
       * physical displays when more than one is connected. */
      openSecondaryWindow: (routePath: string) => Promise<void>
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
      // Entry LPR read a plate that needs a human. Queued rather than shown
      // directly so the dialog (mounted once in AppShell) surfaces on whatever
      // screen the operator is already watching.
      case 'visitor_entry_prompt': {
        enqueueVisitorEntry(event.payload as unknown as VisitorEntryPrompt)
        queryClient.invalidateQueries({ queryKey: ['vms-onsite'] })
        break
      }
      // Exit LPR closed a visit — the vehicle has left, so the on-site list
      // and its parking clock are stale.
      case 'visitor_exit_recorded': {
        queryClient.invalidateQueries({ queryKey: ['vms-onsite'] })
        queryClient.invalidateQueries({ queryKey: ['visitors'] })
        break
      }
      case 'barrier_command': {
        queryClient.invalidateQueries({ queryKey: ['barriers'] })
        queryClient.invalidateQueries({ queryKey: ['barrier-commands'] })
        break
      }
      case 'alert_created': {
        queryClient.invalidateQueries({ queryKey: ['alerts'] })
        queryClient.invalidateQueries({ queryKey: ['analytics-summary'] })
        queryClient.invalidateQueries({ queryKey: ['action-center'] })
        const title = (event.payload.title as string) ?? 'New alert'
        const severity = (event.payload.severity as AlertSeverity) ?? 'medium'
        const moduleType = (event.payload.module_type as string) ?? null
        // site_name is added by the API's Redis→WS bridge; workers don't send it.
        const siteName = (event.payload.site_name as string) ?? null
        push(severity, title)
        playAlertSound(severity)
        announceAlert(severity, moduleType, siteName)
        if (window.electronAPI && (severity === 'high' || severity === 'critical')) {
          window.electronAPI.showNotification(`${PRODUCT_NAME} — Alert`, title)
        }
        break
      }
      case 'incident_created': {
        queryClient.invalidateQueries({ queryKey: ['incidents'] })
        const title = (event.payload.title as string) ?? 'New incident'
        push((event.payload.severity as AlertSeverity) ?? 'medium', title)
        if (window.electronAPI) {
          window.electronAPI.showNotification(`${PRODUCT_NAME} — Incident`, title)
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
