import React, { useState, useCallback } from 'react'
import {
  ActivityIndicator, Alert, FlatList, RefreshControl,
  StyleSheet, Text, View, Pressable,
} from 'react-native'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Ionicons } from '@expo/vector-icons'
import { getDoors, getAccessEvents, lockDoor, unlockDoor, type Door, type AccessEvent } from '@/api/access_control'
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

function DoorRow({ item, onLock, onUnlock }: { item: Door; onLock: (id: string) => void; onUnlock: (id: string) => void }) {
  const lockColor = item.is_locked ? colors.error : colors.success
  return (
    <Card style={styles.row}>
      <View style={styles.rowTop}>
        <View style={[styles.iconWrap, { backgroundColor: lockColor + '20' }]}>
          <Ionicons name={item.is_locked ? 'lock-closed-outline' : 'lock-open-outline'} size={18} color={lockColor} />
        </View>
        <View style={styles.rowInfo}>
          <Text style={styles.name}>{item.name}</Text>
          {item.location && <Text style={styles.sub}>{item.location}</Text>}
          <Text style={styles.sub}>{item.door_type.replace('_', ' ')}</Text>
        </View>
        <View style={[styles.pill, { backgroundColor: lockColor + '20', borderColor: lockColor }]}>
          <Text style={[styles.pillText, { color: lockColor }]}>{item.is_locked ? 'LOCKED' : 'OPEN'}</Text>
        </View>
      </View>
      {item.held_open && (
        <View style={styles.warningBanner}>
          <Ionicons name="warning-outline" size={12} color={colors.warning} />
          <Text style={[styles.sub, { color: colors.warning }]}>Door held open</Text>
        </View>
      )}
      <View style={styles.actionRow}>
        {item.is_locked ? (
          <Pressable style={[styles.actionBtn, { borderColor: colors.success }]} onPress={() => onUnlock(item.id)}>
            <Ionicons name="lock-open-outline" size={14} color={colors.success} />
            <Text style={[styles.actionText, { color: colors.success }]}>Unlock</Text>
          </Pressable>
        ) : (
          <Pressable style={[styles.actionBtn, { borderColor: colors.warning }]} onPress={() => onLock(item.id)}>
            <Ionicons name="lock-closed-outline" size={14} color={colors.warning} />
            <Text style={[styles.actionText, { color: colors.warning }]}>Lock</Text>
          </Pressable>
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

  const lockMutation = useMutation({
    mutationFn: lockDoor,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['access-doors'] }),
    onError: () => Alert.alert('Error', 'Failed to lock door.'),
  })

  const unlockMutation = useMutation({
    mutationFn: (id: string) => unlockDoor(id, 10),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['access-doors'] }),
    onError: () => Alert.alert('Error', 'Failed to unlock door.'),
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
              ? <DoorRow item={item} onLock={lockMutation.mutate} onUnlock={unlockMutation.mutate} />
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
