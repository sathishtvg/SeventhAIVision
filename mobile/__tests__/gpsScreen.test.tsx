/**
 * The phone's GPS screen, mounted, against what /api/v1/gps/vehicles really sends.
 *
 * It used to read `status`, `last_speed_kmh` and `last_seen_at`, which the
 * server never sent, and the dashboard's counts from fields it keeps under
 * `summary`: every pill, speed, time and count was blank. These hold that it
 * shows the server's tracking status - Online, Stale, Offline, Not configured -
 * counts and filters by it, gives a speed only for a vehicle that is online,
 * and calls an older position the last known one.
 */
import React from 'react'
import { fireEvent, render } from '@testing-library/react-native'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

const mockVehicles = [
  { id: 'v1', name: 'Patrol 3', plate_number: 'SBA 1234 K', vehicle_type: 'patrol_car', color: null, driver_name: 'K. Tan',
    site_id: 's1', site_name: 'Depot', is_active: true, current_status: 'moving', tracking_status: 'online',
    last_lat: 1.3644, last_lon: 103.9915, last_speed: '37.04', last_fix_at: '2026-10-15T04:25:49Z', last_position_at: '2026-10-15T04:25:49Z' },
  { id: 'v2', name: 'Van 1', plate_number: 'SKZ 9 B', vehicle_type: 'van', color: null, driver_name: null,
    site_id: 's1', site_name: 'Depot', is_active: true, current_status: 'idle', tracking_status: 'stale',
    last_lat: 1.30, last_lon: 103.80, last_speed: '12.00', last_fix_at: '2026-10-15T04:10:00Z', last_position_at: '2026-10-15T04:10:00Z' },
  { id: 'v3', name: 'Golf cart', plate_number: null, vehicle_type: 'golf_cart', color: null, driver_name: null,
    site_id: 's1', site_name: 'Depot', is_active: true, current_status: 'offline', tracking_status: 'not_configured',
    last_lat: null, last_lon: null, last_speed: null, last_fix_at: null, last_position_at: null },
]
const mockGet = jest.fn(async (url: string) => {
  if (url === '/api/v1/gps/vehicles') return { data: mockVehicles }
  throw new Error(`unexpected ${url}`)
})
jest.mock('@/api/client', () => ({ apiClient: { get: (url: string) => mockGet(url) } }))

import { GPSScreen } from '@/screens/GPSScreen'

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return render(<QueryClientProvider client={qc}><GPSScreen /></QueryClientProvider>)
}

it('shows each vehicle’s tracking status and counts them, from the one list the server sends', async () => {
  const s = mount()
  expect(await s.findByText('SBA 1234 K')).toBeTruthy()
  expect(s.getAllByText('Online').length).toBeGreaterThanOrEqual(2) // the pill and the count's label
  expect(s.getAllByText('Stale').length).toBeGreaterThanOrEqual(2)
  expect(s.getAllByText('Not configured').length).toBeGreaterThanOrEqual(2) // the pill and its filter
  expect(mockGet).toHaveBeenCalledTimes(1)
})

it('gives a speed only for a vehicle that is online, and calls an older position the last known one', async () => {
  const s = mount()
  expect(await s.findByText('1.36440, 103.99150  ·  37 km/h')).toBeTruthy()
  expect(s.getByText('Last known: 1.30000, 103.80000')).toBeTruthy()
})

it('filters by tracking status, and says so when none is', async () => {
  const s = mount()
  await s.findByText('SBA 1234 K')
  fireEvent.press(s.getByRole('button', { name: 'Stale' }))
  expect(s.queryByText('SBA 1234 K')).toBeNull()
  expect(s.getByText('SKZ 9 B')).toBeTruthy()
  fireEvent.press(s.getByRole('button', { name: 'Offline' }))
  expect(s.getByText('No vehicle is offline')).toBeTruthy()
})
