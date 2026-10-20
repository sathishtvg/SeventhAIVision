import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ThemeProvider } from '@mui/material'
import { render, screen, fireEvent, waitFor, within } from '@/test/utils'
import { theme } from '@/theme/glassmorphism'
import { useAuthStore } from '@/store/auth'
import * as api from '@/api/footage'
import type { FootagePage, FootageRules, FootageVideo } from '@/api/footage'
import { instantOf, keptText, titleOf } from '@/components/footage/footageFormat'
import FootageList from './FootageList'

vi.mock('@/store/auth', () => ({ useAuthStore: vi.fn() }))
vi.mock('@/api/sites', () => ({ getSites: vi.fn().mockResolvedValue([{ id: 's1', name: 'Factory A' }]) }))
// PageHeader reads the tenant's page names from the settings.
vi.mock('@/api/settings', () => ({ getSettings: vi.fn().mockResolvedValue([]), upsertSetting: vi.fn().mockResolvedValue({}) }))
vi.mock('@/api/footage', async (orig) => {
  const real = await orig<typeof import('@/api/footage')>()
  const fns = Object.fromEntries(Object.entries(real).map(([k, v]) => [k, typeof v === 'function' ? vi.fn() : v]))
  // The words and the sending loop are the real ones; only the calls to the server are stubbed.
  const sendRest: typeof real.sendRest = (video, file, onProgress, opts = {}) =>
    real.sendRest(video, file, onProgress, { ...opts, send: fns.sendPart as never, finish: fns.finishUpload as never })
  return { ...fns, extensionOf: real.extensionOf, refusalOf: real.refusalOf, sizeText: real.sizeText, sendRest }
})

const RULES: FootageRules = { part_size: 4, max_bytes: 4 * 1024 ** 3, max_seconds: 21600,
                              extensions: ['.avi', '.mkv', '.mov', '.mp4'], retention_days: 30 }
const MAY = { upload: true, review: true, delete: true, hold: true }

const video = (over: Partial<FootageVideo>): FootageVideo => ({
  id: 'v1', source: 'UPLOAD', title: 'Lobby, night of the 3rd', original_filename: 'lobby.mp4', content_type: 'video/mp4',
  size_bytes: 10, received_bytes: 10, part_size: 4, site_id: 's1', site_name: 'Factory A', place: 'Lobby',
  filmed_at: null, duration_seconds: null, width: null, height: null, frame_rate: null, status: 'QUEUED',
  status_label: 'Waiting to be analysed', progress: 0, error: null, sha256: null, uploaded_by_user_id: 'u1',
  uploaded_by_name: 'Olivia Operator', created_at: '2026-10-20T02:00:00Z', updated_at: '2026-10-20T02:00:00Z',
  upload_completed_at: '2026-10-20T02:01:00Z', processed_at: null, expires_at: '2026-11-19T02:00:00Z', held: false,
  hold_id: null, hold_reason: null, hold_placed_at: null, hold_placed_by_name: null, deleted_in_days: 30, ...over,
})

function asUser(id = 'u1', roleId = 4) {
  vi.mocked(useAuthStore).mockImplementation(((sel: (s: unknown) => unknown) =>
    sel({ user: { id, tenantId: 't1', roleId }, accessToken: 'tok', permissions: null })) as never)
}

function page(items: FootageVideo[], may = MAY): FootagePage {
  return { items, total: items.length, limit: 25, offset: 0, may, rules: RULES }
}

function show() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } })
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <ThemeProvider theme={theme}><FootageList /></ThemeProvider>
      </QueryClientProvider>
    </MemoryRouter>, { wrapper: ({ children }) => <>{children}</> })
}

beforeEach(() => {
  vi.clearAllMocks()
  asUser()
})

