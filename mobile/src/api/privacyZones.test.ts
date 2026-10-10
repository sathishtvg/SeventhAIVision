/**
 * Privacy zones on the phone: what goes on the wire, and who is offered the button.
 *
 * WHY THIS MATTERS
 *   The server takes a point as an x and a y and nothing else, so one stray
 *   key and a zone somebody drew is refused. And what the phone opens here
 *   masks a camera for good in what is recorded after it - so it is offered to
 *   somebody known to manage privacy, not to everybody while that is loading.
 */
import { apiClient } from './client'
import { createPrivacyZone, deletePrivacyZone, getMaskedCameras, listCameraPrivacyZones } from './privacyZones'
import {
  MASKED_LABEL, PRIVACY_PERMISSION, WHAT_A_ZONE_DOES, WHAT_DELETING_DOES, drawnLine, managesPrivacy,
} from '@/lib/privacyZoneWords'

jest.mock('./client', () => ({
  apiClient: {
    get: jest.fn(() => Promise.resolve({ data: [{ id: 'z1' }] })),
    post: jest.fn(() => Promise.resolve({ data: { id: 'z2' } })),
    delete: jest.fn(() => Promise.resolve({ data: { deleted: true, id: 'z1' } })),
  },
}))

const mockGet = apiClient.get as jest.Mock
const mockPost = apiClient.post as jest.Mock
const mockDelete = apiClient.delete as jest.Mock

beforeEach(() => { mockGet.mockClear(); mockPost.mockClear(); mockDelete.mockClear() })

test('a camera\'s zones are asked for by its id, and which cameras are masked is asked for whole', async () => {
  await expect(listCameraPrivacyZones('c1')).resolves.toEqual([{ id: 'z1' }])
  expect(mockGet).toHaveBeenLastCalledWith('/api/v1/privacy/zones', { params: { camera_id: 'c1' } })
  await getMaskedCameras()
  expect(mockGet).toHaveBeenLastCalledWith('/api/v1/privacy/masked-cameras')
})

test('a zone is sent as its camera, what it covers and its corners, each corner an x and a y and nothing else', async () => {
  const drawn = [{ x: 0, y: 0, pressure: 0.4 }, { x: 0.5, y: 0 }, { x: 0.5, y: 1 }]
  await createPrivacyZone({ camera_id: 'c1', name: "  The neighbour's window ", polygon: drawn })
  expect(mockPost).toHaveBeenCalledWith('/api/v1/privacy/zones', {
    camera_id: 'c1', name: "The neighbour's window", polygon: [{ x: 0, y: 0 }, { x: 0.5, y: 0 }, { x: 0.5, y: 1 }] })
  // No colour, no switch: the phone offers neither.
  expect(Object.keys(mockPost.mock.calls[0][1])).toEqual(['camera_id', 'name', 'polygon'])
})

test('a zone is deleted by its id', async () => {
  await expect(deletePrivacyZone('z1')).resolves.toEqual({ deleted: true, id: 'z1' })
  expect(mockDelete).toHaveBeenCalledWith('/api/v1/privacy/zones/z1')
})

describe('the words of a privacy zone on the phone', () => {
  it('say when it takes effect, that it cannot be undone, and what deleting leaves', () => {
    expect(WHAT_A_ZONE_DOES).toMatch(/Within about ten seconds/)
    expect(WHAT_A_ZONE_DOES).toMatch(/cannot be unmasked/)
    expect(WHAT_A_ZONE_DOES).toMatch(/before the zone was drawn is not changed/)
    expect(WHAT_DELETING_DOES).toMatch(/stays masked/)
    expect(MASKED_LABEL).toBe('Privacy zone')
  })

  it('offer drawing and deleting only to somebody known to manage privacy', () => {
    expect(PRIVACY_PERMISSION).toBe('privacy:manage')
    expect(managesPrivacy(['camera:read', 'privacy:manage'])).toBe(true)
    expect(managesPrivacy(['camera:read', 'zone:manage'])).toBe(false)
    expect(managesPrivacy([])).toBe(false)
    // Not known yet is not yes: the menu shows a row while this loads, this does not.
    expect(managesPrivacy(null)).toBe(false)
    expect(managesPrivacy(undefined)).toBe(false)
  })

  it('say who drew a zone when that is known, and when either way', () => {
    expect(drawnLine({ created_by_name: 'Siti Rahman', created_at: '2026-10-09T02:30:00Z' })).toMatch(/^Drawn by Siti Rahman · .*2026/)
    expect(drawnLine({ created_by_name: null, created_at: '2026-10-09T02:30:00Z' })).toMatch(/^Drawn .*2026/)
    expect(drawnLine({ created_by_name: null, created_at: '2026-10-09T02:30:00Z' })).not.toMatch(/by/)
  })
})
