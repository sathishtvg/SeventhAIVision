import type { ReactNode } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ThemeProvider } from '@mui/material'
import { render, screen, fireEvent, waitFor, within } from '@/test/utils'
import { theme } from '@/theme/glassmorphism'
import { useAuthStore } from '@/store/auth'
import * as assets from '@/api/securityAssets'
import * as work from '@/api/maintenance'
import type { Asset, AssetDetail, DeviceDetail, Health, Reading, Register } from '@/api/securityAssets'
import type { MaintenanceOptions, MaintenanceSettings, Orders, Schedule, WorkOrder } from '@/api/maintenance'
import {
  downLine, dueIn, every, factLines, heldBy, madeBy, sinceLine, span, warrantyLine,
} from '@/components/assets/assetFormat'
import AssetsMaintenance from './AssetsMaintenance'

vi.mock('@/store/auth', () => ({ useAuthStore: vi.fn() }))
vi.mock('@/api/sites', () => ({ getSites: vi.fn().mockResolvedValue([
  { id: 's1', name: 'Factory A' }, { id: 's2', name: 'Factory B' }]) }))
// PageHeader reads the tenant's page names from the settings.
vi.mock('@/api/settings', () => ({ getSettings: vi.fn().mockResolvedValue([]), upsertSetting: vi.fn().mockResolvedValue({}) }))
vi.mock('@/api/securityAssets', async (orig) => {
  const real = await orig<typeof import('@/api/securityAssets')>()
  const fns = Object.fromEntries(Object.entries(real).map(([k, v]) => [k, typeof v === 'function' ? vi.fn() : v]))
  return { ...fns, apiError: real.apiError }
})
vi.mock('@/api/maintenance', async (orig) => {
  const real = await orig<typeof import('@/api/maintenance')>()
  const fns = Object.fromEntries(Object.entries(real).map(([k, v]) => [k, typeof v === 'function' ? vi.fn() : v]))
  return { ...fns, apiError: real.apiError }
})

function show(el: ReactNode = <AssetsMaintenance />) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } })
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}><ThemeProvider theme={theme}>{el}</ThemeProvider></QueryClientProvider>
    </MemoryRouter>, { wrapper: ({ children }) => <>{children}</> })
}

const NOW = new Date('2026-10-07T04:00:00Z')
const NOTE = 'Each reading is made from what the device or its connection reports to the platform.'
const NOT_MEASURED = ['Frame rate', 'Latency', 'Packet loss', 'The quality of the picture: darkness, glare, focus',
                      'Gaps in a recording']
const reading = (over: Partial<Reading>): Reading => ({
  kind: 'CAMERA', kind_label: 'Camera', device_id: 'c1', name: 'Gate 1', site_id: 's1', site_name: 'Factory A',
  asset_id: 'a1', asset_code: 'AST-0001', state: 'OK', reasons: [], facts: { streams: 1, last_frame_at: '2026-10-07T03:59:40Z' },
  since: null, since_is_when_first_read: false, ...over })
const DARK = reading({ device_id: 'c2', name: 'Loading bay', state: 'DOWN', reasons: ['Every stream is offline.'],
                       asset_id: null, asset_code: null, since: '2026-10-06T22:40:00Z' })
const RECORDER = reading({ kind: 'NVR', kind_label: 'Recorder', device_id: 'n1', name: 'Recorder 1', site_id: null,
                           site_name: null, state: 'NOT_KNOWN', asset_id: null, asset_code: null,
                           reasons: ['It has not been probed for more than a day. Probe it to know how it is now.'] })
const HEALTH: Health = {
  items: [DARK, RECORDER, reading({})], as_of: '2026-10-07T04:00:00Z', note: NOTE, not_measured: NOT_MEASURED,
  summary: { devices: 3, by_state: { OK: 1, DEGRADED: 0, DOWN: 1, NOT_KNOWN: 1, OFF: 0 }, by_kind: [] },
  kinds: [{ key: 'CAMERA', label: 'Camera' }, { key: 'NVR', label: 'Recorder' }],
  states: ['OK', 'DEGRADED', 'DOWN', 'NOT_KNOWN', 'OFF'],
}
const DEVICE: DeviceDetail = {
  ...DARK, as_of: HEALTH.as_of, note: NOTE, not_measured: NOT_MEASURED,
  history: [{ state: 'DOWN', reasons: ['Every stream is offline.'], observed_at: '2026-10-06T22:40:00Z' },
            { state: 'OK', reasons: [], observed_at: '2026-10-05T00:00:00Z' }],
  down: [{ days: 7, known_from: '2026-10-05T00:00:00Z', known_seconds: 187200, down_seconds: 19200, times_down: 1,
           note: 'An outage shorter than five minutes can pass unrecorded.' },
         { days: 30, known_from: '2026-10-05T00:00:00Z', known_seconds: 187200, down_seconds: 19200, times_down: 1, note: '' }],
}
const asset = (over: Partial<Asset>): Asset => ({
  id: 'a1', asset_code: 'AST-0001', kind: 'CAMERA', kind_label: 'Camera', name: 'Gate 1 camera', make: 'Axis', model: 'P3265',
  serial_number: 'ACC-1', location: 'North fence', site_id: 's1', site_name: 'Factory A', place_id: null, place_name: null,
  vendor: 'SecureVision', installed_on: '2025-01-05', warranty_until: '2028-01-05', warranty_days_left: 455, warranty: 'IN',
  status: 'IN_SERVICE', notes: null, created_by_name: 'Siti Rahman', updated_by_name: 'Siti Rahman',
  updated_at: '2026-10-07T00:00:00Z', retired_at: null, retired_by_name: null, retire_reason: null, device_id: 'c1',
  monitored: true, health: { state: 'OK', reasons: [], since: null, since_is_when_first_read: false }, not_monitored: null,
  open_orders: 0, may: { change: true, retire: true, restore: false }, ...over })
