import React, { useState, useCallback } from 'react'
import {
  ActivityIndicator, Alert, FlatList, RefreshControl,
  StyleSheet, Text, View, Pressable,
} from 'react-native'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Ionicons } from '@expo/vector-icons'
import {
  getWorkPermits, getDeliveries, approveWorkPermit,
  type WorkPermit, type Delivery,
} from '@/api/contractors'
import { Card } from '@/components/Card'
import { colors, fontSize, radius, spacing } from '@/theme'

const PERMIT_COLOR: Record<string, string> = {
  pending:   colors.warning,
  approved:  colors.info,
  active:    colors.success,
  completed: colors.textDisabled,
  cancelled: colors.textDisabled,
  rejected:  colors.error,
}

const DELIVERY_COLOR: Record<string, string> = {
  pending:   colors.warning,
  received:  colors.info,
  collected: colors.success,
  returned:  colors.textDisabled,
}

function StatusPill({ status, colorMap }: { status: string; colorMap: Record<string, string> }) {
  const color = colorMap[status] ?? colors.textDisabled
  return (
    <View style={[styles.pill, { backgroundColor: color + '28', borderColor: color }]}>
      <Text style={[styles.pillText, { color }]}>{status}</Text>
    </View>
  )
}

function PermitRow({ item, onApprove }: { item: WorkPermit; onApprove: (id: string) => void }) {
  return (
    <Card style={styles.row}>
      <View style={styles.rowTop}>
        <View style={styles.iconWrap}>
          <Ionicons name="construct-outline" size={18} color={colors.warning} />
        </View>
        <View style={styles.rowInfo}>
          <Text style={styles.name}>{item.contractor_name}</Text>
          {item.company_name && <Text style={styles.sub}>{item.company_name}</Text>}
          <Text style={styles.sub}>{item.work_description}</Text>
        </View>
        <StatusPill status={item.status} colorMap={PERMIT_COLOR} />
      </View>
      <View style={styles.metaRow}>
        <Ionicons name="calendar-outline" size={12} color={colors.textSecondary} />
        <Text style={styles.metaText}>
          {new Date(item.start_at).toLocaleDateString()} – {new Date(item.end_at).toLocaleDateString()}
        </Text>
      </View>
      {item.status === 'pending' && (
        <Pressable style={[styles.actionBtn, { borderColor: colors.success }]} onPress={() => onApprove(item.id)}>
          <Ionicons name="checkmark-outline" size={14} color={colors.success} />
          <Text style={[styles.actionText, { color: colors.success }]}>Approve</Text>
        </Pressable>
      )}
    </Card>
  )
}

function DeliveryRow({ item }: { item: Delivery }) {
  return (
    <Card style={styles.row}>
      <View style={styles.rowTop}>
        <View style={[styles.iconWrap, { backgroundColor: colors.info + '20' }]}>
          <Ionicons name="cube-outline" size={18} color={colors.info} />
        </View>
        <View style={styles.rowInfo}>
          <Text style={styles.name}>{item.recipient_name}</Text>
          {item.sender_name && <Text style={styles.sub}>From: {item.sender_name}</Text>}
          {item.courier && <Text style={styles.sub}>Via: {item.courier}</Text>}
          {item.tracking_number && <Text style={styles.sub}>TRK: {item.tracking_number}</Text>}
        </View>
        <StatusPill status={item.status} colorMap={DELIVERY_COLOR} />
      </View>
      {item.received_at && (
        <View style={styles.metaRow}>
          <Ionicons name="time-outline" size={12} color={colors.textSecondary} />
          <Text style={styles.metaText}>Received {new Date(item.received_at).toLocaleString()}</Text>
        </View>
      )}
    </Card>
  )
}

export function ContractorsScreen() {
  const qc = useQueryClient()
  const [tab, setTab] = useState<'permits' | 'deliveries'>('permits')
  const [refreshing, setRefreshing] = useState(false)

  const { data: permits = [], isLoading: loadingPermits } = useQuery({
    queryKey: ['work-permits'],
    queryFn: () => getWorkPermits({ limit: 50 }),
  })

  const { data: deliveries = [], isLoading: loadingDeliveries } = useQuery({
    queryKey: ['deliveries'],
    queryFn: () => getDeliveries({ limit: 50 }),
    enabled: tab === 'deliveries',
  })

  const approveMutation = useMutation({
    mutationFn: approveWorkPermit,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['work-permits'] }),
    onError: () => Alert.alert('Error', 'Failed to approve work permit.'),
  })

  const onRefresh = useCallback(async () => {
    setRefreshing(true)
    await qc.invalidateQueries({ queryKey: ['work-permits', 'deliveries'] })
    setRefreshing(false)
  }, [qc])

  const isLoading = tab === 'permits' ? loadingPermits : loadingDeliveries
  const data = (tab === 'permits' ? permits : deliveries) as any[]

  return (
    <View style={styles.root}>
      <View style={styles.tabRow}>
        {(['permits', 'deliveries'] as const).map((t) => (
          <Pressable key={t} style={[styles.tabBtn, tab === t && styles.tabActive]} onPress={() => setTab(t)}>
            <Text style={[styles.tabText, tab === t && styles.tabTextActive]}>
              {t === 'permits' ? 'Work Permits' : 'Deliveries'}
            </Text>
          </Pressable>
        ))}
      </View>

      {isLoading ? (
        <View style={styles.center}><ActivityIndicator color={colors.primary} /></View>
      ) : data.length === 0 ? (
        <View style={styles.center}>
          <Ionicons name="construct-outline" size={48} color={colors.textDisabled} />
          <Text style={styles.emptyText}>No {tab}</Text>
        </View>
      ) : (
        <FlatList
          data={data}
          keyExtractor={(item) => item.id}
          renderItem={({ item }) =>
            tab === 'permits'
              ? <PermitRow item={item} onApprove={approveMutation.mutate} />
              : <DeliveryRow item={item} />
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
  name:          { fontSize: fontSize.md, fontWeight: '700', color: colors.text },
  sub:           { fontSize: fontSize.xs, color: colors.textSecondary, marginTop: 1 },
  metaRow:       { flexDirection: 'row', alignItems: 'center', gap: 4 },
  metaText:      { fontSize: fontSize.xs, color: colors.textSecondary },
  pill:          { paddingHorizontal: spacing.sm, paddingVertical: 3, borderRadius: radius.full, borderWidth: 1 },
  pillText:      { fontSize: 10, fontWeight: '700', textTransform: 'uppercase', letterSpacing: 0.5 },
  actionBtn:     { flexDirection: 'row', alignItems: 'center', gap: 4, paddingHorizontal: spacing.sm, paddingVertical: 5, borderRadius: radius.sm, borderWidth: 1, alignSelf: 'flex-start' },
  actionText:    { fontSize: fontSize.xs, fontWeight: '600' },
})
