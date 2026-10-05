/**
 * One security situation, for the person dealing with it on the ground: what
 * it is and where, why the layer thinks so, what it suggests — and then what
 * is theirs to do. "I have this", "I am there" and "this is what I see" are
 * reports; the buttons under them are decisions, each offered only where this
 * person may take it and, where they may not, saying why.
 *
 * What the layer suggests is marked as a suggestion and is never drawn like a
 * decision. A report is not a decision and changes nothing else.
 */
import React, { useCallback, useEffect, useState } from 'react'
import {
  ActivityIndicator, Alert as RNAlert, Linking, Modal, Platform, Pressable, ScrollView, StyleSheet, Text,
  TextInput, View,
} from 'react-native'
import * as Location from 'expo-location'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigation } from '@react-navigation/native'
import { Ionicons } from '@expo/vector-icons'
import { getStreams } from '@/api/cameras'
import {
  decideSituation, getMySituations, getSituation, getSituationAuthority, getSituationObservations,
  getSituationRecommendations, getSituationResponders, intelApiError, recordSituationReview, reportFromSituation,
  type AuthorityAction, type ObservationKind, type Position, type ReasonCode,
} from '@/api/securityIntelligence'
import { Card } from '@/components/Card'
import { SeverityBadge } from '@/components/SeverityBadge'
import { useWebSocket, type RealtimeEvent } from '@/hooks/useWebSocket'
import { canSee } from '@/lib/access'
import { mapsUrl } from '@/lib/droneEvents'
import {
  DECISION_LABEL, STAGE_LABEL, STATUS_LABEL, acceptable, isIntelRealtime, newClientRef, nextStage, offered, pct,
  riskText, sourcesText,
} from '@/lib/situations'
import { useAuthStore } from '@/store/auth'
import { colors, fontSize, radius, spacing } from '@/theme'

type Props = { route: { params: { situationId: string } } }

/** The phone's position, if it will give one. A report is sent without rather than not sent. */
async function whereAmI(): Promise<Position | undefined> {
  try {
    const { status } = await Location.requestForegroundPermissionsAsync()
    if (status !== 'granted') return undefined
    const pos = await Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.Balanced })
    return { latitude: pos.coords.latitude, longitude: pos.coords.longitude }
  } catch {
    return undefined
  }
}

const ROLE: Record<number, string> = { 2: 'Admin', 3: 'Supervisor', 4: 'Operator', 5: 'Guard', 8: 'Manager' }