const UPS = asset({ id: 'a2', asset_code: 'AST-0002', kind: 'UPS', kind_label: 'UPS', name: 'UPS rack 2', make: 'APC',
                    model: 'SMT1500', serial_number: 'AS-99', warranty_until: '2025-03-01', warranty_days_left: -585,
                    warranty: 'OUT', device_id: null, monitored: false, health: null, open_orders: 1,
                    not_monitored: 'The platform does not know this as a device, so it has no reading of it.' })
const REGISTER: Register = {
  items: [asset({}), UPS], limit: 100, offset: 0, has_more: false, can_manage: true,
  kinds: [{ key: 'CAMERA', label: 'Camera', monitored: true }, { key: 'UPS', label: 'UPS', monitored: false }],
  statuses: ['IN_SERVICE', 'UNDER_REPAIR', 'SPARE', 'RETIRED'],
}
const detail = (over: Partial<AssetDetail> = {}): AssetDetail => ({
  ...UPS, note: NOTE, not_measured: [], work_orders: [{
    id: 'w2', number: 'WO-0002', title: 'Replace the battery', kind: 'CORRECTIVE', state: 'DONE', due_at: null,
    raised_at: '2026-10-01T00:00:00Z', completed_at: '2026-10-02T00:00:00Z', completion_note: 'Pack replaced.',
    downtime_minutes: 35 }], ...over })
const SUGGESTION = 'Put forward by the platform from what it read. It is not work until somebody who manages maintenance accepts it.'
const NOTHING = { accept: false, dismiss: false, change: false, start: false, complete: false, cancel: false }
const order = (over: Partial<WorkOrder>): WorkOrder => ({
  id: 'w1', number: 'WO-0003', site_id: 's1', site_name: 'Factory A', asset_id: null, asset_code: null, asset_name: null,
  schedule_id: null, defect_id: null, title: 'Loading bay: down', description: null, kind: 'CORRECTIVE', priority: 'HIGH',
  state: 'SUGGESTED', origin: 'HEALTH',
  suggestion_reason: 'Camera “Loading bay” has been down since 7 Oct 06:40 (5 hours). Every stream is offline.',
  raised_by_name: null, raised_at: '2026-10-07T03:40:00Z', accepted_by_name: null, accepted_at: null,
  assigned_to_user_id: null, assigned_to_user_name: null, assigned_to_name: null, due_at: null, started_at: null,
  started_by_name: null, completed_at: null, completed_by_name: null, completion_note: null, parts_used: null,
  downtime_minutes: null, closed_at: null, closed_by_name: null, closed_reason: null, overdue: false, assigned_to_me: false,
  note: SUGGESTION, may: { ...NOTHING, accept: true, dismiss: true }, ...over })
const OPEN = order({ id: 'w2', number: 'WO-0002', title: 'Replace the battery', state: 'OPEN', origin: 'PERSON',
                     suggestion_reason: null, note: null, asset_code: 'AST-0002', asset_name: 'UPS rack 2',
                     raised_by_name: 'Siti Rahman', assigned_to_user_id: 'u1', assigned_to_user_name: 'Lee Technician',
                     due_at: '2026-10-06T00:00:00Z', overdue: true,
                     may: { ...NOTHING, change: true, start: true, complete: true, cancel: true } })
const ORDERS: Orders = {
  items: [order({}), OPEN], limit: 50, offset: 0, has_more: false, can_manage: true,
  counts: { suggested: 1, open: 1, in_progress: 0, overdue: 1 },
  states: ['SUGGESTED', 'OPEN', 'IN_PROGRESS', 'DONE', 'CANCELLED', 'DISMISSED'],
  kinds: ['CORRECTIVE', 'PREVENTIVE', 'INSPECTION'], priorities: ['LOW', 'NORMAL', 'HIGH', 'URGENT'],
}
const SETTINGS: MaintenanceSettings = { suggest_from_health: false, suggest_after_hours: 4, default_after_hours: 4,
                                        can_manage: true, note: SUGGESTION }
