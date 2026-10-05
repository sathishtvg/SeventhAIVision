/**
 * The intelligence calls, on the wire — and the rules of the phone's situation
 * screens, with nothing mounted.
 *
 * WHY THIS MATTERS
 *   A decision is refused by the server if its body carries a field the server
 *   does not know, and a wrong path is a button that does nothing. Both are
 *   found by a guard at a gate at night unless they are found here.
 */
import {
  decideSituation, getMySituations, getSituationAuthority, getSituationObservations, getSituationRecommendations,
  getSituationResponders, intelApiError, recordSituationReview, reportFromSituation,
  type Authority, type AuthorityAction, type DecisionAction,
} from './securityIntelligence'
import { apiClient } from './client'
import {
  PHONE_DECISIONS, acceptable, isIntelRealtime, newClientRef, nextStage, offered, pct, riskText, sourcesText,
} from '@/lib/situations'

jest.mock('./client', () => {
  const actual = jest.requireActual('./client')
  return {
    ...actual,
    apiClient: {
      get: jest.fn(() => Promise.resolve({ data: [] })),
      post: jest.fn(() => Promise.resolve({ data: {} })),
      defaults: { baseURL: 'http://10.0.0.5:8000/', headers: { common: { Authorization: 'Bearer abc' } } },
    },
  }
})

const get = apiClient.get as jest.Mock
const post = apiClient.post as jest.Mock
const BASE = '/api/v1/security-intelligence'

beforeEach(() => {
  get.mockClear()
  post.mockClear()
})

// ── Reading ───────────────────────────────────────────────────────────────

test('asks for what is in front of this person, and for one situation by its own paths', async () => {
  get.mockResolvedValueOnce({ data: [{ id: 's1' }] })
  expect(await getMySituations()).toEqual([{ id: 's1' }])
  await getSituationRecommendations('s1')
  await getSituationAuthority('s1')
  await getSituationObservations('s1')
  await getSituationResponders('s1')
  expect(get.mock.calls.map((c) => c[0])).toEqual([
    `${BASE}/my-situations`, `${BASE}/situations/s1/recommendations`, `${BASE}/situations/s1/authority`,
    `${BASE}/situations/s1/observations`, `${BASE}/situations/s1/responders`,
  ])
})

test('an empty answer is an empty list, not something a list cannot draw', async () => {
  get.mockResolvedValueOnce({ data: null })
  expect(await getMySituations()).toEqual([])
  get.mockResolvedValueOnce({ data: undefined })
  expect(await getSituationObservations('s1')).toEqual([])
})

// ── Deciding ──────────────────────────────────────────────────────────────

test('a decision says it came from the phone and carries one reference per press', async () => {
  await decideSituation('s1', { action: 'RESOLVE', reason_code: 'AUTHORISED_ACTIVITY', note: 'Cleaner.',
                               seen_assessment_id: 'a1', client_ref: 'ref-1' })
  expect(post).toHaveBeenCalledWith(`${BASE}/situations/s1/decisions`, {
    action: 'RESOLVE', reason_code: 'AUTHORISED_ACTIVITY', note: 'Cleaner.', seen_assessment_id: 'a1',
    client_ref: 'ref-1', via: 'mobile' })
})

test('looking at what was suggested is recorded as a look from the phone', async () => {
  await recordSituationReview('s1')
  expect(post).toHaveBeenCalledWith(`${BASE}/situations/s1/reviews`, { via: 'mobile' })
})

// ── Reporting ─────────────────────────────────────────────────────────────

test('an arrival carries the position when the phone gave one, and none when it did not', async () => {
  await reportFromSituation('s1', 'ARRIVED', { position: { latitude: 1.3, longitude: 103.8 }, clientRef: 'r1' })
  expect(post).toHaveBeenLastCalledWith(`${BASE}/situations/s1/observations`, {
    kind: 'ARRIVED', note: undefined, via: 'mobile', client_ref: 'r1', latitude: 1.3, longitude: 103.8 })
  await reportFromSituation('s1', 'ACCEPTED', { clientRef: 'r2' })
  expect(post.mock.calls[1][1]).toMatchObject({ kind: 'ACCEPTED', latitude: undefined, longitude: undefined })
})

