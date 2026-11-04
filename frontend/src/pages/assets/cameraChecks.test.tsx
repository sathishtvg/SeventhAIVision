/**
 * A camera's checks and recording gaps on the device health page (Phase 6 of the known limits, 4 November 2026):
 * the line under a camera with a figure over its limit marked, an old check greyed and a stream that is not checked
 * saying why; a camera opened with its frame rate chart, the checks that went wrong and its gaps; the limits changed
 * by whoever may change settings and by nobody else.
 */
import type { ReactNode } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ThemeProvider } from '@mui/material'
import { render, screen, fireEvent, waitFor, within } from '@/test/utils'
import { theme } from '@/theme/glassmorphism'
import { useAuthStore } from '@/store/auth'
import * as assets from '@/api/securityAssets'
import * as checksApi from '@/api/cameraChecks'
import type { Check, CameraWeek, CheckedCamera, LimitSettings } from '@/api/cameraChecks'
import type { DeviceDetail, Health, Reading } from '@/api/securityAssets'
import { checkParts, length } from '@/components/assets/cameraCheckFormat'
import AssetsMaintenance from './AssetsMaintenance'

vi.mock('@/store/auth', () => ({ useAuthStore: vi.fn() }))
vi.mock('@/api/sites', () => ({ getSites: vi.fn().mockResolvedValue([{ id: 's1', name: 'Factory A' }]) }))
vi.mock('@/api/settings', () => ({ getSettings: vi.fn().mockResolvedValue([]), upsertSetting: vi.fn().mockResolvedValue({}) }))
vi.mock('@/api/maintenance', async (orig) => {
  const real = await orig<typeof import('@/api/maintenance')>()
  return Object.fromEntries(Object.entries(real).map(([k, v]) => [k, typeof v === 'function' ? vi.fn() : v]))
})
vi.mock('@/api/securityAssets', async (orig) => {
  const real = await orig<typeof import('@/api/securityAssets')>()
  const fns = Object.fromEntries(Object.entries(real).map(([k, v]) => [k, typeof v === 'function' ? vi.fn() : v]))
  return { ...fns, apiError: real.apiError }
})
vi.mock('@/api/cameraChecks', async (orig) => {
  const real = await orig<typeof import('@/api/cameraChecks')>()
  return Object.fromEntries(Object.entries(real).map(([k, v]) => [k, typeof v === 'function' ? vi.fn() : v]))
})

function show(el: ReactNode = <AssetsMaintenance />) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } })
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}><ThemeProvider theme={theme}>{el}</ThemeProvider></QueryClientProvider>
    </MemoryRouter>, { wrapper: ({ children }) => <>{children}</> })
}

const signedIn = (roleId: number) =>
  vi.mocked(useAuthStore).mockImplementation(((sel: (s: unknown) => unknown) =>
    sel({ user: { id: 'me', tenantId: 't1', roleId }, accessToken: 'tok', permissions: null })) as never)

const minutesAgo = (m: number) => new Date(Date.now() - m * 60_000).toISOString()
const check = (over: Partial<Check> = {}): Check => ({
  checked_at: minutesAgo(12), result: 'OK', result_text: 'Checked', listened_ms: 10000, frames: 46, stated_rate: 15,
  received_rate: 4.5, longest_stall_ms: 7000, missed_pct: 0, answer_ms: 40, first_picture_ms: 1200,
  breaks: [{ limit: 'least_rate_pct', text: 'frame rate 4.5 of 15' }, { limit: 'most_stall_ms', text: 'a stall of 7.0 s' }],
  stale: false, ...over })
const camera = (over: Partial<CheckedCamera> = {}): CheckedCamera => ({
  camera_id: 'c1', camera_name: 'Gate 1', site_id: 's1', site_name: 'Factory A',
  streams: [{ stream_id: 'st1', protocol: 'rtsp', stream_status: 'online', records_all_the_time: true,
              not_checked: null, check: check() }],
  gaps_24h: { gaps: 1, longest_s: 200, open: false }, ...over })
const reading = (over: Partial<Reading>): Reading => ({
  kind: 'CAMERA', kind_label: 'Camera', device_id: 'c1', name: 'Gate 1', site_id: 's1', site_name: 'Factory A',
  asset_id: null, asset_code: null, state: 'DEGRADED', reasons: ['At its last check: frame rate 4.5 of 15; a stall of 7.0 s.'],
  facts: { streams: 1 }, since: null, since_is_when_first_read: false, ...over })
const NOTE = 'Each reading is made from what the device or its connection reports to the platform and, for a camera, ' +
  'from a 10-second check every 15 minutes.'
