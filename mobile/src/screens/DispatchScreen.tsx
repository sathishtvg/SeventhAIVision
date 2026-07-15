import React, { useState } from 'react'
import {
  ActivityIndicator, Alert, FlatList, Modal, Pressable,
  RefreshControl, ScrollView, StyleSheet, Text, TextInput, View,
} from 'react-native'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Ionicons } from '@expo/vector-icons'
import { getIncidents, type Incident } from '@/api/incidents'
import { dispatchToIncident, markArrived, type DispatchRecord } from '@/api/dispatch'
import { Card } from '@/components/Card'
import { colors, fontSize, radius, spacing } from '@/theme'

function SeverityDot({ severity }: { severity: string }) {
  const color =
    severity === 'critical' ? colors.error :
    severity === 'high'     ? '#FF6B35' :
    severity === 'medium'   ? colors.warning :
    colors.success
  return <View style={[styles.dot, { backgroundColor: color }]} />
}

function IncidentRow({
  item,
  onDispatch,
  onArrived,
}: {
  item: Incident
  onDispatch: (i: Incident) => void
  onArrived: (i: Incident) => void
}) {
  const hasDispatch = (item as any).dispatch_status === 'dispatched'
  return (
    <Card style={styles.row}>
      <View style={styles.rowTop}>
        <SeverityDot severity={item.severity} />
        <View style={styles.info}>
          <Text style={styles.title} numberOfLines={1}>{item.title}</Text>
          <Text style={styles.sub}>
            {new Date(item.created_at).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
          </Text>
        </View>
        <View style={[styles.statusPill, { backgroundColor: colors.warning + '28', borderColor: colors.warning }]}>
          <Text style={[styles.statusText, { color: colors.warning }]}>{item.status}</Text>
        </View>
      </View>
      {item.description && (
        <Text style={styles.desc} numberOfLines={2}>{item.description}</Text>
      )}
      <View style={styles.actionRow}>
        {!hasDispatch && (
          <Pressable style={[styles.actionBtn, { borderColor: colors.primary }]} onPress={() => onDispatch(item)}>
            <Ionicons name="send-outline" size={14} color={colors.primary} />
            <Text style={[styles.actionText, { color: colors.primary }]}>Dispatch Guard</Text>
          </Pressable>
        )}
        {hasDispatch && (
          <Pressable style={[styles.actionBtn, { borderColor: colors.success }]} onPress={() => onArrived(item)}>
            <Ionicons name="checkmark-circle-outline" size={14} color={colors.success} />
            <Text style={[styles.actionText, { color: colors.success }]}>Mark Arrived</Text>
          </Pressable>
        )}
      </View>
    </Card>
  )
}

export function DispatchScreen() {
  const qc = useQueryClient()
  const [refreshing, setRefreshing] = useState(false)
  const [target, setTarget] = useState<Incident | null>(null)
  const [form, setForm] = useState({ eta: '', notes: '' })

  const { data: incidents = [], isLoading } = useQuery({
    queryKey: ['incidents-open'],
    queryFn: () => getIncidents('open'),
  })

  const dispatchMutation = useMutation({
    mutationFn: ({ incident, form }: { incident: Incident; form: { eta: string; notes: string } }) =>
      dispatchToIncident(incident.id, {
        guard_user_id: '', // would come from a guard picker in a full impl
        eta_minutes: form.eta ? parseInt(form.eta, 10) : undefined,
        notes: form.notes || undefined,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['incidents-open'] })
      setTarget(null)
      setForm({ eta: '', notes: '' })
    },
    onError: () => Alert.alert('Error', 'Failed to dispatch guard.'),
  })

  const arrivedMutation = useMutation({
    mutationFn: (incidentId: string) => markArrived(incidentId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['incidents-open'] }),
    onError: () => Alert.alert('Error', 'Failed to mark guard as arrived.'),
  })

  const onRefresh = async () => {
    setRefreshing(true)
    await qc.invalidateQueries({ queryKey: ['incidents-open'] })
    setRefreshing(false)
  }

  return (
    <View style={styles.root}>
      {isLoading ? (
        <View style={styles.center}>
          <ActivityIndicator color={colors.primary} />
        </View>
      ) : incidents.length === 0 ? (
        <View style={styles.center}>
          <Ionicons name="checkmark-done-circle-outline" size={48} color={colors.success} />
          <Text style={styles.emptyText}>No open incidents</Text>
        </View>
      ) : (
        <FlatList
          data={incidents}
          keyExtractor={(item) => item.id}
          renderItem={({ item }) => (
            <IncidentRow
              item={item}
              onDispatch={setTarget}
              onArrived={(i) => arrivedMutation.mutate(i.id)}
            />
          )}
          ItemSeparatorComponent={() => <View style={{ height: spacing.xs }} />}
          contentContainerStyle={styles.list}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.primary} />}
        />
      )}

      {/* Dispatch modal */}
      <Modal visible={target !== null} transparent animationType="slide">
        <View style={styles.overlay}>
          <View style={styles.modal}>
            <View style={styles.modalHeader}>
              <Text style={styles.modalTitle}>Dispatch Guard</Text>
              <Pressable onPress={() => setTarget(null)}>
                <Ionicons name="close" size={20} color={colors.textSecondary} />
              </Pressable>
            </View>
            <Text style={styles.modalSub} numberOfLines={2}>{target?.title}</Text>

            <View style={styles.fieldGroup}>
              <Text style={styles.fieldLabel}>ETA (minutes)</Text>
              <TextInput
                style={styles.textInput}
                value={form.eta}
                onChangeText={(v) => setForm((p) => ({ ...p, eta: v }))}
                keyboardType="numeric"
                placeholder="e.g. 5"
                placeholderTextColor={colors.textDisabled}
              />
            </View>
            <View style={styles.fieldGroup}>
              <Text style={styles.fieldLabel}>Notes (optional)</Text>
              <TextInput
                style={[styles.textInput, { height: 72 }]}
                value={form.notes}
                onChangeText={(v) => setForm((p) => ({ ...p, notes: v }))}
                placeholder="Any instructions for the guard"
                placeholderTextColor={colors.textDisabled}
                multiline
              />
            </View>

            <Pressable
              style={[styles.confirmBtn, dispatchMutation.isPending && { opacity: 0.5 }]}
              onPress={() => target && dispatchMutation.mutate({ incident: target, form })}
              disabled={dispatchMutation.isPending}
            >
              {dispatchMutation.isPending
                ? <ActivityIndicator size="small" color="#fff" />
                : (
                  <>
                    <Ionicons name="send-outline" size={16} color="#fff" />
                    <Text style={styles.confirmBtnText}>Dispatch</Text>
                  </>
                )}
            </Pressable>
          </View>
        </View>
      </Modal>
    </View>
  )
}

