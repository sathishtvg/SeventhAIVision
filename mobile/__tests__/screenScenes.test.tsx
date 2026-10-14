/**
 * Every screen has its own scene, and no scene ever gets in a guard's way.
 *
 * The scene is put round every screen by the navigators, so a screen cannot
 * forget it - and so nothing on any screen would show that it had gone
 * missing, or that it had begun to cover a screen it must not. These hold:
 *
 *   - every screen the navigators name has a scene of its own;
 *   - a scene takes no touch, the first touch ends it, and the screen beneath
 *     is the same screen throughout;
 *   - a screen where seconds matter, or one that is a camera's picture, is
 *     never covered;
 *   - "Loading" is said only while the screen is really fetching for the
 *     first time;
 *   - nothing moves on a phone that asked for less motion, for somebody who
 *     switched screen animations off, or before either is known.
 */
import React from 'react'
import { AccessibilityInfo, Text } from 'react-native'
import AsyncStorage from '@react-native-async-storage/async-storage'
import { act, fireEvent, render } from '@testing-library/react-native'

const mockFetching: { count: number; asked?: { predicate?: (query: unknown) => boolean } } = { count: 0 }
jest.mock('@tanstack/react-query', () => ({
  ...jest.requireActual('@tanstack/react-query'),
  useIsFetching: (filters?: { predicate?: (query: unknown) => boolean }) => {
    mockFetching.asked = filters
    return mockFetching.count
  },
}))

import { ScreenScene, sceneLayout } from '@/motion/ScreenScene'
import { scenesAllowed, useMotionStore } from '@/motion/motionStore'
import {
  SCENE_CLEAR_MS, SCENE_ENTER_MS, SCENE_HOLD_MS, SCREEN_SCENES, sceneOf,
} from '@/motion/scenes'

// The app is not typed for Node: its reader, by what it is used for. The tests are run from mobile/.
const fs = require('fs') as { readFileSync: (file: string, encoding: 'utf8') => string }
const read = (file: string) => fs.readFileSync(file, 'utf8').replace(/\r\n/g, '\n')
const navigation = read('src/navigation/index.tsx')
const glyphs = require('@expo/vector-icons/build/vendor/react-native-vector-icons/glyphmaps/MaterialCommunityIcons.json') as Record<string, number>

const ALL_MS = SCENE_ENTER_MS + SCENE_HOLD_MS + SCENE_CLEAR_MS
const played = () => act(() => { jest.advanceTimersByTime(ALL_MS + 400) })
const allow = () => act(() => { useMotionStore.setState({ reduced: false, wanted: true }) })
const screen = (name: string) => <ScreenScene routeName={name}><Text>the screen itself</Text></ScreenScene>
/** The scene is hidden from a screen reader, and the testing library leaves out what one cannot reach: a question
 *  about the scene's own parts has to say that it means them too. */
const HIDDEN = { includeHiddenElements: true }

beforeEach(async () => {
  jest.useFakeTimers()
  mockFetching.count = 0
  useMotionStore.setState({ reduced: null, wanted: null })
  await AsyncStorage.clear()
})
afterEach(() => {
  jest.useRealTimers()
  jest.restoreAllMocks()
})

