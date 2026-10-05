/**
 * One drone event, for an officer in the field: what was seen (snapshot and
 * clip), where, how serious and why, the incident it opened — and the actions
 * that officer may take. Each button appears only for a role that holds the
 * permission its endpoint requires.
 *
 * Reached from More → Drone Events and from a drone alert, so it reads its id
 * from the route and assumes nothing about which stack it is mounted in.
 */
import React, { useCallback, useState } from 'react'
import {
  ActivityIndicator, Alert as RNAlert, Image, Linking, Modal, Platform, Pressable, ScrollView,
  StyleSheet, Text, TextInput, View,
} from 'react-native'
import { WebView } from 'react-native-webview'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigation } from '@react-navigation/native'
import { Ionicons } from '@expo/vector-icons'
import {
  decideDroneEvent, dispatchDroneGuard, droneApiError, droneMediaHeaders, droneMediaUrl, getDroneEvent,
  getDroneEventCard, getDroneEventGuards, openDroneIncident, type DroneMedia,
} from '@/api/drones'
import { apiClient } from '@/api/client'
import { Card } from '@/components/Card'
import { SeverityBadge } from '@/components/SeverityBadge'
import { useWebSocket, type RealtimeEvent } from '@/hooks/useWebSocket'
import { canSee } from '@/lib/access'
import {
  clipPlayerHtml, confidenceLabel, droneActions, eventTitle, guardLine, isDroneRealtime, mapsUrl, pretty,
  type DroneAction,
} from '@/lib/droneEvents'
import { useAuthStore } from '@/store/auth'
import { colors, fontSize, radius, spacing } from '@/theme'

type Props = { route: { params: { eventId: string } } }

const ACTION_LABEL: Record<DroneAction, string> = {
  acknowledge: 'Acknowledge', escalate: 'Escalate', incident: 'Open Incident', dispatch: 'Dispatch Guard',
  resolve: 'Resolve', 'false-positive': 'False Positive',
}
const ACTION_ICON: Record<DroneAction, React.ComponentProps<typeof Ionicons>['name']> = {
  acknowledge: 'checkmark-circle', escalate: 'arrow-up-circle-outline', incident: 'warning-outline',
  dispatch: 'send', resolve: 'checkmark-done-outline', 'false-positive': 'close-circle-outline',
}
const ACTION_COLOR: Record<DroneAction, string> = {
  acknowledge: colors.success, escalate: colors.warning, incident: colors.error, dispatch: colors.primary,
  resolve: colors.success, 'false-positive': colors.textSecondary,
}
/** Actions that ask for a note (or, for a false positive, a reason) first. */
const NEEDS_TEXT: DroneAction[] = ['escalate', 'incident', 'resolve', 'false-positive']

function InfoRow({ label, value }: { label: string; value: string | null | undefined }) {
  if (!value) return null
  return (
    <View style={styles.infoRow}>
      <Text style={styles.infoLabel}>{label}</Text>
      <Text style={styles.infoValue}>{value}</Text>
    </View>
  )
}

