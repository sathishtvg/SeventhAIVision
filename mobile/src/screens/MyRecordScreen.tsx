import React, { useState } from 'react'
import {
  ActivityIndicator, Alert, FlatList, Modal, Pressable,
  ScrollView, StyleSheet, Text, TextInput, View,
} from 'react-native'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Ionicons } from '@expo/vector-icons'
import { getMyViolations, getMyViolationsSummary, type Violation } from '@/api/violations'
import {
  getLeaveTypes, getMyLeaveRequests, getMyLeaveBalances,
  createLeaveRequest, cancelLeaveRequest, type LeaveRequest, type LeaveType, type LeaveBalanceRow,
} from '@/api/leave'
import { useAuthStore } from '@/store/auth'
import { Card } from '@/components/Card'
import { StatusBadge } from '@/components/StatusBadge'
import { colors, fontSize, radius, spacing } from '@/theme'

type Tab = 'violations' | 'leave'

function pointsColor(total: number) {
  if (total >= 25) return colors.error
  if (total >= 10) return colors.warning
  return colors.success
}

function ViolationsTab() {
  const { data: summary = [], isLoading: loadSummary } = useQuery({
    queryKey: ['my-violations-summary'],
    queryFn: () => getMyViolationsSummary(),
  })
  const { data: violations = [], isLoading: loadList } = useQuery({
    queryKey: ['my-violations'],
    queryFn: getMyViolations,
  })

  const totalPoints = summary[0]?.total_points ?? 0

  if (loadSummary || loadList) {
    return <View style={styles.center}><ActivityIndicator color={colors.primary} /></View>
  }

  return (
    <View style={styles.tabContent}>
      <Card style={styles.summaryCard}>
        <Text style={[styles.summaryValue, { color: pointsColor(totalPoints) }]}>{totalPoints}</Text>
        <Text style={styles.summaryLabel}>Total Points (last 90 days)</Text>
      </Card>

      {violations.length === 0 ? (
        <View style={styles.center}>
          <Ionicons name="checkmark-done-circle-outline" size={40} color={colors.textDisabled} />
          <Text style={styles.emptyText}>No violations on record</Text>
        </View>
      ) : (
        <FlatList
          data={violations}
          keyExtractor={(item: Violation) => item.id}
          scrollEnabled={false}
          contentContainerStyle={{ gap: spacing.sm }}
          renderItem={({ item }) => (
            <Card style={styles.rowCard}>
              <View style={styles.rowHeader}>
                <Text style={styles.rowTitle}>{item.violation_type.replace(/_/g, ' ')}</Text>
                <Text style={styles.rowPoints}>{item.points} pts</Text>
              </View>
              {item.description && <Text style={styles.rowDesc}>{item.description}</Text>}
              <View style={styles.rowFooter}>
                <StatusBadge value={item.status} />
                <Text style={styles.rowDate}>
                  {new Date(item.occurred_at).toLocaleDateString()}
                  {item.site_name ? ` · ${item.site_name}` : ''}
                </Text>
              </View>
            </Card>
          )}
        />
      )}
    </View>
  )
}

function RequestLeaveModal({ visible, onClose }: { visible: boolean; onClose: () => void }) {
  const qc = useQueryClient()
  const user = useAuthStore((s) => s.user)
  const { data: types = [] } = useQuery({ queryKey: ['leave-types'], queryFn: getLeaveTypes })
  const [typeId, setTypeId] = useState<string | null>(null)
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState('')
  const [reason, setReason] = useState('')

  const { mutate: submit, isPending } = useMutation({
    mutationFn: () => createLeaveRequest({
      guard_user_id: user!.id,
      leave_type_id: typeId!,
      start_date: startDate,
      end_date: endDate,
      reason: reason || undefined,
    }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['my-leave-requests'] })
      qc.invalidateQueries({ queryKey: ['my-leave-balances'] })
      setTypeId(null); setStartDate(''); setEndDate(''); setReason('')
      onClose()
    },
    onError: () => Alert.alert('Request failed', 'Could not submit the leave request. Please try again.'),
  })

  const canSubmit = !!typeId && /^\d{4}-\d{2}-\d{2}$/.test(startDate) && /^\d{4}-\d{2}-\d{2}$/.test(endDate)

  return (
    <Modal visible={visible} animationType="slide" onRequestClose={onClose}>
      <View style={styles.modalRoot}>
        <View style={styles.modalHeader}>
          <Text style={styles.modalTitle}>Request Leave</Text>
          <Pressable onPress={onClose}><Ionicons name="close" size={24} color={colors.text} /></Pressable>
        </View>
        <ScrollView contentContainerStyle={styles.formScroll}>
          <Text style={styles.label}>Leave Type</Text>
          <View style={styles.pillRow}>
            {types.filter((t: LeaveType) => t.is_active).map((t: LeaveType) => (
              <Pressable
                key={t.id}
                style={[styles.pill, typeId === t.id && styles.pillActive]}
                onPress={() => setTypeId(t.id)}
              >
                <Text style={[styles.pillText, typeId === t.id && styles.pillTextActive]}>{t.name}</Text>
              </Pressable>
            ))}
          </View>

          <Text style={styles.label}>Start Date</Text>
          <TextInput
            style={styles.input}
            placeholder="YYYY-MM-DD"
            placeholderTextColor={colors.textDisabled}
            value={startDate}
            onChangeText={setStartDate}
          />
          <Text style={styles.label}>End Date</Text>
          <TextInput
            style={styles.input}
            placeholder="YYYY-MM-DD"
            placeholderTextColor={colors.textDisabled}
            value={endDate}
            onChangeText={setEndDate}
          />
          <Text style={styles.label}>Reason (optional)</Text>
          <TextInput
            style={[styles.input, styles.textArea]}
            placeholder="Reason for leave"
            placeholderTextColor={colors.textDisabled}
            value={reason}
            onChangeText={setReason}
            multiline
          />

          <Pressable
            style={[styles.saveBtn, !canSubmit && styles.saveBtnDisabled]}
            disabled={!canSubmit || isPending}
            onPress={() => submit()}
          >
            {isPending ? <ActivityIndicator color="#fff" size="small" /> : (
              <Text style={styles.saveBtnText}>Submit Request</Text>
            )}
          </Pressable>
        </ScrollView>
      </View>
    </Modal>
  )
}