test('an observation that says nothing is not sent', async () => {
  await expect(reportFromSituation('s1', 'OBSERVATION', { note: '   ', clientRef: 'r' })).rejects.toThrow('Say what you see.')
  await expect(reportFromSituation('s1', 'OBSERVATION', { clientRef: 'r' })).rejects.toThrow('Say what you see.')
  expect(post).not.toHaveBeenCalled()
  await reportFromSituation('s1', 'OBSERVATION', { note: '  Gate is shut.  ', clientRef: 'r' })
  expect(post.mock.calls[0][1]).toMatchObject({ kind: 'OBSERVATION', note: 'Gate is shut.' })
})

test("a refusal is shown in the server's own words", () => {
  expect(intelApiError({ response: { data: { detail: 'The decision policy does not let a guard decide here.' } } }))
    .toBe('The decision policy does not let a guard decide here.')
  expect(intelApiError({ response: { data: { detail: [{ msg: 'field required' }, { msg: 'too long' }] } } }))
    .toBe('field required; too long')
  expect(intelApiError(new Error('Network Error'))).toBe('Network Error')
  expect(intelApiError({})).toBe('Something went wrong.')
})

// ── The rules of the screens ──────────────────────────────────────────────

const ALL: DecisionAction[] = ['MONITOR', 'VERIFY', 'VIEW_CAMERA', 'VERIFY_WITH_DRONE', 'DISPATCH_GUARD', 'ESCALATE',
  'INVESTIGATE', 'CONTACT_SITE', 'CREATE_INCIDENT', 'ACKNOWLEDGE', 'CONFIRM_INCIDENT', 'REQUEST_ASSISTANCE',
  'FALSE_POSITIVE', 'RESOLVE']

function authority(suggested: DecisionAction | null, over: Partial<Record<DecisionAction, Partial<AuthorityAction>>> = {}): Authority {
  return {
    closed: false, in_reach: true, suggested_action: suggested, reasons: [],
    actions: ALL.map((action) => ({ action, allowed: true, how: 'ALONE', basis: action === suggested ? 'FOLLOWED' : 'OVERRIDE',
                                    needs_reason: false, needs: [], why_not: null, ...over[action] } as AuthorityAction)),
  }
}

test('a phone offers the decisions of a person at the gate, not the command centre\'s', () => {
  expect(offered(authority(null)).map((a) => a.action)).toEqual(PHONE_DECISIONS)
  for (const theirs of ['DISPATCH_GUARD', 'CREATE_INCIDENT', 'CONFIRM_INCIDENT', 'VERIFY_WITH_DRONE', 'CONTACT_SITE']) {
    expect(PHONE_DECISIONS).not.toContain(theirs)
  }
  expect(offered(undefined)).toEqual([])
})

test('accept follows what the layer put first, and only where this person may', () => {
  expect(acceptable(authority('INVESTIGATE'))?.action).toBe('INVESTIGATE')
  // Sending a guard is not a phone's to accept: a guard does not dispatch themselves.
  expect(acceptable(authority('DISPATCH_GUARD'))).toBeNull()
  expect(acceptable(authority('INVESTIGATE', { INVESTIGATE: { allowed: false, why_not: 'no' } }))).toBeNull()
  expect(acceptable(authority(null))).toBeNull()
  expect(acceptable(undefined)).toBeNull()
})

test('the next stage is accept, then arrived, then nothing', () => {
  expect(nextStage({ assigned_to_me: true, my_last: null })).toBe('ACCEPTED')
  expect(nextStage({ assigned_to_me: true, my_last: 'ACCEPTED' })).toBe('ARRIVED')
  expect(nextStage({ assigned_to_me: true, my_last: 'ARRIVED' })).toBeNull()
  // Nobody sent them: there is nothing to accept, only being there.
  expect(nextStage({ assigned_to_me: false, my_last: null })).toBe('ARRIVED')
})

test('what is not known is said, never shown as a number', () => {
  expect(riskText(null, null)).toBe('Not assessed yet')
  expect(riskText('HIGH', 65)).toBe('Risk 65')
  expect(pct(null)).toBe('not given')
  expect(pct(0.876)).toBe('88%')
  expect(sourcesText(['CCTV_AI', 'ACCESS_CONTROL', 'SOMETHING_NEW'])).toBe('CCTV · Access control · SOMETHING_NEW')
})

test('the layer\'s live messages are told from everyone else\'s', () => {
  expect(isIntelRealtime({ event_type: 'intel_decision_recorded' })).toBe(true)
  expect(isIntelRealtime({ event_type: 'alert_created' })).toBe(false)
  expect(isIntelRealtime({})).toBe(false)
})

test('a press has its own reference, in the shape the server takes', () => {
  const a = newClientRef()
  expect(a).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/)
  expect(newClientRef()).not.toBe(a)
})
