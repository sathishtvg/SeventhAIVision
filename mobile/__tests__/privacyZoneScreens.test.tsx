/**
 * Privacy zones on the phone, mounted: drawing one, a camera's zones, and the
 * label on a masked picture.
 *
 * A zone is applied by the server and cannot be undone for what is recorded
 * after it. What this holds the phone to: it is offered only to somebody known
 * to manage privacy; it says what it does and asks once more before it masks;
 * a zone is deleted only after the phone has asked; and the two kinds of zone
 * that were there before are drawn exactly as they were.
 */
import React from 'react'
import { Alert } from 'react-native'
import { fireEvent, render, waitFor } from '@testing-library/react-native'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

// The live picture is a web view of the server's MJPEG feed. Here it keeps the page it was given.
const mockPages: string[] = []
jest.mock('react-native-webview', () => ({
  WebView: (props: { source?: { html?: string } }) => { mockPages.push(props.source?.html ?? ''); return null },
}))
// The overlay turns touches into corners. Here it is two buttons.
jest.mock('@/components/ZoneDrawOverlay', () => {
  const ReactInside = require('react')
  const { Pressable, Text } = require('react-native')
  return {
    ZoneDrawOverlay: ({ onAddPoint, onClose, color }: { onAddPoint: (p: unknown) => void; onClose: () => void; color?: string }) => (
      ReactInside.createElement(ReactInside.Fragment, null,
        ReactInside.createElement(Pressable, { onPress: () => {
          onAddPoint({ x: 0, y: 0 }); onAddPoint({ x: 0.5, y: 0 }); onAddPoint({ x: 0.5, y: 1 }) } },
          ReactInside.createElement(Text, null, 'place three points')),
        ReactInside.createElement(Pressable, { onPress: onClose }, ReactInside.createElement(Text, null, 'close the shape')),
        ReactInside.createElement(Text, { testID: 'drawing-colour' }, color ?? 'default'))),
  }
})
jest.mock('@/api/client', () => ({ apiClient: { defaults: { baseURL: 'http://test.local' } } }))

const mockNavigate = jest.fn()
const mockGoBack = jest.fn()
const mockSetOptions = jest.fn()
let mockRouteNames: string[] = ['CameraLive', 'ZoneDraw', 'CameraPrivacyZones']
jest.mock('@react-navigation/native', () => ({
  useNavigation: () => ({ navigate: mockNavigate, goBack: mockGoBack, setOptions: mockSetOptions,
                          getState: () => ({ routeNames: mockRouteNames }) }),
}))

let mockPermissions: string[] | null = []
jest.mock('@/store/auth', () => ({
  useAuthStore: (sel: (s: unknown) => unknown) => sel({ permissions: mockPermissions, user: { roleId: 3 }, accessToken: 'tok' }),
}))

const mockCreateZone = jest.fn(async (..._a: unknown[]) => ({ id: 'r1' }))
const mockCreateCrowdZone = jest.fn(async (..._a: unknown[]) => ({ id: 'cz1' }))
jest.mock('@/api/zones', () => ({
  createZone: (...a: unknown[]) => mockCreateZone(...a), createCrowdZone: (...a: unknown[]) => mockCreateCrowdZone(...a),
}))
const mockList = jest.fn()
const mockCreate = jest.fn()
const mockDelete = jest.fn()
const mockMasked = jest.fn()
jest.mock('@/api/privacyZones', () => ({
  listCameraPrivacyZones: (...a: unknown[]) => mockList(...a), createPrivacyZone: (...a: unknown[]) => mockCreate(...a),
  deletePrivacyZone: (...a: unknown[]) => mockDelete(...a), getMaskedCameras: (...a: unknown[]) => mockMasked(...a),
}))
jest.mock('@/api/cameras', () => ({
  getCameras: jest.fn(async () => [{ id: 'c1', name: 'Gate 1' }, { id: 'c2', name: 'Loading <bay>' }]),
  getStreams: jest.fn(async () => [{ id: 'st1', camera_id: 'c1' }, { id: 'st2', camera_id: 'c2' }]),
}))
jest.mock('@/lib/liveWallStorage', () => ({
  loadLiveWallCameras: jest.fn(async () => [
    { cameraId: 'c1', streamId: 'st1', cameraName: 'Gate 1' }, { cameraId: 'c2', streamId: 'st2', cameraName: 'Loading <bay>' }]),
  saveLiveWallCameras: jest.fn(),
}))

import { CameraLiveScreen } from '@/screens/CameraLiveScreen'
import { CameraPrivacyZonesScreen } from '@/screens/CameraPrivacyZonesScreen'
import { LiveWallScreen } from '@/screens/LiveWallScreen'
import { ZoneDrawScreen } from '@/screens/ZoneDrawScreen'
import { WHAT_A_ZONE_DOES, WHAT_DELETING_DOES } from '@/lib/privacyZoneWords'

