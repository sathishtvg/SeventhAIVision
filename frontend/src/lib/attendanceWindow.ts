/**
 * Opens the live Attendance monitor in its own window, for multi-monitor
 * control-room setups (or quick access without losing the current view).
 * Mirrors liveWallWindow.ts's Electron-IPC-vs-plain-window.open() split —
 * see that file for why Electron needs an explicit IPC call instead of a
 * bare window.open().
 */
export function openAttendanceWindow(): void {
  if (window.electronAPI?.openAttendanceWindow) {
    void window.electronAPI.openAttendanceWindow()
    return
  }
  window.open('/attendance', '_blank', 'width=1280,height=800')
}
