import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ThemeProvider } from '@mui/material'
import { render, screen, fireEvent, waitFor } from '@/test/utils'
import { theme } from '@/theme/glassmorphism'
import * as api from '@/api/platformDroneLicense'
import type { DroneLicense } from '@/api/platformDroneLicense'
import { PLATFORM_MODULE_LABELS } from '@/api/platform_licenses'
import { asLimit, bodyOf, draftOf, limitsValid, untilEndOf } from '@/components/platform/droneLicenseFormat'
import { DroneLicensePanel } from './DroneLicensePanel'

vi.mock('@/api/platformDroneLicense', async (orig) => {
  const real = await orig<typeof import('@/api/platformDroneLicense')>()
  return { ...real, getDroneLicense: vi.fn(), putDroneLicense: vi.fn() }
})

function show() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}><ThemeProvider theme={theme}><DroneLicensePanel tenantId="t1" /></ThemeProvider></QueryClientProvider>,
    { wrapper: ({ children }) => <>{children}</> })
}

const OFF: DroneLicense = {
  tenant_id: 't1', tenant_name: 'Acme Security', tenant_slug: 'acme', licensed: false,
  reason: 'Drone Patrol is not licensed for this organisation.', is_enabled: false, licensed_at: null, expires_at: null,
  max_drones: null, max_missions: null, max_sites: null, notes: null, updated_at: null,
}
const ON: DroneLicense = {
  ...OFF, licensed: true, reason: null, is_enabled: true, licensed_at: '2026-09-01T02:00:00Z',
  expires_at: '2027-08-31T23:59:59Z', max_drones: 4, max_missions: 50, max_sites: 2, notes: 'Pilot for two sites.',
  updated_at: '2026-09-01T02:00:00Z',
}
const refusal = (detail: unknown, status = 422) => Object.assign(new Error('x'), { response: { status, data: { detail } } })
const PATIENT = { timeout: 8000 }

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.getDroneLicense).mockResolvedValue(OFF)
  vi.mocked(api.putDroneLicense).mockResolvedValue(ON)
})

describe('the words of a drone licence', () => {
  it('reads a limit left empty as no limit, and a day as the end of that day', () => {
    expect(asLimit('')).toBeNull()
    expect(asLimit('  ')).toBeNull()
    expect(asLimit('4')).toBe(4)
    expect(asLimit('0')).toBe(0)
    expect(Number.isNaN(asLimit('four'))).toBe(true)
    expect(Number.isNaN(asLimit('-1'))).toBe(true)
    expect(Number.isNaN(asLimit('2.5'))).toBe(true)
    expect(untilEndOf('2027-08-31')).toBe('2027-08-31T23:59:59Z')
    expect(untilEndOf('')).toBeNull()
    expect(draftOf(ON)).toEqual({ on: true, until: '2027-08-31', drones: '4', missions: '50', sites: '2', notes: 'Pilot for two sites.' })
    expect(draftOf(OFF)).toEqual({ on: false, until: '', drones: '', missions: '', sites: '', notes: '' })
    expect(bodyOf(draftOf(ON))).toEqual({ is_enabled: true, expires_at: '2027-08-31T23:59:59Z', max_drones: 4, max_missions: 50,
                                           max_sites: 2, notes: 'Pilot for two sites.' })
    expect(bodyOf(draftOf(OFF))).toEqual({ is_enabled: false, expires_at: null, max_drones: null, max_missions: null,
                                            max_sites: null, notes: null })
    expect(limitsValid({ ...draftOf(ON), drones: 'x' })).toBe(false)
    expect(limitsValid(draftOf(OFF))).toBe(true)
  })

  it('names every module of the catalogue, the newer ones too', () => {
    for (const code of ['vms', 'detections', 'operations', 'analytics', 'compliance', 'client_portal', 'shifts', 'patrols', 'dob',
                        'visitors', 'sos', 'dispatch', 'virtual_patrol', 'security_intelligence', 'investigation',
                        'evidence_packages', 'security_map', 'risk_advice', 'operations_board', 'cases', 'data_retention',
                        'guard_response', 'sop_library', 'visitor_authorisation', 'assets_maintenance', 'workforce_readings']) {
      expect(PLATFORM_MODULE_LABELS[code], code).toBeTruthy()
    }
    // Drone Patrol has a licence of its own, with limits; it is not a row of the catalogue.
    expect(PLATFORM_MODULE_LABELS.drone_patrol).toBeUndefined()
  })
})