const CAMERA = { cameraId: 'c1', streamId: 'st1', cameraName: 'Gate 1' }
const MANAGER = ['camera:read', 'zone:manage', 'privacy:manage']
const OPERATOR = ['camera:read', 'zone:manage']
const SQUARE = [{ x: 0, y: 0 }, { x: 0.5, y: 0 }, { x: 0.5, y: 1 }]
const WINDOW = { id: 'z1', camera_id: 'c1', camera_name: 'Gate 1', name: "The neighbour's window", polygon: SQUARE,
                 is_active: true, created_at: '2026-10-09T02:30:00Z', created_by_name: 'Siti Rahman' }
const refusal = (detail: unknown, status = 409) => ({ response: { status, data: { detail } } })

let qc: QueryClient
function mount(screen: React.ReactElement) {
  qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { gcTime: 0 } } })
  return render(<QueryClientProvider client={qc}>{screen}</QueryClientProvider>)
}
type Button = { text: string; onPress?: () => void }
/** The buttons of the last question the phone asked. */
const asked = () => (Alert.alert as jest.Mock).mock.calls.at(-1) as [string, string, Button[] | undefined]
const answer = (text: string) => asked()[2]!.find((b) => b.text === text)!.onPress?.()

beforeEach(() => {
  jest.clearAllMocks()
  mockPages.length = 0
  mockPermissions = MANAGER
  mockRouteNames = ['CameraLive', 'ZoneDraw', 'CameraPrivacyZones']
  mockList.mockResolvedValue([WINDOW])
  mockCreate.mockResolvedValue({ ...WINDOW, id: 'z2' })
  mockDelete.mockResolvedValue({ deleted: true, id: 'z1' })
  mockMasked.mockResolvedValue({ camera_ids: ['c1'], refresh_seconds: 10 })
  jest.spyOn(Alert, 'alert').mockImplementation(() => undefined)
})

afterEach(() => qc?.clear())

jest.setTimeout(120_000)

/** Place a shape and close it: the form is there once a shape is closed. */
function drawAShape(s: ReturnType<typeof mount>, name: string) {
  fireEvent.press(s.getByText('place three points'))
  fireEvent.press(s.getByText('close the shape'))
  fireEvent.changeText(s.getByPlaceholderText('Zone name'), name)
}