function Media({ media, onPreview, onPlay }: { media: DroneMedia[]; onPreview: (m: DroneMedia) => void
                                               onPlay: (m: DroneMedia) => void }) {
  const headers = droneMediaHeaders()
  if (!media.length) return <Text style={styles.muted}>No snapshot or clip was kept for this event.</Text>
  return (
    <View style={styles.mediaGrid}>
      {media.map((m) => {
        const held = m.storage_location === 'local'
        const clip = m.media_kind !== 'SNAPSHOT'
        return (
          <Pressable key={m.id} style={styles.mediaTile} disabled={held}
                     onPress={() => (clip ? onPlay(m) : onPreview(m))}>
            {held ? (
              <View style={styles.mediaHeld}>
                <Ionicons name="cloud-offline-outline" size={22} color={colors.textDisabled} />
                <Text style={styles.mediaHeldText}>Held at the site ({pretty(m.sync_state).toLowerCase()})</Text>
              </View>
            ) : clip ? (
              <View style={styles.mediaHeld}>
                <Ionicons name="play-circle" size={40} color={colors.primary} />
                <Text style={styles.mediaHeldText}>Play clip</Text>
              </View>
            ) : (
              <Image source={{ uri: droneMediaUrl(m.id), headers }} style={styles.mediaImage} resizeMode="cover" />
            )}
            <Text style={styles.mediaCaption}>
              {pretty(m.media_kind)} · {new Date(m.captured_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
            </Text>
          </Pressable>
        )
      })}
    </View>
  )
}

export function DroneEventDetailScreen({ route }: Props) {
  const { eventId } = route.params
  const qc = useQueryClient()
  const navigation = useNavigation<any>()
  const permissions = useAuthStore((s) => s.permissions)
  const roleId = useAuthStore((s) => s.user?.roleId)
  const can = useCallback((permission: string) => canSee({ permission }, permissions, roleId), [permissions, roleId])

  const [preview, setPreview] = useState<DroneMedia | null>(null)
  const [clip, setClip] = useState<DroneMedia | null>(null)
  const [asking, setAsking] = useState<DroneAction | null>(null)
  const [text, setText] = useState('')
  const [guardId, setGuardId] = useState<string | undefined>(undefined)

  const { data: event, isLoading, error } = useQuery({
    queryKey: ['drone-event', eventId], queryFn: () => getDroneEvent(eventId),
  })
  const { data: card } = useQuery({
    queryKey: ['drone-event-card', eventId], queryFn: () => getDroneEventCard(eventId),
  })
  const { data: guards = [], isLoading: loadingGuards } = useQuery({
    queryKey: ['drone-event-guards', eventId], queryFn: () => getDroneEventGuards(eventId),
    enabled: asking === 'dispatch',
  })

  const refresh = useCallback(() => {
    qc.invalidateQueries({ queryKey: ['drone-event', eventId] })
    qc.invalidateQueries({ queryKey: ['drone-event-card', eventId] })
    qc.invalidateQueries({ queryKey: ['drone-events'] })
  }, [qc, eventId])

  const handleEvent = useCallback((e: RealtimeEvent) => {
    if (isDroneRealtime(e) || e.event_type === 'incident_created') refresh()
  }, [refresh])
  useWebSocket(handleEvent)

  const { mutate: act, isPending } = useMutation({
    mutationFn: async (action: DroneAction): Promise<string> => {
      switch (action) {
        case 'acknowledge': await decideDroneEvent(eventId, 'acknowledge'); return 'The event has been acknowledged.'
        case 'escalate': await decideDroneEvent(eventId, 'escalate', text); return 'The event has been escalated.'
        case 'resolve': await decideDroneEvent(eventId, 'resolve', text); return 'The event has been resolved.'
        case 'false-positive':
          await decideDroneEvent(eventId, 'false-positive', text); return 'Marked as a false positive.'
        case 'incident': {
          const r = await openDroneIncident(eventId, text)
          if (!r.created) return 'This event already has an incident.'
          return r.incident.incident_ref ? `Incident ${r.incident.incident_ref} opened.` : 'An incident has been opened.'
        }
        case 'dispatch': {
          const r = await dispatchDroneGuard(eventId, guardId, text)
          return `${r.guard.full_name} has been dispatched.`
        }
      }
    },
    onSuccess: (message) => {
      setAsking(null); setText(''); setGuardId(undefined)
      refresh()
      qc.invalidateQueries({ queryKey: ['incidents'] })
      qc.invalidateQueries({ queryKey: ['alerts'] })
      RNAlert.alert('Done', message)
    },
    onError: (e) => RNAlert.alert('Could not do that', droneApiError(e)),
  })

  if (isLoading) {
    return <View style={styles.center}><ActivityIndicator color={colors.primary} /></View>
  }
  if (error || !event) {
    return (
      <View style={styles.center}>
        <Ionicons name="alert-circle-outline" size={48} color={colors.textDisabled} />
        <Text style={styles.muted}>{error ? droneApiError(error) : 'Drone event not found'}</Text>
      </View>
    )
  }

  const actions = droneActions(event, can)
  const press = (a: DroneAction) => {
    if (a === 'dispatch' || NEEDS_TEXT.includes(a)) { setText(''); setGuardId(undefined); setAsking(a); return }
    act(a)
  }
  const located = event.drone_latitude != null && event.drone_longitude != null
  const incident = card?.incident
  const textTooShort = asking === 'false-positive' && text.trim().length < 3

  return (
    <ScrollView style={styles.root} contentContainerStyle={styles.content}>
      <Card>
        <Text style={styles.title}>{card?.headline ?? `${eventTitle(event)} detected`}</Text>
        <View style={styles.badgeRow}>
          <SeverityBadge value={event.risk_level.toLowerCase()} />
          <Text style={styles.risk}>{event.risk_score != null ? `Risk ${event.risk_score}` : 'Risk —'}</Text>
          <Text style={styles.confidence}>{confidenceLabel(event.ai_confidence)}</Text>
        </View>
        <Text style={styles.status}>{pretty(event.status)} · {pretty(event.verification_state)}</Text>
        {event.false_positive_reason && (
          <Text style={styles.muted}>False positive: {event.false_positive_reason}</Text>
        )}
      </Card>

      <Card style={styles.section}>
        <Text style={styles.sectionTitle}>What the drone captured</Text>
        <Media media={event.media} onPreview={setPreview} onPlay={setClip} />
      </Card>

      <Card style={styles.section}>
        <Text style={styles.sectionTitle}>Details</Text>
        <InfoRow label="Detected" value={card?.detected_at_site_time ?? new Date(event.detected_at).toLocaleString()} />
        <InfoRow label="Site" value={event.site_name} />
        <InfoRow label="Area" value={event.zone_name ? `${event.zone_name} (${pretty(event.zone_type)})` : null} />
        <InfoRow label="Drone" value={[event.drone_name, event.drone_code].filter(Boolean).join(' · ') || null} />
        <InfoRow label="Mission" value={[event.mission_name, event.session_number].filter(Boolean).join(' · ') || null} />
        <InfoRow label="Seen" value={`${event.detection_count}×${event.observed_seconds ? ` over ${Math.round(event.observed_seconds)}s` : ''}`} />
      </Card>

      {event.risk_factors.length > 0 && (
        <Card style={styles.section}>
          <Text style={styles.sectionTitle}>Why this risk</Text>
          {event.risk_factors.map((f, i) => (
            <View key={i} style={styles.infoRow}>
              <Text style={[styles.infoLabel, styles.factor]}>{f.detail || pretty(f.factor)}</Text>
              <Text style={styles.infoValue}>{f.points > 0 ? `+${f.points}` : f.points}</Text>
            </View>
          ))}
        </Card>
      )}

      <Card style={styles.section}>
        <Text style={styles.sectionTitle}>Location</Text>
        {located ? (
          <>
            <Text style={styles.coords}>
              {event.drone_latitude!.toFixed(6)}, {event.drone_longitude!.toFixed(6)}
            </Text>
            <Text style={styles.muted}>
              {event.location_method === 'DRONE_POSITION'
                ? "The drone's position when it saw this; the subject is within its camera's view."
                : pretty(event.location_method)}
            </Text>
            <Pressable style={styles.linkBtn}
                       onPress={() => Linking.openURL(mapsUrl(event.drone_latitude!, event.drone_longitude!,
                                                              eventTitle(event), Platform.OS))
                         .catch(() => RNAlert.alert('No maps app', 'No app on this phone can open a location.'))}>
              <Ionicons name="navigate-outline" size={16} color={colors.primary} />
              <Text style={styles.linkText}>Open in Maps</Text>
            </Pressable>
          </>
        ) : <Text style={styles.muted}>The drone reported no position for this event.</Text>}
      </Card>

      <Card style={styles.section}>
        <Text style={styles.sectionTitle}>Incident</Text>
        {incident ? (
          <>
            <Text style={styles.incidentTitle}>
              {incident.incident_ref ? `${incident.incident_ref} · ` : ''}{incident.title}
            </Text>
            <Text style={styles.muted}>
              {pretty(incident.status)} · {pretty(incident.severity)}
              {incident.dispatched_guard_name ? ` · ${incident.dispatched_guard_name} dispatched` : ' · no guard dispatched'}
              {incident.guard_arrived_at ? ' · arrived' : ''}
            </Text>
            {can('incident:read') && (
              <Pressable style={styles.linkBtn}
                         onPress={() => navigation.navigate('Incidents', {
                           screen: 'IncidentDetail', params: { incidentId: incident.id }, initial: false, pop: true })}>
                <Ionicons name="open-outline" size={16} color={colors.primary} />
                <Text style={styles.linkText}>Open the incident to update it</Text>
              </Pressable>
            )}
          </>
        ) : <Text style={styles.muted}>No incident has been opened for this event.</Text>}
      </Card>

      {actions.length > 0 ? (
        <View style={styles.actions}>
          {actions.map((a) => (
            <Pressable key={a} style={[styles.actionBtn, { borderColor: ACTION_COLOR[a] }, isPending && styles.disabled]}
                       disabled={isPending} onPress={() => press(a)}>
              <Ionicons name={ACTION_ICON[a]} size={18} color={ACTION_COLOR[a]} />
              <Text style={[styles.actionText, { color: ACTION_COLOR[a] }]}>{ACTION_LABEL[a]}</Text>
            </Pressable>
          ))}
        </View>
      ) : (
        <Text style={[styles.muted, styles.centerText]}>
          {event.status === 'RESOLVED' || event.status === 'FALSE_POSITIVE'
            ? `This event is closed${event.resolved_by_name ? ` by ${event.resolved_by_name}` : ''}.`
            : 'Your role can view this event but not act on it.'}
        </Text>
      )}

      {/* Snapshot, full screen */}
      <Modal visible={!!preview} transparent animationType="fade" onRequestClose={() => setPreview(null)}>
        <Pressable style={styles.modalDark} onPress={() => setPreview(null)}>
          {preview && (
            <Image source={{ uri: droneMediaUrl(preview.id), headers: droneMediaHeaders() }}
                   style={styles.previewImage} resizeMode="contain" />
          )}
        </Pressable>
      </Modal>

      {/* Clip player */}
      <Modal visible={!!clip} animationType="slide" onRequestClose={() => setClip(null)}>
        <View style={styles.clipRoot}>
          <Pressable style={styles.clipClose} onPress={() => setClip(null)}>
            <Ionicons name="close" size={24} color="#fff" />
            <Text style={styles.clipCloseText}>Close</Text>
          </Pressable>
          {clip && (
            <WebView
              source={{ html: clipPlayerHtml(droneMediaUrl(clip.id), droneMediaHeaders().Authorization),
                        baseUrl: apiClient.defaults.baseURL ?? '' }}
              style={styles.clipWeb}
              javaScriptEnabled
              allowsInlineMediaPlayback
              mediaPlaybackRequiresUserAction={false}
            />
          )}
        </View>
      </Modal>

      {/* Note / reason, and the guard picker */}
      <Modal visible={!!asking} transparent animationType="slide" onRequestClose={() => setAsking(null)}>
        <View style={styles.modalDark}>
          <Card style={styles.sheet}>
            <Text style={styles.sectionTitle}>{asking ? ACTION_LABEL[asking] : ''}</Text>
            {asking === 'dispatch' && (
              <ScrollView style={styles.guardList}>
                <Pressable style={[styles.guardRow, guardId === undefined && styles.guardRowActive]}
                           onPress={() => setGuardId(undefined)}>
                  <Text style={styles.guardName}>Nearest available</Text>
                  <Text style={styles.muted}>The closest free guard on shift at this site</Text>
                </Pressable>
                {loadingGuards && <ActivityIndicator color={colors.primary} style={{ marginVertical: spacing.sm }} />}
                {guards.map((g) => (
                  <Pressable key={g.user_id} style={[styles.guardRow, guardId === g.user_id && styles.guardRowActive]}
                             onPress={() => setGuardId(g.user_id)}>
                    <Text style={styles.guardName}>{g.full_name}</Text>
                    <Text style={styles.muted}>{guardLine(g)}</Text>
                  </Pressable>
                ))}
                {!loadingGuards && guards.length === 0 && (
                  <Text style={styles.muted}>No guard is on shift at this site right now.</Text>
                )}
              </ScrollView>
            )}
            <TextInput
              style={styles.input}
              value={text}
              onChangeText={setText}
              multiline
              placeholder={asking === 'false-positive' ? 'Why is it a false positive? (required)'
                : asking === 'dispatch' ? 'Notes for the guard (optional)' : 'Note (optional, recorded)'}
              placeholderTextColor={colors.textDisabled}
            />
            <View style={styles.sheetButtons}>
              <Pressable style={[styles.sheetBtn, styles.sheetCancel]} onPress={() => setAsking(null)}>
                <Text style={styles.sheetCancelText}>Back</Text>
              </Pressable>
              <Pressable style={[styles.sheetBtn, styles.sheetGo, (isPending || textTooShort) && styles.disabled]}
                         disabled={isPending || textTooShort} onPress={() => asking && act(asking)}>
                {isPending ? <ActivityIndicator color="#fff" size="small" />
                  : <Text style={styles.sheetGoText}>{asking ? ACTION_LABEL[asking] : ''}</Text>}
              </Pressable>
            </View>
          </Card>
        </View>
      </Modal>
    </ScrollView>
  )
}

const styles = StyleSheet.create({
  root:            { flex: 1, backgroundColor: colors.background },
  content:         { padding: spacing.md, gap: spacing.md, paddingBottom: spacing.xl },
  center:          { flex: 1, alignItems: 'center', justifyContent: 'center', backgroundColor: colors.background, gap: spacing.sm, padding: spacing.lg },
  centerText:      { textAlign: 'center' },
  title:           { fontSize: fontSize.lg, fontWeight: '700', color: colors.text, marginBottom: spacing.sm },
  badgeRow:        { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, flexWrap: 'wrap', marginBottom: spacing.xs },
  risk:            { fontSize: fontSize.sm, color: colors.text, fontWeight: '700' },
  confidence:      { fontSize: fontSize.sm, color: colors.textSecondary },
  status:          { fontSize: fontSize.sm, color: colors.textSecondary },
  section:         { gap: spacing.xs },
  sectionTitle:    { fontSize: fontSize.md, fontWeight: '700', color: colors.text, marginBottom: spacing.xs },
  muted:           { fontSize: fontSize.sm, color: colors.textSecondary, lineHeight: 19 },
  infoRow:         { flexDirection: 'row', justifyContent: 'space-between', paddingVertical: 4, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.divider },
  infoLabel:       { fontSize: fontSize.sm, color: colors.textSecondary },
  infoValue:       { fontSize: fontSize.sm, color: colors.text, fontWeight: '500', maxWidth: '60%', textAlign: 'right' },
  factor:          { flex: 1, marginRight: spacing.sm },
  coords:          { fontSize: fontSize.md, color: colors.text, fontWeight: '600' },
  incidentTitle:   { fontSize: fontSize.md, color: colors.text, fontWeight: '600' },
  linkBtn:         { flexDirection: 'row', alignItems: 'center', gap: spacing.xs, paddingVertical: spacing.sm },
  linkText:        { fontSize: fontSize.sm, color: colors.primary, fontWeight: '600' },
  mediaGrid:       { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm },
  mediaTile:       { width: '48%' },
  mediaImage:      { width: '100%', aspectRatio: 16 / 9, borderRadius: radius.sm, backgroundColor: colors.glassBgDeep },
  mediaHeld:       { width: '100%', aspectRatio: 16 / 9, borderRadius: radius.sm, backgroundColor: colors.glassBgDeep, alignItems: 'center', justifyContent: 'center', gap: 4, padding: spacing.xs },
  mediaHeldText:   { fontSize: fontSize.xs, color: colors.textSecondary, textAlign: 'center' },
  mediaCaption:    { fontSize: fontSize.xs, color: colors.textSecondary, marginTop: 4 },
  actions:         { gap: spacing.sm },
  actionBtn:       { borderRadius: radius.sm, borderWidth: 1, paddingVertical: spacing.md, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: spacing.sm },
  actionText:      { fontWeight: '700', fontSize: fontSize.md },
  disabled:        { opacity: 0.5 },
  modalDark:       { flex: 1, backgroundColor: 'rgba(0,0,0,0.88)', justifyContent: 'center', padding: spacing.md },
  previewImage:    { width: '100%', height: '80%' },
  clipRoot:        { flex: 1, backgroundColor: '#000' },
  clipClose:       { flexDirection: 'row', alignItems: 'center', gap: spacing.xs, padding: spacing.md, paddingTop: spacing.xl },
  clipCloseText:   { color: '#fff', fontSize: fontSize.md, fontWeight: '600' },
  clipWeb:         { flex: 1, backgroundColor: '#000' },
  sheet:           { backgroundColor: '#0B1224', gap: spacing.sm },
  guardList:       { maxHeight: 260 },
  guardRow:        { paddingVertical: spacing.sm, paddingHorizontal: spacing.sm, borderRadius: radius.sm, borderWidth: 1, borderColor: 'transparent' },
  guardRowActive:  { borderColor: colors.primary, backgroundColor: colors.primaryMuted },
  guardName:       { fontSize: fontSize.md, color: colors.text, fontWeight: '600' },
  input:           { minHeight: 72, borderRadius: radius.sm, borderWidth: 1, borderColor: colors.glassBorder, color: colors.text, padding: spacing.sm, fontSize: fontSize.sm, textAlignVertical: 'top' },
  sheetButtons:    { flexDirection: 'row', gap: spacing.sm },
  sheetBtn:        { flex: 1, borderRadius: radius.sm, paddingVertical: spacing.md, alignItems: 'center', justifyContent: 'center' },
  sheetCancel:     { borderWidth: 1, borderColor: colors.glassBorder },
  sheetCancelText: { color: colors.textSecondary, fontWeight: '600', fontSize: fontSize.md },
  sheetGo:         { backgroundColor: colors.primary },
  sheetGoText:     { color: '#fff', fontWeight: '700', fontSize: fontSize.md },
})
