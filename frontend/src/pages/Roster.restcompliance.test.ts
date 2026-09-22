/**
 * Ordering of rest-day findings: still-changeable before already-worked.
 *
 * THIS CANNOT BE TESTED AGAINST THE LIVE ROSTER, which is why it is here. Every
 * breach in the demo database is in the past, so rendering it exercises the
 * split without ever splitting anything -- a partition that returned its input
 * untouched would look identical on screen. The failure only shows up on a
 * roster that has both, and by then it is a manager not seeing the shift they
 * could still have moved.
 *
 * The card shows five rows per section. The API sorts worst-first, so with no
 * partition those five are simply the five most severe, and a month of history
 * outnumbers the week ahead.
 */
import { describe, expect, it } from 'vitest'

import { actionableFirst } from './Roster'

const NOW = Date.parse('2026-09-22T12:00:00Z')
const at = (r: { when: string }) => r.when

const past = (d: string) => ({ when: `2026-09-${d}T08:00:00Z`, tag: `past-${d}` })
const soon = (d: string) => ({ when: `2026-10-${d}T08:00:00Z`, tag: `soon-${d}` })

describe('actionableFirst', () => {
  it('returns nothing for nothing', () => {
    expect(actionableFirst([], at, NOW)).toEqual([])
  })

  it('marks a finished shift as past and a future one as not', () => {
    const [[, wasPast]] = actionableFirst([past('01')], at, NOW)
    const [[, stillAhead]] = actionableFirst([soon('01')], at, NOW)
    expect(wasPast).toBe(true)
    expect(stillAhead).toBe(false)
  })

  it('puts the upcoming findings first', () => {
    const rows = [past('01'), soon('05'), past('10'), soon('02')]
    expect(actionableFirst(rows, at, NOW).map(([r]) => r.tag)).toEqual([
      'soon-05', 'soon-02', 'past-01', 'past-10',
    ])
  })

  it('keeps the order the API sorted each group into', () => {
    // The API sorts worst-first and that must survive. Re-sorting by date here
    // would push the 19-day run below a 7-day one that happens to be sooner.
    const rows = [soon('09'), soon('03'), past('20'), past('02')]
    expect(actionableFirst(rows, at, NOW).map(([r]) => r.tag)).toEqual([
      'soon-09', 'soon-03', 'past-20', 'past-02',
    ])
  })

  it('does not drop or duplicate a single finding', () => {
    // A partition built from two filters loses anything the predicates disagree
    // about, and silently: the card would just show fewer breaches than exist.
    const rows = [past('01'), soon('05'), past('10'), soon('02'), past('15')]
    const out = actionableFirst(rows, at, NOW)
    expect(out).toHaveLength(rows.length)
    expect(new Set(out.map(([r]) => r.tag)).size).toBe(rows.length)
  })

  it('the five rows that fit are the actionable ones, not the most recent', () => {
    // THE CASE THE CARD ACTUALLY HITS. Thirty days of history against a handful
    // of upcoming breaches: without the partition the slice(0, 5) shows five
    // things nobody can do anything about.
    const rows = [
      ...Array.from({ length: 20 }, (_, n) => past(String(n + 1).padStart(2, '0'))),
      soon('01'), soon('02'),
    ]
    const shown = actionableFirst(rows, at, NOW).slice(0, 5)
    expect(shown.filter(([, isPast]) => !isPast)).toHaveLength(2)
    expect(shown.slice(0, 2).map(([r]) => r.tag)).toEqual(['soon-01', 'soon-02'])
  })
})
