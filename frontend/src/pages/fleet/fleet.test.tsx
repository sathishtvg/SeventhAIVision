import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ThemeProvider } from '@mui/material'
import { render, screen, fireEvent, waitFor, within } from '@/test/utils'
import { theme } from '@/theme/glassmorphism'
import * as api from '@/api/fleet'
import type { FleetOptions, FleetVehicle, FleetVehicleDetail, VehicleList } from '@/api/fleet'
import FleetVehicles from './FleetVehicles'
import FleetVehiclePage from './FleetVehicle'
import FleetFields from './FleetFields'

// PageHeader reads the tenant's page names from the settings.
vi.mock('@/api/settings', () => ({ getSettings: vi.fn().mockResolvedValue([]), upsertSetting: vi.fn().mockResolvedValue({}) }))
vi.mock('@/api/fleet', async (orig) => {
  const real = await orig<typeof import('@/api/fleet')>()
  const fns = Object.fromEntries(Object.entries(real).map(([k, v]) => [k, typeof v === 'function' ? vi.fn() : v]))
  return { ...fns, vehicleLabel: real.vehicleLabel, fleetError: real.fleetError, fieldKeyOf: real.fieldKeyOf }
})

const mocked = vi.mocked(api)
const ALL = { edit: true, archive: true, manage_fields: true }
const NONE = { edit: false, archive: false, manage_fields: false }

const vehicle = (over: Partial<FleetVehicle> = {}): FleetVehicle => ({
  id: 'v1', name: 'Patrol 3', plate_number: 'SBA 1234 K', vehicle_type: 'patrol_car', type_label: 'Patrol car', make: 'Toyota',
  model: 'Hilux', color: 'White', site_id: 's1', site_name: 'Factory A', department: 'Patrol', service_status: 'in_service',
  service_label: 'In service', assigned_driver_user_id: null, driver_name: 'K. Tan', current_status: 'offline',
  last_position_at: null, archived: false, archived_at: null, archive_reason: null,
  created_at: '2026-10-14T02:00:00Z', updated_at: '2026-10-14T02:00:00Z', ...over,
})
const list = (items: FleetVehicle[], may = ALL): VehicleList => ({ items, total: items.length, limit: 25, offset: 0, may })
const OPTIONS: FleetOptions = {
  vehicle_types: [{ value: 'patrol_car', label: 'Patrol car' }, { value: 'van', label: 'Van' }],
  service_statuses: [{ value: 'in_service', label: 'In service' }, { value: 'maintenance', label: 'In maintenance' },
                     { value: 'out_of_service', label: 'Out of service' }],
  field_types: ['text', 'number', 'date', 'select', 'boolean'],
  sites: [{ id: 's1', name: 'Factory A' }], drivers: [], fields: [], site_required: false, may: ALL,
}

function show(element: React.ReactElement, at = '/gps/vehicles', path = '/gps/vehicles') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } })
  return render(
    <MemoryRouter initialEntries={[at]}>
      <QueryClientProvider client={qc}><ThemeProvider theme={theme}>
        <Routes><Route path={path} element={element} /></Routes>
      </ThemeProvider></QueryClientProvider>
    </MemoryRouter>, { wrapper: ({ children }) => <>{children}</> })
}

beforeEach(() => {
  vi.clearAllMocks()
  mocked.getFleetOptions.mockResolvedValue(OPTIONS)
})

