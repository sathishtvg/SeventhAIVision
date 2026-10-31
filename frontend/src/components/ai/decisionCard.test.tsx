import { render, screen, fireEvent, waitFor, within } from '@/test/utils'
import * as ai from '@/api/ai'
import * as assistant from '@/api/aiAssistant'
import * as waitingApi from '@/api/aiWaiting'
import type { AiWaiting } from '@/api/aiWaiting'
import { AssistantButton } from './AssistantPanel'
import { LATER_KEY, LATER_MS, decisionsToShow } from './decisions'

vi.mock('@/api/aiWaiting', () => ({ getAiWaiting: vi.fn() }))
vi.mock('@/api/ai', async (orig) => {
  const real = await orig<typeof import('@/api/ai')>()
  return { ...real, getAiStatus: vi.fn(), decideAiInsight: vi.fn() }
})
vi.mock('@/api/aiAssistant', async (orig) => {
  const real = await orig<typeof import('@/api/aiAssistant')>()
  return { ...real, confirmAction: vi.fn(), cancelAction: vi.fn(), getAssistantUsage: vi.fn(), getConversations: vi.fn() }
})

const PATIENT = { timeout: 8000 }
const IN_20_MIN = new Date(Date.now() + 20 * 60_000).toISOString()

const status = (assistantMine: boolean, warningsMine = true) => ({
  licensed: true, reason: null, expires_at: null, assistant_provider: 'LOCAL' as const,
  features: [
    { key: 'assistant', title: 'AI assistant', group: 'assistant' as const, description: '', ready: true, licensed: true,
      enabled: true, mine: assistantMine },
    { key: 'late_risk', title: 'Late and no-show warnings', group: 'learning' as const, description: '', ready: true,
      licensed: true, enabled: true, mine: warningsMine },
  ],
})

const action = (id: string, title: string): AiWaiting['actions'][number] => ({
  id, conversation_id: 'c1', title, method: 'POST', path: '/api/v1/visitors',
  fields: [{ name: 'full_name', value: 'Tan Wei Ming' }], status: 'PENDING', result: null,
  created_at: new Date().toISOString(), expires_at: IN_20_MIN, decided_at: null,
})

const warning = (id: string, title: string, score = 0.8): AiWaiting['warnings'][number] => ({
  id, feature_key: 'late_risk', feature_title: 'Late and no-show warnings', subject_type: 'shift', title, detail: null,
  score, threshold: 0.5, reasons: [{ factor: 'own_late_rate', words: 'Late on 9 of their last 12 shifts', push: 0.4 }],
  evidence: {}, site_id: 's1', site_name: 'Jurong', person_user_id: null, person_name: null, status: 'OPEN',
  decided_by_name: null, decided_at: null, decision_note: null, outcome: null, valid_until: null,
  created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
})

const waiting = (actions: AiWaiting['actions'], warnings: AiWaiting['warnings'], extra = 0): AiWaiting => ({
  assistant: true, actions, warnings, counts: { actions: actions.length, warnings: warnings.length + extra },
})

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  vi.mocked(assistant.getAssistantUsage).mockResolvedValue({ provider: 'LOCAL', limit: null, used: 0, left: null, month: '2026-10' })
  vi.mocked(assistant.getConversations).mockResolvedValue([])
})

describe('what is asked, in order', () => {
  it('asks the Confirm cards first, leaves out what was put off or has expired, and counts the rest', () => {
    const now = Date.now()
    const gone = { ...action('a0', 'Expired'), expires_at: new Date(now - 1000).toISOString() }
    const w = waiting([gone, action('a1', 'Register visitor')], [warning('w1', 'Lim may be late'), warning('w2', 'Tan may be late')], 5)
    const { list, beyond } = decisionsToShow(w, { w1: now + LATER_MS }, now)
    expect(list.map((d) => (d.kind === 'action' ? d.action.id : d.warning.id))).toEqual(['a1', 'w2'])
    expect(beyond).toBe(5)
    expect(decisionsToShow(w, { w1: now - 1 }, now).list).toHaveLength(3)
  })
})

