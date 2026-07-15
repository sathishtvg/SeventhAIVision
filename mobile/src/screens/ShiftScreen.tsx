import React, { useState } from 'react'
import {
  ActivityIndicator, Alert as RNAlert, FlatList, Modal, Pressable,
  ScrollView, StyleSheet, Text, TextInput, View,
} from 'react-native'
import * as Location from 'expo-location'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigation } from '@react-navigation/native'
import type { NativeStackNavigationProp } from '@react-navigation/native-stack'
import { Ionicons } from '@expo/vector-icons'
import {
  getShifts, startShift, endShift, startBreak, endBreak,
  generateHandover, getRoutes, getShiftBriefing, type ShiftBriefing,
} from '@/api/patrols'
import { Card } from '@/components/Card'
import { CheckInPhotoModal } from '@/components/CheckInPhotoModal'
import { colors, fontSize, radius, spacing } from '@/theme'
import type { PatrolStackParamList } from '@/navigation'

/** Best-effort GPS capture — never blocks check-in/out on permission denial or
 * GPS-off (a missing/failed reading isn't spoofing, just weak signal). `mocked`
 * (Android-only field on expo-location's LocationObject; undefined on iOS,
 * which has no equivalent signal) IS enforced — a positive detection blocks
 * check-in outright at the caller. */
async function tryGetCoords(): Promise<{ latitude: number; longitude: number; mocked: boolean } | undefined> {
  try {
    const { status } = await Location.requestForegroundPermissionsAsync()
    if (status !== 'granted') return undefined
    const pos = await Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.Balanced })
    return { latitude: pos.coords.latitude, longitude: pos.coords.longitude, mocked: pos.mocked === true }
  } catch {
    return undefined
  }
}

type NavProp = NativeStackNavigationProp<PatrolStackParamList>

const STATUS_COLOR: Record<string, string> = {
  scheduled: colors.info,
  active: colors.success,
  completed: colors.textDisabled,
}

