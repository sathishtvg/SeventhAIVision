import { render, screen, fireEvent, waitFor, within } from '@/test/utils'
import { PlaybackPage } from '@/pages/Playback'
import { apiClient } from '@/api/client'
import { getRecordingTimeline } from '@/api/recordings'
import { useAuthStore } from '@/store/auth'

// A day that could not be fetched is not a day with no recordings.

vi.mock('@/api/client', () => ({ apiClient: { get: vi.fn(), defaults: { baseURL: '' } } }))
vi.mock('@/api/recordings', () => ({
  getRecordingTimeline: vi.fn(),
  playRecordingUrl: vi.fn(() => '/play'),
  downloadRecordingUrl: vi.fn(() => '/download'),
}))
vi.mock('@/store/auth', () => ({ useAuthStore: vi.fn() }))
vi.mock('@/hooks/usePageLabels', () => ({ usePageLabelOverrides: () => ({}) }))

const refused = { response: { status: 500, data: {} } }
const cameras = [{ id: 'c1', name: 'Gate 2', site_name: 'Jurong' }]

async function chooseGate2() {
  fireEvent.mouseDown(await screen.findByRole('combobox'))
  fireEvent.click(await within(await screen.findByRole('listbox')).findByText(/Gate 2/))
}

beforeEach(() => {
  vi.mocked(useAuthStore).mockImplementation(((sel: (state: unknown) => unknown) => sel({ user: { id: '1', roleId: 2 }, accessToken: 'tok' })) as never)
  vi.mocked(apiClient.get).mockResolvedValue({ data: cameras })
  vi.mocked(getRecordingTimeline).mockResolvedValue({ camera_id: 'c1', camera_name: 'Gate 2', date: '2026-10-13', segments: [], alerts: [] } as never)
})
afterEach(() => vi.clearAllMocks())

describe('Playback', () => {
  it('says the cameras could not be loaded, and asks again', async () => {
    vi.mocked(apiClient.get).mockRejectedValueOnce(refused)
    render(<PlaybackPage />)
    expect(await screen.findByText('Could not load the cameras')).toBeInTheDocument()
    expect(screen.queryByText(/Select a camera to browse/)).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByText(/Select a camera to browse/)).toBeInTheDocument()
    expect(apiClient.get).toHaveBeenCalledTimes(2)
  })

  it('waits with a placeholder, then says a day with no recordings has none', async () => {
    let answer: (value: unknown) => void = () => {}
    vi.mocked(getRecordingTimeline).mockReturnValueOnce(new Promise((resolve) => { answer = resolve }) as never)
    render(<PlaybackPage />)
    await chooseGate2()

    // Still being fetched: it has not been said that there is nothing.
    await waitFor(() => expect(getRecordingTimeline).toHaveBeenCalled())
    expect(screen.queryByText(/No recordings on/)).not.toBeInTheDocument()
    expect(document.querySelector('.MuiSkeleton-root')).not.toBeNull()

    answer({ camera_id: 'c1', camera_name: 'Gate 2', date: '2026-10-13', segments: [], alerts: [] })
    expect(await screen.findByText(/No recordings on/)).toBeInTheDocument()
    expect(document.querySelector('.MuiSkeleton-root')).toBeNull()
  })

  it('says a day could not be loaded instead of drawing it empty, and asks again', async () => {
    vi.mocked(getRecordingTimeline).mockRejectedValueOnce(refused)
    render(<PlaybackPage />)
    await chooseGate2()

    expect(await screen.findByText("Could not load this day's recordings")).toBeInTheDocument()
    expect(screen.queryByText(/No recordings on/)).not.toBeInTheDocument()
    // The camera and the day can still be changed.
    expect(screen.getByRole('combobox')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByText(/No recordings on/)).toBeInTheDocument()
  })
})
