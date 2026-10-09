/**
 * Privacy zones on the web: the tab, and what a player does about a masked camera.
 *
 * WHY THIS MATTERS
 *   A zone is applied by the server and cannot be undone for what is recorded
 *   after it, so the screen says what it does before the button is pressed and
 *   asks before one is deleted. And a player that chose HLS for a camera it
 *   did not yet know to be unmasked would show nothing but a refusal — so
 *   until it is known, the answer is the masked view.
 */
import type { ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ThemeProvider } from '@mui/material'
import { render, renderHook, screen, fireEvent, waitFor } from '@/test/utils'
import { theme } from '@/theme/glassmorphism'
import * as api from '@/api/privacyZones'
import type { PrivacyZone } from '@/api/privacyZones'
import * as cameras from '@/api/cameras'
import { useMaskedCameras } from '@/hooks/useMaskedCameras'
import { PrivacyZonesPanel } from './PrivacyZonesPanel'
import { MASKED_LABEL, WHAT_A_ZONE_DOES, WHAT_DELETING_DOES, ZONE_LIMITS } from './privacyZoneWords'

vi.mock('@/api/privacyZones', async (orig) => {
  const real = await orig<typeof import('@/api/privacyZones')>()
  return { ...real, listPrivacyZones: vi.fn(), createPrivacyZone: vi.fn(), deletePrivacyZone: vi.fn(), getMaskedCameras: vi.fn() }
})
vi.mock('@/api/cameras', async (orig) => {
  const real = await orig<typeof import('@/api/cameras')>()
  return { ...real, getCameras: vi.fn() }
})
// The editor needs a camera's picture. Here it is a button that draws a square.
const SQUARE = [{ x: 0, y: 0 }, { x: 0.5, y: 0 }, { x: 0.5, y: 1 }, { x: 0, y: 1 }]
vi.mock('@/components/common/ZonePolygonEditor', () => ({
  ZonePolygonEditor: ({ cameraId, onChange }: { cameraId: string; onChange: (p: unknown[]) => void }) => (
    <button type="button" onClick={() => onChange(SQUARE)}>draw on {cameraId}</button>),
}))

function client() {
  return new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } })
}
function within(qc: QueryClient) {
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}><ThemeProvider theme={theme}>{children}</ThemeProvider></QueryClientProvider>)
}
function show() {
  return render(<PrivacyZonesPanel />, { wrapper: within(client()) })
}

const WINDOW: PrivacyZone = {
  id: 'z1', camera_id: 'c1', camera_name: 'Gate 1', name: "The neighbour's window", polygon: SQUARE, fill_color: '#000000',
  is_active: true, created_at: '2026-10-09T02:30:00Z', created_by_name: 'Siti Rahman',
}
const refusal = (detail: unknown, status = 409) => Object.assign(new Error('x'), { response: { status, data: { detail } } })
const PATIENT = { timeout: 8000 }

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.listPrivacyZones).mockResolvedValue([WINDOW])
  vi.mocked(api.createPrivacyZone).mockResolvedValue({ ...WINDOW, id: 'z2', applies_within_seconds: 10 })
  vi.mocked(api.deletePrivacyZone).mockResolvedValue({ deleted: true, id: 'z1' })
  vi.mocked(api.getMaskedCameras).mockResolvedValue({ camera_ids: ['c1'], refresh_seconds: 10 })
  vi.mocked(cameras.getCameras).mockResolvedValue([
    { id: 'c1', name: 'Gate 1', location: 'North fence' }, { id: 'c2', name: 'Loading bay', location: null },
  ] as never)
})

describe('the words of a privacy zone', () => {
  it('say that it cannot be undone, when it takes effect, and what it does not cover', () => {
    expect(WHAT_A_ZONE_DOES).toMatch(/Within about ten seconds/)
    expect(WHAT_A_ZONE_DOES).toMatch(/cannot be unmasked/)
    expect(WHAT_A_ZONE_DOES).toMatch(/before the zone was drawn is not changed/)
    expect(WHAT_DELETING_DOES).toMatch(/stays masked/)
    expect(ZONE_LIMITS).toHaveLength(4)
    expect(ZONE_LIMITS.join(' ')).toMatch(/fixed to the picture.*no HLS live view.*AI sees nothing inside.*recorder at the site/s)
    expect(MASKED_LABEL).toBe('Privacy zone')
  })

  it('give a refusal in the server\'s own words', () => {
    expect(api.apiError(refusal('A camera has at most 20 privacy zones. Delete one first.'))).toBe(
      'A camera has at most 20 privacy zones. Delete one first.')
    expect(api.apiError(refusal([{ msg: 'Value error, A zone has 3 to 64 points.' }], 422))).toBe('A zone has 3 to 64 points.')
    expect(api.apiError(new Error('Network Error'))).toBe('Network Error')
  })
})

