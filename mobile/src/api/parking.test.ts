/**
 * How full a car park is.
 *
 * Shown as a percentage and a bar, so a wrong denominator is invisible: it just
 * draws a confident bar at the wrong length.
 */
import { occupancyPct } from './parking'

describe('occupancyPct', () => {
  it('counts occupied against bays that exist', () => {
    expect(occupancyPct({ bay_count: 200, occupied_bays: 50 })).toBe(25)
  })

  it('says nothing when no bays have been mapped', () => {
    // A car park declared to hold 500 cars but with no bays recorded is
    // unknown, not empty — drawing 0% would state something nobody knows.
    expect(occupancyPct({ bay_count: 0, occupied_bays: 0 })).toBeNull()
  })

  it('handles a full car park', () => {
    expect(occupancyPct({ bay_count: 80, occupied_bays: 80 })).toBe(100)
  })

  it('rounds to whole percent', () => {
    expect(occupancyPct({ bay_count: 3, occupied_bays: 1 })).toBe(33)
  })
})