const styles = StyleSheet.create({
  root:           { flex: 1, backgroundColor: colors.background },
  center:         { flex: 1, alignItems: 'center', justifyContent: 'center', gap: spacing.sm },
  emptyText:      { color: colors.textSecondary, fontSize: fontSize.md },
  list:           { padding: spacing.md, paddingBottom: spacing.xl },
  row:            { gap: 6 },
  rowTop:         { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  dot:            { width: 10, height: 10, borderRadius: 5, flexShrink: 0 },
  info:           { flex: 1 },
  title:          { fontSize: fontSize.md, fontWeight: '700', color: colors.text },
  sub:            { fontSize: fontSize.xs, color: colors.textSecondary, marginTop: 1 },
  desc:           { fontSize: fontSize.sm, color: colors.textSecondary, marginTop: 2, lineHeight: 18 },
  statusPill:     { paddingHorizontal: spacing.sm, paddingVertical: 3, borderRadius: radius.full, borderWidth: 1 },
  statusText:     { fontSize: 10, fontWeight: '700', textTransform: 'uppercase' },
  actionRow:      { flexDirection: 'row', gap: spacing.sm, marginTop: spacing.xs },
  actionBtn:      { flexDirection: 'row', alignItems: 'center', gap: 4, paddingHorizontal: spacing.sm, paddingVertical: 5, borderRadius: radius.sm, borderWidth: 1 },
  actionText:     { fontSize: fontSize.xs, fontWeight: '600' },
  // modal
  overlay:        { flex: 1, backgroundColor: 'rgba(0,0,0,0.6)', justifyContent: 'flex-end' },
  modal:          { backgroundColor: '#0D1B2E', borderTopLeftRadius: radius.xl, borderTopRightRadius: radius.xl, padding: spacing.lg, paddingBottom: 36, borderWidth: 1, borderColor: colors.cardBorder },
  modalHeader:    { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: spacing.xs },
  modalTitle:     { fontSize: fontSize.lg, fontWeight: '700', color: colors.text },
  modalSub:       { fontSize: fontSize.sm, color: colors.textSecondary, marginBottom: spacing.md },
  fieldGroup:     { marginBottom: spacing.sm },
  fieldLabel:     { fontSize: fontSize.xs, color: colors.textSecondary, marginBottom: 4 },
  textInput:      { borderRadius: radius.sm, borderWidth: 1, borderColor: colors.cardBorder, backgroundColor: colors.surface, color: colors.text, paddingHorizontal: spacing.md, fontSize: fontSize.md, height: 42 },
  confirmBtn:     { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: spacing.xs, paddingVertical: 12, borderRadius: radius.sm, backgroundColor: colors.primary, marginTop: spacing.md },
  confirmBtnText: { color: '#fff', fontWeight: '700', fontSize: fontSize.md },
})
