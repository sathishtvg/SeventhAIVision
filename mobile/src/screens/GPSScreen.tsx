import React, { useState, useCallback } from 'react'
import {
  FlatList, RefreshControl,
  StyleSheet, Text, View, Pressable, ScrollView,
} from 'react-native'
import { useQuery, useQueryClient, keepPreviousData } from '@tanstack/react-query'
import { Ionicons } from '@expo/vector-icons'
import { TRACKING_LABEL, countByTracking, getVehicles, type GpsVehicle, type TrackingStatus } from '@/api/gps'
import { Card } from '@/components/Card'
import { colors, fontSize, radius, spacing } from '@/theme'
import { ErrorState, ListSkeleton, RefreshingLine } from '@/components/states'

// Whether the vehicle is being heard from: worked out by the server from its last fix, never from a guess.
const STATUS_COLOR: Record<TrackingStatus, string> = {
  online:         colors.success,
  stale:          colors.warning,
  offline:        colors.textDisabled,
  not_configured: colors.info,
}

function StatusPill({ status }: { status: TrackingStatus }) {
  const color = STATUS_COLOR[status] ?? colors.textDisabled
  return (
    <View style={[styles.pill, { backgroundColor: color + '28', borderColor: color }]}>
      <Text style={[styles.pillText, { color }]}>{TRACKING_LABEL[status] ?? status}</Text>
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
        <StatusPill status={item.tracking_status ?? 'not_configured'} />
      </View>
      {(item.last_lat != null && item.last_lon != null && item.last_fix_at) && (
        <View style={styles.metaRow}>
          <Ionicons name="location-outline" size={12} color={colors.textSecondary} />
          <Text style={styles.metaText}>
            {item.tracking_status === 'online' ? '' : 'Last known: '}
            {item.last_lat.toFixed(5)}, {item.last_lon.toFixed(5)}
            {item.tracking_status === 'online' && item.last_speed != null ? `  ·  ${Math.round(Number(item.last_speed))} km/h` : ''}
          </Text>
        </View>
      )}
      {item.last_fix_at && (
        <View style={styles.metaRow}>
          <Ionicons name="time-outline" size={12} color={colors.textSecondary} />
          <Text style={styles.metaText}>Last fix {new Date(item.last_fix_at).toLocaleString()}</Text>
        </View>
      )}
    </Card>
  )
}

export function GPSScreen() {
  const qc = useQueryClient()
  const [filter, setFilter] = useState<TrackingStatus | undefined>(undefined)
  const [refreshing, setRefreshing] = useState(false)

  // One list, filtered here: the counts above and the rows below are the same answer.
  const { data: everything = [], isLoading, isLoadingError: vehiclesFailed, error: vehiclesError, refetch: refetchVehicles, isPlaceholderData: vehiclesRefreshing } = useQuery({
    queryKey: ['gps-vehicles'],
    placeholderData: keepPreviousData,
    queryFn: () => getVehicles(),
    refetchInterval: 30_000,
  })
  const counts = countByTracking(everything)
  const vehicles = filter ? everything.filter((v) => (v.tracking_status ?? 'not_configured') === filter) : everything

  const onRefresh = useCallback(async () => {
    setRefreshing(true)
    await qc.invalidateQueries({ queryKey: ['gps'] })
    setRefreshing(false)
  }, [qc])

  const FILTERS: { label: string; value: TrackingStatus | undefined }[] = [
    { label: 'All', value: undefined },
    { label: 'Online', value: 'online' },
    { label: 'Stale', value: 'stale' },
    { label: 'Offline', value: 'offline' },
    { label: 'Not configured', value: 'not_configured' },
  ]

  return (
    <View style={styles.root}>
      {/* KPI strip */}
      {everything.length > 0 && (
        <View style={styles.kpiRow}>
          {[
            { label: 'Total', value: everything.length, color: colors.text },
            { label: 'Online', value: counts.online, color: colors.success },
            { label: 'Stale', value: counts.stale, color: colors.warning },
            { label: 'Offline', value: counts.offline, color: colors.textDisabled },
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
            accessibilityRole="button"
            accessibilityState={{ selected: filter === f.value }}
          >
            <Text style={[styles.chipText, filter === f.value && styles.chipTextActive]}>{f.label}</Text>
          </Pressable>
        ))}
      </ScrollView>

      {vehiclesRefreshing && <RefreshingLine />}
      {isLoading ? (
        <ListSkeleton />
      ) : vehiclesFailed ? <ErrorState error={vehiclesError} onRetry={refetchVehicles} /> : vehicles.length === 0 ? (
        <View style={styles.center}>
          <Ionicons name="car-outline" size={48} color={colors.textDisabled} />
          <Text style={styles.emptyText}>{filter ? `No vehicle is ${TRACKING_LABEL[filter].toLowerCase()}` : 'No vehicles'}</Text>
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
