/**
 * The man-down watcher is mounted, and only watches the right people.
 *
 * ManDownGuard renders nothing until a fall fires, and it lives at the app
 * root rather than on a screen. Both of those make it invisible: if a refactor
 * dropped `<ManDownGuard />` out of App.tsx, every screen would still look
 * right, every other test would still pass, and the only symptom would be that
 * a guard who fell was never escalated — discovered, if ever, on the day it
 * mattered.
 *
 * The second test guards the other direction. The watcher is deliberately
 * limited to officers on the ground; a manager at a desk triggering man-down
 * every lunchtime is how the whole feature gets switched off company-wide.
 */
import React from 'react'
import { render, waitFor } from '@testing-library/react-native'
import { Text } from 'react-native'

// ── Native modules with no JS implementation under jest ─────────────────────
const mockAddListener = jest.fn((..._args: unknown[]) => ({ remove: jest.fn() }))
jest.mock('expo-sensors', () => ({
  Accelerometer: {
    // useManDown asks before subscribing — a device with no accelerometer is
    // not an error worth interrupting a guard's shift over.
    isAvailableAsync: jest.fn(async () => true),
    addListener: (...args: unknown[]) => mockAddListener(...args),
    setUpdateInterval: jest.fn(),
  },
}))

// ── The app root's dependencies, stubbed down to what this asserts ──────────
jest.mock('@/navigation', () => ({ RootNavigator: () => null }))
jest.mock('@/hooks/usePushNotifications', () => ({ usePushNotifications: jest.fn() }))
jest.mock('@/components/OfflineBanner', () => ({ OfflineBanner: () => null }))
jest.mock('@/lib/queryPersister', () => ({
  queryPersister: {
    persistClient: jest.fn(async () => undefined),
    restoreClient: jest.fn(async () => undefined),
    removeClient: jest.fn(async () => undefined),
  },
}))
jest.mock('@/api/client', () => ({
  registerRefreshFn: jest.fn(),
  setApiBaseUrl: jest.fn(),
  apiClient: { get: jest.fn(), post: jest.fn(), put: jest.fn(), delete: jest.fn() },
}))
jest.mock('@/store/server', () => ({
  useServerStore: (sel: (s: unknown) => unknown) =>
    sel({ loadServerUrl: jest.fn(async () => 'http://test.local') }),
}))

const mockGetManDownSettings = jest.fn(async (..._a: unknown[]) => ({
  enabled: true, no_motion_seconds: 120, stillness_threshold_mg: 60,
  impact_threshold_g: 3, countdown_seconds: 30,
}))
jest.mock('@/api/mandown', () => ({
  getManDownSettings: (...a: unknown[]) => mockGetManDownSettings(...a),
  raiseManDown: jest.fn(), cancelManDown: jest.fn(), escalateManDown: jest.fn(),
}))

let mockUser: { roleId: number } | null = { roleId: 5 }
let mockToken: string | null = 'tok'
jest.mock('@/store/auth', () => ({
  useAuthStore: (sel: (s: unknown) => unknown) =>
    sel({
      user: mockUser,
      accessToken: mockToken,
      restoreSession: jest.fn(async () => undefined),
      refresh: jest.fn(async () => undefined),
    }),
}))

beforeEach(() => {
  jest.clearAllMocks()
  mockUser = { roleId: 5 }
  mockToken = 'tok'
})

describe('man-down watcher', () => {
  jest.setTimeout(120_000)

  it('is mounted by the app root, not by a screen', async () => {
    // Substituted for the real one so the assertion is about App.tsx rendering
    // it at all — the failure mode is the element going missing, not the
    // component misbehaving.
    jest.doMock('@/components/ManDownGuard', () => ({
      ManDownGuard: () => <Text testID="man-down-guard">watching</Text>,
    }))
    const App = require('../App').default

    const { findByTestId } = render(<App />)
    expect(await findByTestId('man-down-guard')).toBeTruthy()

    jest.dontMock('@/components/ManDownGuard')
  })

  it('watches a security guard', async () => {
    const { ManDownGuard } = require('@/components/ManDownGuard')
    const { QueryClient, QueryClientProvider } = require('@tanstack/react-query')
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    render(
      <QueryClientProvider client={qc}>
        <ManDownGuard />
      </QueryClientProvider>,
    )
    // The settings fetch is gated on the same flag as the sensor, so asking for
    // them is the observable proof that this officer is being watched.
    await waitFor(() => expect(mockGetManDownSettings).toHaveBeenCalled())
    await waitFor(() => expect(mockAddListener).toHaveBeenCalled())
  })

  it('leaves desk roles alone', async () => {
    mockUser = { roleId: 2 } // Admin — never stands a post.
    const { ManDownGuard } = require('@/components/ManDownGuard')
    const { QueryClient, QueryClientProvider } = require('@tanstack/react-query')
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    render(
      <QueryClientProvider client={qc}>
        <ManDownGuard />
      </QueryClientProvider>,
    )
    await new Promise((r) => setTimeout(r, 50))
    expect(mockGetManDownSettings).not.toHaveBeenCalled()
    expect(mockAddListener).not.toHaveBeenCalled()
  })

  it('does not watch a signed-out phone', async () => {
    mockToken = null
    const { ManDownGuard } = require('@/components/ManDownGuard')
    const { QueryClient, QueryClientProvider } = require('@tanstack/react-query')
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    render(
      <QueryClientProvider client={qc}>
        <ManDownGuard />
      </QueryClientProvider>,
    )
    await new Promise((r) => setTimeout(r, 50))
    expect(mockAddListener).not.toHaveBeenCalled()
  })
})
