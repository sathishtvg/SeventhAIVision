/**
 * The AI assistant on the phone, mounted: a guard asks in their own words and gets the reply whole; a change comes as
 * a card that does nothing until Confirm; the screen says why when the assistant is not theirs, or when it cannot
 * answer; and the More menu offers it only to the people it is switched on for.
 */
import React from 'react'
import { fireEvent, render, waitFor } from '@testing-library/react-native'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

jest.mock('@/api/client', () => ({ apiClient: {} }))
const mockStatus = jest.fn()
const mockAsk = jest.fn()
const mockUsage = jest.fn()
const mockConfirm = jest.fn()
const mockCancel = jest.fn()
jest.mock('@/api/aiAssistant', () => ({
  ...jest.requireActual('@/api/aiAssistant'),
  getAiStatus: (...a: unknown[]) => mockStatus(...a),
  askAssistant: (...a: unknown[]) => mockAsk(...a),
  getAssistantUsage: (...a: unknown[]) => mockUsage(...a),
  confirmAction: (...a: unknown[]) => mockConfirm(...a),
  cancelAction: (...a: unknown[]) => mockCancel(...a),
}))
const mockNavigate = jest.fn()
jest.mock('@react-navigation/native', () => ({ useNavigation: () => ({ navigate: mockNavigate }) }))

import { AssistantScreen } from '@/screens/AssistantScreen'
import { MoreMenuScreen } from '@/screens/MoreMenuScreen'
import { useAuthStore } from '@/store/auth'
import { askErrorText, cardOutcome, replyLines } from '@/lib/assistant'

const status = (mine: boolean) => ({
  licensed: true, reason: null, features: [{ key: 'assistant', title: 'AI assistant', mine }],
})
const CARD = {
  id: 'a1', conversation_id: 'c1', title: 'Create Incident: Gate 2 left open', method: 'POST' as const,
  path: '/api/v1/incidents', fields: [{ name: 'title', value: 'Gate 2 left open' }, { name: 'severity', value: 'low' }],
  status: 'PENDING' as const, result: null, expires_at: '2026-10-26T12:30:00Z',
}
const reply = (over: object = {}) => ({
  conversation_id: 'c1', message_id: 'm1', text: 'Your next shift starts at 22:00 at Factory A.', stop: 'end',
  statuses: ['Reading shifts'], actions: [], ...over,
})

/** A first render compiles the screen: under a full run's load the defaults are not always enough. */
const PATIENT = { timeout: 8000 }
jest.setTimeout(30_000)

let qc: QueryClient
function mount(screen: React.ReactElement) {
  qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { gcTime: 0 } } })
  return render(<QueryClientProvider client={qc}>{screen}</QueryClientProvider>)
}

beforeEach(() => {
  jest.clearAllMocks()
  mockStatus.mockResolvedValue(status(true))
  mockUsage.mockResolvedValue({ limit: 500, used: 12, left: 488 })
})

describe('the words', () => {
  it('lays out a reply as what it did, its cards, then its words', () => {
    const lines = replyLines(reply({ actions: [CARD] }) as never, 3)
    expect(lines.map((l) => l.kind)).toEqual(['status', 'card', 'assistant'])
    expect(lines[1].key).toBe('card-a1')
  })
  it('says what became of a card, and why a message got no answer', () => {
    expect(cardOutcome(CARD)).toBeNull()
    expect(cardOutcome({ ...CARD, status: 'CONFIRMED' })).toBe('Done.')
    expect(cardOutcome({ ...CARD, status: 'FAILED', result: { status: 403, answer: 'Not sent: your role cannot do this any more.' } }))
      .toBe('Not done: Not sent: your role cannot do this any more.')
    expect(askErrorText({ code: 'ECONNABORTED' })).toMatch(/took too long/)
    expect(askErrorText({ response: { status: 503, data: { detail: 'The built-in assistant is not running.' } } }))
      .toBe('The built-in assistant is not running.')
  })
})

