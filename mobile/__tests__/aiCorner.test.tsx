/**
 * The AI corner on the phone (docs/plans/2026-10-31-ai-corner-button-and-decision-popups-design.md): the assistant's
 * button on every screen, with the count of what is waiting, and the card asking for one decision at a time. Placed
 * once at the app's root; off on a live picture, a scan, a drawing and the chat itself.
 */
import React from 'react'
import { fireEvent, render, waitFor } from '@testing-library/react-native'
import AsyncStorage from '@react-native-async-storage/async-storage'

jest.mock('@/api/client', () => ({ apiClient: {} }))
const mockWaiting = jest.fn()
const mockDecide = jest.fn()
jest.mock('@/api/aiWaiting', () => ({
  getAiWaiting: (...a: unknown[]) => mockWaiting(...a),
  decideWarning: (...a: unknown[]) => mockDecide(...a),
}))
const mockConfirm = jest.fn()
const mockCancel = jest.fn()
jest.mock('@/api/aiAssistant', () => ({
  confirmAction: (...a: unknown[]) => mockConfirm(...a),
  cancelAction: (...a: unknown[]) => mockCancel(...a),
}))
let mockRoute = 'Dashboard'
const mockNavigate = jest.fn()
jest.mock('@react-navigation/native', () => ({
  useNavigation: () => ({ getCurrentRoute: () => ({ name: mockRoute }), addListener: () => () => {}, navigate: mockNavigate }),
}))
let mockToken: string | null = 'tok'
jest.mock('@/store/auth', () => ({
  useAuthStore: (sel: (s: unknown) => unknown) => sel({ accessToken: mockToken }),
}))

import { AiCorner } from '@/components/AiCorner'
import { HIDDEN_ON, LATER_KEY, LATER_MS, decisionsToShow, putOff } from '@/lib/aiWaiting'
import type { AiWaiting } from '@/api/aiWaiting'

const PATIENT = { timeout: 8000 }
jest.setTimeout(30_000)

const IN_20_MIN = new Date(Date.now() + 20 * 60_000).toISOString()
const action = (id: string, title: string): AiWaiting['actions'][number] => ({
  id, conversation_id: 'c1', title, method: 'POST', path: '/api/v1/visitors',
  fields: [{ name: 'full_name', value: 'Tan Wei Ming' }], status: 'PENDING', result: null, expires_at: IN_20_MIN,
})
const warning = (id: string, title: string): AiWaiting['warnings'][number] => ({
  id, feature_key: 'late_risk', feature_title: 'Late and no-show warnings', title, detail: null, score: 0.8,
  reasons: [{ factor: 'own_late_rate', words: 'Late on 9 of their last 12 shifts', push: 0.4 }],
  site_name: 'Jurong', person_name: 'Late Lim', status: 'OPEN',
})
const waiting = (actions: AiWaiting['actions'], warnings: AiWaiting['warnings'], assistant = true): AiWaiting => ({
  assistant, actions, warnings, counts: { actions: actions.length, warnings: warnings.length },
})

beforeEach(async () => {
  jest.clearAllMocks()
  mockRoute = 'Dashboard'
  mockToken = 'tok'
  await AsyncStorage.clear()
})

describe('what is asked, in order', () => {
  it('asks Confirm cards first, leaves out what was put off or expired, and counts the warnings beyond 20', () => {
    const now = Date.now()
    const gone = { ...action('a0', 'Expired'), expires_at: new Date(now - 1000).toISOString() }
    const w = { ...waiting([gone, action('a1', 'Register visitor')], [warning('w1', 'Lim'), warning('w2', 'Tan')]),
                counts: { actions: 2, warnings: 9 } }
    const { list, beyond } = decisionsToShow(w, { w1: now + 1000 }, now)
    expect(list.map((d) => (d.kind === 'action' ? d.action.id : d.warning.id))).toEqual(['a1', 'w2'])
    expect(beyond).toBe(7)
  })

  it('puts one off for 4 hours and forgets what has come back', () => {
    const now = 1_000_000
    expect(putOff({ old: now - 1, kept: now + 5 }, 'w1', now)).toEqual({ kept: now + 5, w1: now + LATER_MS })
  })

  it('stays off a live picture, a scan, a drawing and the chat itself', () => {
    for (const name of ['CameraLive', 'IncidentCameraLive', 'SituationCameraLive', 'LiveWall', 'PatrolScan',
                        'ZoneDraw', 'CameraPrivacyZones', 'Assistant']) {
      expect(HIDDEN_ON.has(name)).toBe(true)
    }
    expect(HIDDEN_ON.has('Dashboard')).toBe(false)
  })
})

