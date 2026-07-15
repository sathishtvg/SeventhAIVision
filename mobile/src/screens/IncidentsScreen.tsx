import React, { useCallback, useState } from 'react'
import {
  ActivityIndicator, FlatList, Pressable, RefreshControl,
  ScrollView, StyleSheet, Text, View,
} from 'react-native'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigation } from '@react-navigation/native'
import type { NativeStackNavigationProp } from '@react-navigation/native-stack'
import { Ionicons } from '@expo/vector-icons'
import { getIncidents, type Incident } from '@/api/incidents'
import { getSites, type Site } from '@/api/sites'
import { Card } from '@/components/Card'
import { SeverityBadge } from '@/components/SeverityBadge'
import { StatusBadge } from '@/components/StatusBadge'
import { colors, fontSize, radius, spacing } from '@/theme'
import type { IncidentsStackParamList } from '@/navigation'

type NavProp = NativeStackNavigationProp<IncidentsStackParamList>

const STATUS_FILTERS = ['open', 'investigating', 'all'] as const
type StatusFilter = typeof STATUS_FILTERS[number]

const MODULE_FILTERS = [
  { key: 'lpr', label: 'LPR' },
  { key: 'face', label: 'Face' },
  { key: 'intrusion', label: 'Intrusion' },
  { key: 'ppe', label: 'PPE' },
  { key: 'crowd', label: 'Crowd' },
  { key: 'fire_smoke', label: 'Fire/Smoke' },
  { key: 'weapon', label: 'Weapon' },
  { key: 'behavior', label: 'Behavior' },
  { key: 'tampering', label: 'Tampering' },
  { key: 'abandoned', label: 'Abandoned' },
  { key: 'fall', label: 'Fall' },
] as const

function IncidentRow({ item }: { item: Incident }) {
  const nav = useNavigation<NavProp>()
  return (
    <Pressable onPress={() => nav.navigate('IncidentDetail', { incidentId: item.id })}>
      <Card style={styles.row}>
        <View style={styles.rowTop}>
          <Text style={styles.title} numberOfLines={1}>{item.title}</Text>
          <Text style={styles.time}>
            {new Date(item.created_at).toLocaleDateString([], { month: 'short', day: 'numeric' })}
          </Text>
        </View>
        <View style={styles.badgeRow}>
          <SeverityBadge value={item.severity} />
          <StatusBadge value={item.status} />
          {item.is_auto_created && (
            <View style={styles.autoBadge}>
              <Text style={styles.autoText}>AUTO</Text>
            </View>
          )}
        </View>
        {item.site_name && (
          <View style={styles.metaRow}>
            <Ionicons name="location-outline" size={12} color={colors.info} />
            <Text style={[styles.metaText, { color: colors.info }]}>{item.site_name}</Text>
          </View>
        )}
        {item.camera_name && (
          <View style={styles.metaRow}>
            <Ionicons name="videocam-outline" size={12} color={colors.textSecondary} />
            <Text style={styles.metaText}>{item.camera_name}</Text>
          </View>
        )}
      </Card>
    </Pressable>
  )
}