describe('the drone licence panel', () => {
  it('shows an organisation that is not licensed, with the server\'s reason, and nothing to save until something changes', async () => {
    show()
    const panel = await screen.findByTestId('drone-license', {}, PATIENT)
    expect(api.getDroneLicense).toHaveBeenCalledWith('t1')
    expect(panel).toHaveTextContent('Not licensed')
    expect(screen.getByTestId('drone-license-reason')).toHaveTextContent('Drone Patrol is not licensed for this organisation.')
    expect(panel).toHaveTextContent('Drone Patrol is off for this organisation')
    expect(screen.getByRole('button', { name: 'Save the licence' })).toBeDisabled()
    expect(panel).toHaveTextContent('written in the organisation\'s own audit log')
  })

  it('switches it on with an expiry and limits, saved as a whole', async () => {
    show()
    await screen.findByTestId('drone-license', {}, PATIENT)
    fireEvent.click(screen.getByRole('switch'))
    fireEvent.change(screen.getByLabelText('Last day of the licence'), { target: { value: '2027-08-31' } })
    fireEvent.change(screen.getByLabelText('Most drones'), { target: { value: '4' } })
    fireEvent.change(screen.getByLabelText('Most sites'), { target: { value: 'two' } })
    const save = screen.getByRole('button', { name: 'Save the licence' })
    expect(save).toBeDisabled()
    expect(screen.getByText('A whole number, or empty')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Most sites'), { target: { value: '2' } })
    fireEvent.change(screen.getByLabelText('Notes'), { target: { value: ' Pilot for two sites. ' } })
    await waitFor(() => expect(save).toBeEnabled(), PATIENT)
    fireEvent.click(save)
    await waitFor(() => expect(api.putDroneLicense).toHaveBeenCalledWith('t1', {
      is_enabled: true, expires_at: '2027-08-31T23:59:59Z', max_drones: 4, max_missions: null, max_sites: 2,
      notes: 'Pilot for two sites.' }), PATIENT)
    // What the server made of it is shown: licensed, with the limits it kept.
    expect(await screen.findByText('Saved.', {}, PATIENT)).toBeInTheDocument()
    expect(screen.getByTestId('drone-license')).toHaveTextContent('Licensed')
    expect(screen.getByLabelText('Most missions')).toHaveValue('50')
    expect(screen.getByRole('button', { name: 'Save the licence' })).toBeDisabled()
  })

  it('shows the server\'s refusal in its own words, and the platform\'s own organisation as not licensable', async () => {
    vi.mocked(api.putDroneLicense).mockRejectedValue(refusal([{ msg: 'Value error, An enabled licence cannot already have expired.' }]))
    const first = show()
    await screen.findByTestId('drone-license', {}, PATIENT)
    fireEvent.click(screen.getByRole('switch'))
    fireEvent.change(screen.getByLabelText('Last day of the licence'), { target: { value: '2020-01-01' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save the licence' }))
    expect(await screen.findByText('An enabled licence cannot already have expired.', {}, PATIENT)).toBeInTheDocument()
    first.unmount()
    vi.mocked(api.getDroneLicense).mockRejectedValue(
      refusal('The platform tenant does not run drone patrols; license the module to a customer tenant.'))
    show()
    expect(await screen.findByTestId('drone-license-refused', {}, PATIENT)).toHaveTextContent(
      'The platform tenant does not run drone patrols')
    expect(screen.queryByRole('button', { name: 'Save the licence' })).not.toBeInTheDocument()
  })
})
