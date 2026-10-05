/**
 * Shortcuts go back to a screen that is already open; they do not stack a copy.
 *
 * Before React Navigation 7, navigate() to a screen already in a stack went
 * back to it. Since 7 it pushes a second one unless told otherwise — so the
 * dashboard's "check in" reminder would have laid a new My Shifts over a patrol
 * in progress, and choosing a site would have put a second camera list on top
 * of the first, each leaving Back to walk the guard through the leftovers.
 *
 * These mount the real navigators and the real Dashboard and Sites screens,
 * because the rule lives in the navigators: a mocked navigate() would agree
 * with whatever the screen passed it.
 */
import React from 'react'
import { Text } from 'react-native'
import { act, fireEvent, render } from '@testing-library/react-native'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { NavigationContainer, createNavigationContainerRef } from '@react-navigation/native'
import { createNativeStackNavigator } from '@react-navigation/native-stack'
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs'

jest.mock('react-native-safe-area-context', () => require('react-native-safe-area-context/jest/mock').default)
jest.mock('@/hooks/useWebSocket', () => ({ useWebSocket: jest.fn() }))
jest.mock('@/components/ShiftCheckInCard', () => ({ ShiftCheckInCard: () => null }))
jest.mock('@/components/VirtualPatrolCard', () => ({ VirtualPatrolCard: () => null }))
jest.mock('@/store/auth', () => ({
  useAuthStore: (sel: (s: unknown) => unknown) => sel({ permissions: [], user: { roleId: 5 } }),
}))
jest.mock('@/api/dashboard', () => ({
  getSummary: jest.fn(async () => ({ open_alerts: 1, open_incidents: 2, active_cameras: 3, detections_today: 4 })),
}))
jest.mock('@/api/actionCenter', () => ({
  getMyDuties: jest.fn(async () => ({
    summary: { total: 1, critical: 0, high: 1, medium: 0, low: 0 },
    items: [{ id: 'd1', category: 'check_in', severity: 'high', title: 'Check in for your shift',
              subtitle: 'North Yard', entity_id: 's1' }],
  })),
}))
jest.mock('@/api/sites', () => ({
  getSites: jest.fn(async () => [{ id: 'site-1', name: 'North Yard', address: '1 Yard Rd', is_active: true }]),
}))
jest.mock('@/api/cameras', () => ({ getCameras: jest.fn(async () => []) }))

import { DashboardScreen } from '@/screens/DashboardScreen'
import { SitesScreen } from '@/screens/SitesScreen'

const Tab = createBottomTabNavigator()
const Patrol = createNativeStackNavigator()
const Cameras = createNativeStackNavigator()
const More = createNativeStackNavigator()
const Stub = ({ route }: { route: { name: string } }) => <Text>{`screen:${route.name}`}</Text>

const PatrolStack = () => (
  <Patrol.Navigator>
    <Patrol.Screen name="Shifts" component={Stub} />
    <Patrol.Screen name="PatrolSelect" component={Stub} />
    <Patrol.Screen name="PatrolScan" component={Stub} />
  </Patrol.Navigator>
)
const CamerasStack = () => (
  <Cameras.Navigator>
    <Cameras.Screen name="CamerasList" component={Stub} />
    <Cameras.Screen name="Sites" component={SitesScreen} />
  </Cameras.Navigator>
)
const MoreStack = () => (
  <More.Navigator>
    <More.Screen name="MoreMenu" component={Stub} />
    <More.Screen name="Detections" component={Stub} />
    <More.Screen name="Settings" component={Stub} />
  </More.Navigator>
)

type AnyNav = { navigate: (...a: unknown[]) => void }
const ref = createNavigationContainerRef()
const go = (...a: unknown[]) => act(() => { (ref as unknown as AnyNav).navigate(...a) })

let qc: QueryClient
function mount() {
  qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return render(
    <QueryClientProvider client={qc}>
      <NavigationContainer ref={ref}>
        <Tab.Navigator screenOptions={{ headerShown: false }}>
          <Tab.Screen name="Dashboard" component={DashboardScreen} />
          <Tab.Screen name="Cameras" component={CamerasStack} />
          <Tab.Screen name="Patrol" component={PatrolStack} />
          <Tab.Screen name="More" component={MoreStack} />
        </Tab.Navigator>
      </NavigationContainer>
    </QueryClientProvider>,
  )
}

/** The open tab's name, and the screens stacked inside a tab, oldest first. */
function where(tab: string): { focused: string; stack: string[]; top: { name: string; params?: unknown } } {
  const root = ref.getRootState()
  if (!root) throw new Error('the navigators are not mounted')
  const route = root.routes.find((r) => r.name === tab)!
  const routes = (route.state?.routes ?? []) as { name: string; params?: unknown }[]
  return { focused: root.routes[root.index].name, stack: routes.map((r) => r.name), top: routes[routes.length - 1] }
}

afterEach(() => qc.clear())

describe('shortcuts return to the open screen', () => {
  it('takes a guard from a duty back to My Shifts, not to a second one over the patrol', async () => {
    const s = mount()
    await go('Patrol')
    await go('Patrol', { screen: 'PatrolSelect', params: { shiftId: 's1' } })
    await go('Patrol', { screen: 'PatrolScan', params: { routeId: 'r1', shiftId: 's1' } })
    expect(where('Patrol').stack).toEqual(['Shifts', 'PatrolSelect', 'PatrolScan'])

    await go('Dashboard')
    fireEvent.press(await s.findByText('Check in for your shift'))

    const now = where('Patrol')
    expect(now.focused).toBe('Patrol')
    expect(now.stack).toEqual(['Shifts'])
  })

  it('opens AI Detections from the dashboard once, however many screens are above it', async () => {
    const s = mount()
    await go('More')
    await go('More', { screen: 'Detections' })
    await go('More', { screen: 'Settings' })
    expect(where('More').stack).toEqual(['MoreMenu', 'Detections', 'Settings'])

    await go('Dashboard')
    fireEvent.press(await s.findByText('Detections Today'))

    const now = where('More')
    expect(now.focused).toBe('More')
    expect(now.stack).toEqual(['MoreMenu', 'Detections'])
  })

  it('returns from Sites to the one camera list, filtered by the site chosen', async () => {
    const s = mount()
    await go('Cameras')
    await go('Cameras', { screen: 'Sites' })
    expect(where('Cameras').stack).toEqual(['CamerasList', 'Sites'])

    fireEvent.press(await s.findByText('North Yard'))

    const now = where('Cameras')
    expect(now.stack).toEqual(['CamerasList'])
    expect(now.top.params).toEqual({ siteId: 'site-1' })
  })
})

describe('why the screens say so', () => {
  it('a plain navigate() to a screen underneath stacks a second copy', async () => {
    // If this ever fails, React Navigation has changed its mind again and the
    // `pop: true` / popTo in the screens above can be reconsidered.
    mount()
    await go('Cameras')
    await go('Cameras', { screen: 'Sites' })
    await go('Cameras', { screen: 'CamerasList' })
    expect(where('Cameras').stack).toEqual(['CamerasList', 'Sites', 'CamerasList'])
  })
})