const OPTIONS: MaintenanceOptions = {
  assets: [{ id: 'a2', asset_code: 'AST-0002', name: 'UPS rack 2', kind: 'UPS', site_id: 's1', site_name: 'Factory A' }],
  defects: [{ id: 'd1', category: 'cctv', location: 'Gate 1', description: 'Camera housing cracked', severity: 'medium',
              site_id: 's1', site_name: 'Factory A' }],
  people: [{ id: 'u1', name: 'Lee Technician' }], kinds: ['CORRECTIVE', 'PREVENTIVE', 'INSPECTION'],
  priorities: ['LOW', 'NORMAL', 'HIGH', 'URGENT'],
}
const schedule = (over: Partial<Schedule>): Schedule => ({
  id: 'm1', site_id: 's1', site_name: 'Factory A', asset_id: 'a2', asset_code: 'AST-0002', asset_name: 'UPS rack 2',
  title: 'Quarterly battery test', instructions: 'Ten minutes on battery.', every_days: 90, lead_days: 7,
  next_due_on: '2026-10-10', last_done_on: null, is_active: true, days_until_due: 3, ...over })
const refusal = (detail_: unknown, status = 422) => Object.assign(new Error('x'), { response: { status, data: { detail: detail_ } } })
const PATIENT = { timeout: 8000 }

const signedIn = (roleId: number, permissions: string[] | null = null) =>
  vi.mocked(useAuthStore).mockImplementation(((sel: (s: unknown) => unknown) =>
    sel({ user: { id: 'me', tenantId: 't1', roleId }, accessToken: 'tok', permissions })) as never)

beforeEach(() => {
  vi.clearAllMocks()
  signedIn(2)
  vi.mocked(assets.getHealth).mockResolvedValue(HEALTH)
  vi.mocked(assets.getDevice).mockResolvedValue(DEVICE)
  vi.mocked(assets.getRegister).mockResolvedValue(REGISTER)
  vi.mocked(assets.getAsset).mockResolvedValue(detail())
  vi.mocked(assets.getUnregistered).mockResolvedValue([
    { kind: 'CAMERA', kind_label: 'Camera', device_id: 'c2', name: 'Loading bay', site_id: 's1', site_name: 'Factory A',
      make: null, model: null, serial_number: null, location: null },
    { kind: 'NVR', kind_label: 'Recorder', device_id: 'n1', name: 'Recorder 1', site_id: null, site_name: null,
      make: null, model: null, serial_number: null, location: null }])
  vi.mocked(assets.registerDevices).mockResolvedValue({ registered: [], left: [] })
  for (const fn of [assets.addAsset, assets.changeAsset, assets.retireAsset, assets.restoreAsset]) vi.mocked(fn).mockResolvedValue(UPS)
  vi.mocked(work.listOrders).mockResolvedValue(ORDERS)
  vi.mocked(work.getOrder).mockResolvedValue(order({}))
  vi.mocked(work.getMaintenanceSettings).mockResolvedValue(SETTINGS)
  vi.mocked(work.getMaintenanceOptions).mockResolvedValue(OPTIONS)
  vi.mocked(work.listSchedules).mockResolvedValue({ items: [schedule({}), schedule({ id: 'm2', title: 'Clean the domes', is_active: false })],
                                                    can_manage: true })
  vi.mocked(work.setMaintenanceSettings).mockResolvedValue({ suggest_from_health: true, suggest_after_hours: 4, changed: true })
  for (const fn of [work.raiseOrder, work.changeOrder, work.acceptOrder, work.dismissOrder, work.startOrder,
                    work.completeOrder, work.cancelOrder]) vi.mocked(fn).mockResolvedValue(OPEN)
  for (const fn of [work.addSchedule, work.changeSchedule]) vi.mocked(fn).mockResolvedValue(schedule({}))
})

const tab = async (name: string, rowId: string) => {
  show()
  fireEvent.click(await screen.findByRole('tab', { name }, PATIENT))
  return screen.findAllByTestId(rowId, {}, PATIENT)
}
const pick = async (scope: HTMLElement, label: string, option: string) => {
  fireEvent.mouseDown(within(scope).getByLabelText(label))
  fireEvent.click(await screen.findByRole('option', { name: option }))
}

