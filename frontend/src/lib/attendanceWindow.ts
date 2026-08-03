import { openInNewWindow } from './popoutWindow'

/**
 * Opens the live Attendance monitor in its own window, for multi-monitor
 * control-room setups (or quick access without losing the current view).
 */
export function openAttendanceWindow(): void {
  openInNewWindow('/attendance')
}
