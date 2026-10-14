import React, { useEffect, useRef } from 'react'
import { Animated, Easing, StyleSheet, View, type StyleProp, type ViewStyle } from 'react-native'
import { useMotionStore } from '@/motion/motionStore'
import { colors, radius, spacing } from '@/theme'

/**
 * Placeholders shaped like what a screen will show - rows for a list, figures
 * and cards for a dashboard, a block and lines for one record - in place of a
 * spinner in the middle of nothing. They take the room the content will take,
 * so nothing jumps when it arrives.
 *
 * Like the web's: a placeholder waits a moment (150 ms) before it is seen, so a
 * screen that answers at once shows no flash of grey; the content is never held
 * back for it. Its gentle pulse is opacity only, on the phone's animation
 * thread, and is still - just grey - on a phone set to remove animations.
 */
const WAIT_MS = 150

function usePulse() {
  const still = useMotionStore((s) => s.reduced !== false)
  const seen = useRef(new Animated.Value(0)).current
  const beat = useRef(new Animated.Value(0)).current
  useEffect(() => {
    const appear = Animated.timing(seen, { toValue: 1, duration: 200, delay: WAIT_MS, useNativeDriver: true })
    const pulse = Animated.loop(Animated.sequence([
      Animated.timing(beat, { toValue: 1, duration: 700, easing: Easing.inOut(Easing.ease), useNativeDriver: true }),
      Animated.timing(beat, { toValue: 0, duration: 700, easing: Easing.inOut(Easing.ease), useNativeDriver: true }),
    ]))
    appear.start()
    if (!still) pulse.start()
    return () => { appear.stop(); pulse.stop() }
  }, [still, seen, beat])
  const opacity = still ? seen : Animated.multiply(seen, beat.interpolate({ inputRange: [0, 1], outputRange: [0.55, 1] }))
  return opacity
}

/** The placeholders of one screen pulse together: one value drives them all. */
function Group({ children, style, label = 'Loading' }: { children: React.ReactNode; style?: StyleProp<ViewStyle>; label?: string }) {
  const opacity = usePulse()
  return (
    <Animated.View
      style={[{ opacity }, style]}
      accessible
      accessibilityLabel={label}
      accessibilityState={{ busy: true }}
      testID="skeleton"
    >
      {children}
    </Animated.View>
  )
}

/** One grey shape. */
export function Bone({ width = '100%', height = 14, round = radius.sm, style }: {
  width?: number | `${number}%`; height?: number; round?: number; style?: StyleProp<ViewStyle>
}) {
  return <View style={[styles.bone, { width, height, borderRadius: round }, style]} />
}

/** A list: rows the height of the screen's own cards. */
export function ListSkeleton({ rows = 6, rowHeight = 72, style }: { rows?: number; rowHeight?: number; style?: StyleProp<ViewStyle> }) {
  return (
    <Group style={[styles.list, style]}>
      {Array.from({ length: rows }).map((_, i) => (
        <View key={i} style={[styles.row, { height: rowHeight }]}>
          <Bone width={36} height={36} round={radius.full} />
          <View style={styles.lines}>
            <Bone width="70%" height={14} />
            <Bone width="45%" height={11} />
          </View>
        </View>
      ))}
    </Group>
  )
}

/** A dashboard: a grid of figures, then cards. */
export function StatsSkeleton({ figures = 4, cards = 3, style }: { figures?: number; cards?: number; style?: StyleProp<ViewStyle> }) {
  return (
    <Group style={[styles.list, style]}>
      <View style={styles.grid}>
        {Array.from({ length: figures }).map((_, i) => <Bone key={i} width="48%" height={78} round={radius.lg} />)}
      </View>
      {Array.from({ length: cards }).map((_, i) => <Bone key={i} height={96} round={radius.lg} />)}
    </Group>
  )
}

/** One record: a heading, a block, and lines. */
export function DetailSkeleton({ lines = 5, block = 160, style }: { lines?: number; block?: number; style?: StyleProp<ViewStyle> }) {
  return (
    <Group style={[styles.list, style]}>
      <Bone width="60%" height={20} />
      <Bone width="35%" height={12} />
      <Bone height={block} round={radius.lg} />
      {Array.from({ length: lines }).map((_, i) => <Bone key={i} width={i % 2 ? '80%' : '95%'} height={13} />)}
    </Group>
  )
}

const styles = StyleSheet.create({
  bone: { backgroundColor: 'rgba(255,255,255,0.09)' },
  list: { padding: spacing.md, gap: spacing.sm },
  row: {
    flexDirection: 'row', alignItems: 'center', gap: spacing.sm, paddingHorizontal: spacing.md,
    borderRadius: radius.lg, borderWidth: 1, borderColor: colors.glassBorderLight, backgroundColor: colors.glassBgSubtle,
  },
  lines: { flex: 1, gap: 6 },
  grid: { flexDirection: 'row', flexWrap: 'wrap', justifyContent: 'space-between', rowGap: spacing.sm },
})
