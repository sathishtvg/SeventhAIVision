import { describe, expect, it } from 'vitest'

import type { DraftShift, RosterBatchListItem } from '@/api/roster'
import { busiestDay, draftExplanation, draftListNote, generatedMessage, publishedMessage } from './draftMessages'

const post = (day: number, hour: number, guard: string | null): DraftShift => ({
  id: `${day}-${hour}-${guard}`,
  guard_user_id: guard,
  site_id: 's1',
  scheduled_start: new Date(2026, 10, day, hour).toISOString(),
  scheduled_end: new Date(2026, 10, day, hour + 12).toISOString(),
  shift_type: hour < 12 ? 'day' : 'night',
  warnings: [],
  guard_name: null,
  site_name: 'Site',
})

const summary = (people?: number) => ({
  unfilled_slots: 0, coverage_shortfalls: 0, missing_supervisor_days: 0, missing_manager_days: 0,
  people_available: people,
})

const saved = (id: string, start: string, end: string, posts: number, site: string | null = null): RosterBatchListItem => ({
  id, site_id: site, site_name: null, period_start: start, period_end: end, status: 'draft',
  generated_at: '2026-10-25T10:00:00Z', published_at: null, generated_by: null, posts, filled: posts,
})

describe('roster messages', () => {
  it('counts the busiest day', () => {
    expect(busiestDay([post(1, 7, 'a'), post(1, 19, null), post(2, 7, 'a')])).toBe(2)
    expect(busiestDay([])).toBe(0)
  })

  it('says an empty draft has nothing to publish', () => {
    const said = draftExplanation(summary(4), [])
    expect(said.tone).toBe('error')
    expect(said.text).toMatch(/no posts/)
    expect(said.text).toMatch(/Discard it/)
  })

  it('blames too few people when the busiest day needs more than can be rostered', () => {
    const shifts = [post(1, 7, 'a'), post(1, 7, 'b'), post(1, 19, null), post(1, 19, null)]
    const said = draftExplanation(summary(2), shifts)
    expect(said.tone).toBe('warning')
    expect(said.text).toMatch(/^2 of 4 posts are filled; 2 are open\./)
    expect(said.text).toMatch(/needs 4 people, but only 2 can be rostered/)
    expect(said.text).toMatch(/open posts are left out/)
  })

  it('blames the rules when there were people enough', () => {
    const said = draftExplanation(summary(10), [post(1, 7, 'a'), post(1, 19, null)])
    expect(said.text).toMatch(/rules left them open/)
  })

  it('says nothing would be published when no post is filled', () => {
    const said = draftExplanation(summary(0), [post(1, 7, null)])
    expect(said.tone).toBe('error')
    expect(said.text).toMatch(/publishing would add nothing/)
  })

  it('says a full draft is ready', () => {
    expect(draftExplanation(summary(2), [post(1, 7, 'a')])).toEqual({
      tone: 'success', text: 'The one post is filled. Check the draft, then Publish & Notify.',
    })
    expect(draftExplanation(summary(2), [post(1, 7, 'a'), post(1, 19, 'b')]).text).toMatch(/^All 2 posts are filled/)
  })

  it('reads a draft made before people_available was recorded', () => {
    expect(draftExplanation(null, [post(1, 7, 'a'), post(1, 19, null)]).text).toMatch(/rules left them open/)
  })

  it('says what publishing and the 7-day run did', () => {
    expect(publishedMessage(3).text).toMatch(/3 shifts added/)
    expect(publishedMessage(0).tone).toBe('warning')
    expect(generatedMessage(5, 2).text).toMatch(/^5 shifts added/)
    expect(generatedMessage(0, 0).text).toMatch(/no active roster patterns.*Auto-Schedule/)
    expect(generatedMessage(0, 3).text).toMatch(/already have every shift/)
  })

  it('notes empty and overlapping drafts', () => {
    const empty = saved('e', '2026-10-01', '2026-10-31', 0)
    const october = saved('o', '2026-10-25', '2026-10-31', 56)
    const lateOctober = saved('l', '2026-10-28', '2026-11-03', 20)
    const november = saved('n', '2026-11-05', '2026-11-30', 200)
    const otherSite = saved('x', '2026-10-25', '2026-10-31', 14, 'site-2')
    const all = [empty, october, lateOctober, november]
    expect(draftListNote(empty, all)?.text).toMatch(/^Empty/)
    expect(draftListNote(october, all)?.text).toMatch(/same days as another draft/)
    expect(draftListNote(november, all)).toBeNull()
    expect(draftListNote(otherSite, [otherSite, saved('y', '2026-10-25', '2026-10-31', 14, 'site-3')])).toBeNull()
    expect(draftListNote(otherSite, [otherSite, october])?.tone).toBe('warning')
  })
})
