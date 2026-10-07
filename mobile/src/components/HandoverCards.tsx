/**
 * What one shift hands the next, on the first page of the phone.
 *
 * `InstructionsCard` — the instructions in force at the guard's sites, the
 * unread ones first, each with "I have read it". It exists only while there is
 * one in force.
 *
 * `ShiftSummaryCard` — the summary of the guard's own shift. The app drafts it
 * from what was recorded, in fixed sentences: it counts and quotes and does
 * not interpret. The guard reads it, corrects it and confirms it; once
 * confirmed it cannot be changed, and it goes with the handover.
 *
 * Both are for whoever reads handovers (`handover:read`) and hide themselves
 * for anybody else.
 */
import React, { useState } from 'react'
import {
  ActivityIndicator, Alert as RNAlert, Modal, Pressable, ScrollView, StyleSheet, Text, TextInput, View,
} from 'react-native'
import { Ionicons } from '@expo/vector-icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import {
  confirmShiftSummary, draftShiftSummary, editShiftSummary, getInstructions, getShiftSummary, markInstructionRead,
  type ShiftSummary,
} from '@/api/occurrenceBook'
import { getMyShifts } from '@/api/patrols'
import { canSee, isFieldRole } from '@/lib/access'
import {
  SUMMARY_BUTTON, SUMMARY_LINE, instructionMeta, instructionsHeading, shiftToSummarise, summaryStep, unreadFirst,
} from '@/lib/handoverNotes'
import { refusal } from '@/lib/responses'
import { useAuthStore } from '@/store/auth'
import { Card } from '@/components/Card'
import { colors, fontSize, radius, spacing } from '@/theme'

const when = (iso: string) =>
  new Date(iso).toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })

function useReadsHandovers(): boolean {
  const permissions = useAuthStore((s) => s.permissions)
  const roleId = useAuthStore((s) => s.user?.roleId)
  return canSee({ permission: 'handover:read' }, permissions, roleId)
}

export function InstructionsCard() {
  const qc = useQueryClient()
  const allowed = useReadsHandovers()
  const { data: items = [] } = useQuery({ queryKey: ['instructions', 'in-force'], queryFn: getInstructions, enabled: allowed })
  const { mutate: read, isPending, variables } = useMutation({
    mutationFn: markInstructionRead,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['instructions'] }),
    onError: (err) => RNAlert.alert('Not recorded', refusal(err)),
  })
  if (!allowed || !items.length) return null
  return (
    <Card style={styles.card}>
      <View style={styles.head}>
        <Ionicons name="megaphone-outline" size={18} color={colors.primary} />
        <Text style={styles.title}>{instructionsHeading(items)}</Text>
      </View>
      {unreadFirst(items).map((n) => (
        <View key={n.id} style={[styles.instruction, !n.read_by_me && styles.unread]}>
          <Text style={styles.body}>{n.body}</Text>
          <Text style={styles.meta}>{instructionMeta(n, when)}</Text>
          {n.read_by_me ? <Text style={styles.readMark}>You have read it</Text> : (
            <Pressable style={[styles.smallBtn, isPending && variables === n.id && styles.disabled]}
                       disabled={isPending} onPress={() => read(n.id)} accessibilityRole="button"
                       accessibilityLabel={`I have read: ${n.body}`}>
              <Text style={styles.smallBtnText}>I have read it</Text>
            </Pressable>)}
        </View>))}
    </Card>
  )
}

function SummaryModal({ summary, onClose, onDone }: { summary: ShiftSummary; onClose: () => void; onDone: () => void }) {
  const [words, setWords] = useState(summary.final_text)
  const changed = words.trim() !== summary.final_text.trim()
  const { mutate: confirm, isPending } = useMutation({
    // What is on the screen is what is confirmed: a correction is saved first.
    mutationFn: async () => {
      if (changed) await editShiftSummary(summary.id, words.trim())
      return confirmShiftSummary(summary.id)
    },
    onSuccess: () => { onDone(); onClose() },
    onError: (err) => RNAlert.alert('Not confirmed', refusal(err)),
  })
  const ask = () => RNAlert.alert(
    'Confirm this summary', 'Once confirmed it goes with the handover and cannot be changed.',
    [{ text: 'Not yet', style: 'cancel' }, { text: 'Confirm', onPress: () => confirm() }])
  return (
    <Modal visible animationType="slide" onRequestClose={onClose}>
      <View style={styles.modal}>
        <View style={styles.modalHead}>
          <Text style={styles.modalTitle}>{summary.site_name ?? 'Your shift'}</Text>
          <Pressable onPress={onClose} accessibilityRole="button" accessibilityLabel="Close">
            <Ionicons name="close" size={24} color={colors.text} />
          </Pressable>
        </View>
        <ScrollView contentContainerStyle={styles.modalBody} keyboardShouldPersistTaps="handled">
          {summary.may.edit ? (
            <>
              <Text style={styles.meta}>{summary.note}</Text>
              <TextInput style={styles.summaryInput} value={words} onChangeText={setWords} multiline maxLength={20000}
                         textAlignVertical="top" accessibilityLabel="The summary" />
            </>
          ) : <Text style={styles.summaryText}>{summary.final_text}</Text>}
        </ScrollView>
        {summary.may.confirm && (
          <Pressable style={[styles.bigBtn, (!words.trim() || isPending) && styles.disabled]}
                     disabled={!words.trim() || isPending} onPress={ask}>
            {isPending ? <ActivityIndicator color="#fff" size="small" /> : <Text style={styles.bigBtnText}>Confirm it</Text>}
          </Pressable>)}
      </View>
    </Modal>
  )
}

