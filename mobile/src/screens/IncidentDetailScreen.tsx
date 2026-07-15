import React, { useState } from 'react'
import {
  ActivityIndicator, Alert as RNAlert, FlatList, Pressable,
  ScrollView, StyleSheet, Text, TextInput, View,
} from 'react-native'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useRoute, useNavigation, type RouteProp } from '@react-navigation/native'
import type { NativeStackNavigationProp } from '@react-navigation/native-stack'
import { Ionicons } from '@expo/vector-icons'
import {
  getIncidents, getIncidentNotes, addIncidentNote, updateIncidentStatus,
  type Incident, type IncidentStatus,
} from '@/api/incidents'
import { getStreams, type Stream } from '@/api/cameras'
import { triggerSOS } from '@/api/sos'
import { Card } from '@/components/Card'
import { SeverityBadge } from '@/components/SeverityBadge'
import { StatusBadge } from '@/components/StatusBadge'
import { colors, fontSize, radius, spacing } from '@/theme'
import type { IncidentsStackParamList } from '@/navigation'

type RoutePropType = RouteProp<IncidentsStackParamList, 'IncidentDetail'>
type NavProp = NativeStackNavigationProp<IncidentsStackParamList>

const NEXT_STATUS: Record<string, IncidentStatus> = {
  open: 'dispatched',
  dispatched: 'en_route',
  en_route: 'on_scene',
  on_scene: 'contained',
  contained: 'investigating',
  investigating: 'resolved',
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

export function IncidentDetailScreen() {
  const { params } = useRoute<RoutePropType>()
  const navigation = useNavigation<NavProp>()
  const qc = useQueryClient()
  const [note, setNote] = useState('')
  const [sosSending, setSosSending] = useState(false)

  const { data: incidents = [], isLoading: loadingInc } = useQuery({
    queryKey: ['incidents', 'all'],
    queryFn: () => getIncidents(undefined),
  })
  const incident = incidents.find((i: Incident) => i.id === params.incidentId)

  const { data: streams = [] } = useQuery<Stream[]>({
    queryKey: ['streams', incident?.camera_id],
    queryFn: () => getStreams(incident!.camera_id),
    enabled: !!incident?.camera_id,
  })
  const liveStream = streams.find((s: Stream) => s.status === 'online') ?? streams[0]

  const { data: notes = [], isLoading: loadingNotes } = useQuery({
    queryKey: ['incident-notes', params.incidentId],
    queryFn: () => getIncidentNotes(params.incidentId),
    enabled: !!incident,
  })

  const { mutate: postNote, isPending: postingNote } = useMutation({
    mutationFn: () => addIncidentNote(params.incidentId, note.trim()),
    onSuccess: () => {
      setNote('')
      qc.invalidateQueries({ queryKey: ['incident-notes', params.incidentId] })
    },
    onError: () => RNAlert.alert('Error', 'Failed to add note.'),
  })

  const { mutate: changeStatus, isPending: changingStatus } = useMutation({
    mutationFn: (s: IncidentStatus) => updateIncidentStatus(params.incidentId, s),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['incidents'] })
      RNAlert.alert('Updated', 'Incident status has been updated.')
    },
    onError: () => RNAlert.alert('Error', 'Failed to update status.'),
  })

  const handleSOS = () => {
    RNAlert.alert(
      'Send SOS Panic Alert',
      'This will immediately alert all supervisors. Are you sure?',
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'SEND SOS',
          style: 'destructive',
          onPress: async () => {
            setSosSending(true)
            try {
              await triggerSOS({ description: `SOS from incident: ${incident?.title}` })
              RNAlert.alert('SOS Sent', 'Emergency alert has been sent to supervisors.')
            } catch {
              RNAlert.alert('Error', 'Failed to send SOS. Please try again or call directly.')
            } finally {
              setSosSending(false)
            }
          },
        },
      ],
    )
  }

  if (loadingInc) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={colors.primary} />
      </View>
    )
  }

  if (!incident) {
    return (
      <View style={styles.center}>
        <Text style={styles.emptyText}>Incident not found</Text>
      </View>
    )
  }

  const nextStatus = NEXT_STATUS[incident.status]

  return (
    <ScrollView style={styles.root} contentContainerStyle={styles.content}>
      {/* Header */}
      <Card>
        <Text style={styles.title}>{incident.title}</Text>
        <View style={styles.badgeRow}>
          <SeverityBadge value={incident.severity} />
          <StatusBadge value={incident.status} />
          {incident.is_auto_created && (
            <View style={styles.autoBadge}>
              <Text style={styles.autoText}>AUTO</Text>
            </View>
          )}
        </View>
        {incident.description && (
          <Text style={styles.description}>{incident.description}</Text>
        )}
      </Card>

      {/* Live Camera Feed */}
      {liveStream && incident.camera_id && (
        <Pressable
          style={styles.liveBtn}
          onPress={() =>
            navigation.navigate('IncidentCameraLive', {
              cameraId: incident.camera_id,
              streamId: liveStream.id,
              cameraName: incident.camera_name ?? 'Camera',
            })
          }
        >
          <Ionicons name="videocam" size={18} color="#fff" />
          <Text style={styles.btnText}>View Live Feed — {incident.camera_name}</Text>
        </Pressable>
      )}

      {/* SOS Panic Button */}
      <Pressable
        style={[styles.sosBtn, sosSending && styles.btnDisabled]}
        onPress={handleSOS}
        disabled={sosSending}
      >
        {sosSending ? (
          <ActivityIndicator color="#fff" size="small" />
        ) : (
          <>
            <Ionicons name="alert-circle" size={20} color="#fff" />
            <Text style={styles.sosBtnText}>SOS PANIC</Text>
          </>
        )}
      </Pressable>

      {/* Details */}
      <Card style={styles.gap}>
        <Text style={styles.sectionTitle}>Details</Text>
        <InfoRow label="Camera" value={incident.camera_name} />
        <InfoRow label="Created" value={new Date(incident.created_at).toLocaleString()} />
        {incident.resolved_at && (
          <InfoRow label="Resolved" value={new Date(incident.resolved_at).toLocaleString()} />
        )}
      </Card>

      {/* Status action */}
      {nextStatus && (
        <Pressable
          style={[styles.statusBtn, changingStatus && styles.btnDisabled]}
          onPress={() => changeStatus(nextStatus)}
          disabled={changingStatus}
        >
          {changingStatus
            ? <ActivityIndicator color="#fff" size="small" />
            : (
              <>
                <Ionicons name="arrow-forward-circle" size={18} color="#fff" />
                <Text style={styles.btnText}>Mark as {nextStatus.replace('_', ' ')}</Text>
              </>
            )
          }
        </Pressable>
      )}

      {/* Notes */}
      <View>
        <Text style={styles.sectionTitle}>Notes</Text>
        {loadingNotes ? (
          <ActivityIndicator color={colors.primary} style={{ marginVertical: spacing.md }} />
        ) : notes.length === 0 ? (
          <Card>
            <Text style={styles.emptyText}>No notes yet</Text>
          </Card>
        ) : (
          <FlatList
            data={notes}
            keyExtractor={(n) => n.id}
            scrollEnabled={false}
            renderItem={({ item }) => (
              <Card style={styles.noteCard}>
                <Text style={styles.noteText}>{item.note}</Text>
                <Text style={styles.noteTime}>{new Date(item.created_at).toLocaleString()}</Text>
              </Card>
            )}
            ItemSeparatorComponent={() => <View style={{ height: spacing.xs }} />}
          />
        )}
      </View>

      {/* Add note */}
      <Card style={styles.gap}>
        <Text style={styles.sectionTitle}>Add Note</Text>
        <TextInput
          style={styles.noteInput}
          placeholder="Write a note..."
          placeholderTextColor={colors.textDisabled}
          value={note}
          onChangeText={setNote}
          multiline
          numberOfLines={3}
        />
        <Pressable
          style={[styles.noteBtn, (!note.trim() || postingNote) && styles.btnDisabled]}
          onPress={() => postNote()}
          disabled={!note.trim() || postingNote}
        >
          {postingNote
            ? <ActivityIndicator color="#fff" size="small" />
            : <Text style={styles.btnText}>Add Note</Text>
          }
        </Pressable>
      </Card>
    </ScrollView>
  )
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: colors.background,
  },
  content: {
    padding: spacing.md,
    gap: spacing.md,
    paddingBottom: spacing.xl,
  },
  center: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.background,
  },
  title: {
    fontSize: fontSize.lg,
    fontWeight: '700',
    color: colors.text,
    marginBottom: spacing.sm,
  },
  badgeRow: {
    flexDirection: 'row',
    gap: spacing.xs,
    flexWrap: 'wrap',
    marginBottom: spacing.sm,
  },
  autoBadge: {
    borderRadius: 4,
    borderWidth: 1,
    borderColor: colors.info,
    paddingHorizontal: 6,
    paddingVertical: 2,
    alignSelf: 'flex-start',
  },
  autoText: {
    color: colors.info,
    fontSize: fontSize.xs,
    fontWeight: '700',
  },
  description: {
    fontSize: fontSize.sm,
    color: colors.textSecondary,
    lineHeight: 20,
  },
  gap: {
    gap: spacing.xs,
  },
  sectionTitle: {
    fontSize: fontSize.md,
    fontWeight: '700',
    color: colors.text,
    marginBottom: spacing.xs,
  },
  infoRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    paddingVertical: 4,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.divider,
  },
  infoLabel: {
    fontSize: fontSize.sm,
    color: colors.textSecondary,
  },
  infoValue: {
    fontSize: fontSize.sm,
    color: colors.text,
    fontWeight: '500',
  },
  liveBtn: {
    backgroundColor: colors.secondary,
    borderRadius: radius.sm,
    paddingVertical: spacing.md,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: spacing.sm,
  },
  sosBtn: {
    backgroundColor: '#E53935',
    borderRadius: radius.sm,
    paddingVertical: spacing.md + 4,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: spacing.sm,
    borderWidth: 2,
    borderColor: '#FF5252',
  },
  sosBtnText: {
    color: '#fff',
    fontWeight: '900',
    fontSize: fontSize.lg,
    letterSpacing: 2,
  },
  statusBtn: {
    backgroundColor: colors.secondary,
    borderRadius: radius.sm,
    paddingVertical: spacing.md,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: spacing.sm,
  },
  btnDisabled: {
    opacity: 0.5,
  },
  btnText: {
    color: '#fff',
    fontWeight: '700',
    fontSize: fontSize.md,
  },
  noteCard: {
    gap: 4,
  },
  noteText: {
    fontSize: fontSize.sm,
    color: colors.text,
    lineHeight: 20,
  },
  noteTime: {
    fontSize: fontSize.xs,
    color: colors.textSecondary,
  },
  noteInput: {
    borderWidth: 1,
    borderColor: colors.cardBorder,
    borderRadius: radius.sm,
    backgroundColor: colors.surface,
    color: colors.text,
    fontSize: fontSize.sm,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    minHeight: 72,
    textAlignVertical: 'top',
    marginBottom: spacing.sm,
  },
  noteBtn: {
    backgroundColor: colors.primary,
    borderRadius: radius.sm,
    paddingVertical: spacing.sm + 2,
    alignItems: 'center',
  },
  emptyText: {
    color: colors.textSecondary,
    fontSize: fontSize.sm,
    textAlign: 'center',
    paddingVertical: spacing.xs,
  },
})
