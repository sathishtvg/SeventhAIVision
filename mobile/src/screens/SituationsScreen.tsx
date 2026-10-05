/**
 * The security situations in front of this person: what the command centre
 * sent them first, then the rest by risk. A row opens the situation.
 *
 * Two things sit side by side on every row and are never one: the risk, which
 * is the layer's assessment, and where the situation stands, which is set only
 * by what people decided.
 */
import React, { useCallback, useState } from 'react'
import { ActivityIndicator, FlatList, Pressable, RefreshControl, StyleSheet, Text, View } from 'react-native'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigation } from '@react-navigation/native'
import { Ionicons } from '@expo/vector-icons'
import { getMySituations, intelApiError, type MySituation } from '@/api/securityIntelligence'
import { Card } from '@/components/Card'
import { SeverityBadge } from '@/components/SeverityBadge'
import { useWebSocket, type RealtimeEvent } from '@/hooks/useWebSocket'
import { STATUS_LABEL, isIntelRealtime, riskText, sourcesText } from '@/lib/situations'
import { colors, fontSize, radius, spacing } from '@/theme'

function SituationRow({ item, onOpen }: { item: MySituation; onOpen: (id: string) => void }) {
  return (
    <Pressable onPress={() => onOpen(item.id)}>
      <Card style={[styles.row, item.assigned_to_me && styles.rowMine]}>
        {item.assigned_to_me && (
          <View style={styles.sentRow}>
            <Ionicons name="send" size={12} color={colors.primary} />
            <Text style={styles.sent}>
              Sent to you{item.my_last === 'ARRIVED' ? ' · you are there' : item.my_last === 'ACCEPTED' ? ' · accepted' : ''}
            </Text>
          </View>
        )}
        <View style={styles.rowTop}>
          <Text style={styles.title} numberOfLines={2}>{item.label ?? item.title}</Text>
          <Text style={styles.time}>
            {new Date(item.last_event_at).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
          </Text>
        </View>
        <View style={styles.badgeRow}>
          {item.risk_level && <SeverityBadge value={item.risk_level.toLowerCase()} />}
          <Text style={styles.risk}>{riskText(item.risk_level, item.risk_score)}</Text>
          <Text style={styles.stands}>{STATUS_LABEL[item.decision_status] ?? item.decision_status}</Text>
        </View>
        {item.reassessed_since_decision && (
          <View style={styles.metaRow}>
            <Ionicons name="refresh-outline" size={12} color={colors.textSecondary} />
            <Text style={styles.metaText}>Assessed again since the last decision</Text>
          </View>
        )}
        <View style={styles.metaRow}>
          <Ionicons name="location-outline" size={12} color={colors.info} />
          <Text style={[styles.metaText, { color: colors.info }]} numberOfLines={1}>
            {[item.site_name, item.primary_camera_name ?? item.location_label].filter(Boolean).join(' · ') || 'Site unknown'}
          </Text>
        </View>
        <View style={styles.metaRow}>
          <Ionicons name="layers-outline" size={12} color={colors.textSecondary} />
          <Text style={styles.metaText} numberOfLines={1}>
            {sourcesText(item.source_types)} · {item.event_count} event{item.event_count === 1 ? '' : 's'}
          </Text>
        </View>
      </Card>
    </Pressable>
  )
}

export function SituationsScreen() {
  const qc = useQueryClient()
  const navigation = useNavigation<any>()
  const [refreshing, setRefreshing] = useState(false)

  const { data: situations = [], isLoading, error } = useQuery({
    queryKey: ['my-situations'],
    queryFn: getMySituations,
    refetchInterval: 30_000,
  })

  const handleEvent = useCallback((e: RealtimeEvent) => {
    if (isIntelRealtime(e)) qc.invalidateQueries({ queryKey: ['my-situations'] })
  }, [qc])
  useWebSocket(handleEvent)

  const onRefresh = useCallback(async () => {
    setRefreshing(true)
    await qc.invalidateQueries({ queryKey: ['my-situations'] })
    setRefreshing(false)
  }, [qc])

  if (isLoading) return <View style={styles.center}><ActivityIndicator color={colors.primary} /></View>

  return (
    <View style={styles.root}>
      <FlatList
        data={situations}
        keyExtractor={(s) => s.id}
        contentContainerStyle={styles.list}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.primary} />}
        renderItem={({ item }) => (
          <SituationRow item={item} onOpen={(id) => navigation.navigate('SituationDetail', { situationId: id })} />
        )}
        ListEmptyComponent={
          <View style={styles.center}>
            <Ionicons name={error ? 'alert-circle-outline' : 'shield-checkmark-outline'} size={48}
                      color={colors.textDisabled} />
            <Text style={styles.empty}>
              {error ? intelApiError(error) : 'Nothing is open in front of you.'}
            </Text>
            {!error && (
              <Text style={styles.emptyHint}>
                What the command centre sends you appears here, and the open situations at your site where your
                organisation lets you decide on them.
              </Text>
            )}
          </View>
        }
      />
    </View>
  )
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.background },
  list: { padding: spacing.md, gap: spacing.sm, flexGrow: 1 },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: spacing.xl, gap: spacing.sm },
  empty: { color: colors.textSecondary, fontSize: fontSize.md, textAlign: 'center' },
  emptyHint: { color: colors.textDisabled, fontSize: fontSize.sm, textAlign: 'center' },
  row: { gap: 6 },
  rowMine: { borderColor: colors.primary, borderWidth: 1.5 },
  sentRow: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  sent: { color: colors.primary, fontSize: fontSize.xs, fontWeight: '700', letterSpacing: 0.4 },
  rowTop: { flexDirection: 'row', justifyContent: 'space-between', gap: spacing.sm },
  title: { flex: 1, color: colors.text, fontSize: fontSize.md, fontWeight: '600' },
  time: { color: colors.textSecondary, fontSize: fontSize.xs },
  badgeRow: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, flexWrap: 'wrap' },
  risk: { color: colors.text, fontSize: fontSize.sm, fontWeight: '700' },
  stands: {
    color: colors.textSecondary, fontSize: fontSize.xs, borderWidth: 1, borderColor: colors.glassBorder,
    borderRadius: radius.sm, paddingHorizontal: 6, paddingVertical: 2,
  },
  metaRow: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  metaText: { flex: 1, color: colors.textSecondary, fontSize: fontSize.sm },
})
