import React, { useState } from 'react'
import {
  ActivityIndicator, Alert as RNAlert, FlatList, Modal, Pressable,
  ScrollView, StyleSheet, Text, TextInput, View,
} from 'react-native'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useRoute } from '@react-navigation/native'
import type { RouteProp } from '@react-navigation/native'
import { Ionicons } from '@expo/vector-icons'
import { getDOBEntries, createDOBEntry, ENTRY_TYPES, type DOBEntry } from '@/api/dob'
import { enqueueRequest, isNetworkError } from '@/offline/outbox'
import { Card } from '@/components/Card'
import { colors, fontSize, radius, severity as sevColors, spacing } from '@/theme'
import type { PatrolStackParamList } from '@/navigation'

type RouteType = RouteProp<PatrolStackParamList, 'OccurrenceBook'>

const ENTRY_TYPE_ICONS: Record<string, React.ComponentProps<typeof Ionicons>['name']> = {
  general: 'document-text',
  incident: 'warning',
  patrol_start: 'walk',
  patrol_end: 'flag',
  visitor_arrival: 'person-add',
  visitor_departure: 'person-remove',
  guard_relief: 'swap-horizontal',
  equipment_check: 'build',
  maintenance: 'construct',
  alarm_activation: 'alarm',
  fire_drill: 'flame',
  handover: 'clipboard',
}

const SEVERITY_OPTIONS = ['info', 'low', 'medium', 'high', 'critical']

function EntryTypeLabel({ type }: { type: string }) {
  const icon = ENTRY_TYPE_ICONS[type] ?? 'document'
  return (
    <View style={styles.typeRow}>
      <Ionicons name={icon} size={12} color={colors.secondary} />
      <Text style={styles.typeText}>{type.replace(/_/g, ' ')}</Text>
    </View>
  )
}

function SeverityDot({ sev }: { sev: string | null }) {
  if (!sev) return null
  const col = sevColors[sev] ?? colors.textSecondary
  return (
    <View style={[styles.sevDot, { backgroundColor: col + '30', borderColor: col }]}>
      <Text style={[styles.sevText, { color: col }]}>{sev}</Text>
    </View>
  )
}

function DOBEntryCard({ entry }: { entry: DOBEntry }) {
  const ts = new Date(entry.occurred_at)
  return (
    <Card style={styles.entryCard}>
      <View style={styles.entryHeader}>
        <EntryTypeLabel type={entry.entry_type} />
        <SeverityDot sev={entry.severity} />
      </View>
      <Text style={styles.entryBody}>{entry.body}</Text>
      <View style={styles.entryMeta}>
        <Text style={styles.metaText}>{entry.author_name ?? entry.author_email ?? 'Unknown'}</Text>
        {entry.site_name ? <Text style={styles.metaText}> · {entry.site_name}</Text> : null}
        <Text style={styles.metaText}> · {ts.toLocaleString()}</Text>
      </View>
    </Card>
  )
}

