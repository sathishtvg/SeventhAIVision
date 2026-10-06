/**
 * Every Action Center row that can be tapped takes the person somewhere real.
 *
 * WHY THIS MATTERS
 *   "Check in for your shift" named a screen called 'Shift', which does not
 *   exist; "Patrol due" and "Overdue checkpoint" named 'PatrolSelect', a
 *   screen in another tab's stack that needs a shift it was not given. React
 *   Navigation does not complain about a name it cannot find from where it is
 *   asked — the tap simply does nothing. A guard told to check in, tapping the
 *   reminder, got no response from the app.
 *
 *   This mounts the real Action Center inside real navigators laid out as the
 *   app's are, because the fault is only visible there: a mocked navigate()
 *   accepts any name.
 */
import React from 'react'
import { Text } from 'react-native'
import { act, fireEvent, render } from '@testing-library/react-native'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { NavigationContainer, createNavigationContainerRef } from '@react-navigation/native'
import { createNativeStackNavigator } from '@react-navigation/native-stack'
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs'

jest.mock('react-native-safe-area-context', () => require('react-native-safe-area-context/jest/mock').default)

const mockRows = [
  { id: 'r1', category: 'check_in', severity: 'high', title: 'Check in for your shift', subtitle: 'North Yard' },
  { id: 'r2', category: 'patrol_due', severity: 'medium', title: 'Patrol due', subtitle: 'Night round' },
  { id: 'r3', category: 'overdue_checkpoint', severity: 'high', title: 'Checkpoint overdue', subtitle: 'Gate 4' },
  { id: 'r4', category: 'respond_incident', severity: 'critical', title: 'Respond to incident', subtitle: 'Forced gate' },
  { id: 'r5', category: 'ack_alert', severity: 'high', title: 'Unacknowledged alert', subtitle: 'Gate 1' },
  { id: 'r6', category: 'camera_offline', severity: 'medium', title: 'Camera offline', subtitle: 'Dock 7' },
  { id: 'r7', category: 'doc_expiry', severity: 'low', title: 'Licence expiring', subtitle: 'In 12 days' },
  { id: 'r8', category: 'pending_approval', severity: 'low', title: 'Leave awaiting approval', subtitle: 'Tan Wei Ming' },
]
jest.mock('@/api/actionCenter', () => ({
  getMyDuties: jest.fn(async () => ({ summary: { total: 8, critical: 1, high: 3, medium: 2, low: 2 }, items: mockRows })),
}))

import { ActionCenterScreen, destination } from '@/screens/ActionCenterScreen'

const Tab = createBottomTabNavigator()
const Patrol = createNativeStackNavigator()
const More = createNativeStackNavigator()
const Stub = ({ route }: { route: { name: string } }) => <Text>{`screen:${route.name}`}</Text>

// The app's own layout (src/navigation/index.tsx): Action Center lives in the
// More tab's stack; My Shifts and the patrol screens in the Patrol tab's.
const PatrolStack = () => (
  <Patrol.Navigator>
    <Patrol.Screen name="Shifts" component={Stub} />
    <Patrol.Screen name="PatrolSelect" component={Stub} />
    <Patrol.Screen name="PatrolScan" component={Stub} />
  </Patrol.Navigator>
)
const MoreStack = () => (
  <More.Navigator>
    <More.Screen name="MoreMenu" component={Stub} />
    <More.Screen name="ActionCenter" component={ActionCenterScreen} />
    <More.Screen name="MyRecord" component={Stub} />
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
          <Tab.Screen name="Dashboard" component={Stub} />
          <Tab.Screen name="Alerts" component={Stub} />
          <Tab.Screen name="Incidents" component={Stub} />
          <Tab.Screen name="Cameras" component={Stub} />
          <Tab.Screen name="Patrol" component={PatrolStack} />
          <Tab.Screen name="More" component={MoreStack} />
        </Tab.Navigator>
      </NavigationContainer>
    </QueryClientProvider>,
  )
}

/** The open tab, and the screens stacked inside a tab, oldest first. */
function where(tab: string): { focused: string; stack: string[] } {
  const root = ref.getRootState()
  if (!root) throw new Error('the navigators are not mounted')
  const route = root.routes.find((r) => r.name === tab)!
  const routes = (route.state?.routes ?? []) as { name: string }[]
  return { focused: root.routes[root.index].name, stack: routes.map((r) => r.name) }
}

async function openActionCenter() {
  const s = mount()
  await go('More')
  await go('More', { screen: 'ActionCenter' })
  await s.findByText('Check in for your shift')
  return s
}

afterEach(() => qc.clear())

describe('an Action Center row goes where it says', () => {
  jest.setTimeout(120_000)

  it.each([
    ['Check in for your shift'], ['Patrol due'], ['Checkpoint overdue'],
  ])('"%s" opens My Shifts', async (title) => {
    const s = await openActionCenter()
    fireEvent.press(s.getByText(title))
    const now = where('Patrol')
    expect(now.focused).toBe('Patrol')
    expect(now.stack).toEqual(['Shifts'])
  })

  it('goes back to My Shifts under a patrol in progress, not to a second copy on top of it', async () => {
    const s = mount()
    await go('Patrol')
    await go('Patrol', { screen: 'PatrolSelect', params: { shiftId: 's1' } })
    await go('Patrol', { screen: 'PatrolScan', params: { routeId: 'r1', shiftId: 's1' } })
    await go('More')
    await go('More', { screen: 'ActionCenter' })
    fireEvent.press(await s.findByText('Patrol due'))
    expect(where('Patrol')).toEqual({ focused: 'Patrol', stack: ['Shifts'] })
  })

  it.each([
    ['Respond to incident', 'Incidents'], ['Unacknowledged alert', 'Alerts'], ['Camera offline', 'Cameras'],
  ])('"%s" opens the %s tab', async (title, tab) => {
    const s = await openActionCenter()
    fireEvent.press(s.getByText(title))
    expect(where('More').focused).toBe(tab)
  })

  it('an expiring document opens My Record in the same stack', async () => {
    const s = await openActionCenter()
    fireEvent.press(s.getByText('Licence expiring'))
    expect(where('More')).toEqual({ focused: 'More', stack: ['MoreMenu', 'ActionCenter', 'MyRecord'] })
  })

  it('a row with nowhere to go stays where it is', async () => {
    const s = await openActionCenter()
    fireEvent.press(s.getByText('Leave awaiting approval'))
    expect(where('More')).toEqual({ focused: 'More', stack: ['MoreMenu', 'ActionCenter'] })
    expect(destination('pending_approval')).toBeNull()
    expect(destination('contact_guard')).toBeNull()
  })
})
