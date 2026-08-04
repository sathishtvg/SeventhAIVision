/**
 * Live attendance monitor.
 *
 * Answers one question fast: who should be on duty right now, and who isn't
 * where they said they'd be. Guards needing a phone call sort to the top with
 * a one-tap dial, because that is the only actionable thing on the screen —
 * everyone else is simply working.
 *
 * Read-only. Corrections and approvals stay on the web, where there is room to
 * review a photo and a geofence reading before altering someone's attendance
 * record — that is not a decision to make one-handed on a phone.
 */
import React, { useMemo, useState } from 'react'
import {
  ActivityIndicator, FlatList, Linking, Pressable, RefreshControl,
  StyleSheet, Text, View,
} from 'react-native'
import { useQuery } from '@tanstack/react-query'
import { Ionicons } from '@expo/vector-icons'

import { getLiveAttendance, type LiveAttendanceShift } from '@/api/attendance'
import { Card } from '@/components/Card'
import { colors, fontSize, radius, spacing } from '@/theme'

const STATUS_META: Record<
  LiveAttendanceShift['live_status'],
  { label: string; colour: string }
> = {
  checked_in:  { label: 'On duty',        colour: colors.success },
  on_break:    { label: 'On break',       colour: colors.info },
  late:        { label: 'Late',           colour: colors.warning },
  not_started: { label: 'Not checked in', colour: colors.error },
  checked_out: { label: 'Finished',       colour: colors.textDisabled },
}

function hhmm(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
}

