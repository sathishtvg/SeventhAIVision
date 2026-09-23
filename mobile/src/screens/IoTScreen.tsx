/**
 * IoT sensors and their open alerts.
 *
 * REBUILT AGAINST THE REAL API. This screen asked for /iot/devices and
 * /iot/readings, which the server has never had, and rendered a device with a
 * battery percentage and a firmware version that no table stores. Both calls
 * answered 404, and the screen said "No devices" — indistinguishable from an
 * estate that has none.
 *
 * WHAT THE SERVER ACTUALLY HAS is sensors with a type, a unit, thresholds and a
 * current status of normal / warning / critical / offline, plus alerts raised
 * against them. Readings belong to one sensor, so they are shown by opening a
 * sensor rather than as a global feed that was never served.
 *
 * SENSORS THAT NEED ATTENTION SORT FIRST: critical, then warning, then offline.
 * A list in insertion order buries the one reading that matters.
 */
import { useCallback, useMemo, useState } from 'react'
import {
  ActivityIndicator, FlatList, Pressable, RefreshControl, StyleSheet, Text, View,
} from 'react-native'
import { Ionicons } from '@expo/vector-icons'
import { useQuery, useQueryClient } from '@tanstack/react-query'

import {
  getIoTAlerts, getIoTDashboard, getSensorReadings,
  type IoTSensor, type SensorStatus,
} from '@/api/iot'
import { Card } from '@/components/Card'
import { colors, fontSize, radius, spacing } from '@/theme'

const STATUS_COLOR: Record<SensorStatus, string> = {
  normal: colors.success,
  warning: colors.warning,
  critical: colors.error,
  offline: colors.textDisabled,
  unknown: colors.textDisabled,
}

/** Worst first. Exported for the test: a critical sensor sorted below a normal
 *  one is a reading nobody sees. */
export function sensorsByUrgency(sensors: IoTSensor[]): IoTSensor[] {
  const rank: Record<string, number> = { critical: 0, warning: 1, offline: 2, unknown: 3, normal: 4 }
  return [...sensors].sort((a, b) =>
    (rank[a.current_status] ?? 9) - (rank[b.current_status] ?? 9) ||
    a.name.localeCompare(b.name))
}

