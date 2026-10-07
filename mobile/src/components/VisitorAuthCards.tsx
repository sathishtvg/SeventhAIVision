/**
 * Visitor authorisation on the phone.
 *
 * `WaitingVisitorsCard` — on the first page: the visits waiting for this
 * person's answer, each with Approve and Decline. A no says why. It exists only
 * while something is waiting.
 *
 * `VisitStanding` — beside a visitor who is about to be checked in: what
 * stands on the record for the visit, in the server's own sentences, with "Ask
 * the host" when nothing stands and "ID seen" when it can be recorded. It
 * informs the guard; the check-in button beside it works exactly as it did.
 *
 * Both are for whoever reads authorisations (`visitorauth:read`) and hide
 * themselves for anybody else.
 */
import React, { useState } from 'react'
import { ActivityIndicator, Alert as RNAlert, Modal, Pressable, StyleSheet, Text, TextInput, View } from 'react-native'
import { Ionicons } from '@expo/vector-icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import {
  approve, askForVisit, decline, getStanding, getWaitingForMe, recordIdSeen, type Authorisation,
} from '@/api/visitorAuth'
import { canSee } from '@/lib/access'
import { refusal } from '@/lib/responses'
import {
  ID_KINDS, STANDING_LABEL, canAsk, isReason, tone, waitingHeading, waitingMeta, whoWaits, type Tone,
} from '@/lib/visitorAuth'
import { useAuthStore } from '@/store/auth'
import { Card } from '@/components/Card'
import { colors, fontSize, radius, spacing } from '@/theme'

const when = (iso: string) =>
  new Date(iso).toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })

const TONE_COLOUR: Record<Tone, string> = {
  good: colors.success, wait: colors.info, stop: colors.error, plain: colors.textSecondary,
}

function useHolds(permission: string): boolean {
  const permissions = useAuthStore((s) => s.permissions)
  const roleId = useAuthStore((s) => s.user?.roleId)
  return canSee({ permission }, permissions, roleId)
}

function DeclineModal({ about, onClose, onDone }: { about: Authorisation; onClose: () => void; onDone: () => void }) {
  const [reason, setReason] = useState('')
  const { mutate, isPending } = useMutation({
    mutationFn: () => decline(about.id, reason.trim()),
    onSuccess: () => { onDone(); onClose() },
    onError: (err) => RNAlert.alert('Not recorded', refusal(err)),
  })
  return (
    <Modal visible transparent animationType="fade" onRequestClose={onClose}>
      <View style={styles.overlay}>
        <View style={styles.sheet}>
          <Text style={styles.sheetTitle}>Decline {whoWaits(about)}</Text>
          <Text style={styles.meta}>Say why. Whoever asked is told, and the gate reads it.</Text>
          <TextInput style={styles.input} value={reason} onChangeText={setReason} multiline maxLength={2000} autoFocus
                     placeholder="Why it is declined" placeholderTextColor={colors.textDisabled}
                     textAlignVertical="top" accessibilityLabel="Why it is declined" />
          <View style={styles.row}>
            <Pressable style={styles.plainBtn} onPress={onClose} accessibilityRole="button">
              <Text style={styles.plainBtnText}>Not yet</Text>
            </Pressable>
            <Pressable style={[styles.stopBtn, (!isReason(reason) || isPending) && styles.disabled]}
                       disabled={!isReason(reason) || isPending} onPress={() => mutate()} accessibilityRole="button">
              {isPending ? <ActivityIndicator color="#fff" size="small" /> : <Text style={styles.solidBtnText}>Decline it</Text>}
            </Pressable>
          </View>
        </View>
      </View>
    </Modal>
  )
}

export function WaitingVisitorsCard() {
  const qc = useQueryClient()
  const allowed = useHolds('visitorauth:read')
  const [declining, setDeclining] = useState<Authorisation | null>(null)
  const { data: items = [] } = useQuery({
    queryKey: ['visitor-auth', 'mine'], queryFn: getWaitingForMe, enabled: allowed, refetchInterval: 60_000 })
  const again = () => qc.invalidateQueries({ queryKey: ['visitor-auth'] })
  const { mutate: yes, isPending, variables } = useMutation({
    mutationFn: approve, onSuccess: again, onError: (err) => RNAlert.alert('Not recorded', refusal(err)) })
  if (!allowed || !items.length) return null
  return (
    <Card style={styles.card}>
      <View style={styles.head}>
        <Ionicons name="person-add-outline" size={18} color={colors.primary} />
        <Text style={styles.title}>{waitingHeading(items)}</Text>
      </View>
      {items.map((a) => (
        <View key={a.id} style={styles.item}>
          <Text style={styles.body}>{whoWaits(a)}</Text>
          <Text style={styles.meta}>{waitingMeta(a, when)}</Text>
          <View style={styles.row}>
            <Pressable style={[styles.goBtn, isPending && variables === a.id && styles.disabled]} disabled={isPending}
                       onPress={() => yes(a.id)} accessibilityRole="button" accessibilityLabel={`Approve ${whoWaits(a)}`}>
              <Text style={styles.solidBtnText}>Approve</Text>
            </Pressable>
            <Pressable style={styles.plainBtn} disabled={isPending} onPress={() => setDeclining(a)}
                       accessibilityRole="button" accessibilityLabel={`Decline ${whoWaits(a)}`}>
              <Text style={styles.plainBtnText}>Decline</Text>
            </Pressable>
          </View>
        </View>))}
      <Text style={styles.meta}>Saying yes checks nobody in: the gate still does that.</Text>
      {declining && <DeclineModal about={declining} onClose={() => setDeclining(null)} onDone={again} />}
    </Card>
  )
}

