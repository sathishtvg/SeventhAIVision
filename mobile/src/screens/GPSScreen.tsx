import React, { useState, useCallback } from 'react'
import {
  ActivityIndicator, FlatList, RefreshControl,
  StyleSheet, Text, View, Pressable, ScrollView,
} from 'react-native'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Ionicons } from '@expo/vector-icons'
import { getGpsDashboard, getVehicles, type GpsVehicle } from '@/api/gps'
import { Card } from '@/components/Card'
import { colors, fontSize, radius, spacing } from '@/theme'

const STATUS_COLOR: Record<string, string> = {
  moving:  colors.success,
  idle:    colors.warning,
  offline: colors.textDisabled,
}

function StatusPill({ status }: { status: string }) {
  const color = STATUS_COLOR[status] ?? colors.textDisabled
  return (
    <View style={[styles.pill, { backgroundColor: color + '28', borderColor: color }]}>
      <Text style={[styles.pillText, { color }]}>{status}</Text>
    </View>
  )
}

function VehicleRow({ item }: { item: GpsVehicle }) {
  return (
    <Card style={styles.row}>
      <View style={styles.rowTop}>
        <View style={styles.iconWrap}>
          <Ionicons name="car-outline" size={18} color={colors.primary} />
        </View>
        <View style={styles.rowInfo}>
          <Text style={styles.name}>{item.name}</Text>
          {item.plate_number && <Text style={styles.sub}>{item.plate_number}</Text>}
          {item.driver_name && <Text style={styles.sub}>Driver: {item.driver_name}</Text>}
        </View>
        <StatusPill status={item.status} />
      </View>
      {(item.last_lat != null && item.last_lon != null) && (
        <View style={styles.metaRow}>
          <Ionicons name="location-outline" size={12} color={colors.textSecondary} />
          <Text style={styles.metaText}>
            {item.last_lat.toFixed(5)}, {item.last_lon.toFixed(5)}
            {item.last_speed_kmh != null ? `  ·  ${Math.round(item.last_speed_kmh)} km/h` : ''}
          </Text>
        </View>
      )}
      {item.last_seen_at && (
        <View style={styles.metaRow}>
          <Ionicons name="time-outline" size={12} color={colors.textSecondary} />
          <Text style={styles.metaText}>{new Date(item.last_seen_at).toLocaleString()}</Text>
        </View>
      )}
    </Card>
  )
}

export function GPSScreen() {
  const qc = useQueryClient()
  const [filter, setFilter] = useState<string | undefined>(undefined)
  const [refreshing, setRefreshing] = useState(false)

  const { data: dashboard } = useQuery({
    queryKey: ['gps-dashboard'],
    queryFn: () => getGpsDashboard(),
  })

  const { data: vehicles = [], isLoading } = useQuery({
    queryKey: ['gps-vehicles', filter],
    queryFn: () => getVehicles({ status: filter }),
  })

  const onRefresh = useCallback(async () => {
    setRefreshing(true)
    await qc.invalidateQueries({ queryKey: ['gps'] })
    setRefreshing(false)
  }, [qc])

  const FILTERS = [
    { label: 'All', value: undefined },
    { label: 'Moving', value: 'moving' },
    { label: 'Idle', value: 'idle' },
    { label: 'Offline', value: 'offline' },
  ]

  return (
    <View style={styles.root}>
      {/* KPI strip */}
      {dashboard && (
        <View style={styles.kpiRow}>
          {[
            { label: 'Total', value: dashboard.total, color: colors.text },
            { label: 'Moving', value: dashboard.moving, color: colors.success },
            { label: 'Idle', value: dashboard.idle, color: colors.warning },
            { label: 'Offline', value: dashboard.offline, color: colors.textDisabled },
          ].map((k) => (
            <Card key={k.label} style={styles.kpiCard}>
              <Text style={[styles.kpiValue, { color: k.color }]}>{k.value}</Text>
              <Text style={styles.kpiLabel}>{k.label}</Text>
            </Card>
          ))}
        </View>
      )}

      {/* Filter chips */}
      <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.chipScroll} contentContainerStyle={styles.chipContent}>
        {FILTERS.map((f) => (
          <Pressable
            key={String(f.value)}
            style={[styles.chip, filter === f.value && styles.chipActive]}
            onPress={() => setFilter(f.value)}
          >
            <Text style={[styles.chipText, filter === f.value && styles.chipTextActive]}>{f.label}</Text>
          </Pressable>
        ))}
      </ScrollView>

      {isLoading ? (
        <View style={styles.center}><ActivityIndicator color={colors.primary} /></View>
      ) : vehicles.length === 0 ? (
        <View style={styles.center}>
          <Ionicons name="car-outline" size={48} color={colors.textDisabled} />
          <Text style={styles.emptyText}>No vehicles</Text>
        </View>
      ) : (
        <FlatList
          data={vehicles}
          keyExtractor={(item) => item.id}
          renderItem={({ item }) => <VehicleRow item={item} />}
          ItemSeparatorComponent={() => <View style={{ height: spacing.xs }} />}
          contentContainerStyle={styles.list}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.primary} />}
        />
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
  chipScroll:    { flexGrow: 0, paddingHorizontal: spacing.md, paddingVertical: spacing.sm },
  chipContent:   { flexDirection: 'row', gap: spacing.xs },
  chip:          { borderRadius: radius.full, borderWidth: 1, borderColor: colors.cardBorder, paddingHorizontal: spacing.md, paddingVertical: 5 },
  chipActive:    { backgroundColor: colors.primary, borderColor: colors.primary },
  chipText:      { fontSize: fontSize.sm, color: colors.textSecondary },
  chipTextActive:{ color: '#fff', fontWeight: '600' },
  list:          { padding: spacing.md, paddingTop: 0, paddingBottom: spacing.xl },
  row:           { gap: 6 },
  rowTop:        { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  iconWrap:      { width: 36, height: 36, borderRadius: 18, backgroundColor: colors.primaryMuted, alignItems: 'center', justifyContent: 'center' },
  rowInfo:       { flex: 1 },
  name:          { fontSize: fontSize.md, fontWeight: '700', color: colors.text },
  sub:           { fontSize: fontSize.xs, color: colors.textSecondary, marginTop: 1 },
  metaRow:       { flexDirection: 'row', alignItems: 'center', gap: 4 },
  metaText:      { fontSize: fontSize.xs, color: colors.textSecondary },
  pill:          { paddingHorizontal: spacing.sm, paddingVertical: 3, borderRadius: radius.full, borderWidth: 1 },
  pillText:      { fontSize: 10, fontWeight: '700', textTransform: 'uppercase', letterSpacing: 0.5 },
})
