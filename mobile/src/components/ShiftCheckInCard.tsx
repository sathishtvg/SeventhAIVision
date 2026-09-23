/**
 * Check in and out, on the first screen.
 *
 * WHY IT IS HERE. Attendance is the thing a guard does at the start and end of
 * every shift, and it was three taps down: Patrol → My Shifts → find today's
 * row → Start. The dashboard, meanwhile, opened on a count of 9,136 open alerts
 * that a guard cannot act on. This puts the one action they always need at the
 * top of the first page, and leaves the list screen as it was for everything
 * else (breaks, briefings, past shifts).
 *
 * IT SHOWS ONE SHIFT, NOT A LIST. Whatever is running now; failing that, the
 * next one due today. A guard checking in does not need to choose.
 *
 * GPS AND FACE ARE NOT DECORATION, and the card says so before the camera
 * opens: the location is recorded with the check-in, a mock-location app blocks
 * it outright, and the selfie is liveness-checked server-side. Someone asked to
 * photograph themselves deserves to know what is being recorded.
 */
import { useMemo } from 'react'
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native'
import { Ionicons } from '@expo/vector-icons'
import { useQuery } from '@tanstack/react-query'

import { getMyShifts, type Shift } from '@/api/patrols'
import { CheckInPhotoModal } from '@/components/CheckInPhotoModal'
import { useShiftCheckIn } from '@/hooks/useShiftCheckIn'
import { isFieldRole } from '@/lib/access'
import { useAuthStore } from '@/store/auth'
import { colors, fontSize, radius, spacing } from '@/theme'

const time = (iso: string) =>
  new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })

const dayLabel = (iso: string) => {
  const d = new Date(iso)
  const today = new Date()
  const sameDay = d.toDateString() === today.toDateString()
  return sameDay ? 'Today' : d.toLocaleDateString([], { weekday: 'short', day: '2-digit', month: 'short' })
}

/**
 * The shift this card is about: the one in progress, else the next one that has
 * not finished yet. Exported because picking the wrong shift is the failure
 * that matters here — checking a guard into tomorrow's shift is worse than
 * showing nothing — and it is much easier to test than to notice on screen.
 */
export function shiftForCheckIn(shifts: Shift[], now: Date = new Date()): Shift | null {
  const active = shifts.find((s) => s.status === 'active')
  if (active) return active
  const upcoming = shifts
    .filter((s) => s.status === 'scheduled' && new Date(s.scheduled_end) >= now)
    .sort((a, b) => +new Date(a.scheduled_start) - +new Date(b.scheduled_start))
  return upcoming[0] ?? null
}

export function ShiftCheckInCard() {
  const userId = useAuthStore((s) => s.user?.id)
  const roleId = useAuthStore((s) => s.user?.roleId)

  const { data: shifts = [], isLoading, refetch } = useQuery({
    queryKey: ['my-shifts'],
    queryFn: () => getMyShifts(userId!),
    enabled: !!userId,
  })

  const { flow, error, begin, submit, submitting, close } = useShiftCheckIn(() => {
    void refetch()
  })

  const shift = useMemo(() => shiftForCheckIn(shifts), [shifts])

  if (isLoading) {
    return (
      <View style={[styles.card, styles.centre]}>
        <ActivityIndicator color={colors.primary} />
      </View>
    )
  }

  // Nothing rostered that can still be worked. A guard is told so plainly,
  // because for them this card is the point of the screen and its silence would
  // read as a bug. An admin or supervisor, who may never work a shift, is shown
  // nothing at all rather than a permanently empty card.
  if (!shift) {
    if (!isFieldRole(roleId)) return null
    return (
      <View style={styles.card}>
        <Text style={styles.heading}>Attendance</Text>
        <Text style={styles.muted}>No shift to check in to right now.</Text>
      </View>
    )
  }

  const onDuty = shift.status === 'active'
  const action = onDuty ? 'end' : 'start'

  return (
    <View style={[styles.card, onDuty && styles.cardOnDuty]}>
      <View style={styles.headerRow}>
        <Text style={styles.heading}>{onDuty ? 'On duty' : 'Attendance'}</Text>
        {onDuty && (
          <View style={styles.liveDot}>
            <Text style={styles.liveText}>LIVE</Text>
          </View>
        )}
      </View>

      <Text style={styles.site}>{shift.site_name ?? 'Unassigned site'}</Text>
      <Text style={styles.muted}>
        {onDuty && shift.actual_start
          ? `Checked in ${time(shift.actual_start)} · ends ${time(shift.scheduled_end)}`
          : `${dayLabel(shift.scheduled_start)} ${time(shift.scheduled_start)} – ${time(shift.scheduled_end)}`}
      </Text>

      <Pressable
        style={[styles.button, onDuty ? styles.buttonEnd : styles.buttonStart]}
        onPress={() => begin(shift.id, action)}
        accessibilityRole="button"
        accessibilityLabel={onDuty ? 'Check out of this shift' : 'Check in to this shift'}
      >
        <Ionicons name={onDuty ? 'log-out-outline' : 'camera-outline'} size={20} color="#fff" />
        <Text style={styles.buttonText}>{onDuty ? 'Check Out' : 'Check In'}</Text>
      </Pressable>

      <View style={styles.noteRow}>
        <Ionicons name="location-outline" size={13} color={colors.textSecondary} />
        <Text style={styles.note}>Location and a selfie are recorded with your check-in.</Text>
      </View>

      <CheckInPhotoModal
        visible={flow !== null}
        title={flow?.action === 'start' ? 'Check-In Selfie' : 'Check-Out Selfie'}
        onClose={close}
        onConfirm={submit}
        confirming={submitting}
        errorMessage={error}
      />
    </View>
  )
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: colors.surface,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.cardBorder,
    padding: spacing.md,
    marginBottom: spacing.md,
  },
  cardOnDuty: { borderColor: colors.success },
  centre: { alignItems: 'center', justifyContent: 'center', minHeight: 120 },
  headerRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  heading: { fontSize: fontSize.md, fontWeight: '700', color: colors.text },
  liveDot: {
    backgroundColor: colors.success, borderRadius: radius.sm,
    paddingHorizontal: spacing.xs, paddingVertical: 2,
  },
  liveText: { fontSize: 10, fontWeight: '700', color: '#04120a' },
  site: { fontSize: fontSize.lg, fontWeight: '700', color: colors.text, marginTop: spacing.xs },
  muted: { fontSize: fontSize.sm, color: colors.textSecondary, marginTop: 2 },
  button: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: spacing.xs,
    borderRadius: radius.sm, paddingVertical: spacing.md, marginTop: spacing.md,
  },
  buttonStart: { backgroundColor: colors.primary },
  buttonEnd: { backgroundColor: colors.error },
  buttonText: { color: '#fff', fontWeight: '700', fontSize: fontSize.md },
  noteRow: { flexDirection: 'row', alignItems: 'center', gap: 4, marginTop: spacing.sm },
  note: { fontSize: fontSize.xs, color: colors.textSecondary, flex: 1 },
})
