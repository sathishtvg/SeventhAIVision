/**
 * The Dispatch screen sends a guard somebody chose.
 *
 * WHY THIS MATTERS
 *   The sheet had an ETA and a notes box and no way to say who was being sent;
 *   it posted an empty guard, which the server cannot store, so "Dispatch" on
 *   the phone had never once worked. This mounts the real screen and holds
 *   that a guard on duty is offered, that nothing is sent until one is chosen,
 *   and that what is sent is the guard and the notes under the server's names.
 */
import React from 'react'
import { Alert } from 'react-native'
import { fireEvent, render, waitFor } from '@testing-library/react-native'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

const mockDispatch = jest.fn(async (..._a: unknown[]) => ({ id: 'i1', status: 'in_progress' }))
jest.mock('@/api/dispatch', () => ({
  dispatchToIncident: (...a: unknown[]) => mockDispatch(...a),
  markArrived: jest.fn(async () => ({})),
}))
jest.mock('@/api/incidents', () => ({
  getIncidents: jest.fn(async () => [
    { id: 'i1', camera_id: 'c1', site_id: 'site-a', site_name: 'North Yard', title: 'Forced gate', description: null,
      severity: 'high', status: 'open', is_auto_created: false, resolved_at: null,
      created_at: '2026-10-06T02:00:00Z', updated_at: '2026-10-06T02:00:00Z' },
  ]),
}))
let mockBoard: () => Promise<unknown>
jest.mock('@/api/attendance', () => ({ getLiveAttendance: jest.fn(() => mockBoard()) }))

import { DispatchScreen } from '@/screens/DispatchScreen'
import { dispatchNotes, dispatchable, guardLine } from '@/lib/dispatchGuards'
import type { LiveAttendanceShift } from '@/api/attendance'

const shift = (over: Partial<LiveAttendanceShift>): LiveAttendanceShift => ({
  id: `s-${over.guard_user_id}-${over.site_id}`, guard_user_id: 'g1', site_id: 'site-a',
  scheduled_start: '2026-10-06T00:00:00Z', scheduled_end: '2026-10-06T12:00:00Z',
  actual_start: '2026-10-06T00:02:00Z', actual_end: null, status: 'active', is_late: false, late_minutes: null,
  overtime_minutes: null, is_within_geofence: true, check_in_is_mock_location: false, on_break: false,
  guard_name: 'Tan Wei Ming', guard_phone: null, site_name: 'North Yard', live_status: 'checked_in', ...over,
})
const BOARD = [
  shift({ guard_user_id: 'g3', guard_name: 'Rajesh Kumar', site_id: 'site-b', site_name: 'Dock 4' }),
  shift({ guard_user_id: 'g2', guard_name: 'Aisha Rahman', on_break: true }),
  shift({ guard_user_id: 'g1', guard_name: 'Tan Wei Ming' }),
  shift({ guard_user_id: 'g4', guard_name: 'Not Yet In', actual_start: null, status: 'scheduled' }),
  shift({ guard_user_id: 'g5', guard_name: 'Gone Home', actual_end: '2026-10-06T01:00:00Z', status: 'completed' }),
]

let qc: QueryClient
function mount() {
  qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { gcTime: 0 } } })
  return render(<QueryClientProvider client={qc}><DispatchScreen /></QueryClientProvider>)
}

beforeEach(() => {
  jest.clearAllMocks()
  mockBoard = async () => ({ shifts: BOARD, summary: {} })
  jest.spyOn(Alert, 'alert').mockImplementation(() => undefined)
})
afterEach(() => qc?.clear())

describe('who can be sent', () => {
  it('is the guards on duty now: at the incident’s site first, on a break after, each once', () => {
    const guards = dispatchable(BOARD, 'site-a')
    expect(guards.map((g) => g.name)).toEqual(['Tan Wei Ming', 'Aisha Rahman', 'Rajesh Kumar'])
    expect(guards.map(guardLine)).toEqual([
      'On duty at this site', 'On duty at this site · on a break', 'On duty at Dock 4'])
    // Somebody not yet checked in, or already checked out, is not on duty.
    expect(guards.map((g) => g.user_id)).not.toEqual(expect.arrayContaining(['g4', 'g5']))
    // A guard on two shifts today is one guard, shown where the incident is.
    const twice = dispatchable([shift({ guard_user_id: 'g1', site_id: 'site-b', site_name: 'Dock 4' }),
                                shift({ guard_user_id: 'g1' })], 'site-a')
    expect(twice).toHaveLength(1)
    expect(twice[0].at_site).toBe(true)
    // An incident with no site still gets a list; nobody is "at this site".
    expect(dispatchable(BOARD, null).every((g) => !g.at_site)).toBe(true)
    expect(dispatchable([], 'site-a')).toEqual([])
  })

  it('an ETA that was typed is said in the notes, since the server has no field for it', () => {
    expect(dispatchNotes('5', ' Use the north gate. ')).toBe('ETA 5 min. Use the north gate.')
    expect(dispatchNotes('5', '')).toBe('ETA 5 min.')
    expect(dispatchNotes('', 'Use the north gate.')).toBe('Use the north gate.')
    expect(dispatchNotes('', '  ')).toBeUndefined()
    expect(dispatchNotes('soon', '')).toBeUndefined()
    expect(dispatchNotes('0', '')).toBeUndefined()
  })
})

describe('the dispatch sheet', () => {
  jest.setTimeout(120_000)

  it('sends nothing until a guard is chosen, then sends that guard with the notes', async () => {
    const s = mount()
    fireEvent.press(await s.findByText('Dispatch Guard'))
    expect(await s.findByText('Tan Wei Ming')).toBeTruthy()
    expect(s.getByText('On duty at Dock 4')).toBeTruthy()
    expect(s.queryByText('Not Yet In')).toBeNull()

    fireEvent.press(s.getByText('Dispatch'))                 // nobody chosen yet
    expect(mockDispatch).not.toHaveBeenCalled()

    fireEvent.changeText(s.getByPlaceholderText('e.g. 5'), '7')
    fireEvent.changeText(s.getByPlaceholderText('Any instructions for the guard'), 'Use the north gate.')
    fireEvent.press(s.getByText('Aisha Rahman'))
    fireEvent.press(s.getByText('Dispatch'))
    await waitFor(() => expect(mockDispatch).toHaveBeenCalledTimes(1))
    expect(mockDispatch).toHaveBeenCalledWith('i1', { guard_user_id: 'g2', dispatch_notes: 'ETA 7 min. Use the north gate.' })
    // The sheet closes on success.
    await waitFor(() => expect(s.queryByText('Guard to send')).toBeNull())
  })

  it('says so when nobody is on duty, and when the board could not be read', async () => {
    mockBoard = async () => ({ shifts: [], summary: {} })
    const empty = mount()
    fireEvent.press(await empty.findByText('Dispatch Guard'))
    expect(await empty.findByText('No guard is on duty right now.')).toBeTruthy()
    fireEvent.press(empty.getByText('Dispatch'))
    expect(mockDispatch).not.toHaveBeenCalled()
    empty.unmount()
    qc.clear()

    mockBoard = async () => { throw new Error('Request failed with status code 403') }
    const failed = mount()
    fireEvent.press(await failed.findByText('Dispatch Guard'))
    expect(await failed.findByText('The guards on duty could not be loaded.')).toBeTruthy()
    expect(failed.queryByText('No guard is on duty right now.')).toBeNull()
  })
})