export function SituationDetailScreen({ route }: Props) {
  const { situationId } = route.params
  const qc = useQueryClient()
  const navigation = useNavigation<any>()
  const user = useAuthStore((s) => s.user)
  const permissions = useAuthStore((s) => s.permissions)
  const can = useCallback((permission: string) => canSee({ permission }, permissions, user?.roleId),
                          [permissions, user?.roleId])
  const seesSuggestions = can('intel:recommendation:read')
  const mayDecide = can('intel:decide')

  const [choosing, setChoosing] = useState<AuthorityAction | null>(null)
  const [reason, setReason] = useState<ReasonCode | null>(null)
  const [note, setNote] = useState('')
  const [escalateTo, setEscalateTo] = useState<string | null>(null)
  const [clientRef, setClientRef] = useState(newClientRef)
  const [seen, setSeen] = useState('')

  const { data: situation, isLoading, error } = useQuery({
    queryKey: ['situation', situationId], queryFn: () => getSituation(situationId) })
  const { data: mine = [] } = useQuery({ queryKey: ['my-situations'], queryFn: getMySituations })
  const { data: recs } = useQuery({
    queryKey: ['situation-recommendations', situationId], queryFn: () => getSituationRecommendations(situationId),
    enabled: seesSuggestions })
  const { data: authority } = useQuery({
    queryKey: ['situation-authority', situationId], queryFn: () => getSituationAuthority(situationId),
    enabled: mayDecide })
  const { data: observations = [] } = useQuery({
    queryKey: ['situation-observations', situationId], queryFn: () => getSituationObservations(situationId) })
  const { data: streams } = useQuery({
    queryKey: ['streams', situation?.primary_camera_id], queryFn: () => getStreams(situation!.primary_camera_id!),
    enabled: !!situation?.primary_camera_id && can('camera:read') })
  const { data: responders } = useQuery({
    queryKey: ['situation-responders', situationId], queryFn: () => getSituationResponders(situationId),
    enabled: choosing?.action === 'ESCALATE' })

  const refresh = useCallback(() => {
    for (const key of ['situation', 'situation-recommendations', 'situation-authority', 'situation-observations']) {
      qc.invalidateQueries({ queryKey: [key, situationId] })
    }
    qc.invalidateQueries({ queryKey: ['my-situations'] })
  }, [qc, situationId])

  const handleEvent = useCallback((e: RealtimeEvent) => {
    if (isIntelRealtime(e) && e.payload?.situation_id === situationId) refresh()
  }, [refresh, situationId])
  useWebSocket(handleEvent)

  // That this person looked at what was suggested — once per assessment.
  const assessmentId = recs?.assessment?.id
  useEffect(() => {
    if (seesSuggestions && assessmentId) recordSituationReview(situationId).catch(() => undefined)
  }, [seesSuggestions, assessmentId, situationId])

  const report = useMutation({
    mutationFn: async (v: { kind: ObservationKind; note?: string }) => reportFromSituation(situationId, v.kind, {
      note: v.note, position: v.kind === 'OBSERVATION' ? undefined : await whereAmI(), clientRef: newClientRef() }),
    onSuccess: (_d, v) => { if (v.kind === 'OBSERVATION') setSeen(''); refresh() },
    onError: (e) => RNAlert.alert('Could not record that', intelApiError(e)),
  })

  const decide = useMutation({
    mutationFn: (a: AuthorityAction) => decideSituation(situationId, {
      action: a.action, client_ref: clientRef, seen_assessment_id: situation?.assessment?.id,
      reason_code: reason ?? undefined, note: note.trim() || undefined, escalate_to_user_id: escalateTo ?? undefined }),
    onSuccess: (d) => {
      setChoosing(null)
      refresh()
      RNAlert.alert(d.state === 'PENDING_APPROVAL' ? 'Sent for approval' : 'Recorded',
                    d.state === 'PENDING_APPROVAL'
                      ? 'Your decision waits for the command centre. Nothing has been carried out yet.'
                      : `${DECISION_LABEL[d.action]} is recorded as your decision.`)
    },
    onError: (e) => RNAlert.alert('Could not record that', intelApiError(e)),
  })

  if (isLoading) return <View style={styles.center}><ActivityIndicator color={colors.primary} /></View>
  if (error || !situation) {
    return (
      <View style={styles.center}>
        <Ionicons name="alert-circle-outline" size={48} color={colors.textDisabled} />
        <Text style={styles.muted}>{error ? intelApiError(error) : 'Situation not found'}</Text>
      </View>
    )
  }

  const me = mine.find((m) => m.id === situation.id)
  const a = situation.assessment
  const first = recs?.recommendations.find((r) => r.available)
  const closed = situation.closed_at != null
  const stage = !closed && me ? nextStage(me) : null
  const accept = acceptable(authority)
  const located = situation.latitude != null && situation.longitude != null
  const stream = streams?.[0]

  const press = (x: AuthorityAction) => {
    if (!x.allowed) { RNAlert.alert('Not yours to decide here', x.why_not ?? 'This decision is not open to you.'); return }
    setReason(null); setNote(''); setEscalateTo(null); setClientRef(newClientRef()); setChoosing(x)
  }
  const ready = !!choosing && (!choosing.needs_reason || !!reason) && (reason !== 'OTHER' || !!note.trim())
    && (!choosing.needs.includes('escalate_to_user_id') || !!escalateTo)

  return (
    <ScrollView style={styles.root} contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
      <Card>
        <Text style={styles.title}>{a?.label ?? situation.title}</Text>
        <View style={styles.badgeRow}>
          {situation.risk_level && <SeverityBadge value={situation.risk_level.toLowerCase()} />}
          <Text style={styles.risk}>{riskText(situation.risk_level, situation.risk_score)}</Text>
          <Text style={styles.stands}>{STATUS_LABEL[situation.decision_status]}</Text>
        </View>
        <InfoRow label="Where" value={[situation.site_name, situation.primary_camera_name ?? situation.location_label]
          .filter(Boolean).join(' · ') || null} />
        <InfoRow label="Started" value={new Date(situation.started_at).toLocaleString()} />
        <InfoRow label="Evidence" value={`${sourcesText(situation.source_types)} · ${situation.event_count} event`
          + `${situation.event_count === 1 ? '' : 's'}`} />
        <Text style={styles.reference}>{situation.situation_number}</Text>
      </Card>

      {me?.assigned_to_me && (
        <Card style={[styles.section, styles.sentCard]}>
          <Text style={styles.sentTitle}>The command centre sent you to this</Text>
          {me.dispatched_at && <Text style={styles.muted}>{new Date(me.dispatched_at).toLocaleTimeString()}</Text>}
          {!!me.dispatch_notes && <Text style={styles.body}>{me.dispatch_notes}</Text>}
        </Card>
      )}

      {seesSuggestions && (
        <Card style={[styles.section, styles.aiCard]}>
          <View style={styles.markRow}>
            <Ionicons name="sparkles" size={13} color={AI} />
            <Text style={styles.aiMark}>AI SUGGESTS — NOT A DECISION</Text>
          </View>
          {first ? (
            <>
              <Text style={styles.suggestion}>{DECISION_LABEL[first.action]}</Text>
              <Text style={styles.body}>{first.reason}</Text>
              <Text style={styles.muted}>Recommendation confidence {pct(first.recommendation_confidence)}</Text>
            </>
          ) : (
            <Text style={styles.muted}>
              {recs?.recommendations.length ? recs.recommendations[0].unavailable_reason
                : 'Nothing has been suggested for this yet.'}
            </Text>
          )}
        </Card>
      )}

      {/* Reports: where this person is with it. Not decisions. */}
      {mayDecide && !closed && (
        <View style={styles.stageRow}>
          {stage && (
            <Pressable style={[styles.stageBtn, styles.stageGo, report.isPending && styles.disabled]}
                       disabled={report.isPending} onPress={() => report.mutate({ kind: stage })}>
              <Ionicons name={stage === 'ACCEPTED' ? 'hand-left-outline' : 'flag-outline'} size={18} color="#fff" />
              <Text style={styles.stageGoText}>{STAGE_LABEL[stage]}</Text>
            </Pressable>
          )}
          {located && (
            <Pressable style={styles.stageBtn}
                       onPress={() => Linking.openURL(mapsUrl(situation.latitude!, situation.longitude!,
                                                              situation.title, Platform.OS))
                         .catch(() => RNAlert.alert('No maps app', 'No app on this phone can open a location.'))}>
              <Ionicons name="navigate-outline" size={18} color={colors.primary} />
              <Text style={styles.stageText}>Navigate</Text>
            </Pressable>
          )}
          {stream && situation.primary_camera_id && (
            <Pressable style={styles.stageBtn}
                       onPress={() => navigation.navigate('SituationCameraLive', {
                         cameraId: situation.primary_camera_id, streamId: stream.id,
                         cameraName: situation.primary_camera_name ?? 'Camera' })}>
              <Ionicons name="videocam-outline" size={18} color={colors.primary} />
              <Text style={styles.stageText}>View live</Text>
            </Pressable>
          )}
        </View>
      )}

      {a && (
        <Card style={styles.section}>
          <Text style={styles.sectionTitle}>Why this risk</Text>
          {a.risk_factors.map((f, i) => (
            <View key={i} style={styles.infoRow}>
              <Text style={[styles.infoLabel, styles.factor]}>{f.detail}</Text>
              <Text style={styles.infoValue}>{f.points > 0 ? `+${f.points}` : f.points}</Text>
            </View>
          ))}
          <Text style={styles.muted}>
            Detection confidence {pct(a.confidence.detection)} · Risk confidence {pct(a.confidence.risk)}
          </Text>
          {a.unknowns.length > 0 && <Text style={styles.muted}>Not known: {a.unknowns.length} thing(s).</Text>}
        </Card>
      )}

      {/* Decisions: what is this person's to do. */}
      <Card style={[styles.section, styles.humanCard]}>
        <View style={styles.markRow}>
          <Ionicons name="person" size={13} color={HUMAN} />
          <Text style={styles.humanMark}>YOUR DECISION</Text>
        </View>
        {closed ? (
          <Text style={styles.muted}>This situation is closed. Nothing more can be decided on it.</Text>
        ) : !mayDecide ? (
          <Text style={styles.muted}>Your role can view this situation but not decide on it.</Text>
        ) : !authority ? <ActivityIndicator color={colors.primary} /> : (
          <>
            {!authority.in_reach && (
              <Text style={styles.muted}>
                You decide at the site of your own shift, or on a situation you were sent to. This is neither.
              </Text>
            )}
            {accept && (
              <Pressable style={[styles.actionBtn, styles.acceptBtn]} onPress={() => press(accept)}>
                <Ionicons name="checkmark-circle" size={18} color="#fff" />
                <Text style={styles.acceptText}>
                  Accept: {DECISION_LABEL[accept.action]}{accept.how === 'WITH_APPROVAL' ? ' · needs approval' : ''}
                </Text>
              </Pressable>
            )}
            {offered(authority).filter((x) => x.action !== accept?.action).map((x) => (
              <Pressable key={x.action} style={[styles.actionBtn, !x.allowed && styles.disabled]} onPress={() => press(x)}>
                <Text style={styles.actionText}>
                  {DECISION_LABEL[x.action]}{x.allowed && x.how === 'WITH_APPROVAL' ? ' · needs approval' : ''}
                </Text>
              </Pressable>
            ))}
          </>
        )}
      </Card>

      <Card style={styles.section}>
        <Text style={styles.sectionTitle}>From the ground</Text>
        {observations.length === 0 && <Text style={styles.muted}>Nothing has been reported yet.</Text>}
        {observations.map((o) => (
          <View key={o.id} style={styles.observation}>
            <Text style={styles.observationHead}>
              {new Date(o.observed_at).toLocaleTimeString()} · {o.name ?? 'A former user'} ({ROLE[o.role_id] ?? 'Staff'})
            </Text>
            <Text style={styles.body}>
              {o.kind === 'ACCEPTED' ? 'Accepted — on the way' : o.kind === 'ARRIVED' ? 'Arrived' : o.note}
            </Text>
          </View>
        ))}
        {mayDecide && !closed && (
          <>
            <TextInput style={styles.input} value={seen} onChangeText={setSeen} multiline
                       placeholder="What do you see?" placeholderTextColor={colors.textDisabled} />
            <Pressable style={[styles.actionBtn, (!seen.trim() || report.isPending) && styles.disabled]}
                       disabled={!seen.trim() || report.isPending}
                       onPress={() => report.mutate({ kind: 'OBSERVATION', note: seen })}>
              <Text style={styles.actionText}>Record what I see</Text>
            </Pressable>
            <Text style={styles.muted}>A report is recorded as yours. It decides nothing and changes no incident.</Text>
          </>
        )}
      </Card>

      {/* A decision that asks for a reason, a note, or who it goes to. */}
      <Modal visible={!!choosing} transparent animationType="slide" onRequestClose={() => setChoosing(null)}>
        <View style={styles.modalDark}>
          <Card style={styles.sheet}>
            <Text style={styles.sectionTitle}>{choosing ? `Decide: ${DECISION_LABEL[choosing.action]}` : ''}</Text>
            <Text style={styles.muted}>
              {choosing?.basis === 'FOLLOWED' ? 'This follows what the layer suggested.'
                : choosing?.basis === 'OVERRIDE' ? 'The layer did not suggest this. You may still decide it: say why.'
                  : choosing?.basis === 'CLOSING' ? 'This closes the situation. Say how it ended.'
                    : 'This is recorded as your own decision.'}
              {choosing?.how === 'WITH_APPROVAL' ? ' It will wait for the command centre\'s approval; nothing is carried out until then.' : ''}
            </Text>
            {choosing?.needs.includes('escalate_to_user_id') && (
              <ScrollView style={styles.pickList}>
                {(responders?.escalation ?? []).map((u) => (
                  <Pressable key={u.user_id} style={[styles.pickRow, escalateTo === u.user_id && styles.pickRowActive]}
                             onPress={() => setEscalateTo(u.user_id)}>
                    <Text style={styles.body}>{u.name} · {ROLE[u.role_id] ?? 'Staff'}</Text>
                  </Pressable>
                ))}
              </ScrollView>
            )}
            {choosing?.needs_reason && (
              <View style={styles.reasons}>
                {(authority?.reasons ?? []).map((r) => (
                  <Pressable key={r.code} style={[styles.reasonChip, reason === r.code && styles.reasonChipActive]}
                             onPress={() => setReason(r.code)}>
                    <Text style={[styles.reasonText, reason === r.code && styles.reasonTextActive]}>{r.label}</Text>
                  </Pressable>
                ))}
              </View>
            )}
            <TextInput style={styles.input} value={note} onChangeText={setNote} multiline
                       placeholder={reason === 'OTHER' ? 'What was it? (required)' : 'Note (optional, recorded)'}
                       placeholderTextColor={colors.textDisabled} />
            <View style={styles.sheetButtons}>
              <Pressable style={[styles.sheetBtn, styles.sheetCancel]} onPress={() => setChoosing(null)}>
                <Text style={styles.sheetCancelText}>Back</Text>
              </Pressable>
              <Pressable style={[styles.sheetBtn, styles.sheetGo, (!ready || decide.isPending) && styles.disabled]}
                         disabled={!ready || decide.isPending} onPress={() => choosing && decide.mutate(choosing)}>
                {decide.isPending ? <ActivityIndicator color="#fff" size="small" />
                  : <Text style={styles.sheetGoText}>
                    {choosing?.how === 'WITH_APPROVAL' ? 'Send for approval' : 'Record my decision'}</Text>}
              </Pressable>
            </View>
          </Card>
        </View>
      </Modal>
    </ScrollView>
  )
}

