import { create } from 'zustand'
import type { AlertSeverity } from '@/types/api'

export interface Toast {
  id: string
  severity: AlertSeverity
  title: string
  createdAt: number
}

interface NotificationState {
  toasts: Toast[]
  unreadCount: number
  push: (severity: AlertSeverity, title: string) => void
  dismiss: (id: string) => void
  clearAll: () => void
  markRead: () => void
}

export const useNotificationStore = create<NotificationState>((set) => ({
  toasts: [],
  unreadCount: 0,

  push(severity, title) {
    const toast: Toast = { id: crypto.randomUUID(), severity, title, createdAt: Date.now() }
    set((s) => ({
      toasts: [toast, ...s.toasts].slice(0, 20),
      unreadCount: s.unreadCount + 1,
    }))
    // Auto-dismiss after 6s
    setTimeout(() => {
      set((s) => ({ toasts: s.toasts.filter((t) => t.id !== toast.id) }))
    }, 6000)
  },

  dismiss(id) {
    set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) }))
  },

  clearAll() {
    set({ toasts: [] })
  },

  markRead() {
    set({ unreadCount: 0 })
  },
}))