describe('the AI corner', () => {
  it('is placed once at the app root, beside the man-down watcher', () => {
    const app = (require('fs') as { readFileSync: (f: string, e: 'utf8') => string }).readFileSync('App.tsx', 'utf8')
    expect(app.match(/<AiCorner \/>/g)).toHaveLength(1)
    expect(app.indexOf('<AiCorner />')).toBeLessThan(app.indexOf('<ManDownGuard />'))
  })

  it('shows the button with the count and one card, and confirms as the chat does', async () => {
    mockWaiting.mockResolvedValueOnce(waiting([action('a1', 'Register visitor: Tan Wei Ming')], [warning('w1', 'Lim may be late')]))
      .mockResolvedValue(waiting([], [warning('w1', 'Lim may be late')]))
    mockConfirm.mockResolvedValue({ ...action('a1', 'Register visitor: Tan Wei Ming'), status: 'CONFIRMED' })
    const view = render(<AiCorner />)
    expect(await view.findByText('Register visitor: Tan Wei Ming', {}, PATIENT)).toBeTruthy()
    expect(view.getByTestId('ai-waiting-count').props.children).toBe(2)
    expect(view.getByText('and 1 more waiting')).toBeTruthy()
    fireEvent.press(view.getByText('Confirm'))
    expect(await view.findByText('Done: Register visitor: Tan Wei Ming.', {}, PATIENT)).toBeTruthy()
    expect(mockConfirm).toHaveBeenCalledWith('a1')
    await waitFor(() => expect(mockWaiting).toHaveBeenCalledTimes(2))
  })

  it('opens the AI Assistant from the button', async () => {
    mockWaiting.mockResolvedValue(waiting([], []))
    const view = render(<AiCorner />)
    fireEvent.press(await view.findByLabelText('Ask the AI assistant', {}, PATIENT))
    expect(mockNavigate).toHaveBeenCalledWith('Main', { screen: 'More', params: { screen: 'Assistant' } })
    expect(view.queryByTestId('ai-waiting-count')).toBeNull()
  })

  it('puts a warning off with Later, keeps it put off, and shows the next', async () => {
    mockWaiting.mockResolvedValue(waiting([], [warning('w1', 'Lim may be late'), warning('w2', 'Raj may be late')]))
    const view = render(<AiCorner />)
    expect(await view.findByText('Lim may be late', {}, PATIENT)).toBeTruthy()
    expect(view.getByText('• Late on 9 of their last 12 shifts')).toBeTruthy()
    fireEvent.press(view.getByText('Later'))
    expect(await view.findByText('Raj may be late', {}, PATIENT)).toBeTruthy()
    const saved = JSON.parse((await AsyncStorage.getItem(LATER_KEY)) ?? '{}')
    expect(saved.w1 - Date.now()).toBeGreaterThan(LATER_MS - 60_000)
  })

  it('says what the server says when someone else decided first', async () => {
    mockWaiting.mockResolvedValue(waiting([], [warning('w1', 'Lim may be late')]))
    mockDecide.mockRejectedValue({ response: { status: 409, data: { detail: 'This warning is already accepted.' } } })
    const view = render(<AiCorner />)
    fireEvent.press(await view.findByText('Accept', {}, PATIENT))
    expect(await view.findByText('This warning is already accepted.', {}, PATIENT)).toBeTruthy()
    expect(mockDecide).toHaveBeenCalledWith('w1', 'ACCEPT')
  })

  it('stays off the live camera, and asks a supervisor without the assistant in its place', async () => {
    mockRoute = 'CameraLive'
    mockWaiting.mockResolvedValue(waiting([], [warning('w1', 'Lim may be late')], false))
    const live = render(<AiCorner />)
    await waitFor(() => expect(mockWaiting).toHaveBeenCalled())
    expect(live.queryByTestId('ai-corner')).toBeNull()
    live.unmount()
    mockRoute = 'Dashboard'
    const desk = render(<AiCorner />)
    expect(await desk.findByText('Lim may be late', {}, PATIENT)).toBeTruthy()
    expect(desk.queryByLabelText('Ask the AI assistant')).toBeNull()
  })

  it('shows nothing to a person the module is not for, or when signed out', async () => {
    mockWaiting.mockResolvedValue(waiting([], [], false))
    const none = render(<AiCorner />)
    await waitFor(() => expect(mockWaiting).toHaveBeenCalled())
    expect(none.queryByTestId('ai-corner')).toBeNull()
    none.unmount()
    jest.clearAllMocks()
    mockToken = null
    const out = render(<AiCorner />)
    expect(out.queryByTestId('ai-corner')).toBeNull()
    expect(mockWaiting).not.toHaveBeenCalled()
  })
})