const NOT_MEASURED = ['The quality of the picture: darkness, glare, focus']
const HEALTH: Health = {
  items: [reading({}), reading({ device_id: 'c2', name: 'Store', state: 'OK', reasons: [] }),
          reading({ device_id: 'c3', name: 'Lobby', state: 'OK', reasons: [] })],
  as_of: new Date().toISOString(), note: NOTE, not_measured: NOT_MEASURED,
  summary: { devices: 3, by_state: { OK: 2, DEGRADED: 1, DOWN: 0, NOT_KNOWN: 0, OFF: 0 }, by_kind: [] },
  kinds: [{ key: 'CAMERA', label: 'Camera' }], states: ['OK', 'DEGRADED', 'DOWN', 'NOT_KNOWN', 'OFF'],
}
const LATEST = {
  cameras: [
    camera(),
    camera({ camera_id: 'c2', camera_name: 'Store', gaps_24h: { gaps: 0, longest_s: null, open: false },
             streams: [{ stream_id: 'st2', protocol: 'rtsp', stream_status: 'online', records_all_the_time: false,
                         not_checked: null, check: check({ stale: true, checked_at: minutesAgo(70), breaks: [] }) }] }),
    camera({ camera_id: 'c3', camera_name: 'Lobby', gaps_24h: { gaps: 0, longest_s: null, open: false },
             streams: [{ stream_id: 'st3', protocol: 'http', stream_status: 'online', records_all_the_time: false,
                         not_checked: 'Only an RTSP address is checked.', check: null }] }),
  ],
  as_of: new Date().toISOString(), note: 'A camera is listened to for 10 seconds every 15 minutes.',
  stale_after_s: 2700, every_s: 900,
}
const DEVICE: DeviceDetail = {
  ...reading({}), as_of: HEALTH.as_of, note: NOTE, not_measured: NOT_MEASURED, history: [],
  down: [{ days: 7, known_from: null, known_seconds: 0, down_seconds: 0, times_down: 0, note: '' }],
}
const LIMITS = { least_rate_pct: 70, most_stall_ms: 2000, most_missed_pct: 2, most_answer_ms: 500,
                 most_first_picture_ms: 5000, most_gap_s: 60 }
const WEEK: CameraWeek = {
  camera: { id: 'c1', name: 'Gate 1', site_id: 's1', site_name: 'Factory A' }, days: 7,
  checks: [{ ...check({ checked_at: minutesAgo(60 * 30), breaks: [], received_rate: 15, longest_stall_ms: 70 }), stream_id: 'st1' },
           { ...check({ checked_at: minutesAgo(60 * 2), result: 'SIGN_IN_REFUSED', result_text: 'The camera refused the sign-in',
                        received_rate: null, breaks: [] }), stream_id: 'st1' },
           { ...check(), stream_id: 'st1' }],
  gaps: [{ stream_id: 'st1', kind: 'NOT_RECORDED', kind_text: 'Nothing was recorded', started_at: minutesAgo(40),
           ended_at: null, seconds: 2400, camera_was_offline: false, limit_s: 60, over_limit: true },
         { stream_id: 'st1', kind: 'EMPTY', kind_text: 'A recording that failed or holds nothing', started_at: minutesAgo(300),
           ended_at: minutesAgo(296), seconds: 200, camera_was_offline: true, limit_s: 60, over_limit: true }],
  limits: { ...LIMITS, updated_at: null, updated_by_name: null }, as_of: new Date().toISOString(),
  note: 'The answer time and the first picture are not the delay between the scene and the screen.',
}
const SETTINGS: LimitSettings = {
  ...LIMITS, updated_at: null, updated_by_name: null, defaults: LIMITS, may_set: true,
  ranges: { least_rate_pct: { least: 10, most: 100 }, most_stall_ms: { least: 200, most: 60000 },
            most_missed_pct: { least: 0, most: 50 }, most_answer_ms: { least: 10, most: 10000 },
            most_first_picture_ms: { least: 500, most: 60000 }, most_gap_s: { least: 10, most: 3600 } },
  labels: { least_rate_pct: 'Frame rate below (%)', most_stall_ms: 'A stall longer than (ms)',
            most_missed_pct: 'More frames missed than (%)', most_answer_ms: 'An answer slower than (ms)',
            most_first_picture_ms: 'A first picture later than (ms)', most_gap_s: 'A recording gap longer than (seconds)' },
}
const PATIENT = { timeout: 8000 }