describe('drawing a zone on the phone', () => {
  it('offers Privacy only to somebody known to manage privacy', () => {
    for (const [permissions, offered] of [[MANAGER, true], [OPERATOR, false], [null, false]] as const) {
      mockPermissions = permissions as string[] | null
      const s = mount(<ZoneDrawScreen route={{ params: CAMERA }} />)
      drawAShape(s, 'x')
      expect(s.getByText('Restricted')).toBeTruthy()
      expect(s.getByText('Crowd')).toBeTruthy()
      expect(!!s.queryByText('Privacy')).toBe(offered)
      s.unmount()
    }
  })

  it('says what a privacy zone does, asks once more, and masks nothing when the answer is not yet', async () => {
    const s = mount(<ZoneDrawScreen route={{ params: CAMERA }} />)
    drawAShape(s, "  The neighbour's window ")
    fireEvent.press(s.getByText('Privacy'))
    // What it does is on the screen before the button; a privacy zone has no severity, and the button says what it is.
    expect(s.getByTestId('what-a-zone-does').props.children).toBe(WHAT_A_ZONE_DOES)
    expect(s.queryByText('Severity')).toBeNull()
    expect(s.queryByText('Save Zone')).toBeNull()
    expect(s.getByTestId('drawing-colour').props.children).not.toBe('default')
    fireEvent.press(s.getByText('Mask this part of the picture'))
    expect(mockCreate).not.toHaveBeenCalled()
    const [title, words, buttons] = asked()
    expect(title).toBe('Mask this part of the picture?')
    expect(words).toBe(WHAT_A_ZONE_DOES)
    expect(buttons!.map((b) => b.text)).toEqual(['Not yet', 'Mask it'])
    answer('Not yet')
    expect(mockCreate).not.toHaveBeenCalled()
    expect(mockGoBack).not.toHaveBeenCalled()

    // Asked again, and answered yes: the camera, what it covers and the corners, and nothing else.
    fireEvent.press(s.getByText('Mask this part of the picture'))
    answer('Mask it')
    await waitFor(() => expect(mockCreate).toHaveBeenCalledWith({ camera_id: 'c1', name: "  The neighbour's window ", polygon: SQUARE }))
    await waitFor(() => expect(mockGoBack).toHaveBeenCalledTimes(1))
    expect(asked()[0]).toBe('Privacy zone drawn')
    expect(asked()[1]).toMatch(/Within about ten seconds "The neighbour's window" is masked on this camera\./)
    expect(mockCreateZone).not.toHaveBeenCalled()
    expect(mockCreateCrowdZone).not.toHaveBeenCalled()
  })

  it('says a refusal in the server\'s words and stays where it was', async () => {
    mockCreate.mockRejectedValue(
      refusal("A drone's camera cannot have a privacy zone: a zone is fixed to the picture, and a drone's moves."))
    const s = mount(<ZoneDrawScreen route={{ params: { ...CAMERA, kind: 'privacy' } }} />)
    drawAShape(s, 'Road')
    fireEvent.press(s.getByText('Mask this part of the picture'))
    answer('Mask it')
    await waitFor(() => expect(asked()[0]).toBe('Save failed'))
    expect(asked()[1]).toMatch(/A drone's camera cannot have a privacy zone/)
    expect(mockGoBack).not.toHaveBeenCalled()
  })

  it('opens with Privacy chosen when it was reached from a camera\'s privacy zones - for somebody who manages privacy', () => {
    const mine = mount(<ZoneDrawScreen route={{ params: { ...CAMERA, kind: 'privacy' } }} />)
    drawAShape(mine, 'x')
    expect(mine.getByText('Mask this part of the picture')).toBeTruthy()
    mine.unmount()
    mockPermissions = OPERATOR
    const theirs = mount(<ZoneDrawScreen route={{ params: { ...CAMERA, kind: 'privacy' } }} />)
    drawAShape(theirs, 'x')
    expect(theirs.getByText('Save Zone')).toBeTruthy()
    expect(theirs.queryByText('Mask this part of the picture')).toBeNull()
  })

  it('draws a restricted zone and a crowd zone exactly as before, with no question asked first', async () => {
    const s = mount(<ZoneDrawScreen route={{ params: CAMERA }} />)
    drawAShape(s, 'Loading dock')
    expect(s.getByText('Severity')).toBeTruthy()
    expect(s.queryByTestId('what-a-zone-does')).toBeNull()
    fireEvent.press(s.getByText('high'))
    fireEvent.press(s.getByText('Save Zone'))
    await waitFor(() => expect(mockCreateZone).toHaveBeenCalledWith(
      { camera_id: 'c1', name: 'Loading dock', polygon: SQUARE, severity: 'high' }))
    await waitFor(() => expect(asked()[0]).toBe('Zone saved'))
    expect((Alert.alert as jest.Mock).mock.calls).toHaveLength(1)
    expect(mockCreate).not.toHaveBeenCalled()
    s.unmount()

    const crowd = mount(<ZoneDrawScreen route={{ params: CAMERA }} />)
    drawAShape(crowd, 'Queue')
    fireEvent.press(crowd.getByText('Crowd'))
    fireEvent.press(crowd.getByText('Save Zone'))
    await waitFor(() => expect(mockCreateCrowdZone).toHaveBeenCalledWith(
      { camera_id: 'c1', name: 'Queue', polygon: SQUARE, severity: 'medium' }))
  })
})

describe('the privacy zones of a camera', () => {
  it('lists each zone with who drew it, under what a zone does', async () => {
    const s = mount(<CameraPrivacyZonesScreen route={{ params: CAMERA }} />)
    expect(await s.findByText("The neighbour's window")).toBeTruthy()
    expect(mockList).toHaveBeenCalledWith('c1')
    expect(s.getByText(/^Drawn by Siti Rahman · /)).toBeTruthy()
    expect(s.getByTestId('what-a-zone-does').props.children).toBe(WHAT_A_ZONE_DOES)
    fireEvent.press(s.getByText('Draw a privacy zone'))
    expect(mockNavigate).toHaveBeenCalledWith('ZoneDraw', { ...CAMERA, kind: 'privacy' })
  })

  it('deletes a zone only after it has asked and said what that changes', async () => {
    const s = mount(<CameraPrivacyZonesScreen route={{ params: CAMERA }} />)
    fireEvent.press(await s.findByLabelText("Delete the zone The neighbour's window"))
    expect(mockDelete).not.toHaveBeenCalled()
    const [title, words, buttons] = asked()
    expect(title).toBe('Delete this privacy zone?')
    expect(words).toContain("“The neighbour's window” on Gate 1.")
    expect(words).toContain(WHAT_DELETING_DOES)
    expect(buttons!.map((b) => b.text)).toEqual(['Keep it', 'Delete the zone'])
    answer('Keep it')
    expect(mockDelete).not.toHaveBeenCalled()
    fireEvent.press(s.getByLabelText("Delete the zone The neighbour's window"))
    mockList.mockResolvedValue([])
    answer('Delete the zone')
    await waitFor(() => expect(mockDelete).toHaveBeenCalledWith('z1'))
    // The list is read again, and says that the camera is whole.
    expect(await s.findByText(/No privacy zone is drawn on this camera\. It is shown, analysed and recorded whole\./)).toBeTruthy()
  })

  it('says a refusal in the server\'s words, for the list and for a deletion', async () => {
    mockDelete.mockRejectedValue(refusal('This is done by a person who is signed in, not by an API key.', 403))
    const s = mount(<CameraPrivacyZonesScreen route={{ params: CAMERA }} />)
    fireEvent.press(await s.findByLabelText("Delete the zone The neighbour's window"))
    answer('Delete the zone')
    expect(await s.findByText('This is done by a person who is signed in, not by an API key.')).toBeTruthy()
    s.unmount()
    mockList.mockRejectedValue(refusal('Missing permission: privacy:manage', 403))
    const refused = mount(<CameraPrivacyZonesScreen route={{ params: CAMERA }} />)
    expect(await refused.findByText('Missing permission: privacy:manage')).toBeTruthy()
  })

  it('says that a zone switched off masks nothing', async () => {
    mockList.mockResolvedValue([{ ...WINDOW, is_active: false, created_by_name: null }])
    const s = mount(<CameraPrivacyZonesScreen route={{ params: CAMERA }} />)
    expect(await s.findByText('Switched off: it masks nothing.')).toBeTruthy()
    expect(s.getByText(/^Drawn /)).toBeTruthy()
    expect(s.queryByText(/Drawn by/)).toBeNull()
  })
})

describe('a live picture that has a privacy zone', () => {
  /** The buttons in the header of the live view, as last set. */
  const header = () => {
    const right = mockSetOptions.mock.calls.at(-1)![0].headerRight as (() => React.ReactElement) | undefined
    return right ? render(right()) : null
  }

  it('is labelled, and one that has none is not', async () => {
    const masked = mount(<CameraLiveScreen route={{ params: CAMERA }} />)
    expect(await masked.findByText('Privacy zone')).toBeTruthy()
    // The picture is the server's own live view, which is the masked one: the phone plays no other.
    expect(mockPages.at(-1)).toContain('http://test.local/api/v1/cameras/c1/streams/st1/live?token=tok')
    expect(mockPages.join('')).not.toContain('/hls/')
    masked.unmount()
    const plain = mount(<CameraLiveScreen route={{ params: { cameraId: 'c2', streamId: 'st2', cameraName: 'Loading bay' } }} />)
    await waitFor(() => expect(qc.isFetching()).toBe(0))
    expect(mockMasked).toHaveBeenCalled()
    expect(plain.queryByText('Privacy zone')).toBeNull()
  })

  it('gives the way to a camera\'s privacy zones to whoever manages privacy, where the screen is there to open', async () => {
    const mine = mount(<CameraLiveScreen route={{ params: CAMERA }} />)
    await mine.findByText('Privacy zone')
    const both = header()!
    fireEvent.press(both.getByLabelText('Privacy zones of this camera'))
    expect(mockNavigate).toHaveBeenCalledWith('CameraPrivacyZones', CAMERA)
    fireEvent.press(both.getByLabelText('Draw a zone'))
    expect(mockNavigate).toHaveBeenLastCalledWith('ZoneDraw', CAMERA)
    both.unmount()
    mine.unmount()

    // Somebody who does not manage privacy keeps the button they had, and gets no other.
    mockPermissions = OPERATOR
    const theirs = mount(<CameraLiveScreen route={{ params: CAMERA }} />)
    await theirs.findByText('Privacy zone')
    const one = header()!
    expect(one.queryByLabelText('Privacy zones of this camera')).toBeNull()
    expect(one.getByLabelText('Draw a zone')).toBeTruthy()
    one.unmount()
    theirs.unmount()

    // Opened from an incident, where neither screen is in the stack, there is no button at all - as before.
    mockPermissions = MANAGER
    mockRouteNames = ['IncidentDetail', 'IncidentCameraLive']
    const fromAnIncident = mount(<CameraLiveScreen route={{ params: CAMERA }} />)
    await fromAnIncident.findByText('Privacy zone')
    expect(header()).toBeNull()
  })

  it('is named on its tile of the live wall', async () => {
    mount(<LiveWallScreen />)
    await waitFor(() => expect(mockPages.some((page) => page.includes('Privacy zone'))).toBe(true))
    const page = mockPages.filter((p) => p.includes('class="tile"')).at(-1)!
    expect(page).toContain('<div class="label">Gate 1 · Privacy zone</div>')
    // A camera with no zone is named as it was - and its name is still made safe for the page.
    expect(page).toContain('<div class="label">Loading &lt;bay></div>')
    expect(page).not.toContain('/hls/')
  })
})