describe('the decision card', () => {
  it('shows one at a time with the count, and confirms as the chat does', async () => {
    vi.mocked(ai.getAiStatus).mockResolvedValue(status(true))
    vi.mocked(waitingApi.getAiWaiting)
      .mockResolvedValueOnce(waiting([action('a1', 'Register visitor: Tan Wei Ming')], [warning('w1', 'Lim may be late tonight')]))
      .mockResolvedValue(waiting([], [warning('w1', 'Lim may be late tonight')]))
    vi.mocked(assistant.confirmAction).mockResolvedValue({ ...action('a1', 'Register visitor: Tan Wei Ming'), status: 'CONFIRMED' })
    render(<AssistantButton />)
    const card = await screen.findByTestId('ai-decision-card', {}, PATIENT)
    expect(within(card).getByText('Register visitor: Tan Wei Ming')).toBeInTheDocument()
    expect(within(card).getByText('Tan Wei Ming')).toBeInTheDocument()
    expect(within(card).getByText('and 1 more waiting')).toBeInTheDocument()
    expect(screen.getByTestId('ai-waiting-count')).toHaveTextContent('2')
    fireEvent.click(within(card).getByRole('button', { name: 'Confirm' }))
    expect(await screen.findByText('Done: Register visitor: Tan Wei Ming.', {}, PATIENT)).toBeInTheDocument()
    expect(assistant.confirmAction).toHaveBeenCalledWith('a1')
    await waitFor(() => expect(waitingApi.getAiWaiting).toHaveBeenCalledTimes(2))
  })

  it('puts one off for 4 hours with Later, and shows the next', async () => {
    vi.mocked(ai.getAiStatus).mockResolvedValue(status(true))
    vi.mocked(waitingApi.getAiWaiting).mockResolvedValue(
      waiting([], [warning('w1', 'Lim may be late tonight', 0.9), warning('w2', 'Raj may be late', 0.6)]))
    const first = render(<AssistantButton />)
    const card = await screen.findByTestId('ai-decision-card', {}, PATIENT)
    expect(within(card).getByText('Lim may be late tonight')).toBeInTheDocument()
    expect(within(card).getByText('Late on 9 of their last 12 shifts')).toBeInTheDocument()
    fireEvent.click(within(card).getByRole('button', { name: 'Later' }))
    expect(await within(card).findByText('Raj may be late')).toBeInTheDocument()
    const until = JSON.parse(localStorage.getItem(LATER_KEY) ?? '{}').w1
    expect(until - Date.now()).toBeGreaterThan(LATER_MS - 60_000)
    first.unmount()
    render(<AssistantButton />)
    const again = await screen.findByTestId('ai-decision-card', {}, PATIENT)
    expect(within(again).getByText('Raj may be late')).toBeInTheDocument()
  })

  it('says what the server says when someone else decided first', async () => {
    vi.mocked(ai.getAiStatus).mockResolvedValue(status(true))
    vi.mocked(waitingApi.getAiWaiting).mockResolvedValue(waiting([], [warning('w1', 'Lim may be late tonight')]))
    vi.mocked(ai.decideAiInsight).mockRejectedValue({ response: { status: 409, data: { detail: 'This warning is already accepted.' } } })
    render(<AssistantButton />)
    const card = await screen.findByTestId('ai-decision-card', {}, PATIENT)
    fireEvent.click(within(card).getByRole('button', { name: 'Accept' }))
    expect(await screen.findByText('This warning is already accepted.', {}, PATIENT)).toBeInTheDocument()
    expect(ai.decideAiInsight).toHaveBeenCalledWith('w1', { decision: 'ACCEPT' })
  })

  it('keeps the last answer and says nothing when a check fails', async () => {
    vi.mocked(ai.getAiStatus).mockResolvedValue(status(true))
    vi.mocked(waitingApi.getAiWaiting)
      .mockResolvedValueOnce(waiting([], [warning('w1', 'Lim may be late tonight')]))
      .mockRejectedValue(new Error('Network Error'))
    const { rerender } = render(<AssistantButton />)
    const card = await screen.findByTestId('ai-decision-card', {}, PATIENT)
    vi.mocked(ai.decideAiInsight).mockResolvedValue(warning('w1', 'Lim may be late tonight'))
    fireEvent.click(within(card).getByRole('button', { name: 'Dismiss' }))
    expect(await screen.findByText('Dismissed.', {}, PATIENT)).toBeInTheDocument()
    await waitFor(() => expect(waitingApi.getAiWaiting).toHaveBeenCalledTimes(2))
    rerender(<AssistantButton />)
    expect(screen.queryByText('Network Error')).not.toBeInTheDocument()
  })

  it('is hidden while the chat is open', async () => {
    vi.mocked(ai.getAiStatus).mockResolvedValue(status(true))
    vi.mocked(waitingApi.getAiWaiting).mockResolvedValue(waiting([action('a1', 'Add site: Changi')], []))
    render(<AssistantButton />)
    await screen.findByTestId('ai-decision-card', {}, PATIENT)
    fireEvent.click(screen.getByRole('button', { name: 'Ask the AI assistant' }))
    await waitFor(() => expect(screen.queryByTestId('ai-decision-card')).not.toBeInTheDocument())
  })

  it('asks a supervisor without the assistant in the button\'s place, and asks nobody the module is not for', async () => {
    vi.mocked(ai.getAiStatus).mockResolvedValue(status(false))
    vi.mocked(waitingApi.getAiWaiting).mockResolvedValue({ ...waiting([], [warning('w1', 'Lim may be late tonight')]), assistant: false })
    const first = render(<AssistantButton />)
    expect(await screen.findByTestId('ai-decision-card', {}, PATIENT)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Ask the AI assistant' })).not.toBeInTheDocument()
    first.unmount()
    vi.clearAllMocks()
    vi.mocked(ai.getAiStatus).mockResolvedValue(status(false, false))
    render(<AssistantButton />)
    await waitFor(() => expect(ai.getAiStatus).toHaveBeenCalled())
    expect(waitingApi.getAiWaiting).not.toHaveBeenCalled()
    expect(screen.queryByTestId('ai-decision-card')).not.toBeInTheDocument()
  })
})
