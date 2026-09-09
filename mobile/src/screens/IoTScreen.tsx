import React, { useState, useCallback } from 'react'
import {
  ActivityIndicator, FlatList, RefreshControl,
  ScrollView, StyleSheet, Text, View, Pressable,
} from 'react-native'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Ionicons } from '@expo/vector-icons'
import { getIoTDashboard, getIoTDevices, getIoTReadings, type IoTDevice, type IoTReading } from '@/api/iot'
import { Card } from '@/components/Card'
import { colors, fontSize, radius, spacing } from '@/theme'

const STATUS_COLOR: Record<string, string> = {
  online:  colors.success,
  offline: colors.textDisabled,
  error:   colors.error,
  unknown: colors.warning,
}

const DEVICE_ICON: Record<string, React.ComponentProps<typeof Ionicons>['name']> = {
  temperature_sensor: 'thermometer-outline',
  humidity_sensor:    'water-outline',
  motion_sensor:      'body-outline',
  door_sensor:        'log-in-outline',
  smoke_detector:     'flame-outline',
  flood_sensor:       'rainy-outline',
  power_meter:        'flash-outline',
  default:            'hardware-chip-outline',
}

function StatusDot({ status }: { status: string }) {
  const color = STATUS_COLOR[status] ?? colors.warning
  return <View style={[styles.dot, { backgroundColor: color }]} />
}

function DeviceRow({ item }: { item: IoTDevice }) {
  const icon = DEVICE_ICON[item.device_type] ?? DEVICE_ICON.default
  const statusColor = STATUS_COLOR[item.status] ?? colors.warning
  return (
    <Card style={styles.row}>
      <View style={styles.rowTop}>
        <View style={[styles.iconWrap, { backgroundColor: statusColor + '20' }]}>
          <Ionicons name={icon} size={18} color={statusColor} />
        </View>
        <View style={styles.rowInfo}>
          <Text style={styles.name}>{item.name}</Text>
          <Text style={styles.sub}>{item.device_type.replace(/_/g, ' ')}</Text>
          {item.location && <Text style={styles.sub}>{item.location}</Text>}
        </View>
        <View style={styles.statusWrap}>
          <StatusDot status={item.status} />
          <Text style={[styles.statusText, { color: statusColor }]}>{item.status}</Text>
        </View>
      </View>
      <View style={styles.metaStrip}>
        {item.battery_pct != null && (
          <View style={styles.metaItem}>
            <Ionicons name="battery-half-outline" size={12}
              color={item.battery_pct <= 20 ? colors.error : colors.textSecondary} />
            <Text style={styles.metaText}>{item.battery_pct}%</Text>
          </View>
        )}
        {item.firmware_version && (
          <View style={styles.metaItem}>
            <Ionicons name="code-outline" size={12} color={colors.textSecondary} />
            <Text style={styles.metaText}>v{item.firmware_version}</Text>
          </View>
        )}
        {item.last_seen_at && (
          <View style={styles.metaItem}>
            <Ionicons name="time-outline" size={12} color={colors.textSecondary} />
            <Text style={styles.metaText}>{new Date(item.last_seen_at).toLocaleTimeString()}</Text>
          </View>
        )}
      </View>
    </Card>
  )
}

function ReadingRow({ item }: { item: IoTReading }) {
  const color = item.is_alert ? colors.error : colors.textSecondary
  return (
    <Card style={styles.row}>
      <View style={styles.rowTop}>
        {item.is_alert && <View style={[styles.dot, { backgroundColor: colors.error }]} />}
        <View style={styles.rowInfo}>
          <Text style={styles.name}>{item.device_name ?? 'Device'}</Text>
          <Text style={styles.sub}>{item.metric.replace(/_/g, ' ')}</Text>
        </View>
        <Text style={[styles.readingValue, { color }]}>
          {item.value}{item.unit ? ` ${item.unit}` : ''}
        </Text>
        <Text style={styles.time}>{new Date(item.recorded_at).toLocaleTimeString()}</Text>
      </View>
    </Card>
  )
}

