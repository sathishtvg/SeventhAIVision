import React, { useState, useCallback } from 'react'
import {
  ActivityIndicator, Alert, FlatList, RefreshControl,
  ScrollView, StyleSheet, Text, View, Pressable,
} from 'react-native'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Ionicons } from '@expo/vector-icons'
import {
  getAlarmPanels, getAlarmEvents, armPanel, disarmPanel,
  type AlarmPanel, type AlarmEvent,
} from '@/api/alarms'
import { Card } from '@/components/Card'
import { colors, fontSize, radius, spacing } from '@/theme'

const ARM_COLOR: Record<string, string> = {
  disarmed:    colors.success,
  armed_away:  colors.warning,
  armed_stay:  colors.warning,
  armed_night: colors.info,
  triggered:   colors.error,
  alarm:       colors.error,
  fault:       colors.error,
}

function ArmPill({ status }: { status: string }) {
  const color = ARM_COLOR[status] ?? colors.textDisabled
  const label = status.replace('_', ' ')
  return (
    <View style={[styles.pill, { backgroundColor: color + '28', borderColor: color }]}>
      <Text style={[styles.pillText, { color }]}>{label}</Text>
    </View>
  )
}

function PanelRow({ item, onArm, onDisarm }: { item: AlarmPanel; onArm: (p: AlarmPanel) => void; onDisarm: (p: AlarmPanel) => void }) {
  return (
    <Card style={styles.row}>
      <View style={styles.rowTop}>
        <View style={styles.iconWrap}>
          <Ionicons name="shield-outline" size={18} color={colors.warning} />
        </View>
        <View style={styles.rowInfo}>
          <Text style={styles.name}>{item.name}</Text>
          {item.model && <Text style={styles.sub}>{item.model}</Text>}
          {item.zone_count != null && <Text style={styles.sub}>{item.zone_count} zones</Text>}
        </View>
        <ArmPill status={item.arm_status} />
      </View>
      <View style={styles.actionRow}>
        {item.arm_status === 'disarmed' ? (
          <Pressable style={[styles.actionBtn, { borderColor: colors.warning }]} onPress={() => onArm(item)}>
            <Ionicons name="lock-closed-outline" size={14} color={colors.warning} />
            <Text style={[styles.actionText, { color: colors.warning }]}>Arm Away</Text>
          </Pressable>
        ) : (
          <Pressable style={[styles.actionBtn, { borderColor: colors.success }]} onPress={() => onDisarm(item)}>
            <Ionicons name="lock-open-outline" size={14} color={colors.success} />
            <Text style={[styles.actionText, { color: colors.success }]}>Disarm</Text>
          </Pressable>
        )}
      </View>
    </Card>
  )
}

function EventRow({ item }: { item: AlarmEvent }) {
  const color = item.severity === 'critical' || item.severity === 'high' ? colors.error
    : item.severity === 'medium' ? colors.warning : colors.info
  return (
    <Card style={styles.row}>
      <View style={styles.rowTop}>
        <View style={[styles.dot, { backgroundColor: color }]} />
        <View style={styles.rowInfo}>
          <Text style={styles.name}>{item.event_type.replace(/_/g, ' ')}</Text>
          {item.zone_name && <Text style={styles.sub}>Zone: {item.zone_name}</Text>}
          {item.panel_name && <Text style={styles.sub}>{item.panel_name}</Text>}
        </View>
        <Text style={styles.time}>{new Date(item.occurred_at).toLocaleTimeString()}</Text>
      </View>
    </Card>
  )
}

export function AlarmsScreen() {
  const qc = useQueryClient()
  const [tab, setTab] = useState<'panels' | 'events'>('panels')
  const [refreshing, setRefreshing] = useState(false)

  const { data: panels = [], isLoading: loadingPanels } = useQuery({
    queryKey: ['alarm-panels'],
    queryFn: () => getAlarmPanels(),
  })

  const { data: events = [], isLoading: loadingEvents } = useQuery({
    queryKey: ['alarm-events'],
    queryFn: () => getAlarmEvents({ limit: 50 }),
  })

  const armMutation = useMutation({
    mutationFn: (p: AlarmPanel) => armPanel(p.id, 'away'),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['alarm-panels'] }),
    onError: () => Alert.alert('Error', 'Failed to arm panel.'),
  })

  const disarmMutation = useMutation({
    mutationFn: (p: AlarmPanel) => disarmPanel(p.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['alarm-panels'] }),
    onError: () => Alert.alert('Error', 'Failed to disarm panel.'),
  })

  const onRefresh = useCallback(async () => {
    setRefreshing(true)
    await qc.invalidateQueries({ queryKey: ['alarm'] })
    setRefreshing(false)
  }, [qc])

  const isLoading = tab === 'panels' ? loadingPanels : loadingEvents
  const data = (tab === 'panels' ? panels : events) as any[]

  return (
    <View style={styles.root}>
      <View style={styles.tabRow}>
        {(['panels', 'events'] as const).map((t) => (
          <Pressable key={t} style={[styles.tabBtn, tab === t && styles.tabActive]} onPress={() => setTab(t)}>
            <Text style={[styles.tabText, tab === t && styles.tabTextActive]}>{t === 'panels' ? 'Panels' : 'Events'}</Text>
          </Pressable>
        ))}
      </View>

      {isLoading ? (
        <View style={styles.center}><ActivityIndicator color={colors.primary} /></View>
      ) : data.length === 0 ? (
        <View style={styles.center}>
          <Ionicons name="shield-outline" size={48} color={colors.textDisabled} />
          <Text style={styles.emptyText}>No {tab}</Text>
        </View>
      ) : (
        <FlatList
          data={data}
          keyExtractor={(item) => item.id}
          renderItem={({ item }) =>
            tab === 'panels'
              ? <PanelRow item={item} onArm={armMutation.mutate} onDisarm={disarmMutation.mutate} />
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
  iconWrap:      { width: 36, height: 36, borderRadius: 18, backgroundColor: colors.warning + '20', alignItems: 'center', justifyContent: 'center' },
  rowInfo:       { flex: 1 },
  name:          { fontSize: fontSize.md, fontWeight: '700', color: colors.text, textTransform: 'capitalize' },
  sub:           { fontSize: fontSize.xs, color: colors.textSecondary, marginTop: 1 },
  dot:           { width: 10, height: 10, borderRadius: 5, flexShrink: 0 },
  time:          { fontSize: fontSize.xs, color: colors.textSecondary },
  pill:          { paddingHorizontal: spacing.sm, paddingVertical: 3, borderRadius: radius.full, borderWidth: 1 },
  pillText:      { fontSize: 10, fontWeight: '700', textTransform: 'uppercase', letterSpacing: 0.5 },
  actionRow:     { flexDirection: 'row', gap: spacing.sm },
  actionBtn:     { flexDirection: 'row', alignItems: 'center', gap: 4, paddingHorizontal: spacing.sm, paddingVertical: 5, borderRadius: radius.sm, borderWidth: 1 },
  actionText:    { fontSize: fontSize.xs, fontWeight: '600' },
})