describe('every screen has a scene of its own', () => {
  const inStacks = [...navigation.matchAll(/<\w+Stack\.Screen name="(\w+)"/g)].map(([, name]) => name)
  const tabs = [...navigation.matchAll(/<Tab\.Screen\s+name="(\w+)"/g)].map(([, name]) => name)
  /** A tab that only holds a stack: its screens have scenes, it has none. */
  const holdsAStack = [...navigation.matchAll(/<Tab\.Screen\s+name="(\w+)"\s+component=\{\w+Navigator\}/g)].map(([, name]) => name)

  it('is true of every screen the navigators name, and of nothing else', () => {
    expect(inStacks.length).toBeGreaterThanOrEqual(56)
    expect(holdsAStack.sort()).toEqual(['Alerts', 'Cameras', 'Incidents', 'More', 'Patrol'])
    const screens = [...inStacks, ...tabs.filter((name) => !holdsAStack.includes(name))]
    expect(screens.filter((name) => sceneOf(name) === null)).toEqual([])
    expect(Object.keys(SCREEN_SCENES).filter((name) => !screens.includes(name))).toEqual([])
    for (const name of holdsAStack) expect([name, sceneOf(name)]).toEqual([name, null])
  })

  it('is wrapped round every screen by all six navigators, and the phone is asked once what it allows', () => {
    expect(navigation.match(/screenLayout=\{sceneLayout\}/g)).toHaveLength(6)
    expect(navigation).toContain('void initMotion()')
    const wrapped = sceneLayout({ route: { name: 'AlertsList' }, children: <Text>x</Text> })
    expect(wrapped.props.routeName).toBe('AlertsList')
  })

  it('has an emblem that exists, a name, and its own words for loading', () => {
    for (const [name, scene] of Object.entries(SCREEN_SCENES)) {
      expect([name, scene.icon in glyphs, scene.name.length > 1, scene.loading.length > 6]).toEqual([name, true, true, true])
    }
    expect([sceneOf('GPS')?.icon, sceneOf('AlertsList')?.move, sceneOf('DroneEvents')?.icon]).toEqual(['radar', 'swing', 'quadcopter'])
  })

  it('never covers a screen where seconds matter, or one that is a camera’s picture', () => {
    const open = Object.entries(SCREEN_SCENES).filter(([, scene]) => scene.open).map(([name]) => name).sort()
    expect(open).toEqual([
      'ActionCenter', 'Alarms', 'CameraLive', 'Dispatch', 'Emergency', 'IncidentCameraLive', 'LiveWall', 'PatrolScan',
      'SituationCameraLive', 'VirtualPatrolRun', 'ZoneDraw',
    ])
  })
})

describe('the scene as a screen is opened', () => {
  it('shows the emblem and the name over the screen, then clears - and the screen is the same screen throughout', () => {
    allow()
    const view = render(screen('AlertsList'))
    const first = view.getByText('the screen itself')
    expect(view.getByTestId('screen-scene-cover', HIDDEN)).toBeTruthy()
    expect(view.getByText('Alerts', HIDDEN)).toBeTruthy()
    // It takes no touch: whatever is touched is the screen beneath.
    expect(view.getByTestId('screen-scene-cover', HIDDEN).props.pointerEvents).toBe('none')
    // Nor is it read out: a screen reader is given the screen, not the drawing over it.
    expect(view.queryByTestId('screen-scene-cover')).toBeNull()
    expect(view.getByTestId('screen-scene-cover', HIDDEN).props.importantForAccessibility).toBe('no-hide-descendants')

    played()
    expect(view.queryByTestId('screen-scene-cover', HIDDEN)).toBeNull()
    expect(view.queryByTestId('screen-scene-top', HIDDEN)).toBeNull()
    expect(view.getByText('the screen itself')).toBe(first)
  })

  it('goes on, small, as the screen’s loading while it is still fetching for the first time - and only that long', () => {
    allow()
    mockFetching.count = 2
    const view = render(screen('AlertsList'))
    expect(view.queryByTestId('screen-scene-top', HIDDEN)).toBeNull()

    played()
    expect(view.getByTestId('screen-scene-top', HIDDEN)).toBeTruthy()
    expect(view.getByText('Loading alerts…', HIDDEN)).toBeTruthy()
    // Only what has no answer yet counts: a refresh of what is on screen is not the screen loading.
    const counts = mockFetching.asked!.predicate!
    expect([counts({ state: { data: undefined } }), counts({ state: { data: [] } })]).toEqual([true, false])

    mockFetching.count = 0
    view.rerender(screen('AlertsList'))
    expect(view.queryByTestId('screen-scene-top', HIDDEN)).toBeNull()
    mockFetching.count = 1
    view.rerender(screen('AlertsList'))
    expect(view.queryByTestId('screen-scene-top', HIDDEN)).toBeNull()
  })

  it('is ended by the first touch', () => {
    allow()
    const view = render(screen('CamerasList'))
    expect(view.getByTestId('screen-scene-cover', HIDDEN)).toBeTruthy()
    fireEvent(view.getByTestId('screen-scene'), 'touchStart')
    expect(view.queryByTestId('screen-scene-cover', HIDDEN)).toBeNull()
    expect(view.getByText('the screen itself')).toBeTruthy()
  })

  it('does not cover a screen where seconds matter: the emblem plays at the top while the screen is in view', () => {
    allow()
    mockFetching.count = 1
    const view = render(screen('Emergency'))
    expect(view.queryByTestId('screen-scene-cover', HIDDEN)).toBeNull()
    expect(view.getByText('Emergency Broadcast', HIDDEN)).toBeTruthy()

    played()
    expect(view.getByText('Loading emergency broadcasts…', HIDDEN)).toBeTruthy()
    mockFetching.count = 0
    view.rerender(screen('Emergency'))
    expect(view.queryByTestId('screen-scene-top', HIDDEN)).toBeNull()
  })

  it('plays nothing until the phone has said what it allows, on a phone that asked for less motion, or when switched off', () => {
    // Not yet known.
    expect(render(screen('AlertsList')).queryByTestId('screen-scene')).toBeNull()
    act(() => { useMotionStore.setState({ reduced: true, wanted: true }) })
    expect(render(screen('AlertsList')).queryByTestId('screen-scene')).toBeNull()
    act(() => { useMotionStore.setState({ reduced: false, wanted: false }) })
    expect(render(screen('AlertsList')).queryByTestId('screen-scene')).toBeNull()
    allow()
    expect(render(screen('AlertsList')).queryByTestId('screen-scene')).not.toBeNull()
    // A tab that only holds a stack, or a screen nobody listed: the screen, and nothing round it.
    expect(render(screen('Alerts')).queryByTestId('screen-scene')).toBeNull()
  })

  it('is not begun part-way on a screen that was already open when it became allowed', () => {
    const view = render(screen('AlertsList'))
    allow()
    view.rerender(screen('AlertsList'))
    expect(view.queryByTestId('screen-scene')).toBeNull()
  })
})