/** What stands for a visit, for the guard about to check the visitor in. */
export function VisitStanding({ visitorId }: { visitorId: string }) {
  const qc = useQueryClient()
  const allowed = useHolds('visitorauth:read')
  const asks = useHolds('visitorauth:write')
  const [choosingId, setChoosingId] = useState(false)
  const { data, isLoading, isError } = useQuery({
    queryKey: ['visitor-auth', 'standing', visitorId], queryFn: () => getStanding(visitorId), enabled: allowed })
  const again = () => qc.invalidateQueries({ queryKey: ['visitor-auth'] })
  const failed = (err: unknown) => RNAlert.alert('Not recorded', refusal(err))
  const ask = useMutation({ mutationFn: () => askForVisit(visitorId), onSuccess: again, onError: failed })
  const seen = useMutation({
    mutationFn: (kind: string) => recordIdSeen(data!.authorization!.id, kind),
    onSuccess: () => { setChoosingId(false); return again() }, onError: failed })
  if (!allowed) return null
  if (isLoading) return <ActivityIndicator color={colors.primary} size="small" />
  // Not being able to read what stands does not stand in the way of the gate.
  if (isError || !data) return <Text style={styles.meta}>What stands for this visit could not be read just now.</Text>
  const held = data.authorization
  return (
    <View style={styles.standing}>
      <Text style={[styles.standingLabel, { color: TONE_COLOUR[tone(data.standing)] }]}>
        {STANDING_LABEL[data.standing]}
      </Text>
      {data.says.map((line) => <Text key={line} style={styles.body}>{line}</Text>)}
      <Text style={styles.meta}>{data.note}</Text>
      <View style={styles.row}>
        {asks && canAsk(data.standing) && (
          <Pressable style={[styles.plainBtn, ask.isPending && styles.disabled]} disabled={ask.isPending}
                     onPress={() => ask.mutate()} accessibilityRole="button">
            <Text style={styles.plainBtnText}>Ask the host</Text>
          </Pressable>)}
        {held?.may.id_seen && !choosingId && (
          <Pressable style={styles.plainBtn} onPress={() => setChoosingId(true)} accessibilityRole="button">
            <Text style={styles.plainBtnText}>{held.id_document_kind ? 'ID seen again' : 'ID seen'}</Text>
          </Pressable>)}
      </View>
      {choosingId && (
        <View style={styles.kinds}>
          <Text style={styles.meta}>The kind of document you saw. Its number is not taken.</Text>
          {ID_KINDS.map((kind) => (
            <Pressable key={kind} style={[styles.kindBtn, seen.isPending && styles.disabled]} disabled={seen.isPending}
                       onPress={() => seen.mutate(kind)} accessibilityRole="button">
              <Text style={styles.plainBtnText}>{kind}</Text>
            </Pressable>))}
        </View>)}
    </View>
  )
}

const styles = StyleSheet.create({
  card: { gap: spacing.sm },
  head: { flexDirection: 'row', alignItems: 'center', gap: spacing.xs },
  title: { color: colors.text, fontSize: fontSize.md, fontWeight: '700', flex: 1 },
  item: { gap: 4, paddingVertical: spacing.sm, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.divider },
  body: { color: colors.text, fontSize: fontSize.sm, lineHeight: 20 },
  meta: { color: colors.textSecondary, fontSize: fontSize.xs, lineHeight: 18 },
  row: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm, marginTop: 4 },
  goBtn: { backgroundColor: colors.success, borderRadius: radius.sm, paddingHorizontal: spacing.lg,
           paddingVertical: spacing.xs + 3 },
  stopBtn: { backgroundColor: colors.error, borderRadius: radius.sm, paddingHorizontal: spacing.lg,
             paddingVertical: spacing.xs + 3, minWidth: 110, alignItems: 'center' },
  solidBtnText: { color: '#fff', fontWeight: '700', fontSize: fontSize.sm },
  plainBtn: { borderWidth: 1, borderColor: colors.primary, borderRadius: radius.sm, paddingHorizontal: spacing.md,
              paddingVertical: spacing.xs + 2 },
  plainBtnText: { color: colors.primary, fontWeight: '700', fontSize: fontSize.sm },
  disabled: { opacity: 0.5 },
  overlay: { flex: 1, backgroundColor: 'rgba(0,0,0,0.6)', justifyContent: 'center', padding: spacing.lg },
  sheet: { backgroundColor: colors.surface, borderRadius: radius.md, padding: spacing.lg, gap: spacing.sm },
  sheetTitle: { color: colors.text, fontSize: fontSize.md, fontWeight: '700' },
  input: { borderWidth: 1, borderColor: colors.cardBorder, borderRadius: radius.sm, backgroundColor: colors.background,
           color: colors.text, fontSize: fontSize.sm, padding: spacing.md, minHeight: 90, lineHeight: 20 },
  standing: { gap: 4, alignSelf: 'stretch', paddingVertical: spacing.sm, borderTopWidth: StyleSheet.hairlineWidth,
              borderBottomWidth: StyleSheet.hairlineWidth, borderColor: colors.divider },
  standingLabel: { fontSize: fontSize.sm, fontWeight: '700' },
  kinds: { gap: spacing.xs, marginTop: 4 },
  kindBtn: { borderWidth: 1, borderColor: colors.cardBorder, borderRadius: radius.sm, paddingHorizontal: spacing.md,
             paddingVertical: spacing.xs + 2 },
})
