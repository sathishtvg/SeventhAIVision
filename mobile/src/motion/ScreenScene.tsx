import React, { useEffect, useRef, useState } from 'react'
import { Animated, Easing, StyleSheet, Text, View } from 'react-native'
import { MaterialCommunityIcons } from '@expo/vector-icons'
import { useIsFetching } from '@tanstack/react-query'
import { colors } from '@/theme'
import { scenesAllowed, useMotionStore } from './motionStore'
import {
  SCENE_CLEAR_MS, SCENE_ENTER_MS, SCENE_HOLD_MS, SCENE_WAIT_MS, sceneOf,
} from './scenes'
import type { SceneMove, SceneTone, ScreenSceneDef } from './scenes'

/**
 * A screen's own scene, played as the screen is opened, and its own loading
 * after it (`scenes.ts` says which emblem and which words).
 *
 * The navigators wrap every screen in this (`sceneLayout`), so no screen was
 * edited for it. What it does, in order:
 *
 *   entrance  the emblem and the screen's name in the middle of the screen,
 *             and the screen arrives as they clear.
 *   loading   if the screen is still fetching for the first time, the emblem
 *             goes on, small, at the top beside "Loading ..." until the first
 *             answers are in. Read from the requests really in flight - a
 *             request waiting for a signal is not one of them, so a guard
 *             with no signal is never left looking at "Loading".
 *
 * It takes no touch, and the first touch anywhere ends it. A screen where
 * seconds matter, or one that is a camera's picture, is never covered. It is
 * played once, when the screen is opened - not when it is come back to.
 * Nothing is drawn for a phone that has asked for less motion, or for
 * somebody who has switched screen animations off.
 */
export function ScreenScene({ routeName, children }: { routeName: string; children: React.ReactNode }) {
  const allowed = useMotionStore(scenesAllowed)
  const scene = sceneOf(routeName)
  // Decided once, as the screen is opened: a screen already in view is not given a scene part-way through.
  const [play] = useState(() => allowed && scene !== null)
  if (!play || !scene) return <>{children}</>
  return <Scene scene={scene}>{children}</Scene>
}

/** For a navigator's `screenLayout`: every screen it holds is wrapped. */
export function sceneLayout({ route, children }: { route: { name: string }; children: React.ReactElement }) {
  return <ScreenScene routeName={route.name}>{children}</ScreenScene>
}

const firstFetch = (query: { state: { data: unknown } }) => query.state.data === undefined

const TONES: Record<SceneTone, { main: string; glow: string }> = {
  brand: { main: colors.primary, glow: colors.secondary },
  warn: { main: colors.warning, glow: colors.primary },
  danger: { main: colors.error, glow: colors.warning },
}

const LOOP_MS: Record<SceneMove, number> = { ring: 1300, sweep: 1600, swing: 1100, bob: 1000, pulse: 1100 }

function Scene({ scene, children }: { scene: ScreenSceneDef; children: React.ReactNode }) {
  const covered = !scene.open
  /** 0 to 1 the emblem arrives; 1 to 2 it clears and the screen comes in. */
  const stage = useRef(new Animated.Value(0)).current
  /** The part that keeps moving, round and round. */
  const turn = useRef(new Animated.Value(0)).current
  const [over, setOver] = useState(false)
  /** The screen's first answers are in. After that, what it fetches is its own business. */
  const [settled, setSettled] = useState(false)
  const fetching = useIsFetching({ predicate: firstFetch })
  if (over && !settled && fetching === 0) setSettled(true)

  useEffect(() => {
    const entrance = Animated.sequence([
      Animated.timing(stage, { toValue: 1, duration: SCENE_ENTER_MS, easing: Easing.out(Easing.cubic), useNativeDriver: true }),
      Animated.delay(SCENE_HOLD_MS),
      // Leaving is quicker than arriving.
      Animated.timing(stage, { toValue: 2, duration: SCENE_CLEAR_MS, easing: Easing.in(Easing.cubic), useNativeDriver: true }),
    ])
    const moving = Animated.loop(
      Animated.timing(turn, { toValue: 1, duration: LOOP_MS[scene.move], easing: Easing.linear, useNativeDriver: true }),
    )
    entrance.start(({ finished }) => { if (finished) setOver(true) })
    moving.start()
    return () => { entrance.stop(); moving.stop() }
    // Once, when the screen is opened: the scene and its two values are this screen's for as long as it is open.
  }, [])

  // The first touch ends it: the screen beneath is shown at once, and the touch goes where it was aimed.
  const cut = () => {
    stage.stopAnimation()
    stage.setValue(2)
    setOver(true)
  }

  const showTop = covered ? over && !settled : !over || !settled
  return (
    <View style={styles.fill} onTouchStart={over ? undefined : cut} testID="screen-scene">
      {/* The screen is here from the first frame, in the same place in the tree throughout: nothing it holds is
          lost when the scene ends. Behind a scene that covers it, it is clear until the emblem begins to go. */}
      <Animated.View
        style={[styles.fill, covered && { opacity: stage.interpolate({ inputRange: [0, 1, 2], outputRange: [0, 0, 1] }) }]}
      >
        {children}
      </Animated.View>

      {covered && !over && (
        <Animated.View
          pointerEvents="none"
          accessibilityElementsHidden
          importantForAccessibility="no-hide-descendants"
          testID="screen-scene-cover"
          style={[
            StyleSheet.absoluteFill, styles.centre,
            { opacity: stage.interpolate({ inputRange: [0, 1, 2], outputRange: [0, 1, 0] }) },
          ]}
        >
          <Animated.View
            style={[styles.mark, { transform: [{ scale: stage.interpolate({ inputRange: [0, 1, 2], outputRange: [0.9, 1, 1.06] }) }] }]}
          >
            <Emblem scene={scene} size={64} turn={turn} />
            <Text style={styles.name}>{scene.name}</Text>
          </Animated.View>
        </Animated.View>
      )}

      {showTop && (
        <View
          pointerEvents="none"
          accessibilityElementsHidden
          importantForAccessibility="no-hide-descendants"
          style={styles.top}
        >
          <Pill late={covered} testID="screen-scene-top">
            <Emblem scene={scene} size={16} turn={turn} />
            <Text style={styles.pillText}>{over ? `${scene.loading}…` : scene.name}</Text>
          </Pill>
        </View>
      )}
    </View>
  )
}

