/**
 * Turning a picked date into the string the API stores.
 *
 * THE TRAP IS THE TIMEZONE, and it bites exactly where this product is sold.
 * `date.toISOString().slice(0, 10)` is the obvious way to do this and is wrong
 * for everyone east of UTC: in Singapore (UTC+8) a date picked before 08:00
 * becomes the day before. A guard asking for Monday off would be given Sunday,
 * and the form would look perfectly correct while doing it.
 */
import { toISODate, fromISODate } from './DateField'

describe('toISODate', () => {
  it('uses the local calendar date, not UTC', () => {
    // 00:30 local. toISOString() would say the 22nd anywhere east of UTC.
    const early = new Date(2026, 8, 23, 0, 30)
    expect(toISODate(early)).toBe('2026-09-23')
  })

  it('still uses the local date late at night', () => {
    const late = new Date(2026, 8, 23, 23, 45)
    expect(toISODate(late)).toBe('2026-09-23')
  })

  it('pads months and days', () => {
    expect(toISODate(new Date(2026, 0, 5, 12))).toBe('2026-01-05')
  })
})

describe('fromISODate', () => {
  it('reads a stored date back as the same local day', () => {
    const d = fromISODate('2026-09-23')!
    expect([d.getFullYear(), d.getMonth() + 1, d.getDate()]).toEqual([2026, 9, 23])
  })

  it('round-trips', () => {
    expect(toISODate(fromISODate('2026-12-31')!)).toBe('2026-12-31')
  })

  it('returns null for nothing, or for something that is not a date', () => {
    expect(fromISODate('')).toBeNull()
    expect(fromISODate(null)).toBeNull()
    expect(fromISODate('23/09/2026')).toBeNull()
    expect(fromISODate('2026-13-45')).toBeNull()
  })
})
