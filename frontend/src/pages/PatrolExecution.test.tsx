import { render, screen, fireEvent, waitFor } from '@/test/utils'
import { PatrolExecutionPage } from '@/pages/PatrolExecution'
import {
  captureSnapshot, completeCamera, currentCamera, getSession, myPatrols, startSession, submitAnswers,
} from '@/api/virtualPatrol'
import { useAuthStore } from '@/store/auth'

// The tick on a patrol's snapshot says the server has the snapshot. It says nothing sooner, and it is only a tick:
// it does not complete the camera or move the patrol on.

vi.mock('@/api/virtualPatrol', () => ({
  myPatrols: vi.fn(), startSession: vi.fn(), currentCamera: vi.fn(), captureSnapshot: vi.fn(), submitAnswers: vi.fn(),
  completeCamera: vi.fn(), completePatrol: vi.fn(), getSession: vi.fn(),
  snapshotImageUrl: vi.fn(() => '/snapshot.jpg'),
}))
vi.mock('@/store/auth', () => ({ useAuthStore: vi.fn() }))
vi.mock('@/hooks/usePageLabels', () => ({ usePageLabelOverrides: () => ({}) }))

const patrol = {
  id: 's1', schedule_name: 'Night round', site_name: 'North Yard', scheduled_for: '2026-10-14T01:00:00Z',
  completed_camera_count: 0, camera_count: 2, status: 'IN_PROGRESS',
}
const gate = (more: object = {}) => ({
  camera: {
    id: 'c1', sequence_no: 1, camera_name: 'Gate 2', location: null,
    snapshot_path: null, snapshot_taken_at: null, snapshot_error: null, ...more,
  },
  questions: [],
})

beforeEach(() => {
  vi.mocked(useAuthStore).mockImplementation(((sel: (state: unknown) => unknown) => sel({ user: { id: '1', roleId: 4 }, accessToken: 'tok' })) as never)
  vi.mocked(myPatrols).mockResolvedValue([patrol] as never)
  vi.mocked(startSession).mockResolvedValue({} as never)
  vi.mocked(getSession).mockResolvedValue({ ...patrol, cameras: [] } as never)
  vi.mocked(currentCamera).mockResolvedValue(gate() as never)
  vi.mocked(submitAnswers).mockResolvedValue({} as never)
  vi.mocked(completeCamera).mockResolvedValue({} as never)
})
afterEach(() => vi.clearAllMocks())

async function openTheCamera() {
  render(<PatrolExecutionPage />)
  fireEvent.click(await screen.findByRole('button', { name: 'Continue' }))
  await screen.findByText('1. Gate 2')
}

describe('a patrol’s snapshot', () => {
  it('is not said to be saved before it is, nor when the capture failed', async () => {
    vi.mocked(captureSnapshot).mockResolvedValue({ ok: false, error: 'The camera did not answer.' } as never)
    await openTheCamera()
    expect(screen.queryByText('Snapshot saved')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Capture snapshot' }))
    expect(await screen.findByText('The camera did not answer.')).toBeInTheDocument()
    expect(screen.queryByText('Snapshot saved')).not.toBeInTheDocument()
  })

  it('is said to be saved when the server has it - and that is all the tick does', async () => {
    vi.mocked(captureSnapshot).mockImplementation(async () => {
      // From now on the camera has its snapshot, as the server would say.
      vi.mocked(currentCamera).mockResolvedValue(gate({ snapshot_path: 'p/1.jpg', snapshot_taken_at: '2026-10-14T01:02:03Z' }) as never)
      return { ok: true } as never
    })
    await openTheCamera()
    fireEvent.click(screen.getByRole('button', { name: 'Capture snapshot' }))

    expect(await screen.findByText('Snapshot saved')).toBeInTheDocument()
    expect(await screen.findByAltText('Snapshot of Gate 2')).toHaveClass('sav-img')
    // The camera is not completed, the answers are not sent and the patrol has not moved on: only a person does that.
    expect(completeCamera).not.toHaveBeenCalled()
    expect(submitAnswers).not.toHaveBeenCalled()
    expect(screen.getByText('1. Gate 2')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Complete camera' }))
    await waitFor(() => expect(completeCamera).toHaveBeenCalledTimes(1))
    expect(vi.mocked(completeCamera).mock.calls[0].slice(0, 2)).toEqual(['s1', 'c1'])
  })
})
