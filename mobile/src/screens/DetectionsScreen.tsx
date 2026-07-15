import React, { useState } from 'react'
import {
  ActivityIndicator, FlatList, Pressable, RefreshControl,
  ScrollView, StyleSheet, Text, View,
} from 'react-native'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Ionicons } from '@expo/vector-icons'
import {
  getDetections, getLprEvents, getFaceEvents, getIntrusionEvents,
  type Detection, type LprEvent, type FaceEvent, type IntrusionEvent,
} from '@/api/detections'
import { Card } from '@/components/Card'
import { colors, fontSize, radius, spacing } from '@/theme'

const TABS = [
  { key: 'all',       label: 'All',       icon: 'scan-outline' },
  { key: 'lpr',       label: 'LPR',       icon: 'car-outline' },
  { key: 'face',      label: 'Face',      icon: 'person-outline' },
  { key: 'intrusion', label: 'Intrusion', icon: 'warning-outline' },
] as const
type TabKey = typeof TABS[number]['key']

const MODULE_ICON: Record<string, React.ComponentProps<typeof Ionicons>['name']> = {
  lpr:       'car-outline',
  face:      'person-outline',
  intrusion: 'warning-outline',
  ppe:       'construct-outline',
  crowd:     'people-outline',
  fire_smoke:'flame-outline',
  weapon:    'alert-circle-outline',
  behavior:  'body-outline',
}

function DetectionRow({ item }: { item: Detection }) {
  const icon = MODULE_ICON[item.module_type] ?? 'scan-outline'
  return (
    <Card style={styles.row}>
      <View style={styles.rowTop}>
        <View style={styles.iconWrap}>
          <Ionicons name={icon} size={16} color={colors.primary} />
        </View>
        <View style={styles.info}>
          <Text style={styles.module}>{item.module_type.replace('_', ' ').toUpperCase()}</Text>
          {item.confidence != null && (
            <Text style={styles.conf}>{(item.confidence * 100).toFixed(0)}% confidence</Text>
          )}
        </View>
        <Text style={styles.time}>
          {new Date(item.detected_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
        </Text>
      </View>
    </Card>
  )
}

function LprRow({ item }: { item: LprEvent }) {
  const matchColor = item.watchlist_match === 'block'
    ? colors.error
    : item.watchlist_match === 'allow'
    ? colors.success
    : colors.textDisabled
  return (
    <Card style={styles.row}>
      <View style={styles.rowTop}>
        <View style={styles.iconWrap}>
          <Ionicons name="car-outline" size={16} color={colors.secondary} />
        </View>
        <View style={styles.info}>
          <Text style={styles.plateNumber}>{item.plate_number}</Text>
          <View style={styles.metaRow}>
            {item.direction && <Text style={styles.meta}>{item.direction}</Text>}
            {item.vehicle_type && <Text style={styles.meta}>{item.vehicle_type}</Text>}
            {item.vehicle_color && <Text style={styles.meta}>{item.vehicle_color}</Text>}
          </View>
        </View>
        {item.watchlist_match && (
          <View style={[styles.matchBadge, { backgroundColor: matchColor + '28', borderColor: matchColor }]}>
            <Text style={[styles.matchText, { color: matchColor }]}>{item.watchlist_match}</Text>
          </View>
        )}
      </View>
      <Text style={styles.time}>
        {new Date(item.detected_at).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
      </Text>
    </Card>
  )
}

function FaceRow({ item }: { item: FaceEvent }) {
  const matchColor = item.watchlist_match === 'block'
    ? colors.error
    : item.watchlist_match === 'allow'
    ? colors.success
    : colors.textSecondary
  return (
    <Card style={styles.row}>
      <View style={styles.rowTop}>
        <View style={styles.iconWrap}>
          <Ionicons name="person-outline" size={16} color={colors.info} />
        </View>
        <View style={styles.info}>
          <Text style={styles.module}>
            {item.watchlist_match
              ? `${item.watchlist_match === 'block' ? 'Blocklist' : 'Allowlist'} match`
              : 'Unknown face'}
          </Text>
          {item.match_confidence != null && (
            <Text style={styles.conf}>Similarity: {(item.match_confidence * 100).toFixed(0)}%</Text>
          )}
        </View>
        {item.watchlist_match && (
          <View style={[styles.matchBadge, { backgroundColor: matchColor + '28', borderColor: matchColor }]}>
            <Text style={[styles.matchText, { color: matchColor }]}>{item.watchlist_match}</Text>
          </View>
        )}
      </View>
      <Text style={styles.time}>
        {new Date(item.detected_at).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
      </Text>
    </Card>
  )
}

function IntrusionRow({ item }: { item: IntrusionEvent }) {
  return (
    <Card style={styles.row}>
      <View style={styles.rowTop}>
        <View style={styles.iconWrap}>
          <Ionicons name="warning-outline" size={16} color={colors.error} />
        </View>
        <View style={styles.info}>
          <Text style={styles.module}>Zone Breach</Text>
          {item.dwell_time_seconds != null && (
            <Text style={styles.conf}>Dwell: {item.dwell_time_seconds.toFixed(0)}s</Text>
          )}
        </View>
        <Text style={styles.time}>
          {new Date(item.detected_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
        </Text>
      </View>
    </Card>
  )
}

export function DetectionsScreen() {
  const qc = useQueryClient()
  const [activeTab, setActiveTab] = useState<TabKey>('lpr')
  const [refreshing, setRefreshing] = useState(false)

  const allQuery   = useQuery({ queryKey: ['detections-all'], queryFn: () => getDetections({ limit: 50 }), enabled: activeTab === 'all' })
  const lprQuery   = useQuery({ queryKey: ['lpr-events'],     queryFn: () => getLprEvents({ limit: 50 }),   enabled: activeTab === 'lpr' })
  const faceQuery  = useQuery({ queryKey: ['face-events'],    queryFn: () => getFaceEvents({ limit: 50 }),  enabled: activeTab === 'face' })
  const intrQuery  = useQuery({ queryKey: ['intr-events'],    queryFn: () => getIntrusionEvents({ limit: 50 }), enabled: activeTab === 'intrusion' })

  const loading = allQuery.isLoading || lprQuery.isLoading || faceQuery.isLoading || intrQuery.isLoading

  const onRefresh = async () => {
    setRefreshing(true)
    await qc.invalidateQueries({ queryKey: ['detections-all'] })
    await qc.invalidateQueries({ queryKey: ['lpr-events'] })
    await qc.invalidateQueries({ queryKey: ['face-events'] })
    await qc.invalidateQueries({ queryKey: ['intr-events'] })
    setRefreshing(false)
  }

  return (
    <View style={styles.root}>
      {/* Tab bar */}
      <View style={styles.tabBar}>
        {TABS.map((tab) => (
          <Pressable
            key={tab.key}
            style={[styles.tab, activeTab === tab.key && styles.tabActive]}
            onPress={() => setActiveTab(tab.key)}
          >
            <Ionicons
              name={tab.icon as any}
              size={16}
              color={activeTab === tab.key ? colors.primary : colors.textSecondary}
            />
            <Text style={[styles.tabText, activeTab === tab.key && styles.tabTextActive]}>
              {tab.label}
            </Text>
          </Pressable>
        ))}
      </View>

      {loading ? (
        <View style={styles.center}>
          <ActivityIndicator color={colors.primary} />
        </View>
      ) : (
        <>
          {activeTab === 'all' && (
            <FlatList
              data={allQuery.data ?? []}
              keyExtractor={(item) => item.id}
              renderItem={({ item }) => <DetectionRow item={item} />}
              ItemSeparatorComponent={() => <View style={{ height: spacing.xs }} />}
              contentContainerStyle={styles.list}
              refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.primary} />}
              ListEmptyComponent={<View style={styles.center}><Text style={styles.emptyText}>No detections</Text></View>}
            />
          )}
          {activeTab === 'lpr' && (
            <FlatList
              data={lprQuery.data ?? []}
              keyExtractor={(item) => item.detection_id}
              renderItem={({ item }) => <LprRow item={item} />}
              ItemSeparatorComponent={() => <View style={{ height: spacing.xs }} />}
              contentContainerStyle={styles.list}
              refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.primary} />}
              ListEmptyComponent={<View style={styles.center}><Text style={styles.emptyText}>No LPR events</Text></View>}
            />
          )}
          {activeTab === 'face' && (
            <FlatList
              data={faceQuery.data ?? []}
              keyExtractor={(item) => item.detection_id}
              renderItem={({ item }) => <FaceRow item={item} />}
              ItemSeparatorComponent={() => <View style={{ height: spacing.xs }} />}
              contentContainerStyle={styles.list}
              refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.primary} />}
              ListEmptyComponent={<View style={styles.center}><Text style={styles.emptyText}>No face events</Text></View>}
            />
          )}
          {activeTab === 'intrusion' && (
            <FlatList
              data={intrQuery.data ?? []}
              keyExtractor={(item) => item.detection_id}
              renderItem={({ item }) => <IntrusionRow item={item} />}
              ItemSeparatorComponent={() => <View style={{ height: spacing.xs }} />}
              contentContainerStyle={styles.list}
              refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.primary} />}
              ListEmptyComponent={<View style={styles.center}><Text style={styles.emptyText}>No intrusion events</Text></View>}
            />
          )}
        </>
      )}
    </View>
  )
}

