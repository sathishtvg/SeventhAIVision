/**
 * The words of the "Sent by email" screens (4 November 2026): when a schedule sends, and what became of an email.
 */
import type { EmailStatus, Schedule } from '@/api/opsDelivery'

export const WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
export const STATUS_LABEL: Record<EmailStatus, string> = {
  PENDING: 'Waiting to be sent', PROCESSING: 'Being sent', SENT: 'Sent', FAILED: 'Not sent yet', LEFT_OUT: 'Left out',
}
export const STATUS_COLOUR: Record<EmailStatus, 'default' | 'success' | 'warning' | 'error' | 'info'> = {
  PENDING: 'info', PROCESSING: 'info', SENT: 'success', FAILED: 'error', LEFT_OUT: 'warning',
}
export const hourWords = (h: number) => `${String(h).padStart(2, '0')}:00`

/** When a schedule sends, in words: "Every day at 07:00", "Every Monday at 07:00", "On the 1st of each month at 07:00". */
export function whenWords(s: Pick<Schedule, 'frequency' | 'hour' | 'weekday' | 'month_day'>): string {
  if (s.frequency === 'DAILY') return `Every day at ${hourWords(s.hour)}, for the day before`
  if (s.frequency === 'WEEKLY') return `Every ${WEEKDAYS[s.weekday ?? 0]} at ${hourWords(s.hour)}, for the week before`
  const d = s.month_day ?? 1
  const th = d % 10 === 1 && d !== 11 ? 'st' : d % 10 === 2 && d !== 12 ? 'nd' : d % 10 === 3 && d !== 13 ? 'rd' : 'th'
  return `On the ${d}${th} of each month at ${hourWords(s.hour)}, for the month before`
}