describe('the words', () => {
  it('says a length of time the way a person would', () => {
    expect([20, 60, 3540, 3600, 19200, 86400 * 2, 86400 * 9].map(span))
      .toEqual(['less than a minute', '1 min', '59 min', '1 h', '5 h 20 min', '2 days', '9 days'])
  })

  it('says for how long, and "at least" when only the first reading is known', () => {
    expect(sinceLine(DARK, NOW)).toBe('for 5 h 20 min')
    expect(sinceLine({ ...DARK, since_is_when_first_read: true }, NOW)).toBe('for at least 5 h 20 min')
    expect(sinceLine({ since: null, since_is_when_first_read: false }, NOW)).toBe('')
  })

  it('lists what a reading was made of, and adds nothing that was not reported', () => {
    expect(factLines({ last_frame_at: '2026-10-07T03:59:40Z', disconnects_24h: 0, streams: 1 }, NOW))
      .toEqual(['Last frame less than a minute ago'])
    expect(factLines({ last_reading_at: '2026-10-07T00:50:00Z', expected_interval_seconds: 300 }, NOW))
      .toEqual(['Last reading 3 h 10 min ago', 'A reading is expected every 5 min'])
    expect(factLines({ disconnects_24h: 1 }, NOW)).toEqual(['Disconnected 1 time in 24 hours'])
    expect(factLines({ battery_level: 80, reports: 'COMMUNICATION_LOST' }, NOW)).toEqual(['Battery 80%', 'It reports communication lost'])
    for (const line of factLines({ last_frame_at: '2026-10-07T03:00:00Z', disconnects_24h: 6, battery_level: 4 }, NOW)) {
      expect(line).not.toMatch(/fps|frame rate|latency|packet|quality/i)
    }
    expect(factLines({}, NOW)).toEqual([])
  })

  it('counts time down only over what is known', () => {
    expect(downLine(DEVICE.down[0], NOW)).toBe('Last 7 days: down 5 h 20 min in 1 outage, of the 2 days that are known.')
    expect(downLine({ ...DEVICE.down[0], down_seconds: 0, times_down: 0 }, NOW))
      .toBe('Last 7 days: not read as down, of the 2 days that are known.')
    expect(downLine({ days: 30, known_from: null, known_seconds: 0, down_seconds: 0, times_down: 0, note: '' }, NOW))
      .toBe('Last 30 days: nothing has been kept of it yet.')
  })

  it('describes an asset, its warranty, who has an order and when a schedule is due', () => {
    expect(madeBy(asset({}))).toBe('Axis P3265 · S/N ACC-1')
    expect(madeBy(asset({ make: null, model: null, serial_number: null }))).toBe('')
    expect(warrantyLine(UPS)).toMatch(/^Out of warranty since /)
    expect(warrantyLine(asset({}))).toMatch(/^In warranty until .* \(455 days\)$/)
    expect(warrantyLine(asset({ warranty: 'NOT_RECORDED', warranty_until: null, warranty_days_left: null }))).toBe('Warranty not recorded')
    expect(heldBy(OPEN)).toBe('Lee Technician')
    expect(heldBy(order({ assigned_to_name: 'PowerCo Services' }))).toBe('PowerCo Services')
    expect(heldBy(order({}))).toBe('Nobody yet')
    expect([3, 1, 0, -1, -4].map((d) => dueIn({ days_until_due: d, is_active: true })))
      .toEqual(['Due in 3 days', 'Due in 1 day', 'Due today', 'Overdue by 1 day', 'Overdue by 4 days'])
    expect(dueIn({ days_until_due: 3, is_active: false })).toBe('Switched off')
    expect([every({ every_days: 1 }), every({ every_days: 90 })]).toEqual(['Every day', 'Every 90 days'])
  })
})

