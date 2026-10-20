import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ThemeProvider } from '@mui/material'
import { render, screen, fireEvent, waitFor, within } from '@/test/utils'
import { theme } from '@/theme/glassmorphism'
import { useAuthStore } from '@/store/auth'
import * as api from '@/api/footage'
import type { FootageVideo, SearchAnswer, SearchMoment } from '@/api/footage'
import FootageSearch from './FootageSearch'
import FootageVideoPage from './FootageVideo'

vi.mock('@/store/auth', () => ({ useAuthStore: vi.fn() }))
vi.mock('@/api/settings', () => ({ getSettings: vi.fn().mockResolvedValue([]), upsertSetting: vi.fn().mockResolvedValue({}) }))
vi.mock('@/api/footage', async (orig) => {
  const real = await orig<typeof import('@/api/footage')>()
  const fns = Object.fromEntries(Object.entries(real).map(([k, v]) => [k, typeof v === 'function' ? vi.fn() : v]))
  return { ...fns, clock: real.clock, sizeText: real.sizeText, frameUrl: real.frameUrl, videoUrl: real.videoUrl }
})

const moment = (over: Partial<SearchMoment>): SearchMoment => ({
  video_id: 'v1', title: 'Lobby', site_name: 'Factory A', place: 'Lobby', start: 20, end: 22, best: 21, likeness: 0.31,
  match: 0.99, label: 'HIGH', label_words: 'Strong match', at: '2026-10-03T14:00:21Z', ...over,
})
const answer = (over: Partial<SearchAnswer>): SearchAnswer => ({
  text: 'a man in a red jacket', model_version: 'm@1', moments: [moment({})], strong: true, videos_searched: 2,
  frames_searched: 120, not_searched: 0, thresholds: { strong: 0.5, possible: 0.1, weak: 0.01 }, ...over,
})
const video = (over: Partial<FootageVideo>): FootageVideo & { may: { upload: boolean; review: boolean; delete: boolean; hold: boolean } } => ({
  id: 'v1', source: 'UPLOAD', title: 'Lobby', original_filename: 'lobby.mp4', content_type: 'video/mp4', size_bytes: 2048,
  received_bytes: 2048, part_size: 4, site_id: 's1', site_name: 'Factory A', place: 'Lobby', filmed_at: null,
  duration_seconds: 125, width: 320, height: 240, frame_rate: 10, status: 'READY', status_label: 'Ready', progress: 100,
  error: null, sha256: null, uploaded_by_user_id: 'u1', uploaded_by_name: 'Olivia', created_at: '2026-10-20T02:00:00Z',
  updated_at: '2026-10-20T02:00:00Z', upload_completed_at: null, processed_at: null, expires_at: '2026-11-19T02:00:00Z',
  held: false, hold_id: null, hold_reason: null, hold_placed_at: null, hold_placed_by_name: null, deleted_in_days: 30,
  codec: 'h264', frames_indexed: 125, index_model: 'm@1', playable: 'ORIGINAL',
  may: { upload: true, review: true, delete: false, hold: false }, ...over,
})

function show(path: string) {
  vi.mocked(useAuthStore).mockImplementation(((sel: (s: unknown) => unknown) =>
    sel({ user: { id: 'u1', tenantId: 't1', roleId: 4 }, accessToken: 'tok en', permissions: null })) as never)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } })
  return render(
    <MemoryRouter initialEntries={[path]}>
      <QueryClientProvider client={qc}>
        <ThemeProvider theme={theme}>
          <Routes>
            <Route path="/footage/search" element={<FootageSearch />} />
            <Route path="/footage/:id" element={<FootageVideoPage />} />
          </Routes>
        </ThemeProvider>
      </QueryClientProvider>
    </MemoryRouter>, { wrapper: ({ children }) => <>{children}</> })
}

beforeEach(() => vi.clearAllMocks())