function InfoRow({ label, value }: { label: string; value: string | null | undefined }) {
  if (!value) return null
  return (
    <View style={styles.infoRow}>
      <Text style={styles.infoLabel}>{label}</Text>
      <Text style={styles.infoValue}>{value}</Text>
    </View>
  )
}

/** What the layer says is one colour; what a person decides is another. */
const AI = '#8C9CFF'
const HUMAN = '#22C55E'

const styles = StyleSheet.create({
  root:             { flex: 1, backgroundColor: colors.background },
  content:          { padding: spacing.md, gap: spacing.md, paddingBottom: spacing.xl },
  center:           { flex: 1, alignItems: 'center', justifyContent: 'center', backgroundColor: colors.background, gap: spacing.sm, padding: spacing.lg },
  title:            { fontSize: fontSize.lg, fontWeight: '700', color: colors.text, marginBottom: spacing.sm },
  badgeRow:         { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, flexWrap: 'wrap', marginBottom: spacing.xs },
  risk:             { fontSize: fontSize.sm, color: colors.text, fontWeight: '700' },
  stands:           { fontSize: fontSize.xs, color: colors.textSecondary, borderWidth: 1, borderColor: colors.glassBorder, borderRadius: radius.sm, paddingHorizontal: 6, paddingVertical: 2 },
  reference:        { fontSize: fontSize.xs, color: colors.textDisabled, marginTop: spacing.xs },
  section:          { gap: spacing.xs },
  sectionTitle:     { fontSize: fontSize.md, fontWeight: '700', color: colors.text, marginBottom: spacing.xs },
  muted:            { fontSize: fontSize.sm, color: colors.textSecondary, lineHeight: 19 },
  body:             { fontSize: fontSize.sm, color: colors.text, lineHeight: 20 },
  infoRow:          { flexDirection: 'row', justifyContent: 'space-between', paddingVertical: 4, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.divider },
  infoLabel:        { fontSize: fontSize.sm, color: colors.textSecondary },
  infoValue:        { fontSize: fontSize.sm, color: colors.text, fontWeight: '500', maxWidth: '70%', textAlign: 'right' },
  factor:           { flex: 1, marginRight: spacing.sm },
  sentCard:         { borderColor: colors.primary, borderWidth: 1.5 },
  sentTitle:        { fontSize: fontSize.md, fontWeight: '700', color: colors.primary },
  markRow:          { flexDirection: 'row', alignItems: 'center', gap: 6 },
  aiCard:           { borderColor: AI, borderStyle: 'dashed', backgroundColor: 'rgba(140,156,255,0.08)' },
  aiMark:           { fontSize: fontSize.xs, fontWeight: '700', letterSpacing: 0.8, color: AI },
  suggestion:       { fontSize: fontSize.md, fontWeight: '700', color: colors.text },
  humanCard:        { borderColor: HUMAN, backgroundColor: 'rgba(34,197,94,0.06)' },
  humanMark:        { fontSize: fontSize.xs, fontWeight: '700', letterSpacing: 0.8, color: HUMAN },
  stageRow:         { flexDirection: 'row', gap: spacing.sm, flexWrap: 'wrap' },
  stageBtn:         { flexGrow: 1, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: spacing.xs, borderRadius: radius.sm, borderWidth: 1, borderColor: colors.primary, paddingVertical: spacing.md, paddingHorizontal: spacing.md },
  stageGo:          { backgroundColor: colors.primary },
  stageGoText:      { color: '#fff', fontWeight: '700', fontSize: fontSize.md },
  stageText:        { color: colors.primary, fontWeight: '700', fontSize: fontSize.md },
  actionBtn:        { borderRadius: radius.sm, borderWidth: 1, borderColor: colors.glassBorder, paddingVertical: spacing.md, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: spacing.sm },
  actionText:       { fontWeight: '700', fontSize: fontSize.md, color: colors.text },
  acceptBtn:        { backgroundColor: HUMAN, borderColor: HUMAN },
  acceptText:       { fontWeight: '700', fontSize: fontSize.md, color: '#fff' },
  disabled:         { opacity: 0.45 },
  observation:      { paddingVertical: 6, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.divider },
  observationHead:  { fontSize: fontSize.xs, color: colors.textSecondary },
  input:            { minHeight: 64, borderRadius: radius.sm, borderWidth: 1, borderColor: colors.glassBorder, color: colors.text, padding: spacing.sm, fontSize: fontSize.sm, textAlignVertical: 'top' },
  modalDark:        { flex: 1, backgroundColor: 'rgba(0,0,0,0.88)', justifyContent: 'flex-end', padding: spacing.md },
  sheet:            { backgroundColor: '#0B1224', gap: spacing.sm },
  pickList:         { maxHeight: 200 },
  pickRow:          { paddingVertical: spacing.sm, paddingHorizontal: spacing.sm, borderRadius: radius.sm, borderWidth: 1, borderColor: 'transparent' },
  pickRowActive:    { borderColor: colors.primary, backgroundColor: colors.primaryMuted },
  reasons:          { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.xs },
  reasonChip:       { borderRadius: radius.full, borderWidth: 1, borderColor: colors.glassBorder, paddingHorizontal: spacing.sm, paddingVertical: 6 },
  reasonChipActive: { borderColor: colors.primary, backgroundColor: colors.primaryMuted },
  reasonText:       { fontSize: fontSize.sm, color: colors.textSecondary },
  reasonTextActive: { color: colors.text, fontWeight: '700' },
  sheetButtons:     { flexDirection: 'row', gap: spacing.sm },
  sheetBtn:         { flex: 1, borderRadius: radius.sm, paddingVertical: spacing.md, alignItems: 'center', justifyContent: 'center' },
  sheetCancel:      { borderWidth: 1, borderColor: colors.glassBorder },
  sheetCancelText:  { color: colors.textSecondary, fontWeight: '600', fontSize: fontSize.md },
  sheetGo:          { backgroundColor: colors.primary },
  sheetGoText:      { color: '#fff', fontWeight: '700', fontSize: fontSize.md },
})