describe('device health', () => {
  it('shows every device with its state and why, and names what is not measured', async () => {
    show()
    const rows = await screen.findAllByTestId('device-row', {}, PATIENT)
    expect(rows).toHaveLength(3)
    expect(rows[0]).toHaveTextContent('Loading bay')
    expect(rows[0]).toHaveTextContent('Down')
    expect(rows[0]).toHaveTextContent('Every stream is offline.')
    expect(rows[0]).toHaveTextContent('not in the register')
    // Not known is said to be not known — not shown as working.
    expect(rows[1]).toHaveTextContent('Not known')
    expect(rows[1]).not.toHaveTextContent('Working')
    expect(rows[2]).toHaveTextContent('AST-0001')
    const summary = screen.getByTestId('health-summary')
    expect(summary).toHaveTextContent('3 devices')
    expect(summary).toHaveTextContent('Down: 1')
    expect(summary).toHaveTextContent('Not known: 1')
    expect(summary).toHaveTextContent(NOTE)
    expect(within(summary).getByTestId('not-measured')).toHaveTextContent(
      'Not measured by the platform: Frame rate; Latency; Packet loss; The quality of the picture: darkness, glare, focus; Gaps in a recording.')
  })

  it('is narrowed by site, kind and state', async () => {
    show()
    await screen.findAllByTestId('device-row', {}, PATIENT)
    expect(assets.getHealth).toHaveBeenLastCalledWith({ site_id: undefined, kind: undefined, state: undefined })
    await pick(document.body, 'Site', 'Factory B')
    await pick(document.body, 'Kind of device', 'Recorder')
    await pick(document.body, 'State', 'Not known')
    await waitFor(() => expect(assets.getHealth).toHaveBeenLastCalledWith({ site_id: 's2', kind: 'NVR', state: 'NOT_KNOWN' }))
  })

  it('opens one device: what was kept of it, how long it was down, and again what is not measured', async () => {
    show()
    const rows = await screen.findAllByTestId('device-row', {}, PATIENT)
    fireEvent.click(within(rows[0]).getByRole('button', { name: 'Open' }))
    const dialog = await screen.findByRole('dialog')
    await within(dialog).findByTestId('reading', {}, PATIENT)
    expect(assets.getDevice).toHaveBeenCalledWith('CAMERA', 'c2')
    expect(dialog).toHaveTextContent('Every stream is offline.')
    expect(dialog).toHaveTextContent('in 1 outage')
    expect(dialog).toHaveTextContent('An outage shorter than five minutes can pass unrecorded.')
    expect(within(dialog).getAllByTestId('kept')).toHaveLength(2)
    expect(within(dialog).getByTestId('not-measured')).toHaveTextContent('Frame rate; Latency; Packet loss')
  })

  it('says so when the list cannot be read', async () => {
    vi.mocked(assets.getHealth).mockRejectedValue(refusal('Site not found', 404))
    show()
    expect(await screen.findByText('Site not found', {}, PATIENT)).toBeInTheDocument()
  })
})

describe('the asset register', () => {
  it('lists each asset with its health or that there is no reading', async () => {
    const rows = await tab('Asset register', 'asset-row')
    expect(rows[0]).toHaveTextContent('AST-0001 · Gate 1 camera')
    expect(rows[0]).toHaveTextContent('Axis P3265 · S/N ACC-1')
    expect(rows[0]).toHaveTextContent('Working')
    // A UPS has no reading. It is not shown as working.
    expect(rows[1]).toHaveTextContent('No reading')
    expect(rows[1]).not.toHaveTextContent('Working')
    expect(rows[1]).toHaveTextContent('1 open order')
    expect(rows[1]).toHaveTextContent('Out of warranty since')
  })

  it('is narrowed by words, site, kind, status and warranty', async () => {
    await tab('Asset register', 'asset-row')
    fireEvent.change(screen.getByLabelText('Name, code, serial or vendor'), { target: { value: '  apc ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Search' }))
    await pick(document.body, 'Kind of asset', 'UPS')
    await pick(document.body, 'Status', 'Retired')
    await pick(document.body, 'Warranty', 'Out of warranty')
    await pick(document.body, 'Site', 'Factory A')
    await waitFor(() => expect(assets.getRegister).toHaveBeenLastCalledWith({
      q: 'apc', site_id: 's1', kind: 'UPS', status: 'RETIRED', warranty: 'OUT' }))
  })

  it('adds an asset, and says of a kind the platform does not know that it will have no reading', async () => {
    await tab('Asset register', 'asset-row')
    fireEvent.click(screen.getByRole('button', { name: 'Add an asset' }))
    const dialog = await screen.findByRole('dialog')
    const add = within(dialog).getByRole('button', { name: 'Add it' })
    expect(add).toBeDisabled()
    await pick(dialog, 'Kind', 'UPS')
    expect(dialog).toHaveTextContent('it will have no health reading')
    fireEvent.change(within(dialog).getByLabelText('Name'), { target: { value: ' UPS rack 2 ' } })
    fireEvent.change(within(dialog).getByLabelText('Make'), { target: { value: 'APC' } })
    fireEvent.change(within(dialog).getByLabelText('Warranty until'), { target: { value: '2027-03-01' } })
    await pick(dialog, 'Site', 'Factory A')
    fireEvent.click(add)
    await waitFor(() => expect(assets.addAsset).toHaveBeenCalledWith({
      kind: 'UPS', name: 'UPS rack 2', site_id: 's1', make: 'APC', model: null, serial_number: null, location: null,
      vendor: null, installed_on: null, warranty_until: '2027-03-01', status: 'IN_SERVICE', notes: null }))
  })

  it('registers the devices the platform already knows, all of them unless some are unticked', async () => {
    await tab('Asset register', 'asset-row')
    fireEvent.click(screen.getByRole('button', { name: 'Register known devices' }))
    const dialog = await screen.findByRole('dialog')
    const known = await within(dialog).findAllByTestId('known-device', {}, PATIENT)
    expect(known).toHaveLength(2)
    expect(dialog).toHaveTextContent('The vendor, the warranty and the rest are for you to fill in afterwards.')
    fireEvent.click(within(known[1]).getByRole('checkbox'))
    fireEvent.click(within(dialog).getByRole('button', { name: 'Register 1 device' }))
    await waitFor(() => expect(assets.registerDevices).toHaveBeenCalledWith([{ kind: 'CAMERA', device_id: 'c2' }]))
  })

  it('opens an asset the platform has no reading of, and says why there is none', async () => {
    const rows = await tab('Asset register', 'asset-row')
    fireEvent.click(within(rows[1]).getByRole('button', { name: 'Open' }))
    const dialog = await screen.findByRole('dialog')
    const health = await within(dialog).findByTestId('asset-health', {}, PATIENT)
    expect(health).toHaveTextContent('The platform does not know this as a device, so it has no reading of it.')
    expect(within(dialog).queryByTestId('not-measured')).not.toBeInTheDocument()
    expect(within(dialog).getByTestId('asset-order')).toHaveTextContent('WO-0002 · Replace the battery — Done: Pack replaced.')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Retire it' }))
    const retire = within(dialog).getAllByRole('button', { name: 'Retire it' }).at(-1)!
    expect(retire).toBeDisabled()
    fireEvent.change(within(dialog).getByLabelText('Why it is retired'), { target: { value: ' Replaced. ' } })
    fireEvent.click(retire)
    await waitFor(() => expect(assets.retireAsset).toHaveBeenCalledWith('a2', 'Replaced.'))
  })

  it('shows a monitored asset its reading with what is not measured, and offers a reader nothing', async () => {
    vi.mocked(assets.getAsset).mockResolvedValue(detail({
      ...asset({ health: { state: 'DOWN', reasons: ['Every stream is offline.'], since: null, since_is_when_first_read: false },
                 may: { change: false, retire: false, restore: false } }),
      not_measured: NOT_MEASURED, work_orders: null }))
    const rows = await tab('Asset register', 'asset-row')
    fireEvent.click(within(rows[0]).getByRole('button', { name: 'Open' }))
    const dialog = await screen.findByRole('dialog')
    const health = await within(dialog).findByTestId('asset-health', {}, PATIENT)
    expect(health).toHaveTextContent('Every stream is offline.')
    expect(within(health).getByTestId('not-measured')).toHaveTextContent('Frame rate; Latency')
    expect(within(dialog).getAllByRole('button').map((b) => b.textContent)).toEqual(['Close'])
    expect(dialog).not.toHaveTextContent('Work on it')
  })
})