function LeaveTab() {
  const qc = useQueryClient()
  const [modalOpen, setModalOpen] = useState(false)
  const { data: balances = [], isLoading: loadBal } = useQuery({
    queryKey: ['my-leave-balances'],
    queryFn: getMyLeaveBalances,
  })
  const { data: requests = [], isLoading: loadReq } = useQuery({
    queryKey: ['my-leave-requests'],
    queryFn: getMyLeaveRequests,
  })

  const { mutate: cancel } = useMutation({
    mutationFn: (id: string) => cancelLeaveRequest(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['my-leave-requests'] })
      qc.invalidateQueries({ queryKey: ['my-leave-balances'] })
    },
  })

  if (loadBal || loadReq) {
    return <View style={styles.center}><ActivityIndicator color={colors.primary} /></View>
  }

  return (
    <View style={styles.tabContent}>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.balanceScroll}>
        {balances.map((b: LeaveBalanceRow) => (
          <Card key={b.leave_type_id} style={styles.balanceCard}>
            <Text style={styles.balanceType}>{b.name}</Text>
            <Text style={styles.balanceRemaining}>{b.remaining_days}</Text>
            <Text style={styles.balanceSub}>of {b.entitled_days} days left</Text>
          </Card>
        ))}
      </ScrollView>

      <Pressable style={styles.addBtn} onPress={() => setModalOpen(true)}>
        <Ionicons name="add" size={16} color="#fff" />
        <Text style={styles.addBtnText}>Request Leave</Text>
      </Pressable>

      {requests.length === 0 ? (
        <View style={styles.center}>
          <Ionicons name="calendar-outline" size={40} color={colors.textDisabled} />
          <Text style={styles.emptyText}>No leave requests yet</Text>
        </View>
      ) : (
        <FlatList
          data={requests}
          keyExtractor={(item: LeaveRequest) => item.id}
          scrollEnabled={false}
          contentContainerStyle={{ gap: spacing.sm }}
          renderItem={({ item }) => (
            <Card style={styles.rowCard}>
              <View style={styles.rowHeader}>
                <Text style={styles.rowTitle}>{item.leave_type_name}</Text>
                <Text style={styles.rowPoints}>{item.days_count}d</Text>
              </View>
              <Text style={styles.rowDesc}>{item.start_date} → {item.end_date}</Text>
              <View style={styles.rowFooter}>
                <StatusBadge value={item.status} />
                {(item.status === 'pending' || item.status === 'approved') && (
                  <Pressable onPress={() => cancel(item.id)}>
                    <Text style={styles.cancelText}>Cancel</Text>
                  </Pressable>
                )}
              </View>
            </Card>
          )}
        />
      )}

      <RequestLeaveModal visible={modalOpen} onClose={() => setModalOpen(false)} />
    </View>
  )
}

