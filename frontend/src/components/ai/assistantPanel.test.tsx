import { render, screen, fireEvent, waitFor } from '@/test/utils'
import * as assistant from '@/api/aiAssistant'
import * as ai from '@/api/ai'
import { parseEvents } from '@/api/aiAssistant'
import { AssistantButton, AssistantPanel } from './AssistantPanel'

vi.mock('@/api/aiAssistant', async (orig) => {
  const real = await orig<typeof import('@/api/aiAssistant')>()
  return { ...real, streamChat: vi.fn(), getAssistantUsage: vi.fn(), getConversations: vi.fn(), getConversation: vi.fn() }
})
vi.mock('@/api/ai', async (orig) => {
  const real = await orig<typeof import('@/api/ai')>()
  return { ...real, getAiStatus: vi.fn() }
})

const PATIENT = { timeout: 8000 }
const status = (mine: boolean) => ({
  licensed: true, reason: null, expires_at: null, assistant_provider: 'LOCAL' as const,
  features: [{ key: 'assistant', title: 'AI assistant', group: 'assistant' as const, description: '', ready: true,
               licensed: true, enabled: true, mine }],
})

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(assistant.getAssistantUsage).mockResolvedValue({ provider: 'HOSTED', limit: 500, used: 12, left: 488, month: '2026-10' })
  vi.mocked(assistant.getConversations).mockResolvedValue([])
})

describe('reading the stream', () => {
  it('splits whole events and keeps a half-arrived one for the next chunk', () => {
    const { events, rest } = parseEvents(
      'event: status\ndata: {"text":"Reading shifts"}\n\nevent: delta\ndata: {"text":"Two "}\n\nevent: delta\ndata: {"te')
    expect(events).toEqual([{ kind: 'status', data: { text: 'Reading shifts' } }, { kind: 'delta', data: { text: 'Two ' } }])
    expect(rest).toBe('event: delta\ndata: {"te')
  })
})

describe('the assistant panel', () => {
  it('shows what it is doing, then writes the reply as it arrives', async () => {
    vi.mocked(assistant.streamChat).mockImplementation(async (body, onEvent) => {
      expect(body).toEqual({ message: 'Who is late tonight?', conversation_id: null })
      onEvent({ kind: 'conversation', data: { conversation_id: 'c1' } })
      onEvent({ kind: 'status', data: { text: 'Reading shifts' } })
      onEvent({ kind: 'delta', data: { text: 'Late Lim ' } })
      onEvent({ kind: 'delta', data: { text: 'may be late.' } })
      onEvent({ kind: 'done', data: { conversation_id: 'c1', message_id: 'm1', text: 'Late Lim may be late.', stop: 'end', used: [] } })
    })
    render(<AssistantPanel open onClose={() => {}} />)
    fireEvent.change(screen.getByLabelText('Message to the assistant'), { target: { value: 'Who is late tonight?' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    expect(await screen.findByText('Late Lim may be late.', {}, PATIENT)).toBeInTheDocument()
    expect(screen.getByText('Who is late tonight?')).toBeInTheDocument()
    expect(await screen.findByText('488 of 500 replies left this month for your organisation.', {}, PATIENT)).toBeInTheDocument()
  })

  it('carries the conversation into the next message, and says when it cannot answer', async () => {
    vi.mocked(assistant.streamChat)
      .mockImplementationOnce(async (_b, onEvent) => {
        onEvent({ kind: 'conversation', data: { conversation_id: 'c9' } })
        onEvent({ kind: 'done', data: { conversation_id: 'c9', message_id: 'm', text: 'Hello.', stop: 'end', used: [] } })
      })
      .mockImplementationOnce(async (body, onEvent) => {
        expect(body.conversation_id).toBe('c9')
        onEvent({ kind: 'error', data: { message: "This month's 500 assistant replies have been used." } })
      })
    render(<AssistantPanel open onClose={() => {}} />)
    const box = screen.getByLabelText('Message to the assistant')
    fireEvent.change(box, { target: { value: 'Hi' } })
    fireEvent.keyDown(box, { key: 'Enter' })
    await screen.findByText('Hello.', {}, PATIENT)
    fireEvent.change(box, { target: { value: 'Again' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    expect(await screen.findByText("This month's 500 assistant replies have been used.", {}, PATIENT)).toBeInTheDocument()
    await waitFor(() => expect(assistant.streamChat).toHaveBeenCalledTimes(2))
  })
})

describe('the assistant button', () => {
  it('is there only for the people the assistant is switched on for', async () => {
    vi.mocked(ai.getAiStatus).mockResolvedValue(status(false))
    const first = render(<AssistantButton />)
    await waitFor(() => expect(ai.getAiStatus).toHaveBeenCalled())
    expect(screen.queryByRole('button', { name: 'Ask the AI assistant' })).not.toBeInTheDocument()
    first.unmount()
    vi.mocked(ai.getAiStatus).mockResolvedValue(status(true))
    render(<AssistantButton />)
    expect(await screen.findByRole('button', { name: 'Ask the AI assistant' }, PATIENT)).toBeInTheDocument()
  })
})
