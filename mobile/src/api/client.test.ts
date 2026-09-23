/**
 * Unwrapping a list response.
 *
 * This is the shape mismatch that blanked the Alerts and Incidents tabs for
 * every user: the server sends {items, total, ...} and the screens expected an
 * array. A FlatList given an object renders nothing and says nothing.
 */
import { rows } from './client'

describe('rows', () => {
  it('returns a paginated response body as its items', () => {
    expect(rows({ items: [1, 2, 3], total: 3 } as any)).toEqual([1, 2, 3])
  })

  it('passes a bare array straight through', () => {
    // Most endpoints in this app still answer with an array.
    expect(rows([1, 2])).toEqual([1, 2])
  })

  it('gives an empty list rather than crashing on nothing', () => {
    expect(rows(undefined)).toEqual([])
    expect(rows(null)).toEqual([])
    expect(rows({} as any)).toEqual([])
  })
})
