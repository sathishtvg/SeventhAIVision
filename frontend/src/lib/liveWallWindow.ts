/**
 * Opens Live Wall in its own window, for multi-monitor control-room setups
 * (or just quick access from Dashboard/Command Centre without losing the
 * current view). In Electron this needs an explicit IPC call — the
 * renderer's own window.open() is intercepted by main.js and redirected to
 * the system browser. In a plain browser, window.open() already opens a
 * real second window/tab the operator can drag to another monitor.
 */
export function openLiveWallWindow(layoutId?: string): void {
  if (window.electronAPI?.openLiveWallWindow) {
    void window.electronAPI.openLiveWallWindow(layoutId)
    return
  }
  const path = '/live' + (layoutId ? `?layout=${encodeURIComponent(layoutId)}` : '')
  window.open(path, '_blank', 'width=1280,height=800')
}
