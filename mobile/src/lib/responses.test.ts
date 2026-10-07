/**
 * The rules of the response card.
 *
 * On screen the card is a few buttons and a line about time. Which buttons, in
 * which order, and whether that line says "in 3 min" or "3 min ago" is what a
 * guard acts on, and none of it shows as wrong in a screenshot.
 */
import { STATE_LABEL, arrivalText, offered, refusal, sendingFor, span, summary } from './responses'
import type { Clock, May, MySending } from '@/api/responses'

const NO: May = { accept: false, decline: false, en_route: false, arrived: false, report: false, stand_down: false }
const clock = (over: Partial<Clock>): Clock => ({
  started_at: '2026-10-07T02:55:00Z', due_at: '2026-10-07T03:00:00Z', met_at: null, breached: false, running: true,
  seconds_left: 180, ...over })
const sending = (over: Partial<MySending> = {}): MySending => ({
  id: 'i1', title: 'Forced gate', description: null, severity: 'high', status: 'in_progress', site_name: 'Factory A',
  camera_name: 'North Gate', camera_location: 'Gate', dispatched_at: '2026-10-07T02:55:00Z', dispatch_notes: null,
  response: { id: 'r1', incident_id: 'i1', guard_user_id: 'g1', guard_name: 'Tan Wei Ming',
              dispatched_at: '2026-10-07T02:55:00Z', state: 'SENT', accepted_at: null, en_route_at: null, arrived_at: null },
  arrival: clock({}), may: { ...NO, accept: true, decline: true, en_route: true, arrived: true, report: true }, ...over })

describe('offered', () => {
  it('offers nothing to somebody with nothing they may do', () => {
    expect(offered(undefined)).toEqual([])
    expect(offered(NO)).toEqual([])
  })

  it('puts the step forward first and the way out last', () => {
    expect(offered(sending().may).map((s) => s.key)).toEqual(['accept', 'en_route', 'arrived', 'report', 'decline'])
  })

  it('offers only what the server says may be done', () => {
    // On the way: there is nothing left to accept, and it is too late to say no.
    expect(offered({ ...NO, arrived: true, report: true }).map((s) => s.label)).toEqual(['I am there', 'Report what I found'])
    // There: only the report is left.
    expect(offered({ ...NO, report: true }).map((s) => s.key)).toEqual(['report'])
  })

  it('never offers a guard the desk’s step, even if the server allowed it', () => {
    expect(offered({ ...NO, stand_down: true })).toEqual([])
  })

  it('asks for words only where the server requires them', () => {
    const needs = Object.fromEntries(offered(sending().may).map((s) => [s.key, s.needsText]))
    expect(needs).toEqual({ accept: false, en_route: false, arrived: false, report: true, decline: true })
  })
})

describe('arrivalText', () => {
  it('says nothing when no time to arrive is set', () => {
    expect(arrivalText(undefined)).toBeNull()
    expect(arrivalText(clock({ due_at: null, running: false, seconds_left: null }))).toBeNull()
  })

  it('says how long is left, and how long ago it ran out', () => {
    expect(arrivalText(clock({ seconds_left: 180 }))).toEqual({ text: 'Expected there in 3 min', late: false })
    expect(arrivalText(clock({ seconds_left: 40 }))).toEqual({ text: 'Expected there in 40 s', late: false })
    expect(arrivalText(clock({ seconds_left: -95, breached: true }))).toEqual({ text: 'Expected there 2 min ago', late: true })
  })

  it('says how it ended once the guard is there', () => {
    expect(arrivalText(clock({ running: false, seconds_left: null }))).toEqual({ text: 'You arrived in time', late: false })
    expect(arrivalText(clock({ running: false, seconds_left: null, breached: true })))
      .toEqual({ text: 'You arrived after the time allowed', late: true })
  })
})

test('a length of time in the words a person would use', () => {
  expect([5, 59, 60, 90, 3600, 5400].map(span)).toEqual(['5 s', '59 s', '1 min', '2 min', '1 h', '1 h 30 min'])
})

test('finds the sending for an incident, and nothing for one the guard is not on', () => {
  const items = [sending(), sending({ id: 'i2', title: 'Another' })]
  expect(sendingFor(items, 'i2')?.title).toBe('Another')
  expect(sendingFor(items, 'i9')).toBeNull()
  expect(sendingFor(undefined, 'i1')).toBeNull()
})

test('the home card says where, and where the response stands', () => {
  expect(summary(sending())).toBe('Factory A · Gate — You have been sent')
  expect(summary(sending({ camera_location: null, response: { ...sending().response, state: 'EN_ROUTE' } })))
    .toBe('Factory A · North Gate — You are on the way')
  expect(summary(sending({ site_name: null, camera_location: null, camera_name: null }))).toBe('You have been sent')
  expect(Object.keys(STATE_LABEL).sort()).toEqual(['ACCEPTED', 'ARRIVED', 'DECLINED', 'EN_ROUTE', 'SENT', 'STOOD_DOWN'])
})

test('a refusal is given in the server’s own words', () => {
  const refused = (detail: unknown) => ({ response: { data: { detail } } })
  expect(refusal(refused('Only the guard who was sent on this incident can answer for the response.')))
    .toBe('Only the guard who was sent on this incident can answer for the response.')
  expect(refusal(refused({ message: 'Your arrival is already recorded.', state: 'ARRIVED' })))
    .toBe('Your arrival is already recorded.')
  expect(refusal(new Error('Network Error'))).toMatch(/Check your connection/)
})