beforeEach(() => {
  vi.clearAllMocks()
  signedIn(2)
  vi.mocked(assets.getHealth).mockResolvedValue(HEALTH)
  vi.mocked(assets.getDevice).mockResolvedValue(DEVICE)
  vi.mocked(checksApi.getLatestChecks).mockResolvedValue(LATEST)
  vi.mocked(checksApi.getCameraWeek).mockResolvedValue(WEEK)
  vi.mocked(checksApi.getCheckLimits).mockResolvedValue(SETTINGS)
  vi.mocked(checksApi.setCheckLimits).mockResolvedValue({ ...SETTINGS, least_rate_pct: 50 })
})

describe('the wording', () => {
  it('words a check that ran, one that could not, and a gap', () => {
    const now = new Date()
    expect(checkParts(check({ checked_at: now.toISOString() }), now).map((p) => p.text)).toEqual(
      ['4.5 of 15 fps', 'stall 7.0 s', 'missed 0%', 'answer 40 ms', 'first picture 1.2 s', 'checked just now'])
    expect(checkParts(check({ result: 'NO_CONNECTION', result_text: 'The check could not connect to the camera',
                              checked_at: minutesAgo(20) }), now)[0].text)
      .toBe('The check could not connect to the camera · 20 min ago')
    expect([length(45), length(200), length(180), length(2400)]).toEqual(['45 s', '3 min 20 s', '3 min', '40 min'])
  })
})

describe('the device health page', () => {
  it('gives each camera its latest check, marks a figure over its limit, greys an old one and says why one is not checked', async () => {
    show()
    const rows = await screen.findAllByTestId('device-row', {}, PATIENT)
    const gate = await within(rows[0]).findByTestId('check-line', {}, PATIENT)
    expect(gate).toHaveTextContent('4.5 of 15 fps · stall 7.0 s · missed 0% · answer 40 ms · first picture 1.2 s · checked 12 min ago')
    expect([...gate.querySelectorAll('[data-broke]')].map((e) => e.textContent)).toEqual(['4.5 of 15 fps', 'stall 7.0 s'])
    expect(within(gate).getByTestId('gap-line')).toHaveTextContent('1 recording gap over its limit in 24 hours, the longest 3 min 20 s')
    const store = within(rows[1]).getByTestId('check-stream')
    expect(store).toHaveTextContent('(old: not gone by)')
    expect(store.querySelectorAll('[data-broke]')).toHaveLength(0)
    expect(within(rows[2]).getByTestId('check-line')).toHaveTextContent('Only an RTSP address is checked.')
    expect(checksApi.getLatestChecks).toHaveBeenCalledWith({ site_id: undefined })
  })

  it('opens a camera with its frame rate over seven days, the checks that went wrong and its gaps', async () => {
    show()
    const rows = await screen.findAllByTestId('device-row', {}, PATIENT)
    fireEvent.click(within(rows[0]).getByRole('button', { name: 'Open' }))
    const view = await screen.findByTestId('camera-checks', {}, PATIENT)
    expect(checksApi.getCameraWeek).toHaveBeenCalledWith('c1')
    expect(within(view).getByTestId('rate-chart')).toHaveAttribute(
      'aria-label', 'Frame rate: frame rate of each check over 7 days, against its limit')
    const bad = within(view).getAllByTestId('bad-check').map((e) => e.textContent)
    expect(bad).toHaveLength(2)
    expect(bad[0]).toContain('frame rate 4.5 of 15; a stall of 7.0 s')
    expect(bad[1]).toContain('The camera refused the sign-in')
    const gaps = within(view).getAllByTestId('gap').map((e) => e.textContent)
    expect(gaps[0]).toContain('40 min so far, still going on · Nothing was recorded')
    expect(gaps[1]).toContain('3 min 20 s · A recording that failed or holds nothing · the camera was offline then')
    expect(view).toHaveTextContent('not the delay between the scene and the screen')
  })
})

describe('the limits', () => {
  it('are changed by whoever may change settings, with every value checked before it is sent', async () => {
    show()
    fireEvent.click(await screen.findByRole('button', { name: 'Check limits' }, PATIENT))
    const dialog = await screen.findByRole('dialog')
    const rate = await within(dialog).findByLabelText('Frame rate below (%)')
    fireEvent.change(rate, { target: { value: '5' } })
    expect(within(dialog).getByRole('button', { name: 'Save' })).toBeDisabled()
    fireEvent.change(rate, { target: { value: '50' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(checksApi.setCheckLimits).toHaveBeenCalledWith({ ...LIMITS, least_rate_pct: 50 }))
  })

  it('are not offered to somebody who may not change settings', async () => {
    signedIn(6)
    show()
    await screen.findAllByTestId('device-row', {}, PATIENT)
    expect(screen.queryByRole('button', { name: 'Check limits' })).not.toBeInTheDocument()
  })
})