describe('Footage Review: search by description', () => {
  it('sends the words, shows each moment by its best frame and strength, and opens the video just before it', async () => {
    vi.mocked(api.searchFootage).mockResolvedValue(answer({}))
    show('/footage/search')
    fireEvent.change(screen.getByLabelText('Describe what you are looking for'), { target: { value: 'a man in a red jacket' } })
    fireEvent.click(screen.getByRole('button', { name: 'Search' }))
    const card = await screen.findByTestId('moment')
    expect(api.searchFootage).toHaveBeenCalledWith(expect.objectContaining({ text: 'a man in a red jacket', video_id: undefined }))
    expect(within(card).getByText('Strong match')).toBeInTheDocument()
    expect(within(card).getByText(/0:20 – 0:22/)).toBeInTheDocument()
    expect(within(card).getByRole('img')).toHaveAttribute('src', expect.stringMatching(/\/api\/v1\/footage\/v1\/frames\/21\.jpg\?token=tok%20en$/))
    expect(screen.getByText(/2 videos searched, 120 frames/)).toBeInTheDocument()
    vi.mocked(api.getFootage).mockResolvedValue(video({}))
    fireEvent.click(within(card).getByRole('button'))
    expect(await screen.findByTestId('player')).toBeInTheDocument()
    expect(api.getFootage).toHaveBeenCalledWith('v1')
  })

  it('says so when nothing looks like the words', async () => {
    vi.mocked(api.searchFootage).mockResolvedValue(answer({ moments: [], strong: false, not_searched: 1 }))
    show('/footage/search')
    fireEvent.change(screen.getByLabelText('Describe what you are looking for'), { target: { value: 'a purple elephant' } })
    fireEvent.click(screen.getByRole('button', { name: 'Search' }))
    expect(await screen.findByText(/Nothing in the footage looks like/)).toBeInTheDocument()
    expect(screen.queryByTestId('moment')).not.toBeInTheDocument()
    expect(screen.getByText(/1 analysed with an earlier model was not searched/)).toBeInTheDocument()
  })

  it('shows weak matches as weak, saying there is no strong one', async () => {
    vi.mocked(api.searchFootage).mockResolvedValue(answer({
      strong: false, moments: [moment({ label: 'LOW', label_words: 'Weak match', match: 0.05 })] }))
    show('/footage/search')
    fireEvent.change(screen.getByLabelText('Describe what you are looking for'), { target: { value: 'a red picture' } })
    fireEvent.click(screen.getByRole('button', { name: 'Search' }))
    expect(await screen.findByText(/No strong match for/)).toBeInTheDocument()
    expect(within(screen.getByTestId('moment')).getByText('Weak match')).toBeInTheDocument()
  })

  it('says so when the search could not be made', async () => {
    vi.mocked(api.searchFootage).mockRejectedValue(Object.assign(new Error('503'), {
      response: { status: 503, data: { detail: 'Search is not available: the footage worker is not running.' } } }))
    show('/footage/search')
    fireEvent.change(screen.getByLabelText('Describe what you are looking for'), { target: { value: 'a van' } })
    fireEvent.click(screen.getByRole('button', { name: 'Search' }))
    expect(await screen.findByRole('button', { name: /try again/i })).toBeInTheDocument()
  })

  it('will not search for nothing', () => {
    show('/footage/search')
    expect(screen.getByRole('button', { name: 'Search' })).toBeDisabled()
  })
})

describe('Footage Review: one video', () => {
  it('plays the video from the address with the token, and searches inside it', async () => {
    vi.mocked(api.getFootage).mockResolvedValue(video({}))
    vi.mocked(api.searchFootage).mockResolvedValue(answer({}))
    show('/footage/v1?t=16')
    const player = await screen.findByTestId('player')
    expect(player).toHaveAttribute('src', expect.stringMatching(/\/api\/v1\/footage\/v1\/video\?token=tok%20en$/))
    expect(screen.getByText(/125 frames searchable/)).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Describe what you are looking for'), { target: { value: 'a red jacket' } })
    fireEvent.click(screen.getByRole('button', { name: 'Search' }))
    await waitFor(() => expect(api.searchFootage).toHaveBeenCalledWith(expect.objectContaining({ video_id: 'v1' })))
  })

  it('says a video no browser plays cannot be played, and that its frames can still be searched', async () => {
    vi.mocked(api.getFootage).mockResolvedValue(video({ playable: 'NONE' }))
    show('/footage/v1')
    expect(await screen.findByText(/cannot be played in a browser/)).toBeInTheDocument()
    expect(screen.queryByTestId('player')).not.toBeInTheDocument()
    expect(screen.getByText('Search in this video')).toBeInTheDocument()
  })

  it('shows how far the analysis has got, and offers it again to who may review', async () => {
    vi.mocked(api.getFootage).mockResolvedValue(video({ status: 'PROCESSING', status_label: 'Being analysed', progress: 42 }))
    show('/footage/v1')
    expect(await screen.findByText('Being analysed: 42%')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Analyse again' })).not.toBeInTheDocument()
    expect(screen.queryByText('Search in this video')).not.toBeInTheDocument()
  })

  it('analyses a failed video again', async () => {
    vi.mocked(api.getFootage).mockResolvedValue(video({ status: 'FAILED', status_label: 'Could not be analysed', error: 'This file has no picture in it.' }))
    vi.mocked(api.reanalyse).mockResolvedValue({ ...video({ status: 'QUEUED' }), announced: true })
    show('/footage/v1')
    expect(await screen.findByText('This file has no picture in it.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Analyse again' }))
    await waitFor(() => expect(api.reanalyse).toHaveBeenCalledWith('v1'))
  })

  it('says so when the video could not be fetched', async () => {
    vi.mocked(api.getFootage).mockRejectedValue(new Error('Network Error'))
    show('/footage/v1')
    expect(await screen.findByRole('button', { name: /try again/i })).toBeInTheDocument()
  })
})
