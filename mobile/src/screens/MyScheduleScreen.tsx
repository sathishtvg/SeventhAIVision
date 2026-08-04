/**
 * A guard's own upcoming schedule.
 *
 * Until now a guard could only see the shift they were currently on — there was
 * no way to answer "am I working Saturday?" from the phone, which is the single
 * most common thing a guard wants from a workforce app.
 *
 * Shifts are grouped by calendar day and split into upcoming vs past, because
 * "what's next" and "what did I work" are different questions and mixing them
 * into one reverse-chronological list answers neither well.
 */
import React, { useMemo } from 'react'
import {
  ActivityIndicator, FlatList, Pressable, RefreshControl, StyleSheet, Text, View,
} from 'react-native'
import { useQuery } from '@tanstack/react-query'
import { Ionicons } from '@expo/vector-icons'

import { getMyShifts, type Shift } from '@/api/patrols'
import { useAuthStore } from '@/store/auth'
import { Card } from '@/components/Card'
import { colors, fontSize, radius, spacing } from '@/theme'

function dayLabel(iso: string): string {
  const d = new Date(iso)
  const today = new Date()
  const tomorrow = new Date(today.getTime() + 86400000)
  const sameDay = (a: Date, b: Date) =>
    a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate()
  if (sameDay(d, today)) return 'Today'
  if (sameDay(d, tomorrow)) return 'Tomorrow'
  return d.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' })
}

function timeRange(startIso: string, endIso: string): string {
  const fmt = (s: string) =>
    new Date(s).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
  return `${fmt(startIso)} – ${fmt(endIso)}`
}

/** Hours between two ISO timestamps, one decimal. */
function durationHours(startIso: string, endIso: string): string {
  const h = (new Date(endIso).getTime() - new Date(startIso).getTime()) / 3_600_000
  return `${h.toFixed(h % 1 === 0 ? 0 : 1)}h`
}

export function MyScheduleScreen() {
  const userId = useAuthStore((s) => s.user?.id)

  const { data, isLoading, isError, refetch, isRefetching } = useQuery({
    // Guard id is part of the key: switching accounts must not show the
    // previous guard's schedule from cache.
    queryKey: ['my-shifts', userId],
    queryFn: () => getMyShifts(userId!),
    enabled: !!userId,
  })
  const shifts: Shift[] = data ?? []

  const { upcoming, past } = useMemo(() => {
    const now = Date.now()
    const up: Shift[] = []
    const pa: Shift[] = []
    for (const s of shifts) {
      // Classify on scheduled_end: a shift running right now is still
      // "upcoming" to the guard standing on it, not history.
      ;(new Date(s.scheduled_end).getTime() >= now ? up : pa).push(s)
    }
    up.sort((a, b) => +new Date(a.scheduled_start) - +new Date(b.scheduled_start))
    pa.sort((a, b) => +new Date(b.scheduled_start) - +new Date(a.scheduled_start))
    return { upcoming: up, past: pa.slice(0, 20) }
  }, [shifts])

  if (!userId) {
    return <View style={styles.center}><Text style={styles.muted}>Not signed in.</Text></View>
  }
  if (isLoading) {
    return <View style={styles.center}><ActivityIndicator color={colors.primary} /></View>
  }
  if (isError) {
    return (
      <View style={styles.center}>
        <Ionicons name="cloud-offline-outline" size={40} color={colors.textSecondary} />
        <Text style={styles.errorText}>Couldn’t load your schedule</Text>
        <Pressable style={styles.retryBtn} onPress={() => refetch()}>
          <Text style={styles.retryText}>Retry</Text>
        </Pressable>
      </View>
    )
  }

  const rows: Array<{ type: 'header'; label: string } | { type: 'shift'; shift: Shift }> = []
  if (upcoming.length) {
    rows.push({ type: 'header', label: 'Upcoming' })
    let lastDay = ''
    for (const s of upcoming) {
      const d = dayLabel(s.scheduled_start)
      if (d !== lastDay) { rows.push({ type: 'header', label: d }); lastDay = d }
      rows.push({ type: 'shift', shift: s })
    }
  }
  if (past.length) {
    rows.push({ type: 'header', label: 'Recent' })
    for (const s of past) rows.push({ type: 'shift', shift: s })
  }

  return (
    <FlatList
      style={styles.container}
      data={rows}
      keyExtractor={(r, i) => (r.type === 'shift' ? r.shift.id : `h-${i}`)}
      contentContainerStyle={styles.list}
      refreshControl={
        <RefreshControl refreshing={isRefetching} onRefresh={refetch} tintColor={colors.primary} />
      }
      ListEmptyComponent={
        <Card><Text style={styles.muted}>No shifts scheduled for you yet.</Text></Card>
      }
      renderItem={({ item }) => {
        if (item.type === 'header') {
          return <Text style={styles.header}>{item.label}</Text>
        }
        const s = item.shift
        const late = !!s.is_late && (s.late_minutes ?? 0) > 0
        return (
          <Card>
            <View style={styles.row}>
              <Text style={styles.site}>{s.site_name ?? 'Unassigned site'}</Text>
              <Text style={styles.duration}>{durationHours(s.scheduled_start, s.scheduled_end)}</Text>
            </View>
            <Text style={styles.time}>{timeRange(s.scheduled_start, s.scheduled_end)}</Text>
            <View style={styles.chipRow}>
              <View style={[styles.chip, { backgroundColor: statusColour(s.status) }]}>
                <Text style={styles.chipText}>{s.status}</Text>
              </View>
              {s.on_break && (
                <View style={[styles.chip, { backgroundColor: colors.info }]}>
                  <Text style={styles.chipText}>on break</Text>
                </View>
              )}
              {late && (
                <View style={[styles.chip, { backgroundColor: colors.warning }]}>
                  <Text style={styles.chipText}>{s.late_minutes}m late</Text>
                </View>
              )}
              {(s.overtime_minutes ?? 0) > 0 && (
                <View style={[styles.chip, { backgroundColor: colors.secondary }]}>
                  <Text style={styles.chipText}>+{s.overtime_minutes}m OT</Text>
                </View>
              )}
            </View>
          </Card>
        )
      }}
    />
  )
}

function statusColour(status: string): string {
  switch (status) {
    case 'active': return colors.success
    case 'completed': return colors.textDisabled
    case 'missed': return colors.error
    default: return colors.primary   // scheduled
  }
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: spacing.sm },
  list: { padding: spacing.md, gap: spacing.sm },
  header: {
    color: colors.textSecondary, fontSize: fontSize.sm, fontWeight: '700',
    marginTop: spacing.sm, textTransform: 'uppercase', letterSpacing: 0.5,
  },
  row: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  site: { color: colors.text, fontSize: fontSize.md, fontWeight: '600', flex: 1 },
  duration: { color: colors.textSecondary, fontSize: fontSize.sm },
  time: { color: colors.text, fontSize: fontSize.sm, marginTop: 2 },
  chipRow: { flexDirection: 'row', gap: spacing.xs, marginTop: spacing.xs, flexWrap: 'wrap' },
  chip: { paddingHorizontal: spacing.sm, paddingVertical: 2, borderRadius: radius.sm },
  chipText: { color: '#fff', fontSize: fontSize.xs, fontWeight: '700' },
  muted: { color: colors.textSecondary, fontSize: fontSize.sm },
  errorText: { color: colors.text, fontSize: fontSize.md },
  retryBtn: {
    borderColor: colors.primary, borderWidth: 1, borderRadius: radius.md,
    paddingHorizontal: spacing.lg, paddingVertical: spacing.xs,
  },
  retryText: { color: colors.primary, fontWeight: '600' },
})