/** The small line at the top. After an entrance it waits a moment before it is seen, so a screen that answers at once shows none. */
function Pill({ late, testID, children }: { late: boolean; testID: string; children: React.ReactNode }) {
  const seen = useRef(new Animated.Value(0)).current
  useEffect(() => {
    const appear = Animated.timing(seen, {
      toValue: 1, duration: 160, delay: late ? SCENE_WAIT_MS : 0, easing: Easing.out(Easing.cubic), useNativeDriver: true,
    })
    appear.start()
    return () => appear.stop()
  }, [late, seen])
  return <Animated.View testID={testID} style={[styles.pill, { opacity: seen }]}>{children}</Animated.View>
}

/**
 * The emblem: the screen's icon, and one part that goes on moving for as long as it is on screen - a ring going
 * out, a sweep going round, the icon swinging, hovering or pulsing. Opacity and transform only, so it is drawn by
 * the phone's own animation thread and costs the screen beneath nothing.
 */
function Emblem({ scene, size, turn }: { scene: ScreenSceneDef; size: number; turn: Animated.Value }) {
  const tone = TONES[scene.tone ?? 'brand']
  const box = size * 1.9
  const icon = <MaterialCommunityIcons name={scene.icon} size={size} color={tone.main} />
  const moved: Record<SceneMove, React.ReactNode> = {
    ring: (
      <>
        <Animated.View
          style={[styles.ring, {
            width: box, height: box, borderRadius: box / 2, borderColor: tone.glow,
            opacity: turn.interpolate({ inputRange: [0, 1], outputRange: [0.8, 0] }),
            transform: [{ scale: turn.interpolate({ inputRange: [0, 1], outputRange: [0.55, 1.1] }) }],
          }]}
        />
        {icon}
      </>
    ),
    sweep: (
      <>
        <Animated.View
          style={[styles.ring, {
            width: box, height: box, borderRadius: box / 2, borderColor: 'transparent', borderTopColor: tone.glow,
            transform: [{ rotate: turn.interpolate({ inputRange: [0, 1], outputRange: ['0deg', '360deg'] }) }],
          }]}
        />
        {icon}
      </>
    ),
    swing: (
      <Animated.View
        style={{ transform: [{ rotate: turn.interpolate({ inputRange: [0, 0.25, 0.75, 1], outputRange: ['0deg', '10deg', '-10deg', '0deg'] }) }] }}
      >
        {icon}
      </Animated.View>
    ),
    bob: (
      <Animated.View
        style={{ transform: [{ translateY: turn.interpolate({ inputRange: [0, 0.5, 1], outputRange: [0, -size * 0.08, 0] }) }] }}
      >
        {icon}
      </Animated.View>
    ),
    pulse: (
      <Animated.View style={{ opacity: turn.interpolate({ inputRange: [0, 0.5, 1], outputRange: [1, 0.45, 1] }) }}>
        {icon}
      </Animated.View>
    ),
  }
  return <View style={[styles.emblem, { width: box, height: box }]}>{moved[scene.move]}</View>
}

const styles = StyleSheet.create({
  fill: { flex: 1 },
  centre: { alignItems: 'center', justifyContent: 'center' },
  mark: { alignItems: 'center', gap: 6 },
  emblem: { alignItems: 'center', justifyContent: 'center' },
  ring: { position: 'absolute', borderWidth: 2 },
  name: { color: colors.text, fontSize: 18, fontWeight: '800', letterSpacing: 0.2 },
  top: { position: 'absolute', top: 6, left: 0, right: 0, alignItems: 'center' },
  pill: {
    flexDirection: 'row', alignItems: 'center', gap: 2, paddingRight: 12, paddingLeft: 2,
    borderRadius: 999, borderWidth: 1, borderColor: colors.glassBorderLight, backgroundColor: '#0B1224',
  },
  pillText: { color: colors.textSecondary, fontSize: 12, fontWeight: '600' },
})
