import React, { useCallback, useEffect, useRef, useState } from 'react'
import { ActivityIndicator, Animated, Easing, Pressable, StyleSheet, Text, View } from 'react-native'
import { Ionicons } from '@expo/vector-icons'
import { useMotionStore } from '@/motion/motionStore'
import { NEW_ITEM_MS, type NewItemTone } from '@/motion/useNewItems'
import { colors, fontSize, radius, spacing } from '@/theme'

const TONE: Record<NewItemTone, string> = {
  critical: colors.error, high: '#FF6B35', medium: colors.warning, low: colors.info, info: colors.info, success: colors.success,
}

/**
 * Round a row or a card that arrived while its list was open (`useNewItems`):
 * a bar at its leading edge and a tint, in the colour of how serious it is, for
 * six seconds. Both are there without any movement; they fade out at the end,
 * and on a phone set to remove animations they simply go. A screen reader is
 * told how many arrived by the hook, not by this.
 */
export function NewMark({ isNew, tone = 'info', children }: { isNew: boolean; tone?: NewItemTone; children: React.ReactNode }) {
  const still = useMotionStore((s) => s.reduced !== false)
  const shown = useRef(new Animated.Value(isNew ? 1 : 0)).current
  useEffect(() => {
    if (!isNew) { shown.setValue(0); return }
    shown.setValue(1)
    if (still) return
    // Held for most of the time, then faded, so its going is not a jump.
    const fade = Animated.timing(shown, { toValue: 0, duration: NEW_ITEM_MS * 0.25, delay: NEW_ITEM_MS * 0.75, useNativeDriver: true })
    fade.start()
    return () => fade.stop()
  }, [isNew, still, shown])
  const colour = TONE[tone]
  return (
    <View testID={isNew ? 'new-mark' : undefined}>
      {children}
      {isNew && (
        <Animated.View pointerEvents="none" style={[styles.mark, { opacity: shown, borderColor: colour, backgroundColor: `${colour}22` }]} />
      )}
    </View>
  )
}

/**
 * Put above a list whose rows are the last answer's, kept on screen while this
 * one is fetched (`placeholderData: keepPreviousData`, `isPlaceholderData`): a
 * thin line that moves, so the rows below are not taken for the new answer. It
 * waits a moment before it is seen - a filter that answers at once changes the
 * rows and nothing else.
 */
export function RefreshingLine() {
  const still = useMotionStore((s) => s.reduced !== false)
  const seen = useRef(new Animated.Value(0)).current
  const run = useRef(new Animated.Value(0)).current
  useEffect(() => {
    const appear = Animated.timing(seen, { toValue: 1, duration: 150, delay: 150, useNativeDriver: true })
    const sweep = Animated.loop(Animated.timing(run, { toValue: 1, duration: 1100, easing: Easing.inOut(Easing.ease), useNativeDriver: true }))
    appear.start()
    if (!still) sweep.start()
    return () => { appear.stop(); sweep.stop() }
  }, [still, seen, run])
  return (
    <Animated.View
      style={[styles.line, { opacity: seen }]}
      accessible
      accessibilityLabel="Refreshing the list"
      accessibilityState={{ busy: true }}
      testID="refreshing-line"
    >
      <Animated.View
        style={[styles.runner, !still && { transform: [{ translateX: run.interpolate({ inputRange: [0, 1], outputRange: [-120, 420] }) }] }]}
      />
    </Animated.View>
  )
}

export type VideoState = 'connecting' | 'buffering' | 'failed' | 'offline'

const VIDEO_WORDS: Record<VideoState, string> = {
  connecting: 'Connecting', buffering: 'Buffering', failed: 'No picture', offline: 'Camera offline',
}

/**
 * What a camera's view says when it is not showing a picture, so a view that
 * has not connected is not taken for a dark room. Over the picture, drawn
 * cheaply: a flat background, a small spinner, words - and "Try again" when it
 * failed. The phone's copy of the web's `VideoLoadingState`.
 */
