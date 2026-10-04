/**
 * What the drones found: open events first, newest at the top. A row opens the
 * event, where the picture, the place and the response actions are.
 *
 * Risk and AI confidence sit side by side and are labelled apart on every row:
 * "91% sure it is a person" and "how much that matters here, now" are different
 * questions, and an officer deciding whether to walk over needs both.
 */
import React, { useCallback, useState } from 'react'
import {
  ActivityIndicator, FlatList, Pressable, RefreshControl, StyleSheet, Text, View,
} from 'react-native'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigation } from '@react-navigation/native'
import { Ionicons } from '@expo/vector-icons'
import { getDroneEvents, type DroneEvent } from '@/api/drones'
import { Card } from '@/components/Card'
import { SeverityBadge } from '@/components/SeverityBadge'
import { useWebSocket, type RealtimeEvent } from '@/hooks/useWebSocket'
import { confidenceLabel, eventTitle, isDroneRealtime, pretty } from '@/lib/droneEvents'
import { colors, fontSize, radius, spacing } from '@/theme'

const FILTERS = ['open', 'all'] as const
type Filter = typeof FILTERS[number]

function EventRow({ item, onOpen }: { item: DroneEvent; onOpen: (id: string) => void }) {
  return (
    <Pressable onPress={() => onOpen(item.id)}>
      <Card style={styles.row}>
        <View style={styles.rowTop}>
          <Text style={styles.title} numberOfLines={1}>{eventTitle(item)}</Text>
          <Text style={styles.time}>
            {new Date(item.detected_at).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
          </Text>
        </View>
        <View style={styles.badgeRow}>
          <SeverityBadge value={item.risk_level.toLowerCase()} />
          <Text style={styles.risk}>{item.risk_score != null ? `Risk ${item.risk_score}` : 'Risk —'}</Text>
          <Text style={styles.confidence}>{confidenceLabel(item.ai_confidence)}</Text>
        </View>
        <View style={styles.metaRow}>
          <Ionicons name="location-outline" size={12} color={colors.info} />
          <Text style={[styles.metaText, { color: colors.info }]} numberOfLines={1}>
            {[item.site_name, item.zone_name].filter(Boolean).join(' · ') || 'Site unknown'}
          </Text>
        </View>
        <View style={styles.metaRow}>
          <Ionicons name="airplane-outline" size={12} color={colors.textSecondary} />
          <Text style={styles.metaText} numberOfLines={1}>
            {[item.drone_name, pretty(item.status), item.incident_id ? 'has an incident' : null].filter(Boolean).join(' · ')}
          </Text>
        </View>
      </Card>
    </Pressable>
  )
}

export function DroneEventsScreen() {
  const qc = useQueryClient()
  const navigation = useNavigation<any>()
  const [filter, setFilter] = useState<Filter>('open')
  const [refreshing, setRefreshing] = useState(false)

  const { data: events = [], isLoading, error } = useQuery({
    queryKey: ['drone-events', filter],
    queryFn: () => getDroneEvents({ openOnly: filter === 'open' }),
    refetchInterval: 30_000,
  })

  const handleEvent = useCallback((e: RealtimeEvent) => {
    if (isDroneRealtime(e)) qc.invalidateQueries({ queryKey: ['drone-events'] })
  }, [qc])
  useWebSocket(handleEvent)

  const onRefresh = useCallback(async () => {
    setRefreshing(true)
    await qc.invalidateQueries({ queryKey: ['drone-events'] })
    setRefreshing(false)
  }, [qc])

  return (
    <View style={styles.root}>
      <View style={styles.chipRow}>
        {FILTERS.map((f) => (
          <Pressable key={f} style={[styles.chip, filter === f && styles.chipActive]} onPress={() => setFilter(f)}>
            <Text style={[styles.chipText, filter === f && styles.chipTextActive]}>
              {f === 'open' ? 'Open' : 'All'}
            </Text>
          </Pressable>
        ))}
      </View>
      {isLoading ? (
        <View style={styles.center}><ActivityIndicator color={colors.primary} /></View>
      ) : error ? (
        <View style={styles.center}>
          <Ionicons name="cloud-offline-outline" size={48} color={colors.textDisabled} />
          <Text style={styles.emptyText}>Drone events could not be loaded.</Text>
        </View>
      ) : events.length === 0 ? (
        <View style={styles.center}>
          <Ionicons name="checkmark-circle-outline" size={48} color={colors.textDisabled} />
          <Text style={styles.emptyText}>{filter === 'open' ? 'No open drone events' : 'No drone events yet'}</Text>
        </View>
      ) : (
        <FlatList
          data={events}
          keyExtractor={(item) => item.id}
          renderItem={({ item }) => (
            <EventRow item={item} onOpen={(eventId) => navigation.navigate('DroneEventDetail', { eventId })} />
          )}
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
  center:         { flex: 1, alignItems: 'center', justifyContent: 'center', gap: spacing.sm, padding: spacing.lg },
  emptyText:      { fontSize: fontSize.md, color: colors.textSecondary, textAlign: 'center' },
  chipRow:        { flexDirection: 'row', padding: spacing.md, paddingBottom: spacing.sm, gap: spacing.xs },
  chip:           { borderRadius: radius.md, borderWidth: 1, borderColor: colors.cardBorder, paddingHorizontal: spacing.md, paddingVertical: spacing.xs },
  chipActive:     { backgroundColor: colors.primary, borderColor: colors.primary },
  chipText:       { fontSize: fontSize.sm, color: colors.textSecondary },
  chipTextActive: { color: '#fff', fontWeight: '600' },
  list:           { padding: spacing.md, paddingTop: 0, paddingBottom: spacing.xl },
  row:            { gap: 6 },
  rowTop:         { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start' },
  title:          { flex: 1, fontSize: fontSize.md, fontWeight: '600', color: colors.text, marginRight: spacing.sm },
  time:           { fontSize: fontSize.xs, color: colors.textSecondary },
  badgeRow:       { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, flexWrap: 'wrap' },
  risk:           { fontSize: fontSize.xs, color: colors.text, fontWeight: '700' },
  confidence:     { fontSize: fontSize.xs, color: colors.textSecondary },
  metaRow:        { flexDirection: 'row', alignItems: 'center', gap: 4 },
  metaText:       { fontSize: fontSize.xs, color: colors.textSecondary, flexShrink: 1 },
})
