import { openInNewWindow, type OpenWindowOptions } from './popoutWindow'

/**
 * Opens Live Wall in its own window, for multi-monitor control-room setups
 * (or just quick access from Dashboard/Command Centre without losing the
 * current view). Pass { fullscreen: true } to have the new window go straight
 * into full screen — what launching a saved multi-screen profile wants, since
 * each extra screen is destined for its own monitor.
 */
export function openLiveWallWindow(layoutId?: string, opts?: OpenWindowOptions): void {
  openInNewWindow('/live' + (layoutId ? `?layout=${encodeURIComponent(layoutId)}` : ''), opts)
}