export function IoTScreen() {
  const qc = useQueryClient()
  const [tab, setTab] = useState<'devices' | 'readings'>('devices')
  const [statusFilter, setStatusFilter] = useState<string | undefined>(undefined)
  const [refreshing, setRefreshing] = useState(false)

  const { data: dashboard } = useQuery({
    queryKey: ['iot-dashboard'],
    queryFn: () => getIoTDashboard(),
  })

  const { data: devices = [], isLoading: loadingDevices } = useQuery({
    queryKey: ['iot-devices', statusFilter],
    queryFn: () => getIoTDevices({ status: statusFilter }),
  })

  const { data: readings = [], isLoading: loadingReadings } = useQuery({
    queryKey: ['iot-readings'],
    queryFn: () => getIoTReadings({ limit: 50 }),
    enabled: tab === 'readings',
  })

  const onRefresh = useCallback(async () => {
    setRefreshing(true)
    await qc.invalidateQueries({ queryKey: ['iot'] })
    setRefreshing(false)
  }, [qc])

  const FILTERS = [
    { label: 'All', value: undefined },
    { label: 'Online', value: 'online' },
    { label: 'Offline', value: 'offline' },
    { label: 'Error', value: 'error' },
  ]

  const isLoading = tab === 'devices' ? loadingDevices : loadingReadings

  return (
    <View style={styles.root}>
      {/* KPI strip */}
      {dashboard && (
        <View style={styles.kpiRow}>
          {[
            { label: 'Total', value: dashboard.total_devices, color: colors.text },
            { label: 'Online', value: dashboard.online, color: colors.success },
            { label: 'Offline', value: dashboard.offline, color: colors.textDisabled },
            { label: 'Alerts', value: dashboard.alerts_today, color: dashboard.alerts_today > 0 ? colors.error : colors.textSecondary },
          ].map((k) => (
            <Card key={k.label} style={styles.kpiCard}>
              <Text style={[styles.kpiValue, { color: k.color }]}>{k.value}</Text>
              <Text style={styles.kpiLabel}>{k.label}</Text>
            </Card>
          ))}
        </View>
      )}

      <View style={styles.tabRow}>
        {(['devices', 'readings'] as const).map((t) => (
          <Pressable key={t} style={[styles.tabBtn, tab === t && styles.tabActive]} onPress={() => setTab(t)}>
            <Text style={[styles.tabText, tab === t && styles.tabTextActive]}>
              {t === 'devices' ? 'Devices' : 'Readings'}
            </Text>
          </Pressable>
        ))}
      </View>

      {tab === 'devices' && (
        <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.chipScroll} contentContainerStyle={styles.chipContent}>
          {FILTERS.map((f) => (
            <Pressable
              key={String(f.value)}
              style={[styles.chip, statusFilter === f.value && styles.chipActive]}
              onPress={() => setStatusFilter(f.value)}
            >
              <Text style={[styles.chipText, statusFilter === f.value && styles.chipTextActive]}>{f.label}</Text>
            </Pressable>
          ))}
        </ScrollView>
      )}

      {isLoading ? (
        <View style={styles.center}><ActivityIndicator color={colors.primary} /></View>
      ) : (tab === 'devices' ? devices : readings).length === 0 ? (
        <View style={styles.center}>
          <Ionicons name="hardware-chip-outline" size={48} color={colors.textDisabled} />
          <Text style={styles.emptyText}>No {tab}</Text>
        </View>
      ) : (
        // One list per tab, not one list fed a union. The single FlatList
        // this replaced needed `item as IoTDevice` / `item as IoTReading`
        // casts that TypeScript could not check, and it carried its scroll
        // position and recycled item views between two unrelated shapes.
        tab === 'devices' ? (
          <FlatList
            data={devices}
            keyExtractor={(item) => item.id}
            renderItem={({ item }) => <DeviceRow item={item} />}
            ItemSeparatorComponent={() => <View style={{ height: spacing.xs }} />}
            contentContainerStyle={styles.list}
            refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.primary} />}
          />
        ) : (
          <FlatList
            data={readings}
            keyExtractor={(item) => item.id}
            renderItem={({ item }) => <ReadingRow item={item} />}
            ItemSeparatorComponent={() => <View style={{ height: spacing.xs }} />}
            contentContainerStyle={styles.list}
            refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.primary} />}
          />
        )
      )}
    </View>
  )
}

const styles = StyleSheet.create({
  root:          { flex: 1, backgroundColor: colors.background },
  center:        { flex: 1, alignItems: 'center', justifyContent: 'center', gap: spacing.sm },
  emptyText:     { color: colors.textSecondary, fontSize: fontSize.md },
  kpiRow:        { flexDirection: 'row', gap: spacing.xs, padding: spacing.md, paddingBottom: 0 },
  kpiCard:       { flex: 1, alignItems: 'center', paddingVertical: spacing.sm },
  kpiValue:      { fontSize: fontSize.xl, fontWeight: '800' },
  kpiLabel:      { fontSize: 10, color: colors.textSecondary, marginTop: 2 },
  tabRow:        { flexDirection: 'row', padding: spacing.md, paddingBottom: 0, gap: spacing.sm },
  tabBtn:        { flex: 1, paddingVertical: 8, borderRadius: radius.sm, borderWidth: 1, borderColor: colors.cardBorder, alignItems: 'center' },
  tabActive:     { backgroundColor: colors.primary, borderColor: colors.primary },
  tabText:       { fontSize: fontSize.sm, color: colors.textSecondary, fontWeight: '600' },
  tabTextActive: { color: '#fff' },
  chipScroll:    { flexGrow: 0, paddingHorizontal: spacing.md, paddingVertical: spacing.sm },
  chipContent:   { flexDirection: 'row', gap: spacing.xs },
  chip:          { borderRadius: radius.full, borderWidth: 1, borderColor: colors.cardBorder, paddingHorizontal: spacing.md, paddingVertical: 5 },
  chipActive:    { backgroundColor: colors.primary, borderColor: colors.primary },
  chipText:      { fontSize: fontSize.sm, color: colors.textSecondary },
  chipTextActive:{ color: '#fff', fontWeight: '600' },
  list:          { padding: spacing.md, paddingTop: spacing.sm, paddingBottom: spacing.xl },
  row:           { gap: 6 },
  rowTop:        { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  iconWrap:      { width: 36, height: 36, borderRadius: 18, alignItems: 'center', justifyContent: 'center' },
  rowInfo:       { flex: 1 },
  name:          { fontSize: fontSize.md, fontWeight: '700', color: colors.text },
  sub:           { fontSize: fontSize.xs, color: colors.textSecondary, marginTop: 1, textTransform: 'capitalize' },
  statusWrap:    { flexDirection: 'row', alignItems: 'center', gap: 4 },
  statusText:    { fontSize: fontSize.xs, fontWeight: '600', textTransform: 'capitalize' },
  dot:           { width: 8, height: 8, borderRadius: 4, flexShrink: 0 },
  metaStrip:     { flexDirection: 'row', gap: spacing.md, flexWrap: 'wrap' },
  metaItem:      { flexDirection: 'row', alignItems: 'center', gap: 3 },
  metaText:      { fontSize: fontSize.xs, color: colors.textSecondary },
  readingValue:  { fontSize: fontSize.md, fontWeight: '700' },
  time:          { fontSize: fontSize.xs, color: colors.textSecondary },
})
