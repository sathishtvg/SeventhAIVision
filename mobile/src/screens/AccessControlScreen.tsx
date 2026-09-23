import React, { useState, useCallback } from 'react'
import {
  ActivityIndicator, Alert, FlatList, RefreshControl,
  StyleSheet, Text, View, Pressable,
} from 'react-native'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Ionicons } from '@expo/vector-icons'
import { getDoors, getAccessEvents, type Door, type AccessEvent } from '@/api/access_control'
import { Card } from '@/components/Card'
import { colors, fontSize, radius, spacing } from '@/theme'

const EVENT_COLOR: Record<string, string> = {
  granted:     colors.success,
  denied:      colors.error,
  forced:      colors.error,
  tamper:      colors.error,
  held_open:   colors.warning,
  door_opened: colors.info,
  door_closed: colors.textSecondary,
}

function DoorRow({ item }: { item: Door }) {
  /*
   * Shows what the server knows about a door: where it is, what kind it is,
   * which camera watches it. It used to show a padlock and a LOCKED/OPEN pill
   * driven by `is_locked`, a field the API has never returned — so every door
   * read as OPEN, on every site, forever — above Lock and Unlock buttons
   * calling routes that do not exist. Live door state is in the events tab,
   * which is where the readers actually report it.
   */
  const inactive = !item.is_active
  return (
    <Card style={styles.row}>
      <View style={styles.rowTop}>
        <View style={[styles.iconWrap, { backgroundColor: colors.primary + '20' }]}>
          <Ionicons name="git-branch-outline" size={18} color={colors.primary} />
        </View>
        <View style={styles.rowInfo}>
          <Text style={styles.name}>{item.name}</Text>
          {!!item.location && <Text style={styles.sub}>{item.location}</Text>}
          <Text style={styles.sub}>
            {[item.door_type?.replace(/_/g, ' '), item.site_name].filter(Boolean).join(' · ')}
          </Text>
          {!!item.camera_name && (
            <Text style={styles.sub}>Camera: {item.camera_name}</Text>
          )}
        </View>
        {inactive && (
          <View style={[styles.pill, { backgroundColor: colors.textDisabled + '20', borderColor: colors.textDisabled }]}>
            <Text style={[styles.pillText, { color: colors.textDisabled }]}>INACTIVE</Text>
          </View>
        )}
      </View>
    </Card>
  )
}

function EventRow({ item }: { item: AccessEvent }) {
  const color = EVENT_COLOR[item.event_type] ?? colors.textSecondary
  return (
    <Card style={styles.row}>
      <View style={styles.rowTop}>
        <View style={[styles.dot, { backgroundColor: color }]} />
        <View style={styles.rowInfo}>
          <Text style={[styles.name, { textTransform: 'capitalize' }]}>{item.event_type.replace(/_/g, ' ')}</Text>
          {item.door_name && <Text style={styles.sub}>{item.door_name}</Text>}
          {item.holder_name && <Text style={styles.sub}>{item.holder_name}</Text>}
        </View>
        <Text style={styles.time}>{new Date(item.occurred_at).toLocaleTimeString()}</Text>
      </View>
    </Card>
  )
}

export function AccessControlScreen() {
  const qc = useQueryClient()
  const [tab, setTab] = useState<'doors' | 'events'>('doors')
  const [refreshing, setRefreshing] = useState(false)

  const { data: doors = [], isLoading: loadingDoors } = useQuery({
    queryKey: ['access-doors'],
    queryFn: () => getDoors(),
  })

  const { data: events = [], isLoading: loadingEvents } = useQuery({
    queryKey: ['access-events'],
    queryFn: () => getAccessEvents({ limit: 50 }),
    enabled: tab === 'events',
  })

  const onRefresh = useCallback(async () => {
    setRefreshing(true)
    await qc.invalidateQueries({ queryKey: ['access'] })
    setRefreshing(false)
  }, [qc])

  const isLoading = tab === 'doors' ? loadingDoors : loadingEvents
  const data = (tab === 'doors' ? doors : events) as any[]

  return (
    <View style={styles.root}>
      <View style={styles.tabRow}>
        {(['doors', 'events'] as const).map((t) => (
          <Pressable key={t} style={[styles.tabBtn, tab === t && styles.tabActive]} onPress={() => setTab(t)}>
            <Text style={[styles.tabText, tab === t && styles.tabTextActive]}>
              {t === 'doors' ? 'Doors' : 'Events'}
            </Text>
          </Pressable>
        ))}
      </View>

      {isLoading ? (
        <View style={styles.center}><ActivityIndicator color={colors.primary} /></View>
      ) : data.length === 0 ? (
        <View style={styles.center}>
          <Ionicons name="lock-closed-outline" size={48} color={colors.textDisabled} />
          <Text style={styles.emptyText}>No {tab}</Text>
        </View>
      ) : (
        <FlatList
          data={data}
          keyExtractor={(item) => item.id}
          renderItem={({ item }) =>
            tab === 'doors'
              ? <DoorRow item={item} />
              : <EventRow item={item} />
          }
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
  tabRow:        { flexDirection: 'row', padding: spacing.md, gap: spacing.sm },
  tabBtn:        { flex: 1, paddingVertical: 8, borderRadius: radius.sm, borderWidth: 1, borderColor: colors.cardBorder, alignItems: 'center' },
  tabActive:     { backgroundColor: colors.primary, borderColor: colors.primary },
  tabText:       { fontSize: fontSize.sm, color: colors.textSecondary, fontWeight: '600' },
  tabTextActive: { color: '#fff' },
  list:          { padding: spacing.md, paddingTop: 0, paddingBottom: spacing.xl },
  row:           { gap: 6 },
  rowTop:        { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  iconWrap:      { width: 36, height: 36, borderRadius: 18, alignItems: 'center', justifyContent: 'center' },
  rowInfo:       { flex: 1 },
  name:          { fontSize: fontSize.md, fontWeight: '700', color: colors.text },
  sub:           { fontSize: fontSize.xs, color: colors.textSecondary, marginTop: 1 },
  dot:           { width: 10, height: 10, borderRadius: 5, flexShrink: 0 },
  time:          { fontSize: fontSize.xs, color: colors.textSecondary },
  warningBanner: { flexDirection: 'row', alignItems: 'center', gap: 4 },
  pill:          { paddingHorizontal: spacing.sm, paddingVertical: 3, borderRadius: radius.full, borderWidth: 1 },
  pillText:      { fontSize: 10, fontWeight: '700', letterSpacing: 0.5 },
  actionRow:     { flexDirection: 'row', gap: spacing.sm },
  actionBtn:     { flexDirection: 'row', alignItems: 'center', gap: 4, paddingHorizontal: spacing.sm, paddingVertical: 5, borderRadius: radius.sm, borderWidth: 1 },
  actionText:    { fontSize: fontSize.xs, fontWeight: '600' },
})