export function ShiftScreen() {
  const navigation = useNavigation<NavProp>()
  const qc = useQueryClient()
  const [handoverShiftId, setHandoverShiftId] = useState<string | null>(null)
  const [handoverNotes, setHandoverNotes] = useState('')
  const [handoverResult, setHandoverResult] = useState<any | null>(null)
  const [briefingShiftId, setBriefingShiftId] = useState<string | null>(null)

  const { data: shifts = [], isLoading } = useQuery({
    queryKey: ['my-shifts'],
    queryFn: () => getShifts(),
  })

  const { data: routes = [] } = useQuery({
    queryKey: ['patrol-routes'],
    queryFn: () => getRoutes(),
  })

  const { data: briefing, isLoading: briefingLoading } = useQuery({
    queryKey: ['shift-briefing', briefingShiftId],
    queryFn: () => getShiftBriefing(briefingShiftId!),
    enabled: briefingShiftId !== null,
  })

  // ── Check-in/out selfie flow ──────────────────────────────────────────────
  // GPS is captured once, up front, when the guard taps Start/End — mock
  // location is checked there and blocks before the camera ever opens. The
  // photo is captured next; liveness/mock-location are enforced again
  // server-side (defense in depth) when the shift mutation actually fires.
  const [checkinFlow, setCheckinFlow] = useState<{
    shiftId: string; action: 'start' | 'end'; latitude?: number; longitude?: number
  } | null>(null)
  const [checkinError, setCheckinError] = useState<string | null>(null)

  const beginCheckin = async (shiftId: string, action: 'start' | 'end') => {
    const coords = await tryGetCoords()
    if (coords?.mocked) {
      RNAlert.alert(
        'Fake GPS Detected',
        'Mock location is enabled on this device. Disable any fake-GPS app before checking in.',
      )
      return
    }
    setCheckinError(null)
    setCheckinFlow({ shiftId, action, latitude: coords?.latitude, longitude: coords?.longitude })
  }

  const checkinMut = useMutation({
    mutationFn: (photoUri: string) => {
      const { shiftId, action, latitude, longitude } = checkinFlow!
      return action === 'start'
        ? startShift(shiftId, photoUri, false, latitude, longitude)
        : endShift(shiftId, photoUri, false, latitude, longitude)
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['my-shifts'] })
      setCheckinFlow(null)
    },
    onError: (err: any) => {
      const status = err?.response?.status
      const detail = err?.response?.data?.detail
      if (status === 422) {
        setCheckinError(typeof detail === 'string' ? detail : 'Liveness check failed — please retake the photo.')
      } else if (status === 403) {
        setCheckinError(typeof detail === 'string' ? detail : 'Check-in blocked — fake GPS location detected.')
      } else {
        setCheckinError('Failed to submit check-in. Please try again.')
      }
    },
  })

  const startBreakMut = useMutation({
    mutationFn: (id: string) => startBreak(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['my-shifts'] }),
    onError: () => RNAlert.alert('Error', 'Failed to start break.'),
  })

  const endBreakMut = useMutation({
    mutationFn: (id: string) => endBreak(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['my-shifts'] }),
    onError: () => RNAlert.alert('Error', 'Failed to end break.'),
  })

  const handoverMut = useMutation({
    mutationFn: ({ shiftId, notes }: { shiftId: string; notes: string }) =>
      generateHandover(shiftId, notes || undefined),
    onSuccess: (data) => setHandoverResult(data),
    onError: () => RNAlert.alert('Error', 'Failed to generate handover report.'),
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
      {/* Shift Briefing Modal */}
      <Modal visible={briefingShiftId !== null} animationType="slide" transparent>
        <View style={styles.overlay}>
          <View style={[styles.overlayCard, styles.briefingCard]}>
            <View style={styles.briefingHeader}>
              <Text style={styles.overlayTitle}>Shift Briefing</Text>
              <Pressable onPress={() => setBriefingShiftId(null)}>
                <Ionicons name="close-circle" size={24} color={colors.textSecondary} />
              </Pressable>
            </View>

            {briefingLoading ? (
              <View style={styles.briefingCenter}>
                <ActivityIndicator color={colors.primary} />
                <Text style={styles.briefingLoadText}>Loading briefing…</Text>
              </View>
            ) : briefing ? (
              <ScrollView showsVerticalScrollIndicator={false}>
                {/* Summary chips */}
                <View style={styles.chipRow}>
                  {[
                    { label: 'Permits',   value: briefing.summary.work_permits_count,  color: colors.warning },
                    { label: 'Visitors',  value: briefing.summary.visitors_count,       color: colors.info },
                    { label: 'Alerts',    value: briefing.summary.open_alerts_count,    color: colors.error },
                    { label: 'Deliveries',value: briefing.summary.deliveries_count,     color: colors.secondary },
                  ].map(({ label, value, color }) => (
                    <View key={label} style={[styles.summaryChip, { borderColor: color }]}>
                      <Text style={[styles.chipCount, { color }]}>{value}</Text>
                      <Text style={styles.chipLabel}>{label}</Text>
                    </View>
                  ))}
                </View>

                {/* Alarm panels status */}
                {briefing.alarm_panels.length > 0 && (
                  <View style={styles.section}>
                    <Text style={styles.sectionTitle}>
                      <Ionicons name="shield-outline" size={14} color={
                        briefing.summary.panels_armed > 0 ? colors.success : colors.warning
                      } /> Alarm Panels ({briefing.summary.panels_armed}/{briefing.summary.panels_total} armed)
                    </Text>
                    {briefing.alarm_panels.map((p: ShiftBriefing['alarm_panels'][number]) => {
                      const armed = p.arm_state !== 'disarmed'
                      return (
                        <View key={p.id} style={[styles.briefingItem, styles.briefingItemRow]}>
                          <Text style={styles.briefingItemName}>{p.name}</Text>
                          <View style={[styles.warnBadge, { backgroundColor: armed ? colors.success : colors.textDisabled }]}>
                            <Text style={styles.warnBadgeText}>
                              {armed ? p.arm_state.replace('armed_', '').toUpperCase() : 'DISARMED'}
                            </Text>
                          </View>
                        </View>
                      )
                    })}
                  </View>
                )}

                {/* Previous handover */}
                {briefing.previous_handover && (
                  <View style={styles.section}>
                    <Text style={styles.sectionTitle}>
                      <Ionicons name="clipboard-outline" size={14} color={colors.secondary} /> Previous Handover
                    </Text>
                    <Text style={styles.sectionMeta}>
                      From: {briefing.previous_handover.outgoing_guard_name || 'Unknown guard'} ·{' '}
                      {new Date(briefing.previous_handover.created_at).toLocaleString()}
                    </Text>
                    <View style={styles.statGrid}>
                      <View style={styles.statBox}>
                        <Text style={styles.statLabel}>Open Incidents</Text>
                        <Text style={[styles.statValue, { color: briefing.previous_handover.open_incidents_count > 0 ? colors.error : colors.success }]}>
                          {briefing.previous_handover.open_incidents_count}
                        </Text>
                      </View>
                      <View style={styles.statBox}>
                        <Text style={styles.statLabel}>Open Alerts</Text>
                        <Text style={[styles.statValue, { color: briefing.previous_handover.open_alerts_count > 0 ? colors.warning : colors.success }]}>
                          {briefing.previous_handover.open_alerts_count}
                        </Text>
                      </View>
                    </View>
                    {briefing.previous_handover.outgoing_notes ? (
                      <Text style={styles.handoverNoteText}>
                        "{briefing.previous_handover.outgoing_notes}"
                      </Text>
                    ) : null}
                  </View>
                )}

                {/* Active work permits */}
                {briefing.active_work_permits.length > 0 && (
                  <View style={styles.section}>
                    <Text style={styles.sectionTitle}>
                      <Ionicons name="construct-outline" size={14} color={colors.warning} /> Active Work Permits ({briefing.active_work_permits.length})
                    </Text>
                    {briefing.active_work_permits.map((wp: ShiftBriefing['active_work_permits'][number]) => (
                      <View key={wp.id} style={styles.briefingItem}>
                        <View style={styles.briefingItemRow}>
                          <Text style={styles.briefingItemName}>{wp.contractor_name}</Text>
                          {!wp.safety_briefing_done && (
                            <View style={styles.warnBadge}>
                              <Text style={styles.warnBadgeText}>No Safety Brief</Text>
                            </View>
                          )}
                        </View>
                        <Text style={styles.briefingItemSub}>
                          {wp.work_description || 'No description'}
                        </Text>
                        <Text style={styles.briefingItemMeta}>
                          {wp.workers_count ?? 0} workers · Ends {new Date(wp.end_at).toLocaleString([], { dateStyle: 'short', timeStyle: 'short' })}
                        </Text>
                      </View>
                    ))}
                  </View>
                )}

                {/* Expected visitors */}
                {briefing.expected_visitors.length > 0 && (
                  <View style={styles.section}>
                    <Text style={styles.sectionTitle}>
                      <Ionicons name="people-outline" size={14} color={colors.info} /> Expected Visitors ({briefing.expected_visitors.length})
                    </Text>
                    {briefing.expected_visitors.map((v: ShiftBriefing['expected_visitors'][number]) => (
                      <View key={v.id} style={styles.briefingItem}>
                        <View style={styles.briefingItemRow}>
                          <Text style={styles.briefingItemName}>{v.full_name}</Text>
                          <View style={[styles.statusDot, { backgroundColor: v.status === 'arrived' ? colors.success : colors.info }]} />
                        </View>
                        {v.company && <Text style={styles.briefingItemSub}>{v.company}</Text>}
                        <Text style={styles.briefingItemMeta}>
                          Host: {v.host_name || '—'} · {v.purpose || '—'}
                          {v.expected_from ? ` · ${new Date(v.expected_from).toLocaleString([], { timeStyle: 'short' })}` : ''}
                        </Text>
                      </View>
                    ))}
                  </View>
                )}

                {/* Open alerts */}
                {briefing.open_alerts.length > 0 && (
                  <View style={styles.section}>
                    <Text style={styles.sectionTitle}>
                      <Ionicons name="alert-circle-outline" size={14} color={colors.error} /> Open Alerts ({briefing.open_alerts.length})
                    </Text>
                    {briefing.open_alerts.map((a: ShiftBriefing['open_alerts'][number]) => (
                      <View key={a.id} style={[styles.briefingItem, styles.alertItem]}>
                        <Text style={[styles.briefingItemName, { color: a.severity === 'critical' ? colors.error : colors.warning }]}>
                          [{a.severity.toUpperCase()}] {a.title}
                        </Text>
                        <Text style={styles.briefingItemMeta}>
                          {a.module_type} · {a.camera_name || 'Unknown camera'} · {new Date(a.created_at).toLocaleString([], { dateStyle: 'short', timeStyle: 'short' })}
                        </Text>
                      </View>
                    ))}
                  </View>
                )}

                {/* Pending deliveries */}
                {briefing.pending_deliveries.length > 0 && (
                  <View style={styles.section}>
                    <Text style={styles.sectionTitle}>
                      <Ionicons name="cube-outline" size={14} color={colors.secondary} /> Pending Deliveries ({briefing.pending_deliveries.length})
                    </Text>
                    {briefing.pending_deliveries.map((d: ShiftBriefing['pending_deliveries'][number]) => (
                      <View key={d.id} style={styles.briefingItem}>
                        <Text style={styles.briefingItemName}>
                          {d.recipient_name || 'Unknown recipient'}
                          {d.sender_company ? ` — from ${d.sender_company}` : ''}
                        </Text>
                        <Text style={styles.briefingItemMeta}>
                          {d.carrier ? `${d.carrier} · ` : ''}
                          {d.tracking_number || 'No tracking'}
                          {d.expected_at ? ` · Expected ${new Date(d.expected_at).toLocaleString([], { dateStyle: 'short', timeStyle: 'short' })}` : ''}
                        </Text>
                      </View>
                    ))}
                  </View>
                )}

                {briefing.summary.work_permits_count === 0 &&
                 briefing.summary.visitors_count === 0 &&
                 briefing.summary.open_alerts_count === 0 &&
                 briefing.summary.deliveries_count === 0 &&
                 !briefing.previous_handover && (
                  <View style={styles.briefingCenter}>
                    <Ionicons name="checkmark-circle-outline" size={40} color={colors.success} />
                    <Text style={[styles.briefingLoadText, { color: colors.success }]}>All clear — no active items</Text>
                  </View>
                )}
              </ScrollView>
            ) : null}

            <Pressable style={styles.overlayBtn} onPress={() => setBriefingShiftId(null)}>
              <Text style={styles.overlayBtnText}>Close Briefing</Text>
            </Pressable>
          </View>
        </View>
      </Modal>

      {/* Handover overlay */}
      {handoverShiftId && (
        <View style={styles.overlay}>
          <View style={styles.overlayCard}>
            <Text style={styles.overlayTitle}>
              {handoverResult ? 'Handover Report' : 'Generate Handover'}
            </Text>

            {handoverResult ? (
              <>
                <View style={styles.statGrid}>
                  {[
                    { label: 'Open Incidents', value: String(handoverResult.open_incidents) },
                    { label: 'Open Alerts', value: String(handoverResult.open_alerts) },
                    { label: 'Routes Completed', value: `${handoverResult.patrol_routes_completed}/${handoverResult.patrol_routes_total}` },
                    { label: 'Checkpoints', value: `${handoverResult.checkpoints_scanned}/${handoverResult.checkpoints_total}` },
                  ].map(({ label, value }) => (
                    <View key={label} style={styles.statBox}>
                      <Text style={styles.statLabel}>{label}</Text>
                      <Text style={styles.statValue}>{value}</Text>
                    </View>
                  ))}
                </View>
                {handoverResult.outgoing_notes ? (
                  <Text style={styles.notes}>{handoverResult.outgoing_notes}</Text>
                ) : null}
                <Pressable style={styles.overlayBtn} onPress={() => {
                  setHandoverShiftId(null)
                  setHandoverResult(null)
                  setHandoverNotes('')
                }}>
                  <Text style={styles.overlayBtnText}>Close</Text>
                </Pressable>
              </>
            ) : (
              <>
                <TextInput
                  style={styles.notesInput}
                  placeholder="Handover notes for incoming guard…"
                  placeholderTextColor={colors.textDisabled}
                  value={handoverNotes}
                  onChangeText={setHandoverNotes}
                  multiline
                  numberOfLines={4}
                />
                <View style={styles.overlayActions}>
                  <Pressable style={styles.overlayCancel} onPress={() => {
                    setHandoverShiftId(null)
                    setHandoverNotes('')
                  }}>
                    <Text style={styles.overlayCancelText}>Cancel</Text>
                  </Pressable>
                  <Pressable
                    style={[styles.overlayBtn, handoverMut.isPending && styles.btnDisabled]}
                    disabled={handoverMut.isPending}
                    onPress={() => handoverMut.mutate({ shiftId: handoverShiftId!, notes: handoverNotes })}
                  >
                    {handoverMut.isPending
                      ? <ActivityIndicator color="#fff" size="small" />
                      : <Text style={styles.overlayBtnText}>Generate</Text>
                    }
                  </Pressable>
                </View>
              </>
            )}
          </View>
        </View>
      )}

      <FlatList
        data={shifts as any[]}
        keyExtractor={(s) => s.id}
        contentContainerStyle={styles.list}
        ListEmptyComponent={
          <Card>
            <Text style={styles.empty}>No shifts assigned to you.</Text>
          </Card>
        }
        ListHeaderComponent={
          <Text style={styles.header}>My Shifts</Text>
        }
        renderItem={({ item: s }) => (
          <Card style={styles.shiftCard}>
            <View style={styles.shiftRow}>
              <View style={styles.shiftInfo}>
                <Text style={styles.shiftSite}>{s.site_name || 'No site'}</Text>
                <Text style={styles.shiftTime}>
                  {s.scheduled_start ? new Date(s.scheduled_start).toLocaleString() : '—'} →{' '}
                  {s.scheduled_end ? new Date(s.scheduled_end).toLocaleString() : '—'}
                </Text>
              </View>
              <View style={[styles.badge, { backgroundColor: STATUS_COLOR[s.status] + '30', borderColor: STATUS_COLOR[s.status] }]}>
                <Text style={[styles.badgeText, { color: STATUS_COLOR[s.status] }]}>{s.status}</Text>
              </View>
            </View>

            <View style={styles.shiftActions}>
              {s.status === 'scheduled' && (
                <>
                  <Pressable
                    style={[styles.actionBtn, { backgroundColor: colors.info }]}
                    onPress={() => setBriefingShiftId(s.id)}
                  >
                    <Ionicons name="newspaper-outline" size={14} color="#fff" />
                    <Text style={styles.actionBtnText}>Briefing</Text>
                  </Pressable>
                  <Pressable
                    style={[styles.actionBtn, { backgroundColor: colors.success }]}
                    onPress={() => beginCheckin(s.id, 'start')}
                  >
                    <Ionicons name="play" size={14} color="#fff" />
                    <Text style={styles.actionBtnText}>Start</Text>
                  </Pressable>
                </>
              )}

              {s.status === 'active' && (
                <>
                  <Pressable
                    style={[styles.actionBtn, { backgroundColor: colors.primary }]}
                    onPress={() => navigation.navigate('PatrolSelect', { shiftId: s.id })}
                  >
                    <Ionicons name="footsteps" size={14} color="#fff" />
                    <Text style={styles.actionBtnText}>Patrol</Text>
                  </Pressable>

                  {s.on_break ? (
                    <Pressable
                      style={[styles.actionBtn, { backgroundColor: colors.success }]}
                      onPress={() => endBreakMut.mutate(s.id)}
                      disabled={endBreakMut.isPending}
                    >
                      <Ionicons name="play-circle" size={14} color="#fff" />
                      <Text style={styles.actionBtnText}>End Break</Text>
                    </Pressable>
                  ) : (
                    <Pressable
                      style={[styles.actionBtn, { backgroundColor: colors.info }]}
                      onPress={() => startBreakMut.mutate(s.id)}
                      disabled={startBreakMut.isPending}
                    >
                      <Ionicons name="pause-circle" size={14} color="#fff" />
                      <Text style={styles.actionBtnText}>Break</Text>
                    </Pressable>
                  )}

                  <Pressable
                    style={[styles.actionBtn, { backgroundColor: '#7B61FF' }]}
                    onPress={() => navigation.navigate('OccurrenceBook', { shiftId: s.id })}
                  >
                    <Ionicons name="book" size={14} color="#fff" />
                    <Text style={styles.actionBtnText}>DOB Log</Text>
                  </Pressable>

                  <Pressable
                    style={[styles.actionBtn, { backgroundColor: colors.secondary }]}
                    onPress={() => { setHandoverShiftId(s.id); setHandoverNotes(''); setHandoverResult(null) }}
                  >
                    <Ionicons name="clipboard" size={14} color="#fff" />
                    <Text style={styles.actionBtnText}>Handover</Text>
                  </Pressable>

                  <Pressable
                    style={[styles.actionBtn, { backgroundColor: colors.warning }]}
                    onPress={() => RNAlert.alert('End Shift', 'End your current shift?', [
                      { text: 'Cancel', style: 'cancel' },
                      { text: 'End', style: 'destructive', onPress: () => beginCheckin(s.id, 'end') },
                    ])}
                  >
                    <Ionicons name="stop" size={14} color="#fff" />
                    <Text style={styles.actionBtnText}>End</Text>
                  </Pressable>
                </>
              )}
            </View>
          </Card>
        )}
      />

      <CheckInPhotoModal
        visible={checkinFlow !== null}
        title={checkinFlow?.action === 'start' ? 'Check-In Selfie' : 'Check-Out Selfie'}
        onClose={() => { setCheckinFlow(null); setCheckinError(null) }}
        onConfirm={(photoUri) => checkinMut.mutate(photoUri)}
        confirming={checkinMut.isPending}
        errorMessage={checkinError}
      />
    </View>
  )
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.background },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  list: { padding: spacing.md, gap: spacing.sm },
  header: { fontSize: fontSize.lg, fontWeight: '700', color: colors.text, marginBottom: spacing.sm },
  empty: { color: colors.textSecondary, textAlign: 'center', paddingVertical: spacing.sm },
  shiftCard: { gap: spacing.sm },
  shiftRow: { flexDirection: 'row', alignItems: 'flex-start', justifyContent: 'space-between' },
  shiftInfo: { flex: 1 },
  shiftSite: { fontSize: fontSize.md, fontWeight: '700', color: colors.text },
  shiftTime: { fontSize: fontSize.xs, color: colors.textSecondary, marginTop: 2 },
  badge: {
    borderRadius: radius.xs, paddingHorizontal: 8, paddingVertical: 3,
    borderWidth: 1, marginLeft: spacing.sm,
  },
  badgeText: { fontSize: fontSize.xs, fontWeight: '700' },
  shiftActions: { flexDirection: 'row', gap: spacing.xs, flexWrap: 'wrap' },
  actionBtn: {
    flexDirection: 'row', alignItems: 'center', gap: 4,
    paddingHorizontal: spacing.sm, paddingVertical: spacing.xs,
    borderRadius: radius.xs,
  },
  actionBtnText: { color: '#fff', fontSize: fontSize.xs, fontWeight: '700' },
  btnDisabled: { opacity: 0.5 },
  overlay: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: 'rgba(0,0,0,0.7)',
    alignItems: 'center',
    justifyContent: 'center',
    zIndex: 100,
  },
  overlayCard: {
    backgroundColor: colors.surface,
    borderRadius: radius.md,
    padding: spacing.lg,
    width: '88%',
    gap: spacing.md,
  },
  overlayTitle: { fontSize: fontSize.lg, fontWeight: '700', color: colors.text },
  statGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm },
  statBox: {
    width: '47%', backgroundColor: colors.background,
    borderRadius: radius.sm, padding: spacing.sm, gap: 4,
  },
  statLabel: { fontSize: fontSize.xs, color: colors.textSecondary },
  statValue: { fontSize: fontSize.xl, fontWeight: '700', color: colors.primary },
  notes: { fontSize: fontSize.sm, color: colors.textSecondary, fontStyle: 'italic' },
  notesInput: {
    borderWidth: 1, borderColor: colors.cardBorder, borderRadius: radius.sm,
    backgroundColor: colors.background, color: colors.text,
    fontSize: fontSize.sm, paddingHorizontal: spacing.md, paddingVertical: spacing.sm,
    minHeight: 80, textAlignVertical: 'top',
  },
  overlayActions: { flexDirection: 'row', justifyContent: 'flex-end', gap: spacing.sm },
  overlayBtn: {
    backgroundColor: colors.primary, borderRadius: radius.sm,
    paddingHorizontal: spacing.lg, paddingVertical: spacing.sm,
  },
  overlayBtnText: { color: '#fff', fontWeight: '700' },
  overlayCancel: {
    paddingHorizontal: spacing.lg, paddingVertical: spacing.sm,
    borderRadius: radius.sm, borderWidth: 1, borderColor: colors.divider,
  },
  overlayCancelText: { color: colors.textSecondary, fontWeight: '600' },

  // ── Briefing modal ────────────────────────────────────────────────────────
  briefingCard: { maxHeight: '90%' },
  briefingHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: spacing.md },
  briefingCenter: { alignItems: 'center', paddingVertical: spacing.xl, gap: spacing.sm },
  briefingLoadText: { color: colors.textSecondary, fontSize: fontSize.sm },
  chipRow: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm, marginBottom: spacing.md },
  summaryChip: { borderWidth: 1, borderRadius: radius.sm, paddingHorizontal: spacing.md, paddingVertical: spacing.xs, alignItems: 'center', minWidth: 70 },
  chipCount: { fontSize: fontSize.lg, fontWeight: '700' },
  chipLabel: { fontSize: fontSize.xs, color: colors.textSecondary },
  section: { marginBottom: spacing.md, borderTopWidth: 1, borderTopColor: colors.divider, paddingTop: spacing.sm },
  sectionTitle: { fontSize: fontSize.sm, fontWeight: '700', color: colors.text, marginBottom: spacing.xs },
  sectionMeta: { fontSize: fontSize.xs, color: colors.textSecondary, marginBottom: spacing.sm },
  handoverNoteText: { fontSize: fontSize.sm, color: colors.textSecondary, fontStyle: 'italic', borderLeftWidth: 2, borderLeftColor: colors.divider, paddingLeft: spacing.sm },
  briefingItem: { backgroundColor: colors.surface, borderRadius: radius.sm, padding: spacing.sm, marginBottom: spacing.xs },
  briefingItemRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 2 },
  briefingItemName: { fontSize: fontSize.sm, fontWeight: '600', color: colors.text, flex: 1 },
  briefingItemSub: { fontSize: fontSize.xs, color: colors.textSecondary, marginBottom: 2 },
  briefingItemMeta: { fontSize: fontSize.xs, color: colors.textDisabled },
  warnBadge: { backgroundColor: colors.error, borderRadius: 4, paddingHorizontal: 6, paddingVertical: 2 },
  warnBadgeText: { fontSize: 10, color: '#fff', fontWeight: '700' },
  statusDot: { width: 8, height: 8, borderRadius: 4 },
  alertItem: { borderLeftWidth: 2, borderLeftColor: colors.error },
})
