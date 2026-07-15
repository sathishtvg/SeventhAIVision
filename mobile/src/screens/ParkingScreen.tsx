import React, { useState, useCallback } from 'react'
import {
  ActivityIndicator, FlatList, RefreshControl,
  StyleSheet, Text, View, Pressable, ScrollView,
} from 'react-native'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Ionicons } from '@expo/vector-icons'
import { getCarParks, getParkingOccupancy, getParkingSessions, type CarPark, type ParkingSession } from '@/api/parking'
import { Card } from '@/components/Card'
import { colors, fontSize, radius, spacing } from '@/theme'

const SESSION_COLOR: Record<string, string> = {
  active:    colors.success,
  overstay:  colors.error,
  completed: colors.textDisabled,
  reserved:  colors.info,
}

function StatusPill({ status }: { status: string }) {
  const color = SESSION_COLOR[status] ?? colors.textDisabled
  return (
    <View style={[styles.pill, { backgroundColor: color + '28', borderColor: color }]}>
      <Text style={[styles.pillText, { color }]}>{status}</Text>
    </View>
  )
}

function OccupancyBar({ pct }: { pct: number }) {
  const color = pct >= 90 ? colors.error : pct >= 70 ? colors.warning : colors.success
  return (
    <View style={styles.barTrack}>
      <View style={[styles.barFill, { width: `${Math.min(pct, 100)}%` as any, backgroundColor: color }]} />
    </View>
  )
}

function CarParkCard({ item, onSelect, selected }: { item: CarPark; onSelect: (id: string) => void; selected: boolean }) {
  const { data: occ } = useQuery({
    queryKey: ['parking-occ', item.id],
    queryFn: () => getParkingOccupancy(item.id),
  })
  return (
    <Pressable onPress={() => onSelect(item.id)}>
      <Card style={[styles.carparkCard, selected && styles.carparkSelected]}>
        <View style={styles.rowTop}>
          <Ionicons name="car-outline" size={16} color={colors.primary} />
          <Text style={styles.name}>{item.name}</Text>
          {occ && <Text style={[styles.sub, { marginLeft: 'auto' }]}>{occ.occupancy_pct}%</Text>}
        </View>
        {occ && <OccupancyBar pct={occ.occupancy_pct} />}
        <Text style={styles.sub}>{item.total_bays} bays total</Text>
      </Card>
    </Pressable>
  )
}

function SessionRow({ item }: { item: ParkingSession }) {
  return (
    <Card style={styles.row}>
      <View style={styles.rowTop}>
        <View style={styles.iconWrap}>
          <Ionicons name="car-sport-outline" size={18} color={colors.primary} />
        </View>
        <View style={styles.rowInfo}>
          <Text style={styles.name}>{item.vehicle_plate}</Text>
          {item.bay_number && <Text style={styles.sub}>Bay {item.bay_number}</Text>}
          {item.car_park_name && <Text style={styles.sub}>{item.car_park_name}</Text>}
        </View>
        <StatusPill status={item.status} />
      </View>
      <View style={styles.metaRow}>
        <Ionicons name="enter-outline" size={12} color={colors.textSecondary} />
        <Text style={styles.metaText}>{new Date(item.entry_at).toLocaleString()}</Text>
        {item.duration_minutes != null && (
          <Text style={styles.metaText}> · {item.duration_minutes} min</Text>
        )}
      </View>
    </Card>
  )
}