export function IncidentsScreen() {
  const qc = useQueryClient()
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('open')
  const [siteFilter, setSiteFilter] = useState<string | undefined>(undefined)
  const [moduleFilter, setModuleFilter] = useState<string | undefined>(undefined)
  const [refreshing, setRefreshing] = useState(false)

  const { data: sites = [] } = useQuery({
    queryKey: ['sites'],
    queryFn: getSites,
  })

  const { data: incidents = [], isLoading } = useQuery({
    queryKey: ['incidents', statusFilter, siteFilter, moduleFilter],
    queryFn: () => getIncidents(statusFilter === 'all' ? undefined : statusFilter, siteFilter, moduleFilter),
  })

  const onRefresh = useCallback(async () => {
    setRefreshing(true)
    await qc.invalidateQueries({ queryKey: ['incidents'] })
    setRefreshing(false)
  }, [qc])

  return (
    <View style={styles.root}>
      {/* Status filter chips */}
      <View style={styles.chipRow}>
        {STATUS_FILTERS.map((f) => (
          <Pressable
            key={f}
            style={[styles.chip, statusFilter === f && styles.chipActive]}
            onPress={() => setStatusFilter(f)}
          >
            <Text style={[styles.chipText, statusFilter === f && styles.chipTextActive]}>
              {f.charAt(0).toUpperCase() + f.slice(1)}
            </Text>
          </Pressable>
        ))}
      </View>

      {/* Site filter chips */}
      {sites.length > 0 && (
        <ScrollView
          horizontal
          showsHorizontalScrollIndicator={false}
          style={styles.siteScroll}
          contentContainerStyle={styles.siteChipRow}
        >
          <Pressable
            style={[styles.chip, siteFilter === undefined && styles.chipActiveSite]}
            onPress={() => setSiteFilter(undefined)}
          >
            <Text style={[styles.chipText, siteFilter === undefined && styles.chipTextActive]}>
              All Sites
            </Text>
          </Pressable>
          {sites.map((site: Site) => (
            <Pressable
              key={site.id}
              style={[styles.chip, siteFilter === site.id && styles.chipActiveSite]}
              onPress={() => setSiteFilter(siteFilter === site.id ? undefined : site.id)}
            >
              <Text style={[styles.chipText, siteFilter === site.id && styles.chipTextActive]}>
                {site.name}
              </Text>
            </Pressable>
          ))}
        </ScrollView>
      )}

      {/* Module filter chips */}
      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        style={styles.siteScroll}
        contentContainerStyle={styles.siteChipRow}
      >
        <Pressable
          style={[styles.chip, moduleFilter === undefined && styles.chipActiveModule]}
          onPress={() => setModuleFilter(undefined)}
        >
          <Text style={[styles.chipText, moduleFilter === undefined && styles.chipTextActive]}>All</Text>
        </Pressable>
        {MODULE_FILTERS.map((m) => (
          <Pressable
            key={m.key}
            style={[styles.chip, moduleFilter === m.key && styles.chipActiveModule]}
            onPress={() => setModuleFilter(moduleFilter === m.key ? undefined : m.key)}
          >
            <Text style={[styles.chipText, moduleFilter === m.key && styles.chipTextActive]}>{m.label}</Text>
          </Pressable>
        ))}
      </ScrollView>

      {isLoading ? (
        <View style={styles.center}>
          <ActivityIndicator color={colors.primary} />
        </View>
      ) : incidents.length === 0 ? (
        <View style={styles.center}>
          <Ionicons name="checkmark-done-circle-outline" size={48} color={colors.textDisabled} />
          <Text style={styles.emptyText}>No {statusFilter !== 'all' ? statusFilter : ''} incidents</Text>
        </View>
      ) : (
        <FlatList
          data={incidents}
          keyExtractor={(item) => item.id}
          renderItem={({ item }) => <IncidentRow item={item} />}
          ItemSeparatorComponent={() => <View style={{ height: spacing.xs }} />}
          contentContainerStyle={styles.list}
          refreshControl={
            <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.primary} />
          }
        />
      )}
    </View>
  )
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: colors.background,
  },
  chipRow: {
    flexDirection: 'row',
    padding: spacing.md,
    paddingBottom: spacing.sm,
    gap: spacing.xs,
  },
  siteScroll: {
    maxHeight: 44,
  },
  siteChipRow: {
    flexDirection: 'row',
    paddingHorizontal: spacing.md,
    paddingBottom: spacing.sm,
    gap: spacing.xs,
    alignItems: 'center',
  },
  chip: {
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.cardBorder,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.xs,
  },
  chipActive: {
    backgroundColor: colors.primary,
    borderColor: colors.primary,
  },
  chipActiveSite: {
    backgroundColor: colors.info + '33',
    borderColor: colors.info,
  },
  chipActiveModule: {
    backgroundColor: colors.success + '33',
    borderColor: colors.success,
  },
  chipText: {
    fontSize: fontSize.sm,
    color: colors.textSecondary,
  },
  chipTextActive: {
    color: '#fff',
    fontWeight: '600',
  },
  list: {
    padding: spacing.md,
    paddingTop: 0,
    paddingBottom: spacing.xl,
  },
  row: {
    gap: 6,
  },
  rowTop: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
  },
  title: {
    flex: 1,
    fontSize: fontSize.md,
    fontWeight: '600',
    color: colors.text,
    marginRight: spacing.sm,
  },
  time: {
    fontSize: fontSize.xs,
    color: colors.textSecondary,
  },
  badgeRow: {
    flexDirection: 'row',
    gap: spacing.xs,
    flexWrap: 'wrap',
  },
  autoBadge: {
    borderRadius: 4,
    borderWidth: 1,
    borderColor: colors.info,
    paddingHorizontal: 6,
    paddingVertical: 2,
  },
  autoText: {
    color: colors.info,
    fontSize: fontSize.xs,
    fontWeight: '700',
  },
  metaRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
  },
  metaText: {
    fontSize: fontSize.xs,
    color: colors.textSecondary,
  },
  center: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    gap: spacing.sm,
  },
  emptyText: {
    color: colors.textSecondary,
    fontSize: fontSize.md,
  },
})
