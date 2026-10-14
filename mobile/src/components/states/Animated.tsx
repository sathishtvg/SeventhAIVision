import React, { useEffect, useRef, useState } from 'react'
import { Animated, Easing, Text, type StyleProp, type TextStyle, type ViewStyle } from 'react-native'
import { useMotionStore } from '@/motion/motionStore'

/** Cards take turns, 40 ms apart, and never more than ten turns: a long list does not keep the last card waiting. */
const STEP_MS = 40
const MOST_STEPS = 10

/**
 * A card that rises a little into place as a screen first shows it, its turn
 * after the card before. Opacity and position only, on the phone's animation
 * thread; on a phone set to remove animations it is simply there.
 */
export function FadeInView({ index = 0, style, children }: { index?: number; style?: StyleProp<ViewStyle>; children: React.ReactNode }) {
  const still = useMotionStore((s) => s.reduced !== false)
  const t = useRef(new Animated.Value(still ? 1 : 0)).current
  useEffect(() => {
    if (still) { t.setValue(1); return }
    const rise = Animated.timing(t, {
      toValue: 1, duration: 260, delay: Math.min(index, MOST_STEPS) * STEP_MS, easing: Easing.out(Easing.cubic), useNativeDriver: true,
    })
    rise.start()
    return () => rise.stop()
    // Once, when the card is first shown.
  }, [])
  return (
    <Animated.View style={[style, { opacity: t, transform: [{ translateY: t.interpolate({ inputRange: [0, 1], outputRange: [8, 0] }) }] }]}>
      {children}
    </Animated.View>
  )
}

/**
 * A figure that counts up to the value the server gave, the first time it is
 * shown, and moves to a new value when it changes. A screen reader is given the
 * value itself, never a number on its way. Still on a phone set to remove
 * animations.
 */
export function AnimatedNumber({ value, style, format = (n) => n.toLocaleString() }: {
  value: number; style?: StyleProp<TextStyle>; format?: (n: number) => string
}) {
  const still = useMotionStore((s) => s.reduced !== false)
  const [shown, setShown] = useState(still ? value : 0)
  // Where the figure is now: a new value while it is still counting carries on from there, not from the start.
  const now = useRef(still ? value : 0)
  useEffect(() => {
    if (still) { setShown(value); now.current = value; return }
    const start = now.current
    const began = Date.now()
    let frame = 0
    const step = () => {
      const k = Math.min(1, (Date.now() - began) / 600)
      const eased = 1 - (1 - k) ** 3
      now.current = Math.round(start + (value - start) * eased)
      setShown(now.current)
      if (k < 1) frame = requestAnimationFrame(step)
    }
    frame = requestAnimationFrame(step)
    return () => cancelAnimationFrame(frame)
  }, [value, still])
  return <Text style={style} accessibilityLabel={format(value)}>{format(shown)}</Text>
}