export function ParkingScreen() {
  const qc = useQueryClient()
  const [selectedPark, setSelectedPark] = useState<string | undefined>(undefined)
  const [statusFilter, setStatusFilter] = useState<string | undefined>('active')
  const [refreshing, setRefreshing] = useState(false)

  const { data: carParks = [], isLoading: loadingParks } = useQuery({
    queryKey: ['car-parks'],
    queryFn: getCarParks,
  })

  const { data: sessions = [], isLoading: loadingSessions } = useQuery({
    queryKey: ['parking-sessions', selectedPark, statusFilter],
    queryFn: () => getParkingSessions({ car_park_id: selectedPark, status: statusFilter, limit: 50 }),
  })

  const onRefresh = useCallback(async () => {
    setRefreshing(true)
    await qc.invalidateQueries({ queryKey: ['car-parks', 'parking-sessions', 'parking-occ'] })
    setRefreshing(false)
  }, [qc])

  const SESSION_FILTERS = [
    { label: 'Active', value: 'active' },
    { label: 'Overstay', value: 'overstay' },
    { label: 'All', value: undefined },
  ]

  return (
    <View style={styles.root}>
      {/* Car park selector */}
      {loadingParks ? (
        <ActivityIndicator color={colors.primary} style={{ margin: spacing.md }} />
      ) : (
        <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.parkScroll} contentContainerStyle={styles.parkContent}>
          {carParks.map((cp: CarPark) => (
            <CarParkCard
              key={cp.id}
              item={cp}
              selected={selectedPark === cp.id}
              onSelect={(id) => setSelectedPark((prev) => prev === id ? undefined : id)}
            />
          ))}
        </ScrollView>
      )}

      {/* Session status filters */}
      <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.chipScroll} contentContainerStyle={styles.chipContent}>
        {SESSION_FILTERS.map((f) => (
          <Pressable
            key={String(f.value)}
            style={[styles.chip, statusFilter === f.value && styles.chipActive]}
            onPress={() => setStatusFilter(f.value)}
          >
            <Text style={[styles.chipText, statusFilter === f.value && styles.chipTextActive]}>{f.label}</Text>
          </Pressable>
        ))}
      </ScrollView>

      {loadingSessions ? (
        <View style={styles.center}><ActivityIndicator color={colors.primary} /></View>
      ) : sessions.length === 0 ? (
        <View style={styles.center}>
          <Ionicons name="car-outline" size={48} color={colors.textDisabled} />
          <Text style={styles.emptyText}>No sessions</Text>
        </View>
      ) : (
        <FlatList
          data={sessions}
          keyExtractor={(item) => item.id}
          renderItem={({ item }) => <SessionRow item={item} />}
          ItemSeparatorComponent={() => <View style={{ height: spacing.xs }} />}
          contentContainerStyle={styles.list}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.primary} />}
        />
      )}
    </View>
  )
}

const styles = StyleSheet.create({
  root:           { flex: 1, backgroundColor: colors.background },
  center:         { flex: 1, alignItems: 'center', justifyContent: 'center', gap: spacing.sm },
  emptyText:      { color: colors.textSecondary, fontSize: fontSize.md },
  parkScroll:     { flexGrow: 0, paddingLeft: spacing.md },
  parkContent:    { gap: spacing.sm, paddingRight: spacing.md, paddingVertical: spacing.sm },
  carparkCard:    { width: 160, gap: 6 },
  carparkSelected:{ borderColor: colors.primary },
  barTrack:       { height: 4, backgroundColor: colors.cardBorder, borderRadius: 2, marginTop: 4 },
  barFill:        { height: 4, borderRadius: 2 },
  chipScroll:     { flexGrow: 0, paddingHorizontal: spacing.md, paddingVertical: spacing.sm },
  chipContent:    { flexDirection: 'row', gap: spacing.xs },
  chip:           { borderRadius: radius.full, borderWidth: 1, borderColor: colors.cardBorder, paddingHorizontal: spacing.md, paddingVertical: 5 },
  chipActive:     { backgroundColor: colors.primary, borderColor: colors.primary },
  chipText:       { fontSize: fontSize.sm, color: colors.textSecondary },
  chipTextActive: { color: '#fff', fontWeight: '600' },
  list:           { padding: spacing.md, paddingTop: 0, paddingBottom: spacing.xl },
  row:            { gap: 6 },
  rowTop:         { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  iconWrap:       { width: 36, height: 36, borderRadius: 18, backgroundColor: colors.primaryMuted, alignItems: 'center', justifyContent: 'center' },
  rowInfo:        { flex: 1 },
  name:           { fontSize: fontSize.md, fontWeight: '700', color: colors.text },
  sub:            { fontSize: fontSize.xs, color: colors.textSecondary, marginTop: 1 },
  metaRow:        { flexDirection: 'row', alignItems: 'center', gap: 4 },
  metaText:       { fontSize: fontSize.xs, color: colors.textSecondary },
  pill:           { paddingHorizontal: spacing.sm, paddingVertical: 3, borderRadius: radius.full, borderWidth: 1 },
  pillText:       { fontSize: 10, fontWeight: '700', textTransform: 'uppercase', letterSpacing: 0.5 },
})
