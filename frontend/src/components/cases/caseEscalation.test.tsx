/**
 * Cases that escalate, and people told when put on a case (4 November 2026, Phase 3 of the known limits).
 *
 * What these pin: a case's escalations are listed with who they went to, and say so when nobody could be told; the
 * timings are changed only when the server says the reader may, within its limits; and a pop-up about a case is
 * shown to the people it is for and to nobody else.
 */
import { renderHook } from '@testing-library/react'
import { render, screen, fireEvent, waitFor, within } from '@/test/utils'
import { CaseEscalations, EscalationTimingsDialog } from '@/components/cases/CaseEscalation'
import { getCaseEscalations, getEscalationSettings, setEscalationSettings } from '@/api/cases'
import type { EscalationSettings } from '@/api/cases'
import { useRealtimeEvents } from '@/hooks/useRealtimeEvents'
import { useWebSocket } from '@/hooks/useWebSocket'
import { useNotificationStore } from '@/store/notifications'
import { useAuthStore } from '@/store/auth'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'

vi.mock('@/api/cases', () => ({
  getCaseEscalations: vi.fn(), getEscalationSettings: vi.fn(), setEscalationSettings: vi.fn(),
}))
vi.mock('@/hooks/useWebSocket', () => ({ useWebSocket: vi.fn() }))

const TIMINGS = { quiet_high_days: 3, quiet_normal_days: 7, quiet_low_days: 14, overdue_hours: 24, close_wait_days: 2 }
const LIMITS = {
  quiet_high_days: { least: 1, most: 90 }, quiet_normal_days: { least: 1, most: 90 }, quiet_low_days: { least: 1, most: 90 },
  overdue_hours: { least: 1, most: 168 }, close_wait_days: { least: 1, most: 30 },
}
const settings = (may_set: boolean): EscalationSettings => ({
  ...TIMINGS, since: '2026-11-04T00:00:00Z', updated_at: null, updated_by_name: null, defaults: TIMINGS,
  limits: LIMITS, quiet_second_days: 2, may_set,
})

beforeEach(() => {
  vi.clearAllMocks()
  useNotificationStore.setState({ toasts: [], unreadCount: 0 })
})

describe('a case escalations', { timeout: 60_000 }, () => {
  it('says when nothing has escalated', async () => {
    vi.mocked(getCaseEscalations).mockResolvedValue({ escalations: [], note: 'Nothing about this case has escalated.' })
    render(<CaseEscalations caseId="c1" />)
    expect(await screen.findByText('Nothing about this case has escalated.')).toBeInTheDocument()
  })

  it('lists each with who it went to, and says when nobody could be told', async () => {
    vi.mocked(getCaseEscalations).mockResolvedValue({ note: null, escalations: [
      { id: 'e2', kind: 'QUIET', kind_label: 'Nothing has been added', step: 2, task_id: null, task_title: null,
        counted_from: '2026-10-20T00:00:00Z', due_at: '2026-10-29T00:00:00Z', sent_at: '2026-10-29T00:05:00Z',
        addressed: 0, message: 'CASE-0012: East gate has had nothing added for 9 days (normal priority)' },
      { id: 'e1', kind: 'QUIET', kind_label: 'Nothing has been added', step: 1, task_id: null, task_title: null,
        counted_from: '2026-10-20T00:00:00Z', due_at: '2026-10-27T00:00:00Z', sent_at: '2026-10-27T00:05:00Z',
        addressed: 1, message: 'CASE-0012: East gate has had nothing added for 7 days (normal priority)' },
    ] })
    render(<CaseEscalations caseId="c1" />)
    const rows = await screen.findAllByTestId('escalation')
    expect(rows).toHaveLength(2)
    expect(rows[0]).toHaveTextContent('to Managers and Admins')
    expect(rows[0]).toHaveTextContent('nobody could be told')
    expect(rows[1]).toHaveTextContent('1 told')
  })
})

describe('the escalation timings', { timeout: 60_000 }, () => {
  it('are read only for somebody who may not change them', async () => {
    vi.mocked(getEscalationSettings).mockResolvedValue(settings(false))
    render(<EscalationTimingsDialog open onClose={() => {}} />)
    const dialog = await screen.findByRole('dialog')
    expect(await within(dialog).findByLabelText(/normal priority case is quiet after/i)).toBeDisabled()
    expect(within(dialog).getByText(/only whoever may change the organisation's settings/i)).toBeInTheDocument()
    expect(within(dialog).queryByRole('button', { name: 'Save' })).not.toBeInTheDocument()
  })

  it('are changed within their limits, and sent as numbers', async () => {
    vi.mocked(getEscalationSettings).mockResolvedValue(settings(true))
    vi.mocked(setEscalationSettings).mockResolvedValue({ ...settings(true), quiet_normal_days: 5 })
    render(<EscalationTimingsDialog open onClose={() => {}} />)
    const dialog = await screen.findByRole('dialog')
    const normal = await within(dialog).findByLabelText(/normal priority case is quiet after/i)
    const save = within(dialog).getByRole('button', { name: 'Save' })
    fireEvent.change(normal, { target: { value: '0' } })
    expect(save).toBeDisabled()
    fireEvent.change(normal, { target: { value: '91' } })
    expect(save).toBeDisabled()
    fireEvent.change(normal, { target: { value: '5' } })
    expect(save).toBeEnabled()
    fireEvent.click(save)
    await waitFor(() => expect(setEscalationSettings).toHaveBeenCalled())
    expect(vi.mocked(setEscalationSettings).mock.calls[0][0]).toEqual({ ...TIMINGS, quiet_normal_days: 5 })
  })
})

describe('a pop-up about a case', () => {
  function heard(event: object) {
    vi.mocked(useWebSocket).mockReturnValue({ lastMessage: JSON.stringify(event) } as any)
    const qc = new QueryClient()
    const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    renderHook(() => useRealtimeEvents(), { wrapper })
  }
  const event = (userIds: string[]) => ({
    schema_version: 1, event_type: 'case_assigned', tenant_id: 't1', occurred_at: '2026-11-04T01:00:00Z',
    payload: { case_id: 'c1', case_number: 'CASE-0012', user_ids: userIds, message: 'You are the lead on CASE-0012: East gate' },
  })

  it('is shown to the person it is for', () => {
    useAuthStore.setState({ user: { id: 'me' } } as any)
    heard(event(['me']))
    const items = useNotificationStore.getState().toasts.map((t) => t.title)
    expect(JSON.stringify(items)).toContain('You are the lead on CASE-0012: East gate')
  })

  it('is not shown to anybody else', () => {
    useAuthStore.setState({ user: { id: 'someone-else' } } as any)
    heard(event(['me']))
    const items = useNotificationStore.getState().toasts.map((t) => t.title)
    expect(JSON.stringify(items)).not.toContain('CASE-0012')
  })
})
