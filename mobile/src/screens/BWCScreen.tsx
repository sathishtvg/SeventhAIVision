import React, { useState, useCallback } from 'react'
import {
  ActivityIndicator, FlatList, RefreshControl,
  StyleSheet, Text, View, Pressable,
} from 'react-native'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Ionicons } from '@expo/vector-icons'
import { getBwcDashboard, getBodyCameras, getBwcRecordings, type BodyCamera } from '@/api/bwc'
import { Card } from '@/components/Card'
import { colors, fontSize, radius, spacing } from '@/theme'

const STATUS_COLOR: Record<string, string> = {
  available:  colors.success,
  assigned:   colors.info,
  recording:  colors.error,
  docked:     colors.textSecondary,
  low_battery:colors.warning,
  fault:      colors.error,
  retired:    colors.textDisabled,
}

function StatusPill({ status }: { status: string }) {
  const color = STATUS_COLOR[status] ?? colors.textDisabled
  return (
    <View style={[styles.pill, { backgroundColor: color + '28', borderColor: color }]}>
      <Text style={[styles.pillText, { color }]}>{status.replace('_', ' ')}</Text>
    </View>
  )
}

function CameraRow({ item }: { item: BodyCamera }) {
  const battPct = item.battery_pct ?? 0
  const battColor = battPct <= 20 ? colors.error : battPct <= 50 ? colors.warning : colors.success
  return (
    <Card style={styles.row}>
      <View style={styles.rowTop}>
        <View style={styles.iconWrap}>
          <Ionicons name="videocam-outline" size={18} color={colors.info} />
        </View>
        <View style={styles.rowInfo}>
          <Text style={styles.name}>{item.name ?? item.serial_number}</Text>
          <Text style={styles.sub}>S/N: {item.serial_number}</Text>
          {item.assigned_user_name && <Text style={styles.sub}>Officer: {item.assigned_user_name}</Text>}
        </View>
        <StatusPill status={item.status} />
      </View>
      <View style={styles.metaStrip}>
        {item.battery_pct != null && (
          <View style={styles.metaItem}>
            <Ionicons name="battery-half-outline" size={12} color={battColor} />
            <Text style={[styles.metaText, { color: battColor }]}>{item.battery_pct}%</Text>
          </View>
        )}
        {item.storage_used_gb != null && (
          <View style={styles.metaItem}>
            <Ionicons name="save-outline" size={12} color={colors.textSecondary} />
            <Text style={styles.metaText}>{item.storage_used_gb.toFixed(1)} / {item.storage_total_gb?.toFixed(0)} GB</Text>
          </View>
        )}
        <View style={styles.metaItem}>
          <Ionicons name="film-outline" size={12} color={colors.textSecondary} />
          <Text style={styles.metaText}>{item.total_recordings} recordings</Text>
        </View>
      </View>
    </Card>
  )
}