describe('work orders', () => {
  it('lists what is put forward first, with what it was put forward from', async () => {
    const rows = await tab('Work orders', 'order-row')
    expect(rows[0]).toHaveTextContent('WO-0003 · Loading bay: down')
    expect(rows[0]).toHaveTextContent('Put forward from device health')
    expect(rows[0]).toHaveTextContent('has been down since 7 Oct 06:40 (5 hours)')
    expect(rows[0]).toHaveTextContent('Nobody yet')
    expect(rows[1]).toHaveTextContent('Raised by a person')
    expect(rows[1]).toHaveTextContent('Overdue')
    expect(rows[1]).toHaveTextContent('Lee Technician')
    const counts = screen.getByTestId('order-counts')
    expect(counts).toHaveTextContent('Put forward: 1')
    expect(counts).toHaveTextContent('Overdue: 1')
  })

  it('says whether health puts orders forward, and switches it only when somebody asks', async () => {
    await tab('Work orders', 'order-row')
    const card = await screen.findByTestId('suggestions')
    expect(card).toHaveTextContent('does not put a work order forward: nobody has asked for that')
    expect(card).toHaveTextContent(SUGGESTION)
    fireEvent.change(within(card).getByLabelText('After (hours)'), { target: { value: '12' } })
    fireEvent.click(within(card).getByRole('button', { name: 'Put them forward' }))
    await waitFor(() => expect(work.setMaintenanceSettings).toHaveBeenCalledWith({ suggest_from_health: true, suggest_after_hours: 12 }))
  })

  it('does not offer the switch to somebody who only reads', async () => {
    vi.mocked(work.getMaintenanceSettings).mockResolvedValue({ ...SETTINGS, suggest_from_health: true, can_manage: false })
    vi.mocked(work.listOrders).mockResolvedValue({ ...ORDERS, can_manage: false })
    await tab('Work orders', 'order-row')
    const card = await screen.findByTestId('suggestions')
    expect(card).toHaveTextContent('A device read as down for 4 hours or more is put forward as a work order.')
    expect(within(card).queryByRole('button')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Raise a work order' })).not.toBeInTheDocument()
  })

  it('is narrowed by words, state and whether it was given to me', async () => {
    await tab('Work orders', 'order-row')
    fireEvent.change(screen.getByLabelText('Title, number or asset'), { target: { value: ' battery ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Search' }))
    await pick(document.body, 'State', 'Done')
    fireEvent.click(screen.getByLabelText('Given to me'))
    await waitFor(() => expect(work.listOrders).toHaveBeenLastCalledWith({ q: 'battery', state: 'DONE', mine: true }))
  })

  it('shows a suggestion as a suggestion, and accepts it only when a person does', async () => {
    const rows = await tab('Work orders', 'order-row')
    fireEvent.click(within(rows[0]).getByRole('button', { name: 'Open' }))
    const dialog = await screen.findByRole('dialog')
    const origin = await within(dialog).findByTestId('origin', {}, PATIENT)
    expect(origin).toHaveTextContent('Put forward from device health')
    expect(origin).toHaveTextContent('Every stream is offline.')
    expect(origin).toHaveTextContent(SUGGESTION)
    // Until it is accepted there is no starting or completing it.
    expect(within(dialog).queryByRole('button', { name: 'Start the work' })).not.toBeInTheDocument()
    expect(within(dialog).queryByRole('button', { name: 'It is done' })).not.toBeInTheDocument()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Accept as work' }))
    expect(dialog).toHaveTextContent('you are recorded as having accepted it')
    expect(work.acceptOrder).not.toHaveBeenCalled()
    await waitFor(() => expect(work.getMaintenanceOptions).toHaveBeenCalled())
    await pick(dialog, 'Given to', 'Lee Technician')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Accept it' }))
    await waitFor(() => expect(work.acceptOrder).toHaveBeenCalledWith('w1', { assigned_to_user_id: 'u1', assigned_to_name: null }))
  })

  it('dismisses a suggestion only with a reason', async () => {
    const rows = await tab('Work orders', 'order-row')
    fireEvent.click(within(rows[0]).getByRole('button', { name: 'Open' }))
    const dialog = await screen.findByRole('dialog')
    fireEvent.click(await within(dialog).findByRole('button', { name: 'Dismiss' }, PATIENT))
    const go = within(dialog).getByRole('button', { name: 'Dismiss it' })
    expect(go).toBeDisabled()
    expect(dialog).toHaveTextContent('the same thing is not put forward again')
    fireEvent.change(within(dialog).getByLabelText('Why it is dismissed'), { target: { value: ' Bay closed for repainting. ' } })
    fireEvent.click(go)
    await waitFor(() => expect(work.dismissOrder).toHaveBeenCalledWith('w1', 'Bay closed for repainting.'))
  })

  it('starts the work, and records it as done with what was done', async () => {
    vi.mocked(work.getOrder).mockResolvedValue(OPEN)
    const rows = await tab('Work orders', 'order-row')
    fireEvent.click(within(rows[1]).getByRole('button', { name: 'Open' }))
    const dialog = await screen.findByRole('dialog')
    fireEvent.click(await within(dialog).findByRole('button', { name: 'Start the work' }, PATIENT))
    await waitFor(() => expect(work.startOrder).toHaveBeenCalledWith('w2'))
    fireEvent.click(within(dialog).getByRole('button', { name: 'It is done' }))
    const done = within(dialog).getByRole('button', { name: 'Record it as done' })
    expect(done).toBeDisabled()
    fireEvent.change(within(dialog).getByLabelText('What was done'), { target: { value: ' Pack replaced. ' } })
    fireEvent.change(within(dialog).getByLabelText('Minutes out of use'), { target: { value: '-5' } })
    expect(done).toBeDisabled()
    fireEvent.change(within(dialog).getByLabelText('Minutes out of use'), { target: { value: '35' } })
    fireEvent.change(within(dialog).getByLabelText('Parts used'), { target: { value: '1 x RBC7' } })
    fireEvent.click(done)
    await waitFor(() => expect(work.completeOrder).toHaveBeenCalledWith('w2', {
      completion_note: 'Pack replaced.', parts_used: '1 x RBC7', downtime_minutes: 35 }))
  })

  it('offers nothing on an order that is over, and shows what was done', async () => {
    vi.mocked(work.getOrder).mockResolvedValue(order({
      ...OPEN, state: 'DONE', overdue: false, completion_note: 'Pack replaced.', completed_by_name: 'Lee Technician',
      completed_at: '2026-10-07T02:00:00Z', downtime_minutes: 35, parts_used: '1 x RBC7', may: { ...NOTHING } }))
    const rows = await tab('Work orders', 'order-row')
    fireEvent.click(within(rows[1]).getByRole('button', { name: 'Open' }))
    const dialog = await screen.findByRole('dialog')
    const done = await within(dialog).findByTestId('done', {}, PATIENT)
    expect(done).toHaveTextContent('Pack replaced.')
    expect(done).toHaveTextContent('out of use for 35 min, as stated')
    expect(within(dialog).getAllByRole('button').map((b) => b.textContent)).toEqual(['Close'])
  })

  it('raises an order on an asset or for a defect, and says the defect is not changed', async () => {
    await tab('Work orders', 'order-row')
    fireEvent.click(screen.getByRole('button', { name: 'Raise a work order' }))
    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveTextContent('does not change the defect')
    const go = within(dialog).getByRole('button', { name: 'Raise it' })
    expect(go).toBeDisabled()
    fireEvent.change(within(dialog).getByLabelText('What is to be done'), { target: { value: ' Replace the housing ' } })
    await waitFor(() => expect(work.getMaintenanceOptions).toHaveBeenCalled())
    await pick(dialog, 'On', 'Defect: Camera housing cracked — Factory A')
    await pick(dialog, 'Priority', 'Urgent')
    fireEvent.change(within(dialog).getByLabelText('Or a vendor or technician'), { target: { value: ' SecureVision ' } })
    fireEvent.click(go)
    await waitFor(() => expect(work.raiseOrder).toHaveBeenCalledWith({
      title: 'Replace the housing', description: null, kind: 'CORRECTIVE', priority: 'URGENT', due_at: null,
      asset_id: null, defect_id: 'd1', assigned_to_user_id: null, assigned_to_name: 'SecureVision' }))
  })

  it('gives the server\'s reason when a step is refused', async () => {
    vi.mocked(work.getOrder).mockResolvedValue(OPEN)
    vi.mocked(work.startOrder).mockRejectedValue(refusal('This work has already started.', 409))
    const rows = await tab('Work orders', 'order-row')
    fireEvent.click(within(rows[1]).getByRole('button', { name: 'Open' }))
    const dialog = await screen.findByRole('dialog')
    fireEvent.click(await within(dialog).findByRole('button', { name: 'Start the work' }, PATIENT))
    expect(await within(dialog).findByText('This work has already started.')).toBeInTheDocument()
  })
})

describe('schedules', () => {
  it('lists what is done every so many days, and switches one off', async () => {
    const rows = await tab('Schedules', 'schedule-row')
    expect(rows[0]).toHaveTextContent('Quarterly battery test')
    expect(rows[0]).toHaveTextContent('AST-0002 UPS rack 2')
    expect(rows[0]).toHaveTextContent('Every 90 days · Due in 3 days')
    expect(rows[0]).toHaveTextContent('not done yet')
    expect(rows[1]).toHaveTextContent('Switched off')
    expect(screen.getByText(/put forward for somebody to accept/)).toBeInTheDocument()
    fireEvent.click(within(rows[0]).getByRole('button', { name: 'Switch off' }))
    await waitFor(() => expect(work.changeSchedule).toHaveBeenCalledWith('m1', { is_active: false }))
    fireEvent.click(within(rows[1]).getByRole('button', { name: 'Switch on' }))
    await waitFor(() => expect(work.changeSchedule).toHaveBeenCalledWith('m2', { is_active: true }))
  })

  it('adds a schedule, and says the work is put forward and not raised by itself', async () => {
    await tab('Schedules', 'schedule-row')
    fireEvent.click(screen.getByRole('button', { name: 'Add a schedule' }))
    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveTextContent('It is not raised by itself.')
    const add = within(dialog).getByRole('button', { name: 'Add it' })
    fireEvent.change(within(dialog).getByLabelText('What is to be done'), { target: { value: ' Monthly lamp test ' } })
    expect(add).toBeDisabled()
    fireEvent.change(within(dialog).getByLabelText('Next due on'), { target: { value: '2026-11-01' } })
    fireEvent.change(within(dialog).getByLabelText('Every (days)'), { target: { value: '0' } })
    expect(add).toBeDisabled()
    fireEvent.change(within(dialog).getByLabelText('Every (days)'), { target: { value: '30' } })
    await waitFor(() => expect(work.getMaintenanceOptions).toHaveBeenCalled())
    await pick(dialog, 'On', 'AST-0002 · UPS rack 2 — Factory A')
    fireEvent.click(add)
    await waitFor(() => expect(work.addSchedule).toHaveBeenCalledWith({
      title: 'Monthly lamp test', instructions: null, asset_id: 'a2', every_days: 30, lead_days: 7, next_due_on: '2026-11-01' }))
  })

  it('offers a reader no changes', async () => {
    vi.mocked(work.listSchedules).mockResolvedValue({ items: [schedule({})], can_manage: false })
    const rows = await tab('Schedules', 'schedule-row')
    expect(within(rows[0]).queryByRole('button')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Add a schedule' })).not.toBeInTheDocument()
  })
})

describe('who sees which part', () => {
  it('shows only maintenance to somebody who does not read assets', async () => {
    signedIn(4, ['maintenance:read'])
    show()
    expect(await screen.findAllByTestId('order-row', {}, PATIENT)).toHaveLength(2)
    expect(screen.queryByRole('tab', { name: 'Device health' })).not.toBeInTheDocument()
    expect(screen.queryByRole('tab', { name: 'Asset register' })).not.toBeInTheDocument()
    expect(assets.getHealth).not.toHaveBeenCalled()
  })
})
