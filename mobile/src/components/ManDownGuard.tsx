/**
 * The man-down watcher, and the countdown a guard sees when it fires.
 *
 * Mounted once near the root so it keeps watching whatever screen the guard is
 * on — a fall does not wait for them to open the right tab.
 *
 * The countdown is deliberately loud: full-screen, red, counting down out loud
 * in numbers a guard can read at arm's length while getting up off the floor.
 * The cancel button is large and unambiguous, because most triggers are a phone
 * left on a desk and a system that is hard to cancel is a system that gets
 * switched off.
 *
 * WHAT HAPPENS IF THIS SCREEN NEVER GETS DISMISSED. The server holds the same
 * deadline and escalates on its own. Everything here is the fast path; nothing
 * here is the only path.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react'
import {
  AppState, Modal, Pressable, StyleSheet, Text, Vibration, View,
} from 'react-native'
import * as Location from 'expo-location'
import { useQuery } from '@tanstack/react-query'
import { Ionicons } from '@expo/vector-icons'

import {
  cancelManDown, escalateManDown, getManDownSettings, raiseManDown,
} from '@/api/mandown'
import { useManDown, type ManDownTrigger } from '@/hooks/useManDown'
import { useAuthStore } from '@/store/auth'
import { colors, fontSize, radius, spacing } from '@/theme'

/** Only officers on the ground are watched. A manager at a desk triggering
 *  man-down every lunchtime is how the feature gets switched off company-wide. */
const WATCHED_ROLES = new Set([3, 4, 5])

const VIBRATION_PATTERN = [0, 600, 400, 600, 400, 600]

async function currentPosition() {
  try {
    const { status } = await Location.getForegroundPermissionsAsync()
    if (status !== 'granted') return null
    const pos = await Location.getCurrentPositionAsync({
      accuracy: Location.Accuracy.Balanced,
    })
    return {
      latitude: pos.coords.latitude,
      longitude: pos.coords.longitude,
      accuracy_m: pos.coords.accuracy ?? null,
    }
  } catch {
    // No fix in a basement plant room is the normal case, not a failure.
    return null
  }
}

