import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ThemeProvider } from '@mui/material'
import { render, screen, fireEvent, waitFor, within } from '@/test/utils'
import { theme } from '@/theme/glassmorphism'
import { useAuthStore } from '@/store/auth'
import * as api from '@/api/footage'
import type { Finding, FootageVideo, SearchAnswer, SearchMoment } from '@/api/footage'
import { along, eventText } from '@/components/footage/footageFormat'
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

const finding = (over: Partial<Finding>): Finding => ({
  id: 'f1', kind: 'PEOPLE', kind_words: 'People present', start_second: 3, end_second: 5, best_second: 4, score: 0.92,
  most: 2, reasons: { seen_in_seconds: 3, most_at_once: 2 }, model_version: 'yolov8s-coco-onnx', verdict: null,
  verdict_words: null, verdict_note: null, verdict_at: null, verdict_by_name: null, at: null, ...over,
})

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.getFindings).mockResolvedValue({ video_id: 'v1', status: 'READY', duration_seconds: 125, findings: [],
                                                 counts: [], may: { upload: true, review: true, delete: false, hold: false } })
})

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

describe('Footage Review: what was found, on the timeline', () => {
  it('marks each event where it happens and lists them, each jumping the player there', async () => {
    vi.mocked(api.getFootage).mockResolvedValue(video({}))
    vi.mocked(api.getFindings).mockResolvedValue({
      video_id: 'v1', status: 'READY', duration_seconds: 125, counts: [[3, 1, 0], [4, 2, 0]],
      may: { upload: true, review: true, delete: false, hold: false },
      findings: [finding({}), finding({ id: 'f2', kind: 'VEHICLES', kind_words: 'Vehicle present', start_second: 60,
                                        end_second: 80, best_second: 70, most: 1 })],
    })
    show('/footage/v1')
    const events = await screen.findAllByTestId('event')
    expect(events).toHaveLength(2)
    expect(within(events[0]).getByText('People present')).toBeInTheDocument()
    expect(within(events[0]).getByText('0:03 – 0:05, up to 2 at once')).toBeInTheDocument()
    expect(events[0].querySelector('img')).toHaveAttribute('src', expect.stringMatching(/frames\/4\.jpg/))
    const mark = within(screen.getByTestId('timeline')).getByRole('button', { name: 'Vehicle present 1:00 – 1:20' })
    expect(mark).toHaveStyle({ left: `${along(60, 125)}%` })
    const player = screen.getByTestId('player') as HTMLVideoElement
    fireEvent.click(events[1])
    expect(player.currentTime).toBe(60)
  })

  it('says when nobody and nothing was seen', async () => {
    vi.mocked(api.getFootage).mockResolvedValue(video({}))
    show('/footage/v1')
    expect(await screen.findByText(/No people or vehicles were seen/)).toBeInTheDocument()
  })

  it('says an event in a person\'s words', () => {
    expect(eventText(finding({}))).toBe('People present 0:03 – 0:05, up to 2 at once')
    expect(eventText(finding({ start_second: 7, end_second: 7, most: 1 }))).toBe('People present 0:07')
    expect(along(30, 120)).toBe(25)
    expect(along(500, 120)).toBe(100)
    expect(along(5, 0)).toBe(0)
  })
})

describe('Footage Review: a verdict on a possible altercation', () => {
  const altercation = finding({ id: 'f9', kind: 'ALTERCATION', kind_words: 'Possible altercation', start_second: 10,
                                end_second: 14, best_second: 12, most: null,
                                reasons: { looked_like: 'people pushing each other', moments_with_fast_arms_within_reach: 4 } })

  it('is given by who may review, with the reasons, and the page shows it', async () => {
    vi.mocked(api.getFootage).mockResolvedValue(video({}))
    vi.mocked(api.getFindings).mockResolvedValue({ video_id: 'v1', status: 'READY', duration_seconds: 125, counts: [],
      may: { upload: true, review: true, delete: false, hold: false }, findings: [altercation] })
    vi.mocked(api.judgeFinding).mockResolvedValue({ id: 'f9', verdict: 'REJECTED', verdict_words: 'Not an altercation', verdict_note: 'Moving a pallet' })
    show('/footage/v1')
    expect(await screen.findByText('Not reviewed yet')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Give a verdict' }))
    expect(screen.getByText(/people pushing each other/)).toBeInTheDocument()
    expect(screen.getByText(/in 4 of the frames checked, five a second/)).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Note (optional)'), { target: { value: ' Moving a pallet ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Not an altercation' }))
    await waitFor(() => expect(api.judgeFinding).toHaveBeenCalledWith('v1', 'f9', 'REJECTED', 'Moving a pallet'))
  })

  it('is not offered to who may not review', async () => {
    vi.mocked(api.getFootage).mockResolvedValue(video({ may: { upload: false, review: false, delete: false, hold: false } }))
    vi.mocked(api.getFindings).mockResolvedValue({ video_id: 'v1', status: 'READY', duration_seconds: 125, counts: [],
      may: { upload: false, review: false, delete: false, hold: false }, findings: [altercation] })
    show('/footage/v1')
    expect(await screen.findByTestId('event')).toHaveTextContent('Possible altercation')
    expect(screen.queryByRole('button', { name: 'Give a verdict' })).not.toBeInTheDocument()
  })
})
