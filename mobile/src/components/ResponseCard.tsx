/**
 * A guard's response to a dispatch, on the phone.
 *
 * TWO CARDS, ONE READING.
 *
 * `SentCard` goes on the first page and exists only while the person has been
 * sent somewhere: it names the incident and opens it. Until this, a guard who
 * was dispatched from the command centre learned of it by being told on the
 * radio, or not at all — the dispatch wrote a name on the incident and told
 * nobody.
 *
 * `ResponseCard` goes on the incident itself and is where the guard answers:
 * accept, set off, arrive, report what was found, or say they cannot attend
 * and why. Each is sent with where the phone is, if it will say; a step is
 * recorded without a position rather than not recorded.
 *
 * Both hide themselves for anybody who has not been sent on anything, so they
 * cost a screen nothing on an ordinary day.
 */
import React, { useState } from 'react'
import { ActivityIndicator, Alert as RNAlert, Pressable, StyleSheet, Text, TextInput, View } from 'react-native'
import { Ionicons } from '@expo/vector-icons'
import { useNavigation } from '@react-navigation/native'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import * as Location from 'expo-location'

import {
  acceptResponse, arrivedAt, declineResponse, getMySendings, reportFromGround, setOff, type Position,
} from '@/api/responses'
import { canSee } from '@/lib/access'
import { STATE_LABEL, arrivalText, offered, refusal, sendingFor, summary, type Offered, type StepKey } from '@/lib/responses'
import { useAuthStore } from '@/store/auth'
import { Card } from '@/components/Card'
import { colors, fontSize, radius, spacing } from '@/theme'

/** How often the phone asks whether it has been sent somewhere. The push is the alarm; this is the fallback. */
const REFRESH_MS = 30_000

function useMySendings() {
  const permissions = useAuthStore((s) => s.permissions)
  const roleId = useAuthStore((s) => s.user?.roleId)
  // response:act is what every step's endpoint demands.
  const allowed = canSee({ permission: 'response:act' }, permissions, roleId)
  const query = useQuery({ queryKey: ['responses', 'mine'], queryFn: getMySendings, enabled: allowed,
                           refetchInterval: REFRESH_MS })
  return { allowed, ...query }
}

/** The phone's position, if it will give one. A step is sent without rather than not sent. */
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

export function SentCard() {
  const nav = useNavigation<any>()
  const { allowed, data } = useMySendings()
  if (!allowed || !data?.items.length) return null
  return (
    <View style={styles.sentList}>
      {data.items.map((sending) => {
        const due = arrivalText(sending.arrival)
        return (
          <Pressable key={sending.id} style={styles.sent} accessibilityRole="button"
                     accessibilityLabel={`Open the incident you were sent to: ${sending.title}`}
                     onPress={() => nav.navigate('Incidents', { screen: 'IncidentDetail',
                                                                params: { incidentId: sending.id }, pop: true })}>
            <View style={styles.sentIcon}><Ionicons name="navigate" size={20} color={colors.warning} /></View>
            <View style={styles.sentBody}>
              <Text style={styles.sentTitle} numberOfLines={2}>{sending.title}</Text>
              <Text style={styles.meta} numberOfLines={2}>{summary(sending)}</Text>
              {due && <Text style={[styles.meta, due.late && styles.late]}>{due.text}</Text>}
            </View>
            <Ionicons name="chevron-forward" size={18} color={colors.textSecondary} />
          </Pressable>
        )
      })}
    </View>
  )
}

