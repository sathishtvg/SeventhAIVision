/**
 * Action Center — "what needs my attention right now".
 *
 * The server decides what a caller sees based on their role: a guard gets
 * their own duties (check in, patrol due, assigned incident, expiring
 * document), an ops user gets the control-room feed (guards to contact,
 * unacknowledged alerts, overdue checkpoints, offline cameras). This screen
 * renders whatever comes back rather than branching on role itself — the
 * authorization decision belongs on the server, and duplicating it here would
 * be a second source of truth that could drift.
 *
 * Items arrive pre-sorted by severity. Kept in that order deliberately: a
 * critical item must never sit below a low one because of local grouping.
 */
import React from 'react'
import {
  ActivityIndicator, FlatList, Linking, Pressable, RefreshControl,
  StyleSheet, Text, View,
} from 'react-native'
import { useQuery } from '@tanstack/react-query'
import { useNavigation } from '@react-navigation/native'
import { Ionicons } from '@expo/vector-icons'

import { getMyDuties, type ActionCategory, type ActionItem } from '@/api/actionCenter'
import { Card } from '@/components/Card'
import { colors, fontSize, radius, spacing } from '@/theme'

const CATEGORY_ICON: Record<ActionCategory, React.ComponentProps<typeof Ionicons>['name']> = {
  contact_guard: 'call-outline',
  ack_alert: 'warning-outline',
  overdue_checkpoint: 'scan-outline',
  pending_approval: 'checkmark-done-outline',
  camera_offline: 'videocam-off-outline',
  geofence_flag: 'location-outline',
  check_in: 'log-in-outline',
  patrol_due: 'walk-outline',
  respond_incident: 'alert-circle-outline',
  doc_expiry: 'document-text-outline',
}

function severityColour(sev: ActionItem['severity']): string {
  switch (sev) {
    case 'critical': return colors.error
    case 'high': return colors.warning
    case 'medium': return colors.info
    default: return colors.textSecondary
  }
}

/** Where tapping an item should take the guard/operator, when anywhere. */
function targetScreen(category: ActionCategory): string | null {
  switch (category) {
    case 'check_in': return 'Shift'
    case 'patrol_due':
    case 'overdue_checkpoint': return 'PatrolSelect'
    case 'respond_incident': return 'Incidents'
    case 'ack_alert': return 'Alerts'
    case 'camera_offline': return 'Cameras'
    case 'doc_expiry': return 'MyRecord'
    default: return null
  }
}

export function ActionCenterScreen() {
  const nav = useNavigation<any>()
  const { data, isLoading, isError, refetch, isRefetching } = useQuery({
    queryKey: ['action-center'],
    queryFn: getMyDuties,
  })

  const items: ActionItem[] = data?.items ?? []
  const summary = data?.summary

  if (isLoading) {
    return <View style={styles.center}><ActivityIndicator color={colors.primary} /></View>
  }
  if (isError) {
    return (
      <View style={styles.center}>
        <Ionicons name="cloud-offline-outline" size={40} color={colors.textSecondary} />
        <Text style={styles.errorText}>Couldn’t load your duties</Text>
        <Pressable style={styles.retryBtn} onPress={() => refetch()}>
          <Text style={styles.retryText}>Retry</Text>
        </Pressable>
      </View>
    )
  }

  return (
    <FlatList
      style={styles.container}
      data={items}
      keyExtractor={(i) => i.id}
      contentContainerStyle={styles.list}
      refreshControl={
        <RefreshControl refreshing={isRefetching} onRefresh={refetch} tintColor={colors.primary} />
      }
      ListHeaderComponent={
        summary && summary.total > 0 ? (
          <View style={styles.kpiRow}>
            <View style={styles.kpi}>
              <Text style={[styles.kpiNum, { color: colors.error }]}>{summary.critical}</Text>
              <Text style={styles.kpiLabel}>Critical</Text>
            </View>
            <View style={styles.kpi}>
              <Text style={[styles.kpiNum, { color: colors.warning }]}>{summary.high}</Text>
              <Text style={styles.kpiLabel}>High</Text>
            </View>
            <View style={styles.kpi}>
              <Text style={[styles.kpiNum, { color: colors.text }]}>{summary.total}</Text>
              <Text style={styles.kpiLabel}>Total</Text>
            </View>
          </View>
        ) : null
      }
      ListEmptyComponent={
        <Card>
          <View style={styles.emptyRow}>
            <Ionicons name="checkmark-circle" size={22} color={colors.success} />
            <Text style={styles.emptyText}>Nothing needs your attention right now.</Text>
          </View>
        </Card>
      }
      renderItem={({ item }) => {
        const dest = targetScreen(item.category)
        const colour = severityColour(item.severity)
        return (
          <Pressable
            onPress={() => { if (dest) nav.navigate(dest as never) }}
            disabled={!dest}
          >
            <Card>
              <View style={styles.row}>
                <View style={[styles.iconWrap, { backgroundColor: colour }]}>
                  <Ionicons
                    name={CATEGORY_ICON[item.category] ?? 'ellipse-outline'}
                    size={18}
                    color="#fff"
                  />
                </View>
                <View style={styles.body}>
                  <Text style={styles.title}>{item.title}</Text>
                  <Text style={styles.subtitle}>{item.subtitle}</Text>
                </View>
                {/* A phone number only appears on contact-type items, and it is
                    the whole point of those — surface it as a one-tap dial
                    rather than making an operator copy it out. */}
                {item.phone ? (
                  <Pressable
                    style={styles.callBtn}
                    onPress={() => Linking.openURL(`tel:${item.phone}`)}
                    hitSlop={8}
                  >
                    <Ionicons name="call" size={18} color="#fff" />
                  </Pressable>
                ) : dest ? (
                  <Ionicons name="chevron-forward" size={20} color={colors.textSecondary} />
                ) : null}
              </View>
            </Card>
          </Pressable>
        )
      }}
    />
  )
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: spacing.sm },
  list: { padding: spacing.md, gap: spacing.sm },
  kpiRow: { flexDirection: 'row', gap: spacing.sm, marginBottom: spacing.xs },
  kpi: {
    flex: 1, alignItems: 'center', backgroundColor: colors.surface,
    borderRadius: radius.md, paddingVertical: spacing.sm,
  },
  kpiNum: { fontSize: 24, fontWeight: '800' },
  kpiLabel: { color: colors.textSecondary, fontSize: fontSize.xs },
  row: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  iconWrap: {
    width: 34, height: 34, borderRadius: 17,
    alignItems: 'center', justifyContent: 'center',
  },
  body: { flex: 1 },
  title: { color: colors.text, fontSize: fontSize.md, fontWeight: '600' },
  subtitle: { color: colors.textSecondary, fontSize: fontSize.sm, marginTop: 2 },
  callBtn: {
    backgroundColor: colors.success, width: 34, height: 34, borderRadius: 17,
    alignItems: 'center', justifyContent: 'center',
  },
  emptyRow: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  emptyText: { color: colors.textSecondary, fontSize: fontSize.sm, flex: 1 },
  errorText: { color: colors.text, fontSize: fontSize.md },
  retryBtn: {
    borderColor: colors.primary, borderWidth: 1, borderRadius: radius.md,
    paddingHorizontal: spacing.lg, paddingVertical: spacing.xs,
  },
  retryText: { color: colors.primary, fontWeight: '600' },
})
