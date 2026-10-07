/**
 * The rules of the phone's visitor authorisation cards.
 *
 * WHY THIS MATTERS
 *   What a guard reads at the gate is the record, not a verdict. The colour
 *   and the words here are the only place the phone could turn "the host has
 *   not answered" into something that sounds like a finding about the visitor.
 */
import type { Authorisation, Standing } from '@/api/visitorAuth'
import {
  ID_KINDS, STANDING_LABEL, canAsk, isReason, tone, waitingHeading, waitingMeta, whoWaits,
} from './visitorAuth'

const ALL: Standing[] = ['NOT_ASKED', 'AWAITING_HOST', 'LAPSED', 'DECLINED', 'CANCELLED', 'NOT_YET_VALID', 'VALID', 'EXPIRED']

const waiting = (over: Partial<Authorisation> = {}): Authorisation => ({
  id: 'a1', site_name: 'Factory A', purpose: 'Lift servicing', standing: 'AWAITING_HOST',
  says: ['Asked of Tan Wei Ming at 08:40. Not yet answered.'], valid_from: '2026-10-07T01:00:00Z',
  valid_until: '2026-10-07T09:00:00Z', host_name: 'Tan Wei Ming', id_document_kind: null,
  subject: { kind: 'visit', id: 'v1', name: 'Lim Mei Ling', company: 'Acme Lifts', detail: 'walk_in' },
  asked_of_me: true, may: { approve: true, decline: true, cancel: false, id_seen: true }, ...over })

test('every standing has words, and none of them is a word for the visitor', () => {
  for (const s of ALL) {
    expect(STANDING_LABEL[s]).toBeTruthy()
    expect(STANDING_LABEL[s]).not.toMatch(/unauthori|intru|suspect|violat|breach|trespass|illegal/i)
  }
})

test('only a valid authorisation is green, and a no is the colour of a no', () => {
  expect(tone('VALID')).toBe('good')
  expect(tone('AWAITING_HOST')).toBe('wait')
  expect(tone('NOT_YET_VALID')).toBe('wait')
  expect(tone('DECLINED')).toBe('stop')
  expect(tone('CANCELLED')).toBe('stop')
  // Nothing asked, never answered, run out: not good and not a no either.
  for (const s of ['NOT_ASKED', 'LAPSED', 'EXPIRED'] as Standing[]) expect(tone(s)).toBe('plain')
  expect(ALL.filter((s) => tone(s) === 'good')).toEqual(['VALID'])
})

test('the gate is offered "Ask the host" only when nothing stands that could still be answered or used', () => {
  expect(ALL.filter(canAsk)).toEqual(['NOT_ASKED', 'LAPSED', 'DECLINED', 'CANCELLED', 'EXPIRED'])
})

test('the heading counts what is waiting', () => {
  expect(waitingHeading([waiting()])).toBe('1 visit is waiting for your answer')
  expect(waitingHeading([waiting(), waiting({ id: 'a2' })])).toBe('2 visits are waiting for your answer')
})

test('a visitor is named with their company and a contractor by the permit', () => {
  expect(whoWaits(waiting())).toBe('Lim Mei Ling (Acme Lifts)')
  expect(whoWaits(waiting({ subject: { kind: 'visit', id: 'v', name: 'Goh Kim Huat', company: null, detail: null } })))
    .toBe('Goh Kim Huat')
  expect(whoWaits(waiting({ subject: { kind: 'work_permit', id: 'w', name: 'Coolair Services', company: 'Coolair Services',
                                       detail: 'Permit WP-0042: Chiller overhaul' } }))).toBe('Coolair Services — work permit')
  expect(whoWaits(waiting({ subject: { kind: 'visit', id: 'v', name: null, company: null, detail: null } }))).toBe('A visitor')
})

test('where and when, what for, and that no host is named when none is', () => {
  const stamp = (iso: string) => iso.slice(11, 16)
  expect(waitingMeta(waiting(), stamp)).toBe('Factory A · 01:00 to 09:00 · Lift servicing')
  expect(waitingMeta(waiting({ asked_of_me: false, purpose: null }), stamp)).toBe('Factory A · 01:00 to 09:00 · no host is named')
  const permit = waiting({ purpose: 'ignored for a permit', subject: {
    kind: 'work_permit', id: 'w', name: 'Coolair Services', company: null, detail: 'Permit WP-0042: Chiller overhaul' } })
  expect(waitingMeta(permit, stamp)).toBe('Factory A · 01:00 to 09:00 · Permit WP-0042: Chiller overhaul')
})

test('what is offered for an ID is a kind of document, never somewhere to put its number', () => {
  expect(ID_KINDS).toContain('Passport')
  expect(ID_KINDS).toContain('Work pass')
  for (const kind of ID_KINDS) {
    expect(kind).not.toMatch(/\d/)
    expect(kind.length).toBeLessThanOrEqual(30)
  }
})

test('a reason is something other than spaces', () => {
  expect(isReason('')).toBe(false)
  expect(isReason('   \n')).toBe(false)
  expect(isReason(' Not expected today. ')).toBe(true)
})