const styles = StyleSheet.create({
  root:      { flex: 1, backgroundColor: colors.background },
  center:    { flex: 1, alignItems: 'center', justifyContent: 'center', padding: spacing.xl },
  emptyText: { color: colors.textSecondary, fontSize: fontSize.md },
  tabBar:    { flexDirection: 'row', borderBottomWidth: 1, borderBottomColor: colors.divider },
  tab:       { flex: 1, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 5, paddingVertical: 12 },
  tabActive: { borderBottomWidth: 2, borderBottomColor: colors.primary },
  tabText:   { fontSize: fontSize.sm, color: colors.textSecondary },
  tabTextActive: { color: colors.primary, fontWeight: '600' },
  list:      { padding: spacing.md, paddingBottom: spacing.xl },
  row:       { gap: 4 },
  rowTop:    { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  iconWrap:  { width: 32, height: 32, borderRadius: radius.sm, backgroundColor: colors.glassBg, alignItems: 'center', justifyContent: 'center' },
  info:      { flex: 1 },
  module:    { fontSize: fontSize.sm, fontWeight: '700', color: colors.text },
  conf:      { fontSize: fontSize.xs, color: colors.textSecondary },
  plateNumber: { fontSize: fontSize.md, fontWeight: '800', color: colors.secondary, letterSpacing: 1 },
  metaRow:   { flexDirection: 'row', gap: spacing.xs, marginTop: 2 },
  meta:      { fontSize: fontSize.xs, color: colors.textSecondary },
  time:      { fontSize: fontSize.xs, color: colors.textDisabled },
  matchBadge: { paddingHorizontal: spacing.sm, paddingVertical: 3, borderRadius: radius.full, borderWidth: 1 },
  matchText: { fontSize: 10, fontWeight: '700', textTransform: 'uppercase' },
})
