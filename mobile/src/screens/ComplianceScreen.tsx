/**
 * Tour compliance: what was due, what was done, what was missed.
 *
 * REBUILT AGAINST THE API THAT EXISTS. This screen was written for
 * /compliance/summary, /compliance/policies and /compliance/checks — three
 * routes the server has never had. Every request answered 404 and the screen
 * showed "No checks recorded", which reads as "all clear" rather than "this has
 * never worked once". Compliance in this system is patrol tours: schedules, and
 * the occurrences generated from them.
 *
 * MISSED TOURS ARE THE POINT, so they lead: a tour nobody walked is the finding
 * a supervisor is looking for, and it was the one thing the old screen could
 * never have shown.
 */
import { useCallback, useState } from 'react'
import {
  ActivityIndicator, FlatList, Pressable, RefreshControl, StyleSheet, Text, View,
} from 'react-native'
import { Ionicons } from '@expo/vector-icons'
import { useQuery, useQueryClient } from '@tanstack/react-query'

import {
  getComplianceDashboard, getTourOccurrences, getTourSchedules,
  type TourOccurrence, type TourSchedule,
} from '@/api/compliance'
import { Card } from '@/components/Card'
import { colors, fontSize, radius, spacing } from '@/theme'

/** The server's occurrence statuses. */
const STATUS_COLOR: Record<string, string> = {
  completed: colors.success,
  missed: colors.error,
  late: colors.warning,
  partial: colors.warning,
  incomplete: colors.warning,
  pending: colors.info,
}

const when = (iso: string | null) =>
  iso ? new Date(iso).toLocaleString([], {
    day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
  }) : '—'

function StatusPill({ status }: { status: string }) {
  return (
    <View style={[styles.pill, { backgroundColor: STATUS_COLOR[status] ?? colors.info }]}>
      <Text style={styles.pillText}>{status}</Text>
    </View>
  )
}

function OccurrenceRow({ item }: { item: TourOccurrence }) {
  return (
    <Card style={styles.row}>
      <View style={styles.rowHeader}>
        <Text style={styles.rowTitle} numberOfLines={1}>
          {item.schedule_name ?? item.route_name ?? 'Tour'}
        </Text>
        <StatusPill status={item.status} />
      </View>
      <Text style={styles.rowMeta}>Due {when(item.scheduled_at)}</Text>
      {!!item.site_name && <Text style={styles.rowMeta}>{item.site_name}</Text>}
      {!!item.assigned_guard_name && (
        <Text style={styles.rowMeta}>Assigned to {item.assigned_guard_name}</Text>
      )}
    </Card>
  )
}

function ScheduleRow({ item }: { item: TourSchedule }) {
  const rate = item.total_30d ? Math.round((item.completed_30d ?? 0) / item.total_30d * 100) : null
  return (
    <Card style={styles.row}>
      <View style={styles.rowHeader}>
        <Text style={styles.rowTitle} numberOfLines={1}>{item.name}</Text>
        {!item.is_active && <Text style={styles.inactive}>inactive</Text>}
      </View>
      <Text style={styles.rowMeta}>
        {[item.route_name, item.site_name].filter(Boolean).join(' · ') || 'No route'}
      </Text>
      <Text style={styles.rowMeta}>
        {item.scheduled_time ?? '—'}
        {item.recurrence ? ` · ${item.recurrence.toLowerCase()}` : ''}
        {item.window_minutes ? ` · ${item.window_minutes} min window` : ''}
      </Text>
      {rate !== null && (
        <Text style={[styles.rowMeta, { color: rate >= 90 ? colors.success : rate >= 70 ? colors.warning : colors.error }]}>
          {rate}% completed over 30 days ({item.completed_30d}/{item.total_30d})
        </Text>
      )}
    </Card>
  )
}