export function BWCScreen() {
  const qc = useQueryClient()
  const [tab, setTab] = useState<'cameras' | 'recordings'>('cameras')
  const [refreshing, setRefreshing] = useState(false)

  const { data: dashboard } = useQuery({
    queryKey: ['bwc-dashboard'],
    queryFn: getBwcDashboard,
  })

  const { data: cameras = [], isLoading: loadingCameras } = useQuery({
    queryKey: ['bwc-cameras'],
    queryFn: () => getBodyCameras(),
  })

  const { data: recordings = [], isLoading: loadingRecs } = useQuery({
    queryKey: ['bwc-recordings'],
    queryFn: () => getBwcRecordings({ limit: 50 }),
    enabled: tab === 'recordings',
  })

  const onRefresh = useCallback(async () => {
    setRefreshing(true)
    await qc.invalidateQueries({ queryKey: ['bwc'] })
    setRefreshing(false)
  }, [qc])

  return (
    <View style={styles.root}>
      {/* KPI strip */}
      {dashboard && (
        <View style={styles.kpiRow}>
          {[
            { label: 'Total', value: dashboard.total_cameras, color: colors.text },
            { label: 'Avail', value: dashboard.available, color: colors.success },
            { label: 'Rec', value: dashboard.active_recordings, color: colors.error },
            { label: 'Today', value: dashboard.recordings_today, color: colors.info },
          ].map((k) => (
            <Card key={k.label} style={styles.kpiCard}>
              <Text style={[styles.kpiValue, { color: k.color }]}>{k.value}</Text>
              <Text style={styles.kpiLabel}>{k.label}</Text>
            </Card>
          ))}
        </View>
      )}

      <View style={styles.tabRow}>
        {(['cameras', 'recordings'] as const).map((t) => (
          <Pressable key={t} style={[styles.tabBtn, tab === t && styles.tabActive]} onPress={() => setTab(t)}>
            <Text style={[styles.tabText, tab === t && styles.tabTextActive]}>
              {t === 'cameras' ? 'Cameras' : 'Recordings'}
            </Text>
          </Pressable>
        ))}
      </View>

      {(tab === 'cameras' ? loadingCameras : loadingRecs) ? (
        <View style={styles.center}><ActivityIndicator color={colors.primary} /></View>
      ) : tab === 'cameras' ? (
        cameras.length === 0 ? (
          <View style={styles.center}>
            <Ionicons name="videocam-outline" size={48} color={colors.textDisabled} />
            <Text style={styles.emptyText}>No cameras</Text>
          </View>
        ) : (
          <FlatList
            data={cameras}
            keyExtractor={(item) => item.id}
            renderItem={({ item }) => <CameraRow item={item} />}
            ItemSeparatorComponent={() => <View style={{ height: spacing.xs }} />}
            contentContainerStyle={styles.list}
            refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.primary} />}
          />
        )
      ) : (
        recordings.length === 0 ? (
          <View style={styles.center}>
            <Ionicons name="film-outline" size={48} color={colors.textDisabled} />
            <Text style={styles.emptyText}>No recordings</Text>
          </View>
        ) : (
          <FlatList
            data={recordings}
            keyExtractor={(item) => item.id}
            renderItem={({ item }) => (
              <Card style={styles.row}>
                <View style={styles.rowTop}>
                  <Ionicons name="film-outline" size={16} color={colors.info} />
                  <View style={styles.rowInfo}>
                    <Text style={styles.name}>{item.trigger.replace('_', ' ')} · {item.status}</Text>
                    <Text style={styles.sub}>{new Date(item.started_at).toLocaleString()}</Text>
                    {item.duration_seconds != null && (
                      <Text style={styles.sub}>{Math.round(item.duration_seconds / 60)} min</Text>
                    )}
                  </View>
                </View>
              </Card>
            )}
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
  tabRow:        { flexDirection: 'row', padding: spacing.md, gap: spacing.sm },
  tabBtn:        { flex: 1, paddingVertical: 8, borderRadius: radius.sm, borderWidth: 1, borderColor: colors.cardBorder, alignItems: 'center' },
  tabActive:     { backgroundColor: colors.primary, borderColor: colors.primary },
  tabText:       { fontSize: fontSize.sm, color: colors.textSecondary, fontWeight: '600' },
  tabTextActive: { color: '#fff' },
  list:          { padding: spacing.md, paddingTop: 0, paddingBottom: spacing.xl },
  row:           { gap: 6 },
  rowTop:        { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  iconWrap:      { width: 36, height: 36, borderRadius: 18, backgroundColor: colors.info + '20', alignItems: 'center', justifyContent: 'center' },
  rowInfo:       { flex: 1 },
  name:          { fontSize: fontSize.md, fontWeight: '700', color: colors.text, textTransform: 'capitalize' },
  sub:           { fontSize: fontSize.xs, color: colors.textSecondary, marginTop: 1 },
  metaStrip:     { flexDirection: 'row', gap: spacing.md, flexWrap: 'wrap' },
  metaItem:      { flexDirection: 'row', alignItems: 'center', gap: 3 },
  metaText:      { fontSize: fontSize.xs, color: colors.textSecondary },
  pill:          { paddingHorizontal: spacing.sm, paddingVertical: 3, borderRadius: radius.full, borderWidth: 1 },
  pillText:      { fontSize: 10, fontWeight: '700', textTransform: 'uppercase', letterSpacing: 0.5 },
})
