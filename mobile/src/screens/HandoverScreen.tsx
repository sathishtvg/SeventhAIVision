/**
 * Handover acceptance, done at the gate.
 *
 * This is the screen the whole structured-handover feature exists for. The
 * incoming guard arrives at 07:00, reads what the outgoing guard is walking
 * away from, counts what they are being told to count, and either takes
 * responsibility for it or says they do not agree. None of that happens at a
 * desk an hour later, which is why acceptance is a guard's own permission
 * rather than a supervisor's.
 *
 * Disputing is given equal weight to accepting on purpose. If the only visible
 * action is "Accept", a guard who counted eleven keys against a handover
 * claiming twelve will tap it anyway, and the discrepancy is gone.
 */
import React, { useState } from 'react'
import {
  ActivityIndicator, Alert, FlatList, Modal, Pressable, ScrollView,
  StyleSheet, Switch, Text, TextInput, View,
} from 'react-native'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Ionicons } from '@expo/vector-icons'

import {
  acceptHandover, disputeHandover, getHandover, listHandovers,
  updateHandoverChecks, type Handover, type HandoverDetail,
} from '@/api/guardhouse'
import { Card } from '@/components/Card'
import { colors, fontSize, radius, spacing } from '@/theme'

const STATUS_COLOUR: Record<string, string> = {
  submitted: colors.warning,
  accepted: colors.success,
  disputed: colors.error,
  resolved: colors.secondary,
}

const STATUS_LABEL: Record<string, string> = {
  submitted: 'Awaiting you',
  accepted: 'Accepted',
  disputed: 'Disputed',
  resolved: 'Resolved',
}

function fmt(ts: string | null) {
  if (!ts) return '—'
  return new Date(ts).toLocaleString(undefined, {
    day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
  })
}

function apiError(e: unknown, fallback: string) {
  const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
  return detail || fallback
}