export function ComplianceScreen() {
  const qc = useQueryClient()
  const [tab, setTab] = useState<'today' | 'tours' | 'schedules'>('today')
  const [refreshing, setRefreshing] = useState(false)

  const { data: dash, isLoading: loadingDash } = useQuery({
    queryKey: ['compliance', 'dashboard'],
    queryFn: getComplianceDashboard,
  })
  const { data: occurrences = [], isLoading: loadingOcc } = useQuery({
    queryKey: ['compliance', 'occurrences'],
    queryFn: () => getTourOccurrences({ limit: 50 }),
    enabled: tab === 'tours',
  })
  const { data: schedules = [], isLoading: loadingSched } = useQuery({
    queryKey: ['compliance', 'schedules'],
    queryFn: () => getTourSchedules(),
    enabled: tab === 'schedules',
  })

  const onRefresh = useCallback(async () => {
    setRefreshing(true)
    await qc.invalidateQueries({ queryKey: ['compliance'] })
    setRefreshing(false)
  }, [qc])

  const loading = tab === 'today' ? loadingDash : tab === 'tours' ? loadingOcc : loadingSched

  const stats = dash ? [
    { label: 'Due today', value: dash.tours_today, color: colors.text },
    { label: 'Completed', value: dash.completed_today, color: colors.success },
    { label: 'Missed', value: dash.missed_today, color: dash.missed_today > 0 ? colors.error : colors.success },
    { label: 'Late', value: dash.late_today, color: dash.late_today > 0 ? colors.warning : colors.success },
    {
      label: '7-day rate',
      value: dash.compliance_rate_7d === null ? '—' : `${dash.compliance_rate_7d}%`,
      color: colors.info,
    },
    { label: 'Active schedules', value: dash.active_schedules, color: colors.text },
  ] : []

  return (
    <View style={styles.root}>
      <View style={styles.tabRow}>
        {(['today', 'tours', 'schedules'] as const).map((t) => (
          <Pressable key={t} style={[styles.tabBtn, tab === t && styles.tabActive]} onPress={() => setTab(t)}>
            <Text style={[styles.tabText, tab === t && styles.tabTextActive]}>
              {t === 'today' ? 'Today' : t === 'tours' ? 'Tours' : 'Schedules'}
            </Text>
          </Pressable>
        ))}
      </View>

      {loading ? (
        <View style={styles.center}><ActivityIndicator color={colors.primary} /></View>
      ) : tab === 'today' ? (
        <FlatList
          data={dash?.recent_missed ?? []}
          keyExtractor={(_, i) => String(i)}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.primary} />}
          contentContainerStyle={styles.content}
          ListHeaderComponent={
            <>
              <View style={styles.statGrid}>
                {stats.map((s) => (
                  <Card key={s.label} style={styles.statCard}>
                    <Text style={[styles.statValue, { color: s.color }]}>{s.value}</Text>
                    <Text style={styles.statLabel}>{s.label}</Text>
                  </Card>
                ))}
              </View>
              <Text style={styles.sectionTitle}>Recently missed</Text>
            </>
          }
          ListEmptyComponent={
            <View style={styles.center}>
              <Ionicons name="checkmark-done-outline" size={40} color={colors.textDisabled} />
              <Text style={styles.emptyText}>No missed tours.</Text>
            </View>
          }
          renderItem={({ item }) => (
            <Card style={styles.row}>
              <Text style={styles.rowTitle} numberOfLines={1}>
                {item.schedule_name ?? item.route_name ?? 'Tour'}
              </Text>
              <Text style={styles.rowMeta}>
                {[item.route_name, item.assigned_guard ? `assigned to ${item.assigned_guard}` : null]
                  .filter(Boolean).join(' · ') || 'Unassigned'}
              </Text>
              {!!item.scheduled_at && <Text style={styles.rowMeta}>Due {when(item.scheduled_at)}</Text>}
            </Card>
          )}
        />
      ) : tab === 'tours' ? (
        <FlatList
          data={occurrences}
          keyExtractor={(o) => o.id}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.primary} />}
          contentContainerStyle={styles.content}
          ListEmptyComponent={
            <View style={styles.center}>
              <Ionicons name="footsteps-outline" size={40} color={colors.textDisabled} />
              <Text style={styles.emptyText}>No tours in this window.</Text>
            </View>
          }
          renderItem={({ item }) => <OccurrenceRow item={item} />}
        />
      ) : (
        <FlatList
          data={schedules}
          keyExtractor={(s) => s.id}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.primary} />}
          contentContainerStyle={styles.content}
          ListEmptyComponent={
            <View style={styles.center}>
              <Ionicons name="calendar-outline" size={40} color={colors.textDisabled} />
              <Text style={styles.emptyText}>No tour schedules yet.</Text>
            </View>
          }
          renderItem={({ item }) => <ScheduleRow item={item} />}
        />
      )}
    </View>
  )
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.background },
  content: { padding: spacing.md, gap: spacing.sm },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: spacing.xl, gap: spacing.sm },
  emptyText: { color: colors.textSecondary, fontSize: fontSize.sm },
  tabRow: { flexDirection: 'row', padding: spacing.sm, gap: spacing.xs },
  tabBtn: {
    flex: 1, alignItems: 'center', paddingVertical: spacing.sm,
    borderRadius: radius.sm, backgroundColor: colors.surface,
  },
  tabActive: { backgroundColor: colors.primary },
  tabText: { color: colors.textSecondary, fontWeight: '600', fontSize: fontSize.sm },
  tabTextActive: { color: '#fff' },
  statGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm, marginBottom: spacing.md },
  statCard: { flexBasis: '31%', flexGrow: 1, alignItems: 'center', paddingVertical: spacing.md },
  statValue: { fontSize: fontSize.xl, fontWeight: '700' },
  statLabel: { fontSize: fontSize.xs, color: colors.textSecondary, marginTop: 2, textAlign: 'center' },
  sectionTitle: {
    fontSize: fontSize.sm, fontWeight: '700', color: colors.text, marginBottom: spacing.xs,
  },
  row: { padding: spacing.md },
  rowHeader: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: spacing.sm },
  rowTitle: { flex: 1, fontSize: fontSize.md, fontWeight: '700', color: colors.text },
  rowMeta: { fontSize: fontSize.sm, color: colors.textSecondary, marginTop: 2 },
  inactive: { fontSize: fontSize.xs, color: colors.textDisabled, textTransform: 'uppercase' },
  pill: { borderRadius: radius.sm, paddingHorizontal: spacing.xs, paddingVertical: 2 },
  pillText: { fontSize: 10, fontWeight: '700', color: '#04120a', textTransform: 'uppercase' },
})
