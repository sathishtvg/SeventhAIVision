/**
 * A camera's latest check and its recording gaps on the phone (Phase 6 of the known limits, 4 November 2026).
 *
 * WHY THIS MATTERS
 *   A guard looking at a camera is told what the platform measured of it, in the web's words: the figures of its
 *   last check, why a check could not run, a check too old to go by greyed and not called wrong, and its gaps.
 *   Nothing is worked out here - the server says what broke a limit.
 */
import { apiClient } from '@/api/client'
import { getLatestChecks, type Check, type CheckedStream } from '@/api/cameraChecks'
import { brokeText, checkText, gapText, length, streamLine } from './cameraCheckWords'

jest.mock('@/api/client', () => ({
  apiClient: { get: jest.fn(() => Promise.resolve({ data: { cameras: [], note: '' } })) },
}))

const NOW = new Date('2026-11-04T06:00:00Z')
const at = (minutes: number) => new Date(NOW.getTime() - minutes * 60_000).toISOString()
const check = (over: Partial<Check> = {}): Check => ({
  checked_at: at(12), result: 'OK', result_text: 'Checked', stated_rate: 15, received_rate: 4.5,
  longest_stall_ms: 7000, missed_pct: 0, answer_ms: 40, first_picture_ms: 1200,
  breaks: [{ limit: 'least_rate_pct', text: 'frame rate 4.5 of 15' }, { limit: 'most_stall_ms', text: 'a stall of 7.0 s' }],
  stale: false, ...over })
const stream = (over: Partial<CheckedStream> = {}): CheckedStream => ({
  stream_id: 's1', not_checked: null, check: check(), ...over })

test('the latest checks are asked for by site or by camera', async () => {
  await getLatestChecks({ camera_id: 'c1' })
  expect(apiClient.get).toHaveBeenLastCalledWith('/api/v1/camera-checks/latest', { params: { camera_id: 'c1' } })
  await getLatestChecks()
  expect(apiClient.get).toHaveBeenLastCalledWith('/api/v1/camera-checks/latest', { params: {} })
})

test('a check that ran is its figures, one that could not run says why', () => {
  expect(checkText(check(), NOW)).toBe(
    '4.5 of 15 fps · stall 7.0 s · missed 0% · answer 40 ms · first picture 1.2 s · checked 12 min ago')
  expect(checkText(check({ result: 'SIGN_IN_REFUSED', result_text: 'The camera refused the sign-in', checked_at: at(90) }), NOW))
    .toBe('The camera refused the sign-in · 1 h ago')
})

test('a broken limit is wrong, an old check is greyed and never wrong, and a stream not checked says why', () => {
  expect(streamLine(stream(), NOW)).toMatchObject({ wrong: true, old: false })
  expect(brokeText(stream())).toBe('Over its limit: frame rate 4.5 of 15; a stall of 7.0 s.')
  const old = stream({ check: check({ stale: true, checked_at: at(70) }) })
  expect(streamLine(old, NOW)).toEqual({ text: expect.stringContaining('(old: not gone by)'), wrong: false, old: true })
  expect(brokeText(old)).toBeNull()
  expect(streamLine(stream({ check: check({ breaks: [] }) }), NOW).wrong).toBe(false)
  expect(streamLine(stream({ check: null, not_checked: 'Only an RTSP address is checked.' }), NOW).text)
    .toBe('Only an RTSP address is checked.')
  expect(streamLine(stream({ check: null }), NOW).text).toBe('Not checked yet')
})

test('the gaps of the last 24 hours, or that it is not recording now', () => {
  expect(gapText({ gaps_24h: { gaps: 0, longest_s: null, open: false } })).toBeNull()
  expect(gapText({ gaps_24h: { gaps: 2, longest_s: 200, open: false } }))
    .toBe('2 recording gaps over its limit in 24 hours, the longest 3 min 20 s')
  expect(gapText({ gaps_24h: { gaps: 1, longest_s: 2400, open: true } })).toBe('Not recording now, for longer than its limit')
  expect([length(45), length(180), length(2400), length(5400)]).toEqual(['45 s', '3 min', '40 min', '1 h 30 min'])
})