const ago = (iso: string | null) => {
  if (!iso) return 'no reading yet'
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins} min ago`
  const hrs = Math.round(mins / 60)
  return hrs < 24 ? `${hrs} h ago` : `${Math.round(hrs / 24)} d ago`
}

function SensorRow({ sensor, onPress, expanded, readings }: {
  sensor: IoTSensor
  onPress: () => void
  expanded: boolean
  readings: { id: string; value: number; recorded_at: string }[]
}) {
  return (
    <Card style={styles.row}>
      <Pressable onPress={onPress} accessibilityRole="button">
        <View style={styles.rowHeader}>
          <Text style={styles.name} numberOfLines={1}>{sensor.name}</Text>
          <View style={[styles.pill, { backgroundColor: STATUS_COLOR[sensor.current_status] }]}>
            <Text style={styles.pillText}>{sensor.current_status}</Text>
          </View>
        </View>
        <Text style={styles.meta}>
          {[sensor.sensor_type, sensor.location, sensor.site_name].filter(Boolean).join(' · ')}
        </Text>
        <View style={styles.readingRow}>
          <Text style={styles.reading}>
            {sensor.last_reading_value ?? '—'}
            {sensor.unit ? ` ${sensor.unit}` : ''}
          </Text>
          <Text style={styles.meta}>{ago(sensor.last_reading_at)}</Text>
          {sensor.open_alerts > 0 && (
            <View style={styles.alertBadge}>
              <Ionicons name="warning" size={11} color="#1a1205" />
              <Text style={styles.alertBadgeText}>{sensor.open_alerts}</Text>
            </View>
          )}
        </View>
      </Pressable>

      {expanded && (
        <View style={styles.readings}>
          {readings.length === 0 ? (
            <Text style={styles.meta}>No recent readings.</Text>
          ) : readings.slice(0, 8).map((r) => (
            <View key={r.id} style={styles.readingLine}>
              <Text style={styles.meta}>
                {new Date(r.recorded_at).toLocaleString([], {
                  day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
                })}
              </Text>
              <Text style={styles.readingValue}>{r.value}{sensor.unit ? ` ${sensor.unit}` : ''}</Text>
            </View>
          ))}
        </View>
      )}
    </Card>
  )
}

export function IoTScreen() {
  const qc = useQueryClient()
  const [tab, setTab] = useState<'sensors' | 'alerts'>('sensors')
  const [openSensor, setOpenSensor] = useState<string | null>(null)
  const [refreshing, setRefreshing] = useState(false)

  const { data: dashboard, isLoading } = useQuery({
    queryKey: ['iot-dashboard'],
    queryFn: () => getIoTDashboard(),
  })
  const { data: alerts = [], isLoading: loadingAlerts } = useQuery({
    queryKey: ['iot-alerts'],
    queryFn: () => getIoTAlerts({ status: 'open', limit: 50 }),
    enabled: tab === 'alerts',
  })
  const { data: readings = [] } = useQuery({
    queryKey: ['iot-readings', openSensor],
    queryFn: () => getSensorReadings(openSensor!),
    enabled: !!openSensor,
  })

  const sensors = useMemo(() => sensorsByUrgency(dashboard?.sensors ?? []), [dashboard])

  const onRefresh = useCallback(async () => {
    setRefreshing(true)
    await qc.invalidateQueries({ queryKey: ['iot-dashboard'] })
    await qc.invalidateQueries({ queryKey: ['iot-alerts'] })
    setRefreshing(false)
  }, [qc])

  const s = dashboard?.summary
  const stats = s ? [
    { label: 'Sensors', value: s.total, color: colors.text },
    { label: 'Normal', value: s.normal, color: colors.success },
    { label: 'Warning', value: s.warning, color: s.warning > 0 ? colors.warning : colors.textSecondary },
    { label: 'Critical', value: s.critical, color: s.critical > 0 ? colors.error : colors.textSecondary },
    { label: 'Offline', value: s.offline, color: s.offline > 0 ? colors.textDisabled : colors.textSecondary },
    { label: 'Open alerts', value: s.open_alerts, color: s.open_alerts > 0 ? colors.error : colors.success },
  ] : []

  return (
    <View style={styles.root}>
      <View style={styles.tabRow}>
        {(['sensors', 'alerts'] as const).map((t) => (
          <Pressable key={t} style={[styles.tabBtn, tab === t && styles.tabActive]} onPress={() => setTab(t)}>
            <Text style={[styles.tabText, tab === t && styles.tabTextActive]}>
              {t === 'sensors' ? 'Sensors' : 'Alerts'}
            </Text>
          </Pressable>
        ))}
      </View>

      {(tab === 'sensors' ? isLoading : loadingAlerts) ? (
        <View style={styles.center}><ActivityIndicator color={colors.primary} /></View>
      ) : tab === 'sensors' ? (
        <FlatList
          data={sensors}
          keyExtractor={(x) => x.id}
          contentContainerStyle={styles.content}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.primary} />}
          ListHeaderComponent={
            stats.length > 0 ? (
              <View style={styles.statGrid}>
                {stats.map((x) => (
                  <Card key={x.label} style={styles.statCard}>
                    <Text style={[styles.statValue, { color: x.color }]}>{x.value}</Text>
                    <Text style={styles.statLabel}>{x.label}</Text>
                  </Card>
                ))}
              </View>
            ) : null
          }
          ListEmptyComponent={
            <View style={styles.center}>
              <Ionicons name="hardware-chip-outline" size={40} color={colors.textDisabled} />
              <Text style={styles.emptyText}>No sensors configured.</Text>
            </View>
          }
          renderItem={({ item }) => (
            <SensorRow
              sensor={item}
              expanded={openSensor === item.id}
              readings={openSensor === item.id ? readings : []}
              onPress={() => setOpenSensor(openSensor === item.id ? null : item.id)}
            />
          )}
        />
      ) : (
        <FlatList
          data={alerts}
          keyExtractor={(a) => a.id}
          contentContainerStyle={styles.content}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.primary} />}
          ListEmptyComponent={
            <View style={styles.center}>
              <Ionicons name="checkmark-circle-outline" size={40} color={colors.textDisabled} />
              <Text style={styles.emptyText}>No open sensor alerts.</Text>
            </View>
          }
          renderItem={({ item }) => (
            <Card style={styles.row}>
              <View style={styles.rowHeader}>
                <Text style={styles.name} numberOfLines={1}>{item.sensor_name ?? 'Sensor'}</Text>
                <View style={[styles.pill, { backgroundColor: item.severity === 'critical' ? colors.error : colors.warning }]}>
                  <Text style={styles.pillText}>{item.severity}</Text>
                </View>
              </View>
              {!!item.message && <Text style={styles.meta}>{item.message}</Text>}
              <Text style={styles.meta}>{new Date(item.created_at).toLocaleString()}</Text>
            </Card>
          )}
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
  row: { padding: spacing.md },
  rowHeader: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: spacing.sm },
  name: { flex: 1, fontSize: fontSize.md, fontWeight: '700', color: colors.text },
  meta: { fontSize: fontSize.sm, color: colors.textSecondary, marginTop: 2 },
  readingRow: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, marginTop: spacing.xs },
  reading: { fontSize: fontSize.lg, fontWeight: '700', color: colors.text },
  alertBadge: {
    flexDirection: 'row', alignItems: 'center', gap: 3, marginLeft: 'auto',
    backgroundColor: colors.warning, borderRadius: radius.sm, paddingHorizontal: 6, paddingVertical: 1,
  },
  alertBadgeText: { fontSize: 11, fontWeight: '700', color: '#1a1205' },
  readings: {
    marginTop: spacing.sm, borderTopWidth: 1, borderTopColor: colors.cardBorder, paddingTop: spacing.sm,
  },
  readingLine: { flexDirection: 'row', justifyContent: 'space-between', paddingVertical: 2 },
  readingValue: { fontSize: fontSize.sm, color: colors.text, fontWeight: '600' },
  pill: { borderRadius: radius.sm, paddingHorizontal: spacing.xs, paddingVertical: 2 },
  pillText: { fontSize: 10, fontWeight: '700', color: '#04120a', textTransform: 'uppercase' },
})