export function VideoLoadingState({ state, detail, onRetry }: { state: VideoState; detail?: string; onRetry?: () => unknown }) {
  const failed = state === 'failed' || state === 'offline'
  return (
    <View
      pointerEvents={failed && onRetry ? 'auto' : 'none'}
      style={[styles.video, state === 'buffering' && styles.videoBuffering]}
      accessibilityRole={failed ? 'alert' : undefined}
      accessibilityLiveRegion="polite"
      testID="video-state"
    >
      {failed
        ? <Ionicons name="videocam-off-outline" size={24} color="rgba(248,250,252,0.82)" />
        : <ActivityIndicator size="small" color="rgba(248,250,252,0.72)" />}
      <Text style={styles.videoWords}>{VIDEO_WORDS[state]}</Text>
      {detail ? <Text style={styles.videoDetail} numberOfLines={2}>{detail}</Text> : null}
      {failed && onRetry && (
        <Pressable onPress={() => { void onRetry() }} accessibilityRole="button" style={styles.videoRetry} hitSlop={8}>
          <Text style={styles.videoRetryText}>Try again</Text>
        </Pressable>
      )}
    </View>
  )
}

/**
 * A short confirmation where something was really done - "Checkpoint
 * recorded", "Snapshot saved". It is shown only while `show` is true, which the
 * screen sets when the server has said so, and the tick's small pop is the only
 * part that moves. A screen reader is told the words.
 */
export function SuccessTick({ show, label }: { show: boolean; label: string }) {
  const still = useMotionStore((s) => s.reduced !== false)
  const pop = useRef(new Animated.Value(still ? 1 : 0)).current
  useEffect(() => {
    if (!show) return
    if (still) { pop.setValue(1); return }
    pop.setValue(0)
    const go = Animated.spring(pop, { toValue: 1, friction: 6, tension: 140, useNativeDriver: true })
    go.start()
    return () => go.stop()
  }, [show, still, pop])
  if (!show) return null
  return (
    <Animated.View
      style={[styles.tick, { opacity: pop, transform: [{ scale: pop.interpolate({ inputRange: [0, 1], outputRange: [0.7, 1] }) }] }]}
      accessibilityRole="text"
      accessibilityLiveRegion="polite"
      accessibilityLabel={label}
      testID="success-tick"
    >
      <Ionicons name="checkmark-circle" size={16} color={colors.success} />
      <Text style={styles.tickText}>{label}</Text>
    </Animated.View>
  )
}

/** How long a confirmation stays, unless another takes its place. */
const BRIEF_MS = 3000

/**
 * The words of a confirmation (`SuccessTick`), shown for a few seconds:
 * `say("Checkpoint recorded")` from where the server has said so, and they go
 * by themselves. Saying the same words again shows them again for as long.
 */
export function useBriefWord(ms: number = BRIEF_MS): [string | null, (words: string) => void] {
  const [said, setSaid] = useState<{ words: string; turn: number } | null>(null)
  useEffect(() => {
    if (!said) return
    const timer = setTimeout(() => setSaid(null), ms)
    return () => clearTimeout(timer)
  }, [said, ms])
  const say = useCallback((words: string) => setSaid((was) => ({ words, turn: (was?.turn ?? 0) + 1 })), [])
  return [said?.words ?? null, say]
}

const styles = StyleSheet.create({
  mark: { position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, borderRadius: radius.lg, borderLeftWidth: 4 },
  line: { height: 2, overflow: 'hidden', backgroundColor: colors.primaryMuted, marginHorizontal: spacing.md, borderRadius: 1 },
  runner: { width: 120, height: 2, backgroundColor: colors.primary, borderRadius: 1 },
  video: {
    position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, alignItems: 'center', justifyContent: 'center', gap: 4, padding: spacing.sm,
    backgroundColor: '#05070f',
  },
  videoBuffering: { backgroundColor: 'rgba(2,6,23,0.62)' },
  videoWords: { color: 'rgba(248,250,252,0.85)', fontSize: fontSize.sm, fontWeight: '700' },
  videoDetail: { color: 'rgba(248,250,252,0.6)', fontSize: fontSize.xs, textAlign: 'center' },
  videoRetry: { marginTop: 4, minHeight: 36, justifyContent: 'center', paddingHorizontal: spacing.md, borderRadius: radius.md, borderWidth: 1, borderColor: 'rgba(248,250,252,0.5)' },
  videoRetryText: { color: colors.text, fontSize: fontSize.sm, fontWeight: '600' },
  tick: {
    flexDirection: 'row', alignItems: 'center', gap: 6, alignSelf: 'flex-start', paddingHorizontal: 10, paddingVertical: 4,
    borderRadius: radius.full, borderWidth: 1, borderColor: 'rgba(34,197,94,0.45)', backgroundColor: 'rgba(34,197,94,0.16)',
  },
  tickText: { color: colors.text, fontSize: fontSize.sm, fontWeight: '600' },
})
