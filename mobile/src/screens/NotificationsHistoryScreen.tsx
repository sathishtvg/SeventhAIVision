import React, { useState } from 'react'
import {
  ActivityIndicator, FlatList, Pressable, RefreshControl,
  StyleSheet, Text, View,
} from 'react-native'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Ionicons } from '@expo/vector-icons'
import { getLogs, type NotificationLog } from '@/api/notifications'
import { Card } from '@/components/Card'
import { colors, fontSize, radius, spacing } from '@/theme'

const STATUS_COLOR: Record<string, string> = {
  sent:    colors.success,
  failed:  colors.error,
  pending: colors.warning,
}

const CHANNEL_ICON: Record<string, React.ComponentProps<typeof Ionicons>['name']> = {
  email:   'mail-outline',
  sms:     'chatbubble-outline',
  webhook: 'globe-outline',
}

function LogRow({ item }: { item: NotificationLog }) {
  const statusColor = STATUS_COLOR[item.status] ?? colors.textDisabled
  const icon = CHANNEL_ICON[item.channel_type] ?? 'notifications-outline'
  return (
    <Card style={styles.row}>
      <View style={styles.rowTop}>
        <View style={[styles.iconWrap, { backgroundColor: statusColor + '20' }]}>
          <Ionicons name={icon} size={16} color={statusColor} />
        </View>
        <View style={styles.info}>
          <Text style={styles.channel}>{item.channel_type.toUpperCase()}</Text>
          <Text style={styles.alertId} numberOfLines={1}>Alert {item.alert_id.slice(0, 8)}…</Text>
        </View>
        <View style={[styles.pill, { backgroundColor: statusColor + '28', borderColor: statusColor }]}>
          <Text style={[styles.pillText, { color: statusColor }]}>{item.status}</Text>
        </View>
      </View>
      {item.error_detail && (
        <Text style={styles.errorText} numberOfLines={2}>{item.error_detail}</Text>
      )}
      <Text style={styles.time}>
        {item.sent_at
          ? new Date(item.sent_at).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
          : new Date(item.created_at).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
      </Text>
    </Card>
  )
}

export function NotificationsHistoryScreen() {
  const qc = useQueryClient()
  const [statusFilter, setStatusFilter] = useState<string | undefined>(undefined)
  const [refreshing, setRefreshing] = useState(false)

  const { data = [], isLoading } = useQuery({
    queryKey: ['notification-logs', statusFilter],
    queryFn: () => getLogs({ status_filter: statusFilter, limit: 100 }),
  })

  const onRefresh = async () => {
    setRefreshing(true)
    await qc.invalidateQueries({ queryKey: ['notification-logs'] })
    setRefreshing(false)
  }

  const FILTERS = [
    { label: 'All', value: undefined },
    { label: 'Sent', value: 'sent' },
    { label: 'Failed', value: 'failed' },
    { label: 'Pending', value: 'pending' },
  ]

  return (
    <View style={styles.root}>
      {/* Filter chips */}
      <View style={styles.chipRow}>
        {FILTERS.map((f) => (
          <Pressable
            key={String(f.value)}
            style={[styles.chip, statusFilter === f.value && styles.chipActive]}
            onPress={() => setStatusFilter(f.value)}
          >
            <Text style={[styles.chipText, statusFilter === f.value && styles.chipTextActive]}>
              {f.label}
            </Text>
          </Pressable>
        ))}
      </View>

      {isLoading ? (
        <View style={styles.center}>
          <ActivityIndicator color={colors.primary} />
        </View>
      ) : data.length === 0 ? (
        <View style={styles.center}>
          <Ionicons name="notifications-off-outline" size={48} color={colors.textDisabled} />
          <Text style={styles.emptyText}>No notification logs</Text>
        </View>
      ) : (
        <FlatList
          data={data}
          keyExtractor={(item) => item.id}
          renderItem={({ item }) => <LogRow item={item} />}
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
  chipRow:       { flexDirection: 'row', padding: spacing.md, paddingBottom: spacing.sm, gap: spacing.xs },
  chip:          { borderRadius: radius.full, borderWidth: 1, borderColor: colors.cardBorder, paddingHorizontal: spacing.md, paddingVertical: 5 },
  chipActive:    { backgroundColor: colors.primary, borderColor: colors.primary },
  chipText:      { fontSize: fontSize.sm, color: colors.textSecondary },
  chipTextActive:{ color: '#fff', fontWeight: '600' },
  list:          { padding: spacing.md, paddingTop: 0, paddingBottom: spacing.xl },
  row:           { gap: 4 },
  rowTop:        { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  iconWrap:      { width: 32, height: 32, borderRadius: radius.sm, alignItems: 'center', justifyContent: 'center' },
  info:          { flex: 1 },
  channel:       { fontSize: fontSize.xs, fontWeight: '800', color: colors.text, letterSpacing: 0.5 },
  alertId:       { fontSize: fontSize.xs, color: colors.textSecondary, marginTop: 1 },
  pill:          { paddingHorizontal: spacing.sm, paddingVertical: 3, borderRadius: radius.full, borderWidth: 1 },
  pillText:      { fontSize: 10, fontWeight: '700', textTransform: 'uppercase' },
  errorText:     { fontSize: fontSize.xs, color: colors.error, marginTop: 2 },
  time:          { fontSize: fontSize.xs, color: colors.textDisabled, marginTop: 2 },
})
