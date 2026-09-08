/**
 * Man-down detection from the phone's accelerometer.
 *
 * WHAT COUNTS AS DOWN
 *   A guard on patrol produces continuous small accelerations — footsteps, the
 *   phone shifting in a pocket. A phone lying on a desk produces almost none.
 *   So "still" is a low deviation from the resting magnitude, sustained.
 *
 *   Two ways in. Sustained stillness for the configured window is the ordinary
 *   path, and it is deliberately slow because a guard writing in the occurrence
 *   book is also still. An impact — a spike well above 1g — shortcuts that
 *   window to a quarter of it, because a fall is a spike followed by stillness
 *   and waiting the full two minutes after one is two minutes wasted.
 *
 * WHY THE ACCELEROMETER AND NOT DeviceMotion
 *   DeviceMotion gives rotation too, which would let us detect a phone lying
 *   flat. It also samples far more expensively. Tilt alone is a bad signal — a
 *   phone on a desk is flat and its guard is fine — so the extra cost buys
 *   nothing this design uses.
 *
 * WHAT THIS DOES NOT DO
 *   iOS suspends sensors when the app is backgrounded, so detection only runs
 *   while the app is open. That is a real limitation and the settings screen
 *   says so rather than implying cover we do not provide. A guard is expected
 *   to leave the app foregrounded on patrol, which is also what the patrol
 *   scanner needs.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { AppState } from 'react-native'
import { Accelerometer } from 'expo-sensors'

const SAMPLE_INTERVAL_MS = 500

/** How much of the no-motion window survives an impact. A fall is a spike then
 *  stillness; waiting the whole window after one wastes the time that matters. */
const IMPACT_WINDOW_FRACTION = 0.25

export type ManDownTrigger = 'no_motion' | 'impact'

interface Options {
  enabled: boolean
  noMotionSeconds: number
  /** Deviation from rest, in milli-g, below which the phone counts as still. */
  stillnessThresholdMg: number
  /** Total acceleration, in g, above which a sample counts as an impact. */
  impactThresholdG: number
  onDetected: (trigger: ManDownTrigger) => void
}

interface State {
  /** Seconds of continuous stillness so far. Drives the "about to trigger"
   *  hint on the settings screen — a guard should be able to see it working
   *  rather than trust that it is. */
  stillSeconds: number
  monitoring: boolean
}

export function useManDown({
  enabled,
  noMotionSeconds,
  stillnessThresholdMg,
  impactThresholdG,
  onDetected,
}: Options): State {
  const [stillSeconds, setStillSeconds] = useState(0)
  const [monitoring, setMonitoring] = useState(false)

  const stillSinceRef = useRef<number | null>(null)
  const impactAtRef = useRef<number | null>(null)
  const firedRef = useRef(false)
  // Held in a ref so changing the callback does not tear down the subscription
  // and restart the stillness window from zero.
  const onDetectedRef = useRef(onDetected)
  onDetectedRef.current = onDetected

  const reset = useCallback(() => {
    stillSinceRef.current = null
    impactAtRef.current = null
    firedRef.current = false
    setStillSeconds(0)
  }, [])

  useEffect(() => {
    if (!enabled) {
      reset()
      setMonitoring(false)
      return
    }

    let subscription: { remove: () => void } | null = null
    let cancelled = false

    const start = () => {
      Accelerometer.setUpdateInterval(SAMPLE_INTERVAL_MS)
      subscription = Accelerometer.addListener(({ x, y, z }) => {
        if (cancelled) return
        const magnitude = Math.sqrt(x * x + y * y + z * z)
        // At rest the magnitude is 1g (gravity). Deviation from that is
        // movement, in either direction.
        const deviationMg = Math.abs(magnitude - 1) * 1000
        const now = Date.now()

        if (magnitude >= impactThresholdG) {
          impactAtRef.current = now
          // An impact is itself movement, so the stillness window restarts —
          // what matters is the stillness that follows it.
          stillSinceRef.current = null
          setStillSeconds(0)
          return
        }

        if (deviationMg > stillnessThresholdMg) {
          if (stillSinceRef.current !== null) {
            stillSinceRef.current = null
            setStillSeconds(0)
          }
          // Movement clears a recent impact too: a guard who dropped their
          // phone and picked it up is not down.
          impactAtRef.current = null
          firedRef.current = false
          return
        }

        if (stillSinceRef.current === null) stillSinceRef.current = now
        const stillFor = (now - stillSinceRef.current) / 1000
        setStillSeconds(Math.floor(stillFor))

        const hadImpact = impactAtRef.current !== null
        const window = hadImpact
          ? noMotionSeconds * IMPACT_WINDOW_FRACTION
          : noMotionSeconds

        if (stillFor >= window && !firedRef.current) {
          firedRef.current = true
          onDetectedRef.current(hadImpact ? 'impact' : 'no_motion')
        }
      })
      setMonitoring(true)
    }

    Accelerometer.isAvailableAsync()
      .then((available) => {
        if (cancelled) return
        // A device with no accelerometer is not an error worth interrupting a
        // shift over; it just cannot do this.
        if (available) start()
        else setMonitoring(false)
      })
      .catch(() => setMonitoring(false))

    // Sensors are suspended when the app is backgrounded. Coming back with a
    // stale stillness window would fire immediately on a guard who was simply
    // using another app, so the window restarts.
    const appStateSub = AppState.addEventListener('change', (next) => {
      if (next === 'active') reset()
    })

    return () => {
      cancelled = true
      subscription?.remove()
      appStateSub.remove()
      setMonitoring(false)
    }
  }, [enabled, noMotionSeconds, stillnessThresholdMg, impactThresholdG, reset])

  return { stillSeconds, monitoring }
}
