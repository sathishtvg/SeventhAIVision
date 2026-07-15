import React, { useCallback, useState } from 'react'
import {
  ActivityIndicator, FlatList, Pressable, RefreshControl, ScrollView,
  StyleSheet, Text, View,
} from 'react-native'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigation } from '@react-navigation/native'
import { Ionicons } from '@expo/vector-icons'
import { getSummary } from '@/api/dashboard'
import { getMyDuties, ActionItem } from '@/api/actionCenter'
import { Card } from '@/components/Card'
import { SeverityBadge } from '@/components/SeverityBadge'
import { StatusBadge } from '@/components/StatusBadge'
import { useWebSocket, RealtimeEvent } from '@/hooks/useWebSocket'
import { useAuthStore } from '@/store/auth'
import { colors, fontSize, spacing, radius, severity as severityColors } from '@/theme'

const GUARD_ROLE_ID = 5

const DUTY_ICON: Record<ActionItem['category'], React.ComponentProps<typeof Ionicons>['name']> = {
  contact_guard: 'call-outline',
  ack_alert: 'alert-circle-outline',
  overdue_checkpoint: 'footsteps-outline',
  pending_approval: 'checkmark-done-outline',
  camera_offline: 'videocam-off-outline',
  geofence_flag: 'location-outline',
  check_in: 'log-in-outline',
  patrol_due: 'footsteps-outline',
  respond_incident: 'warning-outline',
  doc_expiry: 'document-text-outline',
}

function navigateToDuty(navigation: any, item: ActionItem) {
  switch (item.category) {
    case 'check_in':
    case 'patrol_due':
      navigation.navigate('Patrol', { screen: 'Shifts' })
      break
    case 'respond_incident':
      navigation.navigate('Incidents', { screen: 'IncidentDetail', params: { incidentId: item.entity_id } })
      break
    case 'doc_expiry':
      navigation.navigate('More', { screen: 'MyRecord' })
      break
    default:
      break
  }
}

function DutyRow({ item, onPress }: { item: ActionItem; onPress: () => void }) {
  const color = severityColors[item.severity] ?? colors.primary
  return (
    <Pressable onPress={onPress}>
      <Card style={styles.dutyCard}>
        <View style={[styles.dutyIconBg, { backgroundColor: color + '28', borderColor: color + '60' }]}>
          <Ionicons name={DUTY_ICON[item.category]} size={16} color={color} />
        </View>
        <View style={styles.dutyTextWrap}>
          <Text style={styles.dutyTitle} numberOfLines={2}>{item.title}</Text>
          <Text style={styles.dutySubtitle} numberOfLines={1}>{item.subtitle}</Text>
        </View>
        <Ionicons name="chevron-forward" size={16} color={colors.textDisabled} />
      </Card>
    </Pressable>
  )
}

interface KpiProps {
  icon: React.ComponentProps<typeof Ionicons>['name']
  label: string
  value: number | string
  color?: string
  onPress?: () => void
}

function KpiCard({ icon, label, value, color, onPress }: KpiProps) {
  const accentColor = color ?? colors.primary
  return (
    <Pressable style={styles.kpiPressable} onPress={onPress} disabled={!onPress}>
      <Card style={styles.kpiCard}>
        <View style={[styles.kpiIconBg, { backgroundColor: accentColor + '28', borderColor: accentColor + '60' }]}>
          <Ionicons name={icon} size={20} color={accentColor} />
        </View>
        <Text style={styles.kpiValue}>{value}</Text>
        <Text style={styles.kpiLabel}>{label}</Text>
      </Card>
    </Pressable>
  )
}

interface LiveEvent {
  id: string
  type: string
  title: string
  severity?: string
  status?: string
  ts: string
}