export function AttendanceScreen() {
  const [onlyAttention, setOnlyAttention] = useState(false)

  const { data, isLoading, isError, refetch, isRefetching } = useQuery({
    queryKey: ['attendance-live'],
    queryFn: () => getLiveAttendance(),
    // Attendance changes minute to minute during a shift handover; a stale
    // board is worse than a slow one here.
    refetchInterval: 60_000,
  })

  const shifts: LiveAttendanceShift[] = data?.shifts ?? []
  const summary = data?.summary

  const rows = useMemo(() => {
    // "Needs attention" = late, or should have started and hasn't. Those sort
    // first; everything else keeps its server order.
    const rank = (s: LiveAttendanceShift) =>
      s.live_status === 'late' || s.live_status === 'not_started' ? 0 : 1
    const filtered = onlyAttention ? shifts.filter((s) => rank(s) === 0) : shifts
    return [...filtered].sort((a, b) => rank(a) - rank(b))
  }, [shifts, onlyAttention])

  const attentionCount = shifts.filter(
    (s) => s.live_status === 'late' || s.live_status === 'not_started',
  ).length

  if (isLoading) {
    return <View style={styles.center}><ActivityIndicator color={colors.primary} /></View>
  }
  if (isError) {
    return (
      <View style={styles.center}>
        <Ionicons name="cloud-offline-outline" size={40} color={colors.textSecondary} />
        <Text style={styles.errorText}>Couldn’t load attendance</Text>
        <Pressable style={styles.retryBtn} onPress={() => refetch()}>
          <Text style={styles.retryText}>Retry</Text>
        </Pressable>
      </View>
    )
  }

  return (
    <FlatList
      style={styles.container}
      data={rows}
      keyExtractor={(s) => s.id}
      contentContainerStyle={styles.list}
      refreshControl={
        <RefreshControl refreshing={isRefetching} onRefresh={refetch} tintColor={colors.primary} />
      }
      ListHeaderComponent={
        <View>
          {summary && (
            <View style={styles.kpiRow}>
              <View style={styles.kpi}>
                <Text style={[styles.kpiNum, { color: colors.success }]}>{summary.checked_in}</Text>
                <Text style={styles.kpiLabel}>On duty</Text>
              </View>
              <View style={styles.kpi}>
                <Text style={[styles.kpiNum, { color: colors.info }]}>{summary.on_break}</Text>
                <Text style={styles.kpiLabel}>Break</Text>
              </View>
              <View style={styles.kpi}>
                <Text style={[styles.kpiNum, { color: colors.warning }]}>{summary.late}</Text>
                <Text style={styles.kpiLabel}>Late</Text>
              </View>
              <View style={styles.kpi}>
                <Text style={[styles.kpiNum, { color: colors.error }]}>{summary.not_started}</Text>
                <Text style={styles.kpiLabel}>No show</Text>
              </View>
            </View>
          )}
          {attentionCount > 0 && (
            <Pressable
              style={[styles.filterBtn, onlyAttention && styles.filterBtnOn]}
              onPress={() => setOnlyAttention((v) => !v)}
            >
              <Ionicons
                name={onlyAttention ? 'funnel' : 'funnel-outline'}
                size={16}
                color={onlyAttention ? '#fff' : colors.warning}
              />
              <Text style={[styles.filterText, onlyAttention && { color: '#fff' }]}>
                {attentionCount} need{attentionCount === 1 ? 's' : ''} attention
              </Text>
            </Pressable>
          )}
        </View>
      }
      ListEmptyComponent={
        <Card>
          <Text style={styles.muted}>
            {onlyAttention ? 'Nobody needs chasing right now.' : 'No shifts scheduled today.'}
          </Text>
        </Card>
      }
      renderItem={({ item }) => {
        const meta = STATUS_META[item.live_status]
        const needsCall =
          (item.live_status === 'late' || item.live_status === 'not_started') && !!item.guard_phone
        return (
          <Card>
            <View style={styles.row}>
              <View style={styles.body}>
                <Text style={styles.name}>{item.guard_name ?? 'Unassigned'}</Text>
                <Text style={styles.meta}>
                  {item.site_name ?? 'No site'} · {hhmm(item.scheduled_start)}–{hhmm(item.scheduled_end)}
                </Text>
                <View style={styles.chipRow}>
                  <View style={[styles.chip, { backgroundColor: meta.colour }]}>
                    <Text style={styles.chipText}>{meta.label}</Text>
                  </View>
                  {item.is_late && (item.late_minutes ?? 0) > 0 && (
                    <View style={[styles.chip, { backgroundColor: colors.warning }]}>
                      <Text style={styles.chipText}>{item.late_minutes}m late</Text>
                    </View>
                  )}
                  {item.is_within_geofence === false && (
                    <View style={[styles.chip, { backgroundColor: colors.error }]}>
                      <Text style={styles.chipText}>outside geofence</Text>
                    </View>
                  )}
                  {/* Surfaced loudly: the server already refuses a check-in
                      flagged as mock GPS, so seeing this at all means someone
                      tried. */}
                  {item.check_in_is_mock_location === true && (
                    <View style={[styles.chip, { backgroundColor: colors.error }]}>
                      <Text style={styles.chipText}>mock GPS</Text>
                    </View>
                  )}
                </View>
              </View>
              {needsCall && (
                <Pressable
                  style={styles.callBtn}
                  onPress={() => Linking.openURL(`tel:${item.guard_phone}`)}
                  hitSlop={8}
                >
                  <Ionicons name="call" size={18} color="#fff" />
                </Pressable>
              )}
            </View>
          </Card>
        )
      }}
    />
  )
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: spacing.sm },
  list: { padding: spacing.md, gap: spacing.sm },
  kpiRow: { flexDirection: 'row', gap: spacing.xs, marginBottom: spacing.sm },
  kpi: {
    flex: 1, alignItems: 'center', backgroundColor: colors.surface,
    borderRadius: radius.md, paddingVertical: spacing.sm,
  },
  kpiNum: { fontSize: 22, fontWeight: '800' },
  kpiLabel: { color: colors.textSecondary, fontSize: fontSize.xs },
  filterBtn: {
    flexDirection: 'row', alignItems: 'center', gap: spacing.xs,
    alignSelf: 'flex-start', borderColor: colors.warning, borderWidth: 1,
    borderRadius: radius.md, paddingHorizontal: spacing.sm, paddingVertical: 4,
    marginBottom: spacing.sm,
  },
  filterBtnOn: { backgroundColor: colors.warning },
  filterText: { color: colors.warning, fontSize: fontSize.xs, fontWeight: '700' },
  row: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  body: { flex: 1 },
  name: { color: colors.text, fontSize: fontSize.md, fontWeight: '600' },
  meta: { color: colors.textSecondary, fontSize: fontSize.sm, marginTop: 2 },
  chipRow: { flexDirection: 'row', gap: spacing.xs, marginTop: spacing.xs, flexWrap: 'wrap' },
  chip: { paddingHorizontal: spacing.sm, paddingVertical: 2, borderRadius: radius.sm },
  chipText: { color: '#fff', fontSize: fontSize.xs, fontWeight: '700' },
  callBtn: {
    backgroundColor: colors.success, width: 36, height: 36, borderRadius: 18,
    alignItems: 'center', justifyContent: 'center',
  },
  muted: { color: colors.textSecondary, fontSize: fontSize.sm },
  errorText: { color: colors.text, fontSize: fontSize.md },
  retryBtn: {
    borderColor: colors.primary, borderWidth: 1, borderRadius: radius.md,
    paddingHorizontal: spacing.lg, paddingVertical: spacing.xs,
  },
  retryText: { color: colors.primary, fontWeight: '600' },
})
