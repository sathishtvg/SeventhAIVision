/**
 * Checking on and off a shift: GPS, then a selfie, then the server's verdict.
 *
 * EXTRACTED, NOT REWRITTEN. This flow was written once inside ShiftScreen,
 * buried three taps deep under Patrol → My Shifts → Start. Putting the same
 * thing on the home screen by writing it again would have produced two copies
 * of the mock-location check, and the copy that drifts is the one that stops
 * catching a fake-GPS app. Both screens now call this.
 *
 * ORDER MATTERS. GPS is taken first, when the guard taps, so a mock location
 * blocks before the camera ever opens — there is no point photographing someone
 * whose check-in is going to be refused. The server enforces liveness and mock
 * location again when the mutation lands, because a phone is not a place to
 * make that decision.
 */
import { useState } from 'react'
import { Alert as RNAlert } from 'react-native'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import * as Location from 'expo-location'

import { startShift, endShift } from '@/api/patrols'

export type CheckInAction = 'start' | 'end'

interface Flow {
  shiftId: string
  action: CheckInAction
  latitude?: number
  longitude?: number
}

/** Best-effort GPS — never blocks check-in on permission denial or GPS-off (a
 *  missing reading is weak signal, not spoofing). `mocked` IS enforced:
 *  Android-only, undefined on iOS, which has no equivalent signal. */
async function tryGetCoords(): Promise<
  { latitude: number; longitude: number; mocked: boolean } | undefined
> {
  try {
    const { status } = await Location.requestForegroundPermissionsAsync()
    if (status !== 'granted') return undefined
    const pos = await Location.getCurrentPositionAsync({
      accuracy: Location.Accuracy.Balanced,
    })
    return {
      latitude: pos.coords.latitude,
      longitude: pos.coords.longitude,
      mocked: pos.mocked === true,
    }
  } catch {
    return undefined
  }
}

export function useShiftCheckIn(onDone?: () => void) {
  const qc = useQueryClient()
  const [flow, setFlow] = useState<Flow | null>(null)
  const [error, setError] = useState<string | null>(null)

  const begin = async (shiftId: string, action: CheckInAction) => {
    const coords = await tryGetCoords()
    if (coords?.mocked) {
      RNAlert.alert(
        'Fake GPS Detected',
        'Mock location is enabled on this device. Disable any fake-GPS app before checking in.',
      )
      return
    }
    setError(null)
    setFlow({ shiftId, action, latitude: coords?.latitude, longitude: coords?.longitude })
  }

  const mutation = useMutation({
    mutationFn: (photoUri: string) => {
      const { shiftId, action, latitude, longitude } = flow!
      return action === 'start'
        ? startShift(shiftId, photoUri, false, latitude, longitude)
        : endShift(shiftId, photoUri, false, latitude, longitude)
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['my-shifts'] })
      setFlow(null)
      onDone?.()
    },
    onError: (err: any) => {
      const status = err?.response?.status
      const detail = err?.response?.data?.detail
      if (status === 422) {
        setError(typeof detail === 'string' ? detail : 'Liveness check failed — please retake the photo.')
      } else if (status === 403) {
        setError(typeof detail === 'string' ? detail : 'Check-in blocked — fake GPS location detected.')
      } else {
        setError('Failed to submit check-in. Please try again.')
      }
    },
  })

  return {
    /** Non-null while the selfie sheet should be open. */
    flow,
    error,
    begin,
    submit: (photoUri: string) => mutation.mutate(photoUri),
    submitting: mutation.isPending,
    close: () => { setFlow(null); setError(null) },
  }
}