function AddEntryModal({
  visible,
  shiftId,
  onClose,
}: {
  visible: boolean
  shiftId?: string
  onClose: () => void
}) {
  const qc = useQueryClient()
  const [entryType, setEntryType] = useState<string>('general')
  const [body, setBody] = useState('')
  const [sev, setSev] = useState<string>('')
  const [showTypeMenu, setShowTypeMenu] = useState(false)
  const [showSevMenu, setShowSevMenu] = useState(false)

  const reset = () => {
    setEntryType('general')
    setBody('')
    setSev('')
  }

  const { mutate, isPending } = useMutation({
    mutationFn: () =>
      createDOBEntry({
        entry_type: entryType,
        body: body.trim(),
        severity: sev || undefined,
        shift_id: shiftId,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['dob-entries'] })
      reset()
      onClose()
    },
    onError: (err) => {
      // Offline (Gap 88): queue the entry with its original occurrence time;
      // the outbox replays it when signal returns.
      if (isNetworkError(err)) {
        void enqueueRequest(
          '/api/v1/dob',
          {
            entry_type: entryType,
            body: body.trim(),
            severity: sev || undefined,
            shift_id: shiftId,
            occurred_at: new Date().toISOString(),
          },
          `DOB entry — ${entryType.replace(/_/g, ' ')}`,
        )
        reset()
        onClose()
        RNAlert.alert('Saved Offline', 'No signal — the entry is queued and will sync automatically.')
        return
      }
      RNAlert.alert('Error', 'Failed to create entry. Please try again.')
    },
  })

  return (
    <Modal visible={visible} animationType="slide" transparent onRequestClose={onClose}>
      <View style={styles.modalBg}>
        <View style={styles.modalCard}>
          <Text style={styles.modalTitle}>New Occurrence Entry</Text>

          {/* Entry type selector */}
          <Pressable style={styles.picker} onPress={() => setShowTypeMenu(!showTypeMenu)}>
            <Text style={styles.pickerValue}>{entryType.replace(/_/g, ' ')}</Text>
            <Ionicons name="chevron-down" size={16} color={colors.textSecondary} />
          </Pressable>
          {showTypeMenu && (
            <ScrollView style={styles.dropdown} nestedScrollEnabled>
              {ENTRY_TYPES.map((t) => (
                <Pressable key={t} style={styles.dropItem} onPress={() => { setEntryType(t); setShowTypeMenu(false) }}>
                  <Text style={[styles.dropItemText, t === entryType && styles.dropItemActive]}>
                    {t.replace(/_/g, ' ')}
                  </Text>
                </Pressable>
              ))}
            </ScrollView>
          )}

          {/* Body text */}
          <TextInput
            style={styles.bodyInput}
            placeholder="Describe what happened…"
            placeholderTextColor={colors.textDisabled}
            value={body}
            onChangeText={setBody}
            multiline
            numberOfLines={5}
            textAlignVertical="top"
          />

          {/* Severity selector */}
          <Pressable style={styles.picker} onPress={() => setShowSevMenu(!showSevMenu)}>
            <Text style={styles.pickerValue}>{sev || 'Severity (optional)'}</Text>
            <Ionicons name="chevron-down" size={16} color={colors.textSecondary} />
          </Pressable>
          {showSevMenu && (
            <View style={styles.dropdown}>
              <Pressable style={styles.dropItem} onPress={() => { setSev(''); setShowSevMenu(false) }}>
                <Text style={styles.dropItemText}>None</Text>
              </Pressable>
              {SEVERITY_OPTIONS.map((s) => (
                <Pressable key={s} style={styles.dropItem} onPress={() => { setSev(s); setShowSevMenu(false) }}>
                  <Text style={[styles.dropItemText, s === sev && styles.dropItemActive]}>{s}</Text>
                </Pressable>
              ))}
            </View>
          )}

          <View style={styles.modalActions}>
            <Pressable style={styles.cancelBtn} onPress={() => { reset(); onClose() }}>
              <Text style={styles.cancelBtnText}>Cancel</Text>
            </Pressable>
            <Pressable
              style={[styles.submitBtn, (!body.trim() || isPending) && styles.btnDisabled]}
              disabled={!body.trim() || isPending}
              onPress={() => mutate()}
            >
              {isPending
                ? <ActivityIndicator color="#fff" size="small" />
                : <Text style={styles.submitBtnText}>Add Entry</Text>
              }
            </Pressable>
          </View>
        </View>
      </View>
    </Modal>
  )
}