export function DashboardScreen() {
  const qc = useQueryClient()
  const navigation = useNavigation<any>()
  const roleId = useAuthStore((s) => s.user?.roleId)
  const [liveEvents, setLiveEvents] = useState<LiveEvent[]>([])
  const [refreshing, setRefreshing] = useState(false)

  const { data: summary, isLoading } = useQuery({
    queryKey: ['dashboard-summary'],
    queryFn: getSummary,
    refetchInterval: 30_000,
  })

  const { data: duties } = useQuery({
    queryKey: ['my-duties'],
    queryFn: getMyDuties,
    enabled: roleId === GUARD_ROLE_ID,
    refetchInterval: 30_000,
  })

  const handleEvent = useCallback((e: RealtimeEvent) => {
    setLiveEvents((prev) => {
      const entry: LiveEvent = {
        id: String(e.payload.id ?? Date.now()),
        type: e.event_type,
        title: (e.payload.title as string) ?? e.event_type.replace(/_/g, ' '),
        severity: e.payload.severity as string | undefined,
        status: e.payload.status as string | undefined,
        ts: e.occurred_at,
      }
      return [entry, ...prev].slice(0, 20)
    })
    if (e.event_type === 'alert_created') {
      qc.invalidateQueries({ queryKey: ['alerts'] })
      qc.invalidateQueries({ queryKey: ['dashboard-summary'] })
    }
    if (e.event_type === 'incident_created') {
      qc.invalidateQueries({ queryKey: ['incidents'] })
      qc.invalidateQueries({ queryKey: ['dashboard-summary'] })
    }
    if (e.event_type === 'camera_status_changed') {
      qc.invalidateQueries({ queryKey: ['cameras'] })
    }
    if (e.event_type === 'attendance_status_changed' || e.event_type === 'incident_created') {
      qc.invalidateQueries({ queryKey: ['my-duties'] })
    }
  }, [qc])

  useWebSocket(handleEvent)

  const onRefresh = useCallback(async () => {
    setRefreshing(true)
    await qc.invalidateQueries({ queryKey: ['dashboard-summary'] })
    await qc.invalidateQueries({ queryKey: ['my-duties'] })
    setRefreshing(false)
  }, [qc])

  if (isLoading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={colors.primary} size="large" />
      </View>
    )
  }

  return (
    <ScrollView
      style={styles.root}
      contentContainerStyle={styles.content}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.primary} />}
    >
      {/* Your Duties — guard-only reminder feed, self-scoped server-side */}
      {roleId === GUARD_ROLE_ID && duties && duties.items.length > 0 && (
        <>
          <View style={styles.sectionHeader}>
            <Text style={styles.sectionTitle}>Your Duties</Text>
            <View style={[styles.liveDot, styles.liveDotActive]} />
          </View>
          <View style={styles.dutyList}>
            {duties.items.map((item: ActionItem) => (
              <DutyRow key={item.id} item={item} onPress={() => navigateToDuty(navigation, item)} />
            ))}
          </View>
        </>
      )}

      {/* Overview section */}
      <View style={styles.sectionHeader}>
        <Text style={styles.sectionTitle}>Overview</Text>
        <View style={styles.sectionDot} />
      </View>

      <View style={styles.kpiGrid}>
        <KpiCard icon="alert-circle" label="Open Alerts"     value={summary?.open_alerts ?? 0}    color={colors.error}
          onPress={() => navigation.navigate('Alerts')} />
        <KpiCard icon="warning"      label="Open Incidents"  value={summary?.open_incidents ?? 0} color={colors.warning}
          onPress={() => navigation.navigate('Incidents')} />
        <KpiCard icon="videocam"     label="Cameras Online"  value={summary?.active_cameras ?? 0} color={colors.success}
          onPress={() => navigation.navigate('Cameras')} />
        <KpiCard icon="scan"         label="Detections Today" value={summary?.detections_today ?? 0} color={colors.primary}
          onPress={() => navigation.navigate('More', { screen: 'Detections' })} />
      </View>

      {/* Live feed section */}
      <View style={styles.sectionHeader}>
        <Text style={styles.sectionTitle}>Live Feed</Text>
        <View style={[styles.liveDot, liveEvents.length > 0 && styles.liveDotActive]} />
      </View>

      {liveEvents.length === 0 ? (
        <Card>
          <View style={styles.emptyFeed}>
            <Ionicons name="radio-outline" size={28} color={colors.textDisabled} />
            <Text style={styles.emptyText}>Waiting for live events…</Text>
          </View>
        </Card>
      ) : (
        <FlatList
          data={liveEvents}
          keyExtractor={(item) => item.id + item.ts}
          scrollEnabled={false}
          renderItem={({ item }) => {
            const onPress = item.type === 'alert_created'
              ? () => navigation.navigate('Alerts', { screen: 'AlertDetail', params: { alertId: item.id } })
              : item.type === 'incident_created'
              ? () => navigation.navigate('Incidents', { screen: 'IncidentDetail', params: { incidentId: item.id } })
              : item.type === 'camera_status_changed'
              ? () => navigation.navigate('Cameras')
              : undefined
            return (
              <Pressable onPress={onPress} disabled={!onPress}>
                <Card style={styles.eventCard}>
                  <View style={styles.eventRow}>
                    <View style={styles.eventTypeIndicator}>
                      <Ionicons
                        name={
                          item.type === 'alert_created' ? 'alert-circle-outline'
                            : item.type === 'incident_created' ? 'warning-outline'
                            : 'videocam-outline'
                        }
                        size={14}
                        color={colors.textSecondary}
                      />
                    </View>
                    <Text style={styles.eventTitle} numberOfLines={1}>{item.title}</Text>
                    <Text style={styles.eventTime}>
                      {new Date(item.ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                    </Text>
                  </View>
                  <View style={styles.badgeRow}>
                    {item.severity && <SeverityBadge value={item.severity} />}
                    {item.status && <StatusBadge value={item.status} />}
                  </View>
                </Card>
              </Pressable>
            )
          }}
          ItemSeparatorComponent={() => <View style={{ height: spacing.xs }} />}
        />
      )}
    </ScrollView>
  )
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: colors.background,
  },
  content: {
    padding: spacing.md,
    paddingBottom: spacing.xxl,
  },
  center: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.background,
  },
  sectionHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.xs,
    marginBottom: spacing.sm,
    marginTop: spacing.md,
  },
  sectionTitle: {
    fontSize: fontSize.lg,
    fontWeight: '700',
    color: colors.text,
    letterSpacing: -0.3,
  },
  sectionDot: {
    width: 6,
    height: 6,
    borderRadius: 3,
    backgroundColor: colors.primary,
    opacity: 0.6,
    marginTop: 1,
  },
  liveDot: {
    width: 7,
    height: 7,
    borderRadius: 3.5,
    backgroundColor: colors.textDisabled,
    marginTop: 1,
  },
  liveDotActive: {
    backgroundColor: colors.success,
  },
  kpiGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.sm,
    marginBottom: spacing.xs,
  },
  kpiPressable: {
    flex: 1,
    minWidth: '45%',
  },
  kpiCard: {
    alignItems: 'center',
    paddingVertical: spacing.lg,
  },
  kpiIconBg: {
    width: 44,
    height: 44,
    borderRadius: 13,
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: spacing.sm,
    borderWidth: 1,
  },
  kpiValue: {
    fontSize: fontSize.xxl,
    fontWeight: '800',
    color: colors.text,
    letterSpacing: -0.5,
  },
  kpiLabel: {
    fontSize: fontSize.xs,
    color: colors.textSecondary,
    marginTop: 2,
    textAlign: 'center',
  },
  eventCard: {
    paddingVertical: spacing.sm,
  },
  eventRow: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 4,
    gap: spacing.xs,
  },
  eventTypeIndicator: {
    width: 22,
    height: 22,
    borderRadius: radius.xs,
    backgroundColor: 'rgba(255,255,255,0.06)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  eventTitle: {
    flex: 1,
    fontSize: fontSize.sm,
    fontWeight: '600',
    color: colors.text,
  },
  eventTime: {
    fontSize: fontSize.xs,
    color: colors.textSecondary,
  },
  badgeRow: {
    flexDirection: 'row',
    gap: spacing.xs,
    flexWrap: 'wrap',
    marginLeft: 26,
  },
  emptyFeed: {
    alignItems: 'center',
    gap: spacing.sm,
    paddingVertical: spacing.md,
  },
  emptyText: {
    color: colors.textSecondary,
    fontSize: fontSize.sm,
    textAlign: 'center',
  },
  dutyList: {
    gap: spacing.xs,
    marginBottom: spacing.xs,
  },
  dutyCard: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    paddingVertical: spacing.sm,
  },
  dutyIconBg: {
    width: 34,
    height: 34,
    borderRadius: radius.sm,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1,
    flexShrink: 0,
  },
  dutyTextWrap: {
    flex: 1,
    minWidth: 0,
  },
  dutyTitle: {
    fontSize: fontSize.sm,
    fontWeight: '700',
    color: colors.text,
  },
  dutySubtitle: {
    fontSize: fontSize.xs,
    color: colors.textSecondary,
    marginTop: 1,
  },
})