describe('Footage Review: the list', () => {
  it('lists the videos with where they are, their status and how long they are kept', async () => {
    vi.mocked(api.listFootage).mockResolvedValue(page([
      video({}),
      video({ id: 'v2', title: 'Car park', status: 'UPLOADING', status_label: 'Uploading', received_bytes: 4,
              place: null, deleted_in_days: 29 }),
      video({ id: 'v3', title: 'Gate', status: 'FAILED', status_label: 'Could not be analysed',
              error: 'This file is not a video.', held: true, hold_id: 'h1', hold_reason: 'A claim', deleted_in_days: null }),
    ]))
    show()
    const rows = await screen.findAllByTestId('footage-row')
    expect(rows).toHaveLength(3)
    expect(within(rows[0]).getByText('Factory A · Lobby')).toBeInTheDocument()
    expect(within(rows[0]).getByText('Deleted in 30 days')).toBeInTheDocument()
    expect(within(rows[1]).getByText('4 bytes of 10 bytes arrived')).toBeInTheDocument()
    expect(within(rows[1]).getByRole('button', { name: 'Carry on' })).toBeInTheDocument()
    expect(within(rows[2]).getByText('This file is not a video.')).toBeInTheDocument()
    expect(within(rows[2]).getByText('Held')).toBeInTheDocument()
    expect(within(rows[2]).queryByRole('button', { name: 'Delete' })).not.toBeInTheDocument()
    expect(screen.getByText(/raises an alert or an incident/)).toBeInTheDocument()
  })

  it('offers only what the person may do', async () => {
    vi.mocked(api.listFootage).mockResolvedValue(page([video({})], { upload: false, review: false, delete: false, hold: false }))
    show()
    await screen.findByTestId('footage-row')
    expect(screen.queryByRole('button', { name: 'Upload a video' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Hold' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Delete' })).not.toBeInTheDocument()
  })

  it('carries on only one\'s own upload', async () => {
    asUser('someone-else')
    vi.mocked(api.listFootage).mockResolvedValue(page([video({ status: 'UPLOADING', received_bytes: 4 })],
                                                       { ...MAY, delete: false }))
    show()
    const row = await screen.findByTestId('footage-row')
    expect(within(row).queryByRole('button', { name: 'Carry on' })).not.toBeInTheDocument()
    expect(within(row).queryByRole('button', { name: 'Cancel' })).not.toBeInTheDocument()
  })

  it('says so when the list could not be fetched', async () => {
    vi.mocked(api.listFootage).mockRejectedValue(new Error('Network Error'))
    show()
    expect(await screen.findByRole('button', { name: /try again/i })).toBeInTheDocument()
  })

  it('holds and deletes with a reason', async () => {
    vi.mocked(api.listFootage).mockResolvedValue(page([video({})]))
    vi.mocked(api.holdFootage).mockResolvedValue({ id: 'h1', placed_at: '2026-10-20T03:00:00Z' })
    vi.mocked(api.deleteFootage).mockResolvedValue({ deleted: true })
    show()
    fireEvent.click(await screen.findByRole('button', { name: 'Hold' }))
    fireEvent.change(screen.getByLabelText('Why it must be kept'), { target: { value: 'Asked for by the police' } })
    fireEvent.click(screen.getByRole('button', { name: 'Hold it' }))
    await waitFor(() => expect(api.holdFootage).toHaveBeenCalledWith('v1', 'Asked for by the police'))
    fireEvent.click(await screen.findByRole('button', { name: 'Delete' }))
    fireEvent.change(screen.getByLabelText('Why it is being deleted'), { target: { value: 'Not needed any more' } })
    fireEvent.click(screen.getByRole('button', { name: 'Delete it' }))
    await waitFor(() => expect(api.deleteFootage).toHaveBeenCalledWith('v1', 'Not needed any more'))
  })
})

describe('Footage Review: uploading', () => {
  const file = (name: string, size: number) => new File([new Uint8Array(size)], name, { type: 'video/mp4' })

  it('refuses what is not a video, and sends the rest in parts with the server\'s progress', async () => {
    vi.mocked(api.listFootage).mockResolvedValue(page([]))
    vi.mocked(api.startUpload).mockResolvedValue({ ...video({ id: 'new', status: 'UPLOADING', received_bytes: 0, size_bytes: 10 }), rules: RULES })
    let reached = 0
    vi.mocked(api.sendPart).mockImplementation(async (_id, offset, part) => {
      reached = offset + part.size
      return { id: 'new', received_bytes: reached, size_bytes: 10, complete: reached === 10 }
    })
    vi.mocked(api.finishUpload).mockResolvedValue({ ...video({ id: 'new' }), announced: true })
    show()
    fireEvent.click(await screen.findByRole('button', { name: 'Upload a video' }))
    const input = screen.getByTestId('file-input') as HTMLInputElement
    fireEvent.change(input, { target: { files: [file('notes.exe', 10)] } })
    expect(screen.getByText('Only MP4, MOV, MKV and AVI videos can be uploaded.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Upload' })).toBeDisabled()
    fireEvent.change(input, { target: { files: [file('Gate camera.MP4', 10)] } })
    expect(screen.getByLabelText('Title')).toHaveValue('Gate camera')
    fireEvent.click(screen.getByRole('button', { name: 'Upload' }))
    expect(await screen.findByText('Uploaded. It is waiting to be analysed.')).toBeInTheDocument()
    expect(api.startUpload).toHaveBeenCalledWith(expect.objectContaining({ filename: 'Gate camera.MP4', size_bytes: 10, title: 'Gate camera' }))
    expect(vi.mocked(api.sendPart).mock.calls.map((c) => c[1])).toEqual([0, 4, 8])
    expect(api.finishUpload).toHaveBeenCalledWith('new')
  })

  it('carries on an upload only with the same file, from where the server is', async () => {
    const stopped = video({ id: 'old', status: 'UPLOADING', received_bytes: 8, size_bytes: 10, original_filename: 'lobby.mp4' })
    vi.mocked(api.listFootage).mockResolvedValue(page([stopped]))
    vi.mocked(api.uploadState).mockResolvedValue({ id: 'old', status: 'UPLOADING', size_bytes: 10, received_bytes: 8, part_size: 4 })
    vi.mocked(api.sendPart).mockResolvedValue({ id: 'old', received_bytes: 10, size_bytes: 10, complete: true })
    vi.mocked(api.finishUpload).mockResolvedValue({ ...stopped, status: 'QUEUED', announced: true })
    show()
    fireEvent.click(await screen.findByRole('button', { name: 'Carry on' }))
    const input = screen.getByTestId('file-input') as HTMLInputElement
    fireEvent.change(input, { target: { files: [file('other.mp4', 10)] } })
    expect(screen.getByText(/Choose the same file as before: lobby.mp4/)).toBeInTheDocument()
    fireEvent.change(input, { target: { files: [file('lobby.mp4', 10)] } })
    fireEvent.click(screen.getByRole('button', { name: 'Carry on' }))
    expect(await screen.findByText('Uploaded. It is waiting to be analysed.')).toBeInTheDocument()
    expect(api.startUpload).not.toHaveBeenCalled()
    expect(vi.mocked(api.sendPart).mock.calls.map((c) => c[1])).toEqual([8])
  })
})

