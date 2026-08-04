'use strict'

const { contextBridge, ipcRenderer } = require('electron')

// Read synchronously so it's available before React's axios client initialises
const _serverUrl = ipcRenderer.sendSync('get-server-url-sync')

contextBridge.exposeInMainWorld('electronAPI', {
  // Synchronous — available immediately when window.electronAPI is accessed
  serverUrl: _serverUrl,

  // Server URL management
  getServerUrl: () => ipcRenderer.invoke('get-server-url'),
  setServerUrl: (url) => ipcRenderer.invoke('set-server-url', url),

  // Native notifications (used by useRealtimeEvents hook)
  showNotification: (title, body) => ipcRenderer.invoke('show-notification', { title, body }),

  // Tray badge — number of unread alerts
  setBadgeCount: (n) => ipcRenderer.invoke('set-badge-count', n),

  // Auto-launch toggle (Settings page)
  getAutoLaunch: () => ipcRenderer.invoke('get-auto-launch'),
  setAutoLaunch: (enabled) => ipcRenderer.invoke('set-auto-launch', enabled),

  // Kiosk mode for the control-room video wall (Gap 83) — resolves to the
  // new kiosk state so the renderer can sync its toggle button. Kiosks
  // whichever window made this call (main.js resolves it from the IPC
  // event's own sender), not always the main window.
  setKiosk: (enabled) => ipcRenderer.invoke('set-kiosk', enabled),

  // Multi-monitor control room: opens an independent window at the given
  // app-relative route (e.g. "/live?layout=abc123", "/attendance",
  // "/command-centre", "/action-center"). Secondary windows are
  // auto-spread across physical displays when more than one is connected.
  openSecondaryWindow: (routePath) => ipcRenderer.invoke('open-secondary-window', routePath),

  // Platform detection so frontend can show desktop-specific UI
  platform: process.platform,
  isElectron: true,
})