export function ManDownGuard() {
  const roleId = useAuthStore((s) => s.user?.roleId ?? 0)
  const token = useAuthStore((s) => s.accessToken)
  const watched = Boolean(token) && WATCHED_ROLES.has(roleId)

  const { data: settings } = useQuery({
    queryKey: ['man-down-settings'],
    queryFn: () => getManDownSettings(),
    enabled: watched,
    // The thresholds change rarely; asking on every screen would be noise.
    staleTime: 10 * 60 * 1000,
  })

  const [eventId, setEventId] = useState<string | null>(null)
  const [secondsLeft, setSecondsLeft] = useState(0)
  const [escalated, setEscalated] = useState(false)
  const raisingRef = useRef(false)

  const onDetected = useCallback(async (trigger: ManDownTrigger) => {
    // Guard against a second detection arriving while the first is in flight.
    if (raisingRef.current || eventId) return
    raisingRef.current = true
    try {
      const position = await currentPosition()
      const event = await raiseManDown({
        trigger,
        latitude: position?.latitude ?? null,
        longitude: position?.longitude ?? null,
        accuracy_m: position?.accuracy_m ?? null,
      })
      setEventId(event.id)
      setSecondsLeft(event.seconds_remaining ?? event.countdown_seconds)
      setEscalated(event.status === 'escalated')
      Vibration.vibrate(VIBRATION_PATTERN, true)
    } catch {
      // The server refused or the network is down. Do not trap the guard
      // behind a modal we cannot cancel; the sensor will fire again.
      raisingRef.current = false
    }
  }, [eventId])

  const { stillSeconds, monitoring } = useManDown({
    enabled: watched && Boolean(settings?.enabled),
    noMotionSeconds: settings?.no_motion_seconds ?? 120,
    stillnessThresholdMg: settings?.stillness_threshold_mg ?? 60,
    impactThresholdG: settings?.impact_threshold_g ?? 3,
    onDetected,
  })

  // The countdown itself.
  useEffect(() => {
    if (!eventId || escalated) return
    if (secondsLeft <= 0) {
      escalateManDown(eventId).catch(() => undefined)
      setEscalated(true)
      return
    }
    const timer = setTimeout(() => setSecondsLeft((s) => s - 1), 1000)
    return () => clearTimeout(timer)
  }, [eventId, secondsLeft, escalated])

  // A phone put to sleep mid-countdown would otherwise resume from where the
  // JS timer stopped, which is later than the deadline the server is holding.
  useEffect(() => {
    if (!eventId || escalated) return
    const deadline = Date.now() + secondsLeft * 1000
    const sub = AppState.addEventListener('change', (next) => {
      if (next !== 'active') return
      const remaining = Math.ceil((deadline - Date.now()) / 1000)
      setSecondsLeft(remaining > 0 ? remaining : 0)
    })
    return () => sub.remove()
    // Intentionally keyed on the event, not on secondsLeft: re-registering
    // every tick would recompute the deadline from a shrinking value.
  }, [eventId, escalated]) // eslint-disable-line react-hooks/exhaustive-deps

  const dismiss = useCallback(() => {
    Vibration.cancel()
    setEventId(null)
    setSecondsLeft(0)
    setEscalated(false)
    raisingRef.current = false
  }, [])

  const onCancel = useCallback(async () => {
    if (!eventId) return
    Vibration.cancel()
    try {
      await cancelManDown(eventId, 'Cancelled by the guard')
    } catch {
      // Already escalated — the server swept it up first. Say so rather than
      // silently closing, because somebody is now on their way.
      setEscalated(true)
      return
    }
    dismiss()
  }, [eventId, dismiss])

  // Nothing to show, but the hook still needs to be running.
  if (!eventId) {
    // stillSeconds and monitoring are surfaced on the settings screen; the
    // reads here keep the values live without rendering anything.
    void stillSeconds
    void monitoring
    return null
  }

  return (
    <Modal visible animationType="fade" onRequestClose={() => undefined}>
      <View style={[styles.root, escalated && styles.rootEscalated]}>
        <Ionicons
          name={escalated ? 'megaphone' : 'alert-circle'}
          size={72} color="#fff"
        />
        {escalated ? (
          <>
            <Text style={styles.heading}>Help is being sent</Text>
            <Text style={styles.body}>
              Your supervisor has been alerted with your last known position.
              Stay where you are if you can.
            </Text>
            <Pressable style={styles.secondaryBtn} onPress={dismiss}>
              <Text style={styles.secondaryText}>Close</Text>
            </Pressable>
          </>
        ) : (
          <>
            <Text style={styles.heading}>Are you all right?</Text>
            <Text style={styles.body}>
              Your phone has not moved. If you do not respond, your supervisor
              will be alerted.
            </Text>
            <Text style={styles.count}>{secondsLeft}</Text>
            <Pressable style={styles.cancelBtn} onPress={onCancel}>
              <Ionicons name="checkmark-circle" size={26} color={colors.error} />
              <Text style={styles.cancelText}>I&apos;m fine</Text>
            </Pressable>
            <Pressable
              style={styles.secondaryBtn}
              onPress={() => {
                Vibration.cancel()
                escalateManDown(eventId).catch(() => undefined)
                setEscalated(true)
              }}
            >
              <Text style={styles.secondaryText}>I need help now</Text>
            </Pressable>
          </>
        )}
      </View>
    </Modal>
  )
}

const styles = StyleSheet.create({
  root: {
    flex: 1, backgroundColor: colors.error,
    alignItems: 'center', justifyContent: 'center',
    padding: spacing.lg, gap: spacing.md,
  },
  rootEscalated: { backgroundColor: '#7A1020' },
  heading: { color: '#fff', fontSize: 28, fontWeight: '800', textAlign: 'center' },
  body: {
    color: '#fff', fontSize: fontSize.md, textAlign: 'center',
    opacity: 0.9, lineHeight: 22,
  },
  count: {
    color: '#fff', fontSize: 88, fontWeight: '800',
    fontVariant: ['tabular-nums'], marginVertical: spacing.sm,
  },
  cancelBtn: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
    gap: spacing.sm, backgroundColor: '#fff', borderRadius: radius.md,
    paddingVertical: spacing.lg, paddingHorizontal: spacing.xl, minWidth: 240,
  },
  cancelText: { color: colors.error, fontSize: 22, fontWeight: '800' },
  secondaryBtn: {
    borderColor: '#fff', borderWidth: 1, borderRadius: radius.md,
    paddingVertical: spacing.sm, paddingHorizontal: spacing.lg, minWidth: 240,
    alignItems: 'center',
  },
  secondaryText: { color: '#fff', fontSize: fontSize.md, fontWeight: '600' },
})