export function HandoverScreen() {
  const qc = useQueryClient()
  const [openId, setOpenId] = useState<string | null>(null)
  const [notes, setNotes] = useState('')
  const [disputeReason, setDisputeReason] = useState('')
  const [disputing, setDisputing] = useState(false)

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['handovers'],
    queryFn: () => listHandovers({ open_only: true }),
  })
  // useQuery is `any` in this app's typings; see KeyRegisterScreen.
  const handovers: Handover[] = data ?? []

  const { data: detailData } = useQuery({
    queryKey: ['handover', openId],
    queryFn: () => getHandover(openId!),
    enabled: Boolean(openId),
  })
  const detail: HandoverDetail | null = detailData ?? null

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['handovers'] })
    if (openId) qc.invalidateQueries({ queryKey: ['handover', openId] })
  }

  const tick = useMutation({
    mutationFn: (patch: { id: string; checked: boolean; counted_value: number | null }) =>
      updateHandoverChecks(openId!, [patch]),
    onSuccess: invalidate,
    onError: (e) => Alert.alert('Could not record that', apiError(e, 'Try again.')),
  })

  const accept = useMutation({
    mutationFn: () => acceptHandover(openId!, notes.trim() || null),
    onSuccess: () => {
      invalidate()
      setOpenId(null); setNotes('')
    },
    onError: (e) => Alert.alert('Could not accept', apiError(e, 'Try again.')),
  })

  const dispute = useMutation({
    mutationFn: () => disputeHandover(openId!, disputeReason.trim()),
    onSuccess: () => {
      invalidate()
      setDisputing(false); setDisputeReason('')
    },
    onError: (e) => Alert.alert('Could not record the dispute', apiError(e, 'Try again.')),
  })

  const close = () => {
    setOpenId(null); setNotes(''); setDisputing(false); setDisputeReason('')
  }

  if (isLoading) {
    return <View style={styles.center}><ActivityIndicator color={colors.primary} /></View>
  }
  if (isError) {
    return (
      <View style={styles.center}>
        <Ionicons name="cloud-offline-outline" size={40} color={colors.textSecondary} />
        <Text style={styles.errorText}>Couldn’t load handovers</Text>
        <Pressable style={styles.retryBtn} onPress={() => refetch()}>
          <Text style={styles.retryText}>Retry</Text>
        </Pressable>
      </View>
    )
  }

  const editable = Boolean(detail && ['submitted', 'disputed'].includes(detail.status))

  return (
    <View style={styles.container}>
      <FlatList
        data={handovers}
        keyExtractor={(h) => h.id}
        contentContainerStyle={styles.list}
        ListEmptyComponent={
          <Card>
            <Text style={styles.muted}>
              Nothing waiting. A handover raised at the end of a shift appears here for you
              to accept.
            </Text>
          </Card>
        }
        renderItem={({ item }) => (
          <Pressable onPress={() => setOpenId(item.id)}>
            <Card>
              <View style={styles.row}>
                <View style={styles.flex}>
                  <Text style={styles.title}>{item.site_name || 'No site'}</Text>
                  <Text style={styles.meta}>
                    {fmt(item.created_at)}
                    {item.outgoing_guard_name ? ` · from ${item.outgoing_guard_name}` : ''}
                  </Text>
                  <Text style={styles.meta}>
                    {item.keys_outstanding} keys out
                    {item.keys_overdue > 0 ? ` (${item.keys_overdue} late)` : ''}
                    {' · '}{item.lost_found_held} held
                    {' · '}{item.open_defects_count} defects
                  </Text>
                  {!!item.dispute_reason && (
                    <Text style={styles.disputeText}>{item.dispute_reason}</Text>
                  )}
                </View>
                <View style={[styles.badge, { backgroundColor: STATUS_COLOUR[item.status] }]}>
                  <Text style={styles.badgeText}>
                    {STATUS_LABEL[item.status] ?? item.status}
                  </Text>
                </View>
              </View>
            </Card>
          </Pressable>
        )}
      />

      <Modal visible={!!openId} animationType="slide" onRequestClose={close}>
        <View style={styles.modal}>
          <View style={styles.modalHeader}>
            <Text style={styles.modalTitle} numberOfLines={2}>
              {detail?.site_name || 'Handover'}
            </Text>
            <Pressable onPress={close} hitSlop={12}>
              <Ionicons name="close" size={26} color={colors.text} />
            </Pressable>
          </View>

          {!detail ? (
            <View style={styles.center}><ActivityIndicator color={colors.primary} /></View>
          ) : (
            <>
              <ScrollView contentContainerStyle={styles.body}>
                {detail.status === 'disputed' && (
                  <View style={styles.disputeBanner}>
                    <Ionicons name="alert-circle" size={16} color="#fff" />
                    <Text style={styles.bannerText}>Disputed: {detail.dispute_reason}</Text>
                  </View>
                )}
                {detail.mismatches > 0 && (
                  <View style={[styles.disputeBanner, { backgroundColor: colors.warning }]}>
                    <Ionicons name="warning" size={16} color="#fff" />
                    <Text style={styles.bannerText}>
                      {detail.mismatches}{' '}
                      {detail.mismatches === 1 ? 'count does' : 'counts do'} not match the register
                    </Text>
                  </View>
                )}

                <Text style={styles.label}>WHAT THE SITE OWES</Text>
                <View style={styles.statRow}>
                  {[
                    ['Keys out', detail.keys_outstanding],
                    ['Held', detail.lost_found_held],
                    ['Kit out', detail.equipment_out_count],
                    ['Defects', detail.open_defects_count],
                  ].map(([label, n]) => (
                    <View key={String(label)} style={styles.statTile}>
                      <Text style={styles.statValue}>{String(n)}</Text>
                      <Text style={styles.statLabel}>{String(label)}</Text>
                    </View>
                  ))}
                </View>

                {!!detail.outgoing_notes && (
                  <>
                    <Text style={styles.label}>OUTGOING NOTES</Text>
                    <Text style={styles.bodyText}>{detail.outgoing_notes}</Text>
                  </>
                )}

                <Text style={styles.label}>CHECKLIST</Text>
                {detail.checks.length === 0 ? (
                  <Text style={styles.muted}>No checklist is set up for this site.</Text>
                ) : detail.checks.map((check) => (
                  <View
                    key={check.id}
                    style={[styles.checkRow, check.mismatch && styles.checkRowMismatch]}
                  >
                    <Switch
                      value={check.checked}
                      disabled={!editable || tick.isPending}
                      onValueChange={(v) => tick.mutate({
                        id: check.id, checked: v, counted_value: check.counted_value,
                      })}
                    />
                    <View style={styles.flex}>
                      <Text style={styles.checkLabel}>
                        {check.label}
                        {!check.is_required ? ' (optional)' : ''}
                      </Text>
                      {check.expected_value !== null && (
                        <Text style={styles.meta}>register says {check.expected_value}</Text>
                      )}
                    </View>
                    {check.requires_count && (
                      <TextInput
                        style={styles.countInput}
                        editable={editable}
                        keyboardType="number-pad"
                        value={check.counted_value === null ? '' : String(check.counted_value)}
                        onChangeText={(v) => tick.mutate({
                          id: check.id,
                          checked: check.checked,
                          counted_value: v === '' ? null : Number(v.replace(/[^0-9]/g, '')),
                        })}
                        placeholder="—"
                        placeholderTextColor={colors.textSecondary}
                      />
                    )}
                  </View>
                ))}

                {editable && (
                  <>
                    <Text style={styles.label}>YOUR NOTES</Text>
                    <TextInput
                      style={[styles.input, styles.multiline]} value={notes}
                      onChangeText={setNotes} multiline
                      placeholder="Anything worth recording"
                      placeholderTextColor={colors.textSecondary}
                    />
                  </>
                )}

                {editable && disputing && (
                  <>
                    <Text style={styles.label}>WHAT DOES NOT MATCH</Text>
                    <TextInput
                      style={[styles.input, styles.multiline]} value={disputeReason}
                      onChangeText={setDisputeReason} multiline autoFocus
                      placeholder="Eleven keys on the board, handover says twelve"
                      placeholderTextColor={colors.textSecondary}
                    />
                  </>
                )}
              </ScrollView>

              {editable && (
                <View style={styles.footer}>
                  {disputing ? (
                    <>
                      <Pressable
                        style={[styles.secondaryBtn, styles.flex]}
                        onPress={() => { setDisputing(false); setDisputeReason('') }}
                      >
                        <Text style={styles.secondaryText}>Cancel</Text>
                      </Pressable>
                      <Pressable
                        style={[
                          styles.dangerBtn, styles.flex,
                          (!disputeReason.trim() || dispute.isPending) && styles.disabled,
                        ]}
                        onPress={() => dispute.mutate()}
                        disabled={!disputeReason.trim() || dispute.isPending}
                      >
                        <Text style={styles.primaryText}>Raise dispute</Text>
                      </Pressable>
                    </>
                  ) : (
                    <>
                      {detail.status === 'submitted' && (
                        <Pressable
                          style={[styles.secondaryBtn, styles.flex]}
                          onPress={() => setDisputing(true)}
                        >
                          <Ionicons name="alert-circle-outline" size={16} color={colors.error} />
                          <Text style={[styles.secondaryText, { color: colors.error }]}>
                            Doesn’t match
                          </Text>
                        </Pressable>
                      )}
                      <Pressable
                        style={[
                          styles.primaryBtn, styles.flex,
                          (accept.isPending || detail.unchecked_required > 0) && styles.disabled,
                        ]}
                        onPress={() => accept.mutate()}
                        disabled={accept.isPending || detail.unchecked_required > 0}
                      >
                        <Text style={styles.primaryText}>
                          {detail.unchecked_required > 0
                            ? `${detail.unchecked_required} left to check`
                            : 'Accept handover'}
                        </Text>
                      </Pressable>
                    </>
                  )}
                </View>
              )}
            </>
          )}
        </View>
      </Modal>
    </View>
  )
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: spacing.sm },
  flex: { flex: 1 },
  list: { padding: spacing.md, gap: spacing.sm },
  row: { flexDirection: 'row', alignItems: 'flex-start', justifyContent: 'space-between', gap: spacing.sm },
  title: { color: colors.text, fontSize: fontSize.md, fontWeight: '600' },
  meta: { color: colors.textSecondary, fontSize: fontSize.xs, marginTop: 2 },
  muted: { color: colors.textSecondary, fontSize: fontSize.sm },
  disputeText: { color: colors.error, fontSize: fontSize.xs, marginTop: 4 },
  badge: { paddingHorizontal: spacing.sm, paddingVertical: 2, borderRadius: radius.sm },
  badgeText: { color: '#fff', fontSize: fontSize.xs, fontWeight: '700' },
  errorText: { color: colors.text, fontSize: fontSize.md },
  retryBtn: {
    borderColor: colors.primary, borderWidth: 1, borderRadius: radius.md,
    paddingHorizontal: spacing.lg, paddingVertical: spacing.xs,
  },
  retryText: { color: colors.primary, fontWeight: '600' },
  modal: { flex: 1, backgroundColor: colors.background, paddingTop: spacing.xl },
  modalHeader: {
    flexDirection: 'row', alignItems: 'flex-start', justifyContent: 'space-between',
    paddingHorizontal: spacing.md, gap: spacing.sm,
  },
  modalTitle: { color: colors.text, fontSize: fontSize.lg, fontWeight: '700', flex: 1 },
  body: { padding: spacing.md, paddingBottom: spacing.xl, gap: spacing.xs },
  bodyText: { color: colors.text, fontSize: fontSize.md, lineHeight: 21 },
  label: {
    color: colors.textSecondary, fontSize: fontSize.xs,
    marginTop: spacing.md, fontWeight: '700',
  },
  disputeBanner: {
    flexDirection: 'row', alignItems: 'center', gap: spacing.xs,
    backgroundColor: colors.error, borderRadius: radius.md,
    paddingVertical: spacing.xs, paddingHorizontal: spacing.sm, marginBottom: spacing.xs,
  },
  bannerText: { color: '#fff', fontWeight: '700', fontSize: fontSize.sm, flex: 1 },
  statRow: { flexDirection: 'row', gap: spacing.xs, marginTop: spacing.xs },
  statTile: {
    flex: 1, backgroundColor: colors.surface, borderRadius: radius.md,
    paddingVertical: spacing.sm, alignItems: 'center',
  },
  statValue: { color: colors.text, fontSize: fontSize.lg, fontWeight: '800' },
  statLabel: { color: colors.textSecondary, fontSize: fontSize.xs },
  checkRow: {
    flexDirection: 'row', alignItems: 'center', gap: spacing.sm,
    backgroundColor: colors.surface, borderRadius: radius.md,
    paddingHorizontal: spacing.sm, paddingVertical: spacing.xs, marginTop: spacing.xs,
  },
  checkRowMismatch: { borderWidth: 1, borderColor: colors.error },
  checkLabel: { color: colors.text, fontSize: fontSize.sm },
  countInput: {
    width: 64, backgroundColor: colors.background, borderRadius: radius.sm,
    color: colors.text, paddingHorizontal: spacing.sm, paddingVertical: spacing.xs,
    textAlign: 'center', fontSize: fontSize.md,
  },
  input: {
    backgroundColor: colors.surface, borderRadius: radius.md, color: colors.text,
    paddingHorizontal: spacing.md, paddingVertical: spacing.sm, fontSize: fontSize.md,
    marginTop: spacing.xs,
  },
  multiline: { minHeight: 72, textAlignVertical: 'top' },
  footer: { flexDirection: 'row', gap: spacing.sm, padding: spacing.md },
  primaryBtn: {
    backgroundColor: colors.primary, borderRadius: radius.md,
    paddingVertical: spacing.md, alignItems: 'center',
  },
  primaryText: { color: '#fff', fontWeight: '700', fontSize: fontSize.md },
  secondaryBtn: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: spacing.xs,
    borderColor: colors.error, borderWidth: 1, borderRadius: radius.md,
    paddingVertical: spacing.md,
  },
  secondaryText: { fontWeight: '700', fontSize: fontSize.md },
  dangerBtn: {
    backgroundColor: colors.error, borderRadius: radius.md,
    paddingVertical: spacing.md, alignItems: 'center',
  },
  disabled: { opacity: 0.5 },
})