describe('the vehicle list', () => {
  it('shows the vehicles the server sends, by registration number, with what the person may do', async () => {
    mocked.listFleetVehicles.mockResolvedValue(list([vehicle()]))
    show(<FleetVehicles />)
    expect(await screen.findByText('SBA 1234 K')).toBeInTheDocument()
    expect(screen.getByText('Factory A')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Register a vehicle' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Archive SBA 1234 K' })).toBeInTheDocument()
  })

  it('offers nothing a person may not do: the server says so, and checks again', async () => {
    mocked.listFleetVehicles.mockResolvedValue(list([vehicle()], NONE))
    show(<FleetVehicles />)
    await screen.findByText('SBA 1234 K')
    expect(screen.queryByRole('button', { name: 'Register a vehicle' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Archive/ })).not.toBeInTheDocument()
  })

  it('archives only with a reason, in a dialog that names the vehicle', async () => {
    mocked.listFleetVehicles.mockResolvedValue(list([vehicle()]))
    mocked.archiveFleetVehicle.mockResolvedValue(vehicle({ archived: true }))
    show(<FleetVehicles />)
    fireEvent.click(await screen.findByRole('button', { name: 'Archive SBA 1234 K' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText('Archive SBA 1234 K — Patrol 3?')).toBeInTheDocument()
    expect(within(dialog).getByText(/Nothing is deleted/)).toBeInTheDocument()
    const go = within(dialog).getByRole('button', { name: 'Archive' })
    expect(go).toBeDisabled()
    fireEvent.change(within(dialog).getByLabelText(/Why is it archived/), { target: { value: 'Sold at auction' } })
    fireEvent.click(go)
    await waitFor(() => expect(mocked.archiveFleetVehicle).toHaveBeenCalledWith('v1', 'Sold at auction'))
  })

  it('says the server’s reason when archiving is refused, and keeps the dialog open', async () => {
    mocked.listFleetVehicles.mockResolvedValue(list([vehicle()]))
    mocked.archiveFleetVehicle.mockRejectedValue({ response: { status: 409, data: { detail: 'This vehicle is already archived' } } })
    show(<FleetVehicles />)
    fireEvent.click(await screen.findByRole('button', { name: 'Archive SBA 1234 K' }))
    const dialog = await screen.findByRole('dialog')
    fireEvent.change(within(dialog).getByLabelText(/Why is it archived/), { target: { value: 'Sold' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Archive' }))
    expect(await within(dialog).findByText(/This vehicle is already archived/)).toBeInTheDocument()
  })

  it('says when the list could not be loaded, and asks again', async () => {
    mocked.listFleetVehicles.mockRejectedValueOnce({ message: 'Network Error', code: 'ERR_NETWORK' })
    mocked.listFleetVehicles.mockResolvedValue(list([vehicle()]))
    show(<FleetVehicles />)
    fireEvent.click(await screen.findByRole('button', { name: /Try again/ }))
    expect(await screen.findByText('SBA 1234 K')).toBeInTheDocument()
  })

  it('restores an archived vehicle from the archived view', async () => {
    mocked.listFleetVehicles.mockResolvedValue(list([vehicle({ archived: true, archive_reason: 'Sold' })]))
    mocked.restoreFleetVehicle.mockResolvedValue(vehicle())
    show(<FleetVehicles />)
    fireEvent.click(await screen.findByRole('button', { name: 'Restore SBA 1234 K' }))
    await waitFor(() => expect(mocked.restoreFleetVehicle).toHaveBeenCalledWith('v1'))
  })
})

describe('one vehicle', () => {
  const detail = (over: Partial<FleetVehicleDetail> = {}): FleetVehicleDetail => ({
    ...vehicle(), driver_display: 'K. Tan', driver_user_name: null, archived_by_name: null, groups: [],
    custom_fields: { fuel: 'Diesel', old_tag: 'A-7' },
    fields: [
      { id: 'f1', field_key: 'fuel', label: 'Fuel', field_type: 'select', options: ['Petrol', 'Diesel'], is_required: true,
        help_text: null, sort_order: 0, is_active: true },
      { id: 'f2', field_key: 'old_tag', label: 'Old tag', field_type: 'text', options: [], is_required: false,
        help_text: null, sort_order: 1, is_active: false },
    ],
    may: ALL, archived_note: 'Archived vehicles keep their history and can be restored. Nothing about them is deleted.', ...over,
  })

  it('shows its record and its own fields, a retired one said to be retired', async () => {
    mocked.getFleetVehicle.mockResolvedValue(detail())
    show(<FleetVehiclePage />, '/gps/vehicles/v1', '/gps/vehicles/:id')
    expect(await screen.findByText('Diesel')).toBeInTheDocument()
    expect(screen.getByText('Old tag (retired)')).toBeInTheDocument()
    expect(screen.getByText('A-7')).toBeInTheDocument()
  })

  it('says when, by whom and why it was archived, and offers to restore it', async () => {
    mocked.getFleetVehicle.mockResolvedValue(detail({ archived: true, archived_at: '2026-10-14T03:00:00Z',
                                                      archive_reason: 'Written off', archived_by_name: 'Siti Rahman' }))
    show(<FleetVehiclePage />, '/gps/vehicles/v1', '/gps/vehicles/:id')
    expect(await screen.findByText(/by Siti Rahman: Written off/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Restore' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Change' })).not.toBeInTheDocument()
  })

  it('says when it could not be loaded', async () => {
    mocked.getFleetVehicle.mockRejectedValue({ response: { status: 404, data: { detail: 'Vehicle not found' } } })
    show(<FleetVehiclePage />, '/gps/vehicles/v9', '/gps/vehicles/:id')
    expect(await screen.findByText('Vehicle not found')).toBeInTheDocument()
  })

  it('shows who changed it, the fields named and not their values', async () => {
    mocked.getFleetVehicle.mockResolvedValue(detail())
    mocked.getVehicleHistory.mockResolvedValue({ items: [
      { action: 'fleet.vehicle.changed', created_at: '2026-10-14T04:00:00Z', detail: { changed: ['service_status'] }, by_name: 'Arun Kumar' },
      { action: 'fleet.vehicle.created', created_at: '2026-10-14T02:00:00Z', detail: null, by_name: 'Siti Rahman' }] })
    show(<FleetVehiclePage />, '/gps/vehicles/v1', '/gps/vehicles/:id')
    fireEvent.click(await screen.findByRole('tab', { name: 'History of changes' }))
    expect(await screen.findByText('Changed: service status')).toBeInTheDocument()
    expect(screen.getByText('Registered')).toBeInTheDocument()
  })
})

describe('custom fields', () => {
  it('offers a key from the label, and keeps key and type once a field exists', async () => {
    mocked.listVehicleFields.mockResolvedValue({ items: [] })
    mocked.createVehicleField.mockResolvedValue({} as never)
    show(<FleetFields />, '/gps/fields', '/gps/fields')
    fireEvent.click(await screen.findByRole('button', { name: 'New field' }))
    const dialog = await screen.findByRole('dialog')
    fireEvent.change(within(dialog).getByLabelText(/^Label/), { target: { value: 'Fuel card no.' } })
    expect(within(dialog).getByLabelText(/^Key/)).toHaveValue('fuel_card_no')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add the field' }))
    await waitFor(() => expect(mocked.createVehicleField).toHaveBeenCalledWith(expect.objectContaining({
      field_key: 'fuel_card_no', field_type: 'text', label: 'Fuel card no.' })))
  })
})