describe('Footage Review: the sending loop', () => {
  it('carries on from the server\'s offset when it has reached another, and gives up on a refusal', async () => {
    const blob = new Blob([new Uint8Array(10)])
    const conflict = Object.assign(new Error('409'), { response: { status: 409, data: { detail: { received_bytes: 8 } } } })
    vi.mocked(api.sendPart)
      .mockRejectedValueOnce(conflict)
      .mockResolvedValueOnce({ id: 'v', received_bytes: 10, size_bytes: 10, complete: true })
    vi.mocked(api.finishUpload).mockResolvedValue({ ...video({ id: 'v' }), announced: false })
    const seen: number[] = []
    await api.sendRest({ id: 'v', size_bytes: 10, received_bytes: 0, part_size: 4 }, blob, (n) => seen.push(n))
    expect(vi.mocked(api.sendPart).mock.calls.map((c) => c[1])).toEqual([0, 8])
    expect(seen).toEqual([0, 8, 10])

    vi.mocked(api.sendPart).mockReset().mockRejectedValue(Object.assign(new Error('403'), { response: { status: 403 } }))
    await expect(api.sendRest({ id: 'v', size_bytes: 10, received_bytes: 0, part_size: 4 }, blob, () => {})).rejects.toThrow('403')
    expect(api.sendPart).toHaveBeenCalledTimes(1)
  })

  it('tries a part again when the connection failed, and stops after so many tries', async () => {
    const blob = new Blob([new Uint8Array(4)])
    vi.mocked(api.sendPart).mockReset()
      .mockRejectedValueOnce(new Error('Network Error'))
      .mockResolvedValueOnce({ id: 'v', received_bytes: 4, size_bytes: 4, complete: true })
    vi.mocked(api.finishUpload).mockResolvedValue({ ...video({ id: 'v' }), announced: true })
    await api.sendRest({ id: 'v', size_bytes: 4, received_bytes: 0, part_size: 4 }, blob, () => {}, { pause: 0 })
    expect(api.sendPart).toHaveBeenCalledTimes(2)

    vi.mocked(api.sendPart).mockReset().mockRejectedValue(new Error('Network Error'))
    await expect(api.sendRest({ id: 'v', size_bytes: 4, received_bytes: 0, part_size: 4 }, blob, () => {},
                               { pause: 0, tries: 3 })).rejects.toThrow('Network Error')
    expect(api.sendPart).toHaveBeenCalledTimes(3)
  })
})

describe('Footage Review: words', () => {
  it('says how long a video is kept', () => {
    expect(keptText({ held: true, deleted_in_days: null })).toBe('Held')
    expect(keptText({ held: false, deleted_in_days: 0 })).toBe('Deleted today')
    expect(keptText({ held: false, deleted_in_days: 1 })).toBe('Deleted tomorrow')
    expect(keptText({ held: false, deleted_in_days: 12 })).toBe('Deleted in 12 days')
  })

  it('names a video after its file, and gives a time with its offset', () => {
    expect(titleOf('Gate camera.MP4')).toBe('Gate camera')
    expect(titleOf('.hidden')).toBe('.hidden')
    expect(instantOf('')).toBeNull()
    // The person's own clock, wherever the test runs: an instant, not a wall time.
    expect(instantOf('2026-10-03T22:15')).toBe(new Date(2026, 9, 3, 22, 15).toISOString())
  })
})