export function MyRecordScreen() {
  const [tab, setTab] = useState<Tab>('violations')

  return (
    <View style={styles.root}>
      <View style={styles.segmented}>
        {(['violations', 'leave'] as Tab[]).map((t) => (
          <Pressable
            key={t}
            style={[styles.segment, tab === t && styles.segmentActive]}
            onPress={() => setTab(t)}
          >
            <Text style={[styles.segmentText, tab === t && styles.segmentTextActive]}>
              {t === 'violations' ? 'Violations' : 'Leave'}
            </Text>
          </Pressable>
        ))}
      </View>
      <ScrollView contentContainerStyle={styles.scrollContent}>
        {tab === 'violations' ? <ViolationsTab /> : <LeaveTab />}
      </ScrollView>
    </View>
  )
}

const styles = StyleSheet.create({
  root:            { flex: 1, backgroundColor: colors.background },
  scrollContent:   { paddingBottom: spacing.xl },
  center:          { alignItems: 'center', justifyContent: 'center', gap: spacing.sm, paddingVertical: spacing.xl },
  emptyText:       { color: colors.textSecondary, fontSize: fontSize.md },
  tabContent:      { padding: spacing.md, gap: spacing.md },
  segmented:       { flexDirection: 'row', margin: spacing.md, marginBottom: 0, backgroundColor: colors.glassBgSubtle, borderRadius: radius.md, padding: 4, gap: 4 },
  segment:         { flex: 1, alignItems: 'center', paddingVertical: spacing.sm, borderRadius: radius.sm },
  segmentActive:   { backgroundColor: colors.primary },
  segmentText:     { fontSize: fontSize.sm, fontWeight: '600', color: colors.textSecondary },
  segmentTextActive: { color: '#fff' },
  summaryCard:     { alignItems: 'center', paddingVertical: spacing.lg },
  summaryValue:    { fontSize: fontSize.xxxl, fontWeight: '800' },
  summaryLabel:    { fontSize: fontSize.xs, color: colors.textSecondary, marginTop: 4 },
  rowCard:         { gap: 6 },
  rowHeader:       { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  rowTitle:        { fontSize: fontSize.md, fontWeight: '700', color: colors.text, textTransform: 'capitalize' },
  rowPoints:       { fontSize: fontSize.sm, fontWeight: '700', color: colors.warning },
  rowDesc:         { fontSize: fontSize.sm, color: colors.textSecondary },
  rowFooter:       { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginTop: 2 },
  rowDate:         { fontSize: fontSize.xs, color: colors.textSecondary },
  cancelText:      { fontSize: fontSize.sm, color: colors.error, fontWeight: '700' },
  balanceScroll:   { gap: spacing.sm, paddingBottom: spacing.xs },
  balanceCard:     { alignItems: 'center', width: 120, paddingVertical: spacing.md },
  balanceType:     { fontSize: fontSize.xs, color: colors.textSecondary, textAlign: 'center' },
  balanceRemaining:{ fontSize: fontSize.xxl, fontWeight: '800', color: colors.text, marginTop: 4 },
  balanceSub:      { fontSize: fontSize.xs, color: colors.textSecondary, marginTop: 2, textAlign: 'center' },
  addBtn:          { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 6, backgroundColor: colors.primary, borderRadius: radius.md, paddingVertical: spacing.sm },
  addBtnText:      { color: '#fff', fontWeight: '700', fontSize: fontSize.sm },
  modalRoot:       { flex: 1, backgroundColor: colors.background },
  modalHeader:     { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', padding: spacing.md, borderBottomWidth: 1, borderBottomColor: colors.divider },
  modalTitle:      { fontSize: fontSize.lg, fontWeight: '700', color: colors.text },
  formScroll:      { padding: spacing.md, gap: spacing.xs },
  label:           { fontSize: fontSize.xs, color: colors.textSecondary, textTransform: 'uppercase', letterSpacing: 0.5, marginTop: spacing.sm, marginBottom: 4 },
  pillRow:         { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.xs },
  pill:            { borderRadius: radius.full, borderWidth: 1, borderColor: colors.cardBorder, paddingHorizontal: spacing.md, paddingVertical: 6 },
  pillActive:      { backgroundColor: colors.primary, borderColor: colors.primary },
  pillText:        { fontSize: fontSize.sm, color: colors.textSecondary },
  pillTextActive:  { color: '#fff', fontWeight: '700' },
  input:           { borderWidth: 1, borderColor: colors.cardBorder, borderRadius: radius.md, paddingHorizontal: spacing.md, paddingVertical: spacing.sm, color: colors.text, fontSize: fontSize.md },
  textArea:        { minHeight: 70, textAlignVertical: 'top' },
  saveBtn:         { backgroundColor: colors.primary, borderRadius: radius.md, alignItems: 'center', paddingVertical: spacing.sm, marginTop: spacing.lg },
  saveBtnDisabled: { opacity: 0.4 },
  saveBtnText:     { color: '#fff', fontWeight: '700', fontSize: fontSize.md },
})