describe('the privacy zones tab', () => {
  it('lists each zone with its camera and who drew it, under what a zone does and does not do', async () => {
    show()
    expect(await screen.findByText("The neighbour's window", {}, PATIENT)).toBeInTheDocument()
    expect(screen.getByText('Gate 1')).toBeInTheDocument()
    expect(screen.getByText('Siti Rahman')).toBeInTheDocument()
    expect(screen.getByText(WHAT_A_ZONE_DOES)).toBeInTheDocument()
    for (const limit of ZONE_LIMITS) expect(screen.getByText(limit)).toBeInTheDocument()
  })

  it('says that every camera is whole when no zone is drawn, and that a switched-off zone masks nothing', async () => {
    vi.mocked(api.listPrivacyZones).mockResolvedValue([])
    const first = show()
    expect(await screen.findByText(/No privacy zone is drawn\. Every camera is shown, analysed and recorded whole\./, {}, PATIENT))
      .toBeInTheDocument()
    first.unmount()
    vi.mocked(api.listPrivacyZones).mockResolvedValue([{ ...WINDOW, is_active: false }])
    show()
    expect(await screen.findByText(/switched off, masks nothing/, {}, PATIENT)).toBeInTheDocument()
  })

  it('draws a zone only once it has a camera, a name and a shape, and says first what that does', async () => {
    show()
    await screen.findByText("The neighbour's window", {}, PATIENT)
    fireEvent.click(screen.getByRole('button', { name: 'Draw a privacy zone' }))
    const go = await screen.findByRole('button', { name: 'Mask this part of the picture' })
    // What it does is said in the dialog too, beside the button that does it.
    expect(screen.getAllByText(WHAT_A_ZONE_DOES)).toHaveLength(2)
    expect(go).toBeDisabled()
    fireEvent.mouseDown(screen.getByRole('combobox', { name: 'Camera' }))
    fireEvent.click(await screen.findByRole('option', { name: /Loading bay/ }))
    expect(go).toBeDisabled()
    fireEvent.change(screen.getByLabelText('What it covers'), { target: { value: '  Staff door  ' } })
    expect(go).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'draw on c2' }))
    expect(go).toBeEnabled()
    fireEvent.click(go)
    // The camera, the name and the corners: nothing else is the browser's to say.
    await waitFor(() => expect(api.createPrivacyZone).toHaveBeenCalledWith({ camera_id: 'c2', name: 'Staff door', polygon: SQUARE }))
    // The list is read again, and so is which cameras are masked: a wall on this screen keeps up.
    await waitFor(() => expect(api.listPrivacyZones).toHaveBeenCalledTimes(2), PATIENT)
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Mask this part of the picture' })).not.toBeInTheDocument())
  })

  it('says a refusal in the server\'s words and keeps what was drawn', async () => {
    vi.mocked(api.createPrivacyZone).mockRejectedValue(
      refusal("A drone's camera cannot have a privacy zone: a zone is fixed to the picture, and a drone's moves."))
    show()
    await screen.findByText("The neighbour's window", {}, PATIENT)
    fireEvent.click(screen.getByRole('button', { name: 'Draw a privacy zone' }))
    fireEvent.mouseDown(await screen.findByRole('combobox', { name: 'Camera' }))
    fireEvent.click(await screen.findByRole('option', { name: /Gate 1/ }))
    fireEvent.change(screen.getByLabelText('What it covers'), { target: { value: 'Road' } })
    fireEvent.click(screen.getByRole('button', { name: 'draw on c1' }))
    fireEvent.click(screen.getByRole('button', { name: 'Mask this part of the picture' }))
    expect(await screen.findByText(/A drone's camera cannot have a privacy zone/, {}, PATIENT)).toBeInTheDocument()
    expect(screen.getByLabelText('What it covers')).toHaveValue('Road')
    expect(api.listPrivacyZones).toHaveBeenCalledTimes(1)
  })

  it('asks before a zone is deleted, says what that changes, and deletes nothing when it is kept', async () => {
    show()
    await screen.findByText("The neighbour's window", {}, PATIENT)
    fireEvent.click(screen.getByRole('button', { name: "Delete the zone The neighbour's window" }))
    expect(await screen.findByText('Delete this privacy zone?')).toBeInTheDocument()
    expect(screen.getByText(WHAT_DELETING_DOES)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Keep it' }))
    await waitFor(() => expect(screen.queryByText('Delete this privacy zone?')).not.toBeInTheDocument())
    expect(api.deletePrivacyZone).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: "Delete the zone The neighbour's window" }))
    fireEvent.click(await screen.findByRole('button', { name: 'Delete the zone' }))
    await waitFor(() => expect(api.deletePrivacyZone).toHaveBeenCalledWith('z1'))
    await waitFor(() => expect(api.listPrivacyZones).toHaveBeenCalledTimes(2), PATIENT)
  })
})

describe('a player and a masked camera', () => {
  it('plays nothing through HLS until it is known which cameras have a zone, and never a camera that has one', async () => {
    let answer: (value: api.MaskedCameras) => void = () => undefined
    vi.mocked(api.getMaskedCameras).mockReturnValue(new Promise((resolve) => { answer = resolve }))
    const { result } = renderHook(() => useMaskedCameras(), { wrapper: within(client()) })
    // Not known yet: the masked view is right for every camera, so that is what is shown.
    expect(result.current.known).toBe(false)
    expect(result.current.mayPlayHls('c1')).toBe(false)
    expect(result.current.mayPlayHls('c2')).toBe(false)
    answer({ camera_ids: ['c1'], refresh_seconds: 10 })
    await waitFor(() => expect(result.current.known).toBe(true), PATIENT)
    expect(result.current.has('c1')).toBe(true)
    expect(result.current.mayPlayHls('c1')).toBe(false)
    expect(result.current.has('c2')).toBe(false)
    expect(result.current.mayPlayHls('c2')).toBe(true)
  })

  it('plays nothing through HLS when the list could not be read', async () => {
    vi.mocked(api.getMaskedCameras).mockRejectedValue(new Error('Network Error'))
    const { result } = renderHook(() => useMaskedCameras(), { wrapper: within(client()) })
    await waitFor(() => expect(api.getMaskedCameras).toHaveBeenCalled())
    expect(result.current.known).toBe(false)
    expect(result.current.mayPlayHls('c2')).toBe(false)
  })
})