export function ResponseCard({ incidentId }: { incidentId: string }) {
  const qc = useQueryClient()
  const { allowed, data } = useMySendings()
  const [asking, setAsking] = useState<Offered | null>(null)
  const [text, setText] = useState('')
  const sending = sendingFor(data?.items, incidentId)

  const { mutate: take, isPending } = useMutation({
    mutationFn: async ({ key, words }: { key: StepKey; words: string }) => {
      const at = await whereAmI()
      if (key === 'accept') return acceptResponse(incidentId, at)
      if (key === 'en_route') return setOff(incidentId, at)
      if (key === 'arrived') return arrivedAt(incidentId, at)
      if (key === 'report') return reportFromGround(incidentId, words, at)
      return declineResponse(incidentId, words, at)
    },
    onSuccess: () => {
      setAsking(null)
      setText('')
      qc.invalidateQueries({ queryKey: ['responses'] })
      qc.invalidateQueries({ queryKey: ['incidents'] })
    },
    onError: (err) => RNAlert.alert('Not recorded', refusal(err)),
  })

  if (!allowed || !sending) return null
  const due = arrivalText(sending.arrival)
  const steps = offered(sending.may)

  return (
    <Card style={styles.card}>
      <View style={styles.head}>
        <Ionicons name="navigate" size={18} color={colors.warning} />
        <Text style={styles.state}>{STATE_LABEL[sending.response.state]}</Text>
      </View>
      {!!sending.dispatch_notes && <Text style={styles.notes}>“{sending.dispatch_notes}”</Text>}
      {due && <Text style={[styles.meta, due.late && styles.late]}>{due.text}</Text>}

      {asking ? (
        <View style={styles.ask}>
          <Text style={styles.meta}>
            {asking.key === 'decline' ? 'Say why. The command centre will have to send somebody else.'
              : 'What did you find? It is added to this response as you write it.'}
          </Text>
          <TextInput style={styles.input} value={text} onChangeText={setText} multiline autoFocus maxLength={2000}
                     placeholder={asking.key === 'decline' ? 'Why you cannot attend' : 'What you found'}
                     placeholderTextColor={colors.textDisabled} />
          <View style={styles.row}>
            <Pressable style={[styles.btn, styles.plain]} onPress={() => { setAsking(null); setText('') }} disabled={isPending}>
              <Text style={styles.plainText}>Back</Text>
            </Pressable>
            <Pressable style={[styles.btn, asking.tone === 'stop' ? styles.stop : styles.go,
                               (!text.trim() || isPending) && styles.disabled]}
                       disabled={!text.trim() || isPending}
                       onPress={() => take({ key: asking.key, words: text.trim() })}>
              {isPending ? <ActivityIndicator color="#fff" size="small" />
                : <Text style={styles.btnText}>{asking.key === 'decline' ? 'Say I cannot attend' : 'Send the report'}</Text>}
            </Pressable>
          </View>
        </View>
      ) : (
        <View style={styles.steps}>
          {steps.map((step) => (
            <Pressable key={step.key} disabled={isPending} accessibilityRole="button" accessibilityLabel={step.label}
                       style={[styles.btn, step.tone === 'go' ? styles.go : step.tone === 'stop' ? styles.stopOutline : styles.plain,
                               isPending && styles.disabled]}
                       onPress={() => (step.needsText ? setAsking(step) : take({ key: step.key, words: '' }))}>
              <Text style={step.tone === 'go' ? styles.btnText : step.tone === 'stop' ? styles.stopText : styles.plainText}>
                {step.label}</Text>
            </Pressable>
          ))}
        </View>
      )}
    </Card>
  )
}

const styles = StyleSheet.create({
  sentList: { gap: spacing.sm },
  sent: {
    flexDirection: 'row', alignItems: 'center', gap: spacing.sm, padding: spacing.md, borderRadius: radius.lg,
    borderWidth: 1, borderColor: colors.warning, backgroundColor: colors.warningMuted,
  },
  sentIcon: { width: 36, height: 36, borderRadius: 18, alignItems: 'center', justifyContent: 'center',
              backgroundColor: colors.glassBg },
  sentBody: { flex: 1, gap: 2 },
  sentTitle: { color: colors.text, fontSize: fontSize.md, fontWeight: '700' },
  card: { gap: spacing.sm, borderColor: colors.warning },
  head: { flexDirection: 'row', alignItems: 'center', gap: spacing.xs },
  state: { color: colors.text, fontSize: fontSize.md, fontWeight: '700' },
  notes: { color: colors.text, fontSize: fontSize.sm, fontStyle: 'italic', lineHeight: 20 },
  meta: { color: colors.textSecondary, fontSize: fontSize.sm },
  late: { color: colors.error, fontWeight: '700' },
  steps: { gap: spacing.sm, marginTop: spacing.xs },
  ask: { gap: spacing.sm, marginTop: spacing.xs },
  row: { flexDirection: 'row', gap: spacing.sm },
  btn: { flex: 1, borderRadius: radius.sm, paddingVertical: spacing.md, alignItems: 'center', justifyContent: 'center' },
  go: { backgroundColor: colors.secondary },
  stop: { backgroundColor: colors.error },
  stopOutline: { borderWidth: 1, borderColor: colors.error },
  plain: { borderWidth: 1, borderColor: colors.cardBorder },
  disabled: { opacity: 0.5 },
  btnText: { color: '#fff', fontWeight: '700', fontSize: fontSize.md },
  stopText: { color: colors.error, fontWeight: '700', fontSize: fontSize.md },
  plainText: { color: colors.text, fontWeight: '600', fontSize: fontSize.md },
  input: {
    borderWidth: 1, borderColor: colors.cardBorder, borderRadius: radius.sm, backgroundColor: colors.surface,
    color: colors.text, fontSize: fontSize.sm, paddingHorizontal: spacing.md, paddingVertical: spacing.sm,
    minHeight: 72, textAlignVertical: 'top',
  },
})