export function ShiftSummaryCard() {
  const qc = useQueryClient()
  const allowed = useReadsHandovers()
  const userId = useAuthStore((s) => s.user?.id)
  const roleId = useAuthStore((s) => s.user?.roleId)
  const [open, setOpen] = useState(false)
  // The people who work a shift. A supervisor writes summaries on the web.
  const works = allowed && isFieldRole(roleId) && !!userId
  const { data: shifts = [] } = useQuery({ queryKey: ['my-shifts'], queryFn: () => getMyShifts(userId!), enabled: works })
  const shift = shiftToSummarise(shifts)
  const { data: summary } = useQuery({
    queryKey: ['shift-summary', shift?.id], queryFn: () => getShiftSummary(shift!.id), enabled: works && !!shift })
  const again = () => qc.invalidateQueries({ queryKey: ['shift-summary'] })
  const { mutate: draft, isPending } = useMutation({
    mutationFn: () => draftShiftSummary(shift!.id),
    onSuccess: () => { void again().then(() => setOpen(true)) },
    onError: (err) => RNAlert.alert('Not drafted', refusal(err)),
  })
  if (!works || !shift) return null
  const step = summaryStep(summary)
  return (
    <Card style={styles.card}>
      <View style={styles.head}>
        <Ionicons name="document-text-outline" size={18} color={colors.primary} />
        <Text style={styles.title}>Shift summary</Text>
      </View>
      <Text style={styles.meta}>{SUMMARY_LINE[step]}</Text>
      <Pressable style={[styles.smallBtn, isPending && styles.disabled]} disabled={isPending} accessibilityRole="button"
                 onPress={() => (step === 'draft' ? draft() : setOpen(true))}>
        {isPending ? <ActivityIndicator color={colors.primary} size="small" />
          : <Text style={styles.smallBtnText}>{SUMMARY_BUTTON[step]}</Text>}
      </Pressable>
      {open && summary && <SummaryModal key={summary.id} summary={summary} onClose={() => setOpen(false)} onDone={again} />}
    </Card>
  )
}

const styles = StyleSheet.create({
  card: { gap: spacing.sm },
  head: { flexDirection: 'row', alignItems: 'center', gap: spacing.xs },
  title: { color: colors.text, fontSize: fontSize.md, fontWeight: '700', flex: 1 },
  instruction: { gap: 4, paddingVertical: spacing.sm, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.divider },
  unread: { borderLeftWidth: 3, borderLeftColor: colors.warning, paddingLeft: spacing.sm },
  body: { color: colors.text, fontSize: fontSize.sm, lineHeight: 20 },
  meta: { color: colors.textSecondary, fontSize: fontSize.xs, lineHeight: 18 },
  readMark: { color: colors.success, fontSize: fontSize.xs, fontWeight: '600' },
  smallBtn: { alignSelf: 'flex-start', borderWidth: 1, borderColor: colors.primary, borderRadius: radius.sm,
              paddingHorizontal: spacing.md, paddingVertical: spacing.xs + 2, marginTop: 2 },
  smallBtnText: { color: colors.primary, fontWeight: '700', fontSize: fontSize.sm },
  disabled: { opacity: 0.5 },
  modal: { flex: 1, backgroundColor: colors.background, padding: spacing.md, gap: spacing.md },
  modalHead: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingTop: spacing.lg },
  modalTitle: { color: colors.text, fontSize: fontSize.lg, fontWeight: '700', flex: 1 },
  modalBody: { gap: spacing.sm, paddingBottom: spacing.lg },
  summaryInput: { borderWidth: 1, borderColor: colors.cardBorder, borderRadius: radius.sm, backgroundColor: colors.surface,
                  color: colors.text, fontSize: fontSize.sm, padding: spacing.md, minHeight: 360, lineHeight: 20 },
  summaryText: { color: colors.text, fontSize: fontSize.sm, lineHeight: 20 },
  bigBtn: { backgroundColor: colors.secondary, borderRadius: radius.sm, paddingVertical: spacing.md, alignItems: 'center' },
  bigBtnText: { color: '#fff', fontWeight: '700', fontSize: fontSize.md },
})