describe('what the phone allows', () => {
  it('is read once: the phone’s own setting, and this person’s switch, which is on unless turned off', async () => {
    jest.useRealTimers()
    jest.spyOn(AccessibilityInfo, 'isReduceMotionEnabled').mockResolvedValue(false)
    jest.spyOn(AccessibilityInfo, 'addEventListener').mockReturnValue({ remove: jest.fn() } as never)
    expect(scenesAllowed(useMotionStore.getState())).toBe(false)
    await act(async () => { await useMotionStore.getState().init() })
    expect(useMotionStore.getState()).toMatchObject({ reduced: false, wanted: true })
    expect(scenesAllowed(useMotionStore.getState())).toBe(true)

    await act(async () => { await useMotionStore.getState().setWanted(false) })
    expect(await AsyncStorage.getItem('seventh_ai_screen_scenes')).toBe('off')
    useMotionStore.setState({ reduced: null, wanted: null })
    await act(async () => { await useMotionStore.getState().init() })
    expect(useMotionStore.getState().wanted).toBe(false)

    await act(async () => { await useMotionStore.getState().setWanted(true) })
    expect(await AsyncStorage.getItem('seventh_ai_screen_scenes')).toBeNull()
  })

  it('follows a phone that asks for less motion, and takes one that cannot say to have asked', async () => {
    jest.useRealTimers()
    jest.spyOn(AccessibilityInfo, 'addEventListener').mockReturnValue({ remove: jest.fn() } as never)
    jest.spyOn(AccessibilityInfo, 'isReduceMotionEnabled').mockResolvedValue(true)
    await act(async () => { await useMotionStore.getState().init() })
    expect(scenesAllowed(useMotionStore.getState())).toBe(false)

    jest.spyOn(AccessibilityInfo, 'isReduceMotionEnabled').mockRejectedValue(new Error('no such module'))
    useMotionStore.setState({ reduced: null, wanted: null })
    await act(async () => { await useMotionStore.getState().init() })
    expect(useMotionStore.getState().reduced).toBe(true)
  })

  it('has a switch in Settings, which a phone set to remove animations cannot turn on', () => {
    const settings = read('src/screens/SettingsScreen.tsx')
    expect(settings).toContain('<Text style={styles.settingLabel}>Screen animations</Text>')
    expect(settings).toContain('disabled={reduced !== false}')
    expect(settings).toContain('onValueChange={(value) => { void setWanted(value) }}')
  })
})