export function OccurrenceBookScreen() {
  const route = useRoute<RouteType>()
  const shiftId = route.params?.shiftId
  const [addOpen, setAddOpen] = useState(false)

  const { data: entries = [], isLoading, refetch } = useQuery({
    queryKey: ['dob-entries', shiftId],
    queryFn: () => getDOBEntries({ shift_id: shiftId, limit: 100 }),
  })

  if (isLoading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={colors.primary} />
      </View>
    )
  }

  return (
    <View style={styles.root}>
      <AddEntryModal visible={addOpen} shiftId={shiftId} onClose={() => setAddOpen(false)} />

      <FlatList
        data={entries as DOBEntry[]}
        keyExtractor={(e) => e.id}
        contentContainerStyle={styles.list}
        onRefresh={refetch}
        refreshing={isLoading}
        ListHeaderComponent={
          <View style={styles.listHeader}>
            <Text style={styles.header}>
              {shiftId ? 'Shift Log' : 'Occurrence Book'}
            </Text>
            <Pressable style={styles.addBtn} onPress={() => setAddOpen(true)}>
              <Ionicons name="add" size={18} color="#fff" />
              <Text style={styles.addBtnText}>New Entry</Text>
            </Pressable>
          </View>
        }
        ListEmptyComponent={
          <Card>
            <Text style={styles.empty}>No entries yet. Tap "New Entry" to log an occurrence.</Text>
          </Card>
        }
        renderItem={({ item }) => <DOBEntryCard entry={item} />}
      />
    </View>
  )
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.background },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  list: { padding: spacing.md, gap: spacing.sm },
  listHeader: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: spacing.sm },
  header: { fontSize: fontSize.lg, fontWeight: '700', color: colors.text },
  addBtn: {
    flexDirection: 'row', alignItems: 'center', gap: 4,
    backgroundColor: colors.primary,
    paddingHorizontal: spacing.sm, paddingVertical: spacing.xs,
    borderRadius: radius.xs,
  },
  addBtnText: { color: '#fff', fontSize: fontSize.xs, fontWeight: '700' },
  empty: { color: colors.textSecondary, textAlign: 'center', paddingVertical: spacing.sm },
  entryCard: { gap: spacing.xs },
  entryHeader: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  typeRow: { flexDirection: 'row', alignItems: 'center', gap: 4 },
  typeText: { fontSize: fontSize.xs, color: colors.secondary, fontWeight: '600', textTransform: 'capitalize' },
  sevDot: {
    borderRadius: radius.xs, paddingHorizontal: 6, paddingVertical: 2,
    borderWidth: 1,
  },
  sevText: { fontSize: 10, fontWeight: '700' },
  entryBody: { fontSize: fontSize.sm, color: colors.text, lineHeight: 20 },
  entryMeta: { flexDirection: 'row', flexWrap: 'wrap' },
  metaText: { fontSize: fontSize.xs, color: colors.textSecondary },
  // Modal
  modalBg: {
    flex: 1, backgroundColor: 'rgba(0,0,0,0.7)',
    justifyContent: 'flex-end',
  },
  modalCard: {
    backgroundColor: colors.surface,
    borderTopLeftRadius: radius.lg, borderTopRightRadius: radius.lg,
    padding: spacing.lg, gap: spacing.sm,
    maxHeight: '90%',
  },
  modalTitle: { fontSize: fontSize.lg, fontWeight: '700', color: colors.text, marginBottom: spacing.xs },
  picker: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    borderWidth: 1, borderColor: colors.cardBorder,
    borderRadius: radius.sm, paddingHorizontal: spacing.md, paddingVertical: spacing.sm,
    backgroundColor: colors.card,
  },
  pickerValue: { fontSize: fontSize.sm, color: colors.text, textTransform: 'capitalize' },
  dropdown: {
    backgroundColor: colors.card,
    borderWidth: 1, borderColor: colors.cardBorder,
    borderRadius: radius.sm, maxHeight: 200,
  },
  dropItem: { paddingHorizontal: spacing.md, paddingVertical: spacing.sm },
  dropItemText: { fontSize: fontSize.sm, color: colors.text, textTransform: 'capitalize' },
  dropItemActive: { color: colors.primary, fontWeight: '700' },
  bodyInput: {
    borderWidth: 1, borderColor: colors.cardBorder,
    borderRadius: radius.sm, padding: spacing.md,
    color: colors.text, backgroundColor: colors.card,
    fontSize: fontSize.sm, minHeight: 100,
  },
  modalActions: { flexDirection: 'row', gap: spacing.sm, marginTop: spacing.xs },
  cancelBtn: {
    flex: 1, alignItems: 'center', paddingVertical: spacing.sm,
    borderRadius: radius.sm, borderWidth: 1, borderColor: colors.divider,
  },
  cancelBtnText: { color: colors.textSecondary, fontSize: fontSize.sm, fontWeight: '600' },
  submitBtn: {
    flex: 1, alignItems: 'center', paddingVertical: spacing.sm,
    borderRadius: radius.sm, backgroundColor: colors.primary,
  },
  submitBtnText: { color: '#fff', fontSize: fontSize.sm, fontWeight: '700' },
  btnDisabled: { opacity: 0.5 },
})