describe('the assistant screen', () => {
  it('sends the message in the guard’s own words and shows the reply, keeping the conversation', async () => {
    mockAsk.mockResolvedValueOnce(reply()).mockResolvedValueOnce(reply({ message_id: 'm2', text: 'Gate 2, then the car park.' }))
    const s = mount(<AssistantScreen />)
    const box = await s.findByLabelText('Message to the assistant', {}, PATIENT)
    fireEvent.changeText(box, 'Bila shift saya seterusnya?')
    fireEvent.press(s.getByLabelText('Send'))
    expect(await s.findByText('Your next shift starts at 22:00 at Factory A.', {}, PATIENT)).toBeTruthy()
    expect(mockAsk).toHaveBeenCalledWith('Bila shift saya seterusnya?', null)
    expect(s.getByText('Bila shift saya seterusnya?')).toBeTruthy()
    expect(s.getByText('Reading shifts')).toBeTruthy()
    expect(await s.findByText('488 of 500 replies left this month for your organisation.', {}, PATIENT)).toBeTruthy()
    fireEvent.changeText(box, 'And my patrol?')
    fireEvent.press(s.getByLabelText('Send'))
    expect(await s.findByText('Gate 2, then the car park.', {}, PATIENT)).toBeTruthy()
    expect(mockAsk).toHaveBeenLastCalledWith('And my patrol?', 'c1')
  })

  it('shows a change as a card, sends it only on Confirm, then lets the guard carry on', async () => {
    mockAsk.mockResolvedValueOnce(reply({ text: 'Confirm it on the card.', actions: [CARD] }))
      .mockResolvedValueOnce(reply({ text: 'Anything else?' }))
    mockConfirm.mockResolvedValue({ ...CARD, status: 'CONFIRMED', result: { status: 201, answer: {} } })
    const s = mount(<AssistantScreen />)
    fireEvent.changeText(await s.findByLabelText('Message to the assistant', {}, PATIENT), 'Report gate 2 left open')
    fireEvent.press(s.getByLabelText('Send'))
    const card = await s.findByTestId('assistant-card-a1', {}, PATIENT)
    expect(s.getByText('Create Incident: Gate 2 left open')).toBeTruthy()
    expect(mockConfirm).not.toHaveBeenCalled()
    fireEvent.press(s.getByText('Confirm'))
    expect(await s.findByText('Done.', {}, PATIENT)).toBeTruthy()
    expect(mockConfirm).toHaveBeenCalledWith('a1')
    expect(card).toBeTruthy()
    fireEvent.press(s.getByText('Carry on'))
    await waitFor(() => expect(mockAsk).toHaveBeenLastCalledWith('Carry on.', 'c1'), PATIENT)
    expect(await s.findByText('Anything else?', {}, PATIENT)).toBeTruthy()
  })

  it('says why it could not answer, in the server’s words', async () => {
    mockAsk.mockRejectedValue({ response: { status: 429, data: { detail: "This month's 500 assistant replies have been used." } } })
    const s = mount(<AssistantScreen />)
    fireEvent.changeText(await s.findByLabelText('Message to the assistant', {}, PATIENT), 'Hello')
    fireEvent.press(s.getByLabelText('Send'))
    // A 429 is said as the phone's own "too many" words.
    expect(await s.findByText(/Too many requests|replies have been used/, {}, PATIENT)).toBeTruthy()
  })

  it('says it is not switched on for the role, and fetches nothing else', async () => {
    mockStatus.mockResolvedValue(status(false))
    const s = mount(<AssistantScreen />)
    expect((await s.findByTestId('assistant-not-mine', {}, PATIENT)).props.children).toMatch(/not switched on for your role/)
    expect(s.queryByLabelText('Message to the assistant')).toBeNull()
    expect(mockUsage).not.toHaveBeenCalled()
  })

  it('says when the assistant could not be reached, and asks again', async () => {
    mockStatus.mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce(status(true))
    const s = mount(<AssistantScreen />)
    fireEvent.press(await s.findByText('Try again', {}, PATIENT))
    expect(await s.findByLabelText('Message to the assistant', {}, PATIENT)).toBeTruthy()
  })
})

describe('the More menu', () => {
  beforeEach(() => {
    useAuthStore.setState({ user: { id: 'g1', email: 'guard@example.com', roleId: 5 } as never, permissions: ['ai:assistant:use'] })
  })

  it('offers the assistant only when it is switched on for the person', async () => {
    mockStatus.mockResolvedValue(status(false))
    const off = mount(<MoreMenuScreen />)
    await waitFor(() => expect(mockStatus).toHaveBeenCalled(), PATIENT)
    expect(off.queryByText('AI Assistant')).toBeNull()
    off.unmount()
    mockStatus.mockResolvedValue(status(true))
    const on = mount(<MoreMenuScreen />)
    fireEvent.press(await on.findByText('AI Assistant', {}, PATIENT))
    expect(mockNavigate).toHaveBeenCalledWith('Assistant')
  })
})
