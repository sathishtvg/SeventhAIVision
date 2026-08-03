/** Query flag the opened window reads on mount to enter full screen by itself
 * (see useKioskToggle). Exported so both the opener and the reader agree on
 * the name rather than duplicating a magic string. */
export const FULLSCREEN_PARAM = 'fullscreen'

export interface OpenWindowOptions {
  /** Open the new window already in full screen — "new window AND into full
   * screen" as one action, which is what a control-room operator wants when
   * sending a view to another monitor. The opener can't fullscreen a window it
   * doesn't own, so this is passed as a query flag the new window acts on. */
  fullscreen?: boolean
}

/**
 * Opens a route in its own independent OS window — the generalized form of
 * the Electron-IPC-vs-window.open() split every "pop out to a new window"
 * caller needs (Live Wall, Attendance, Command Centre, Action Center). In
 * Electron, the renderer's own window.open() is intercepted by main.js and
 * redirected to the system browser, so a secondary window needs an
 * explicit IPC call instead; in a plain browser, window.open() already
 * opens a real second window/tab the operator can drag to another monitor.
 *
 * routePath should be a full app-relative path including any query string,
 * e.g. "/live?layout=abc123" — Electron's main process treats it as an
 * opaque string and doesn't need to know what any of the query params mean.
 */
export function openInNewWindow(routePath: string, opts: OpenWindowOptions = {}): void {
  let target = routePath
  if (opts.fullscreen) {
    target += (target.includes('?') ? '&' : '?') + `${FULLSCREEN_PARAM}=1`
  }
  if (window.electronAPI?.openSecondaryWindow) {
    void window.electronAPI.openSecondaryWindow(target)
    return
  }
  window.open(target, '_blank', 'width=1280,height=800')
}
