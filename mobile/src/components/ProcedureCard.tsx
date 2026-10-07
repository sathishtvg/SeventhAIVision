/**
 * The procedure for an incident, on the incident.
 *
 * A guard standing at a forced gate should not have to remember what the
 * procedure says, or go and find it. This card puts the approved procedure for
 * an incident of this kind beside it, word for word, with which version it is
 * and who approved it — and lets the guard look for the passage on anything
 * else in the library.
 *
 * It shows procedure text and never an answer of its own. When no procedure is
 * for this kind of incident it says so, and why.
 *
 * For whoever may read procedures (`sop:read`); it hides itself for anybody else.
 */
import React, { useState } from 'react'
import { ActivityIndicator, Pressable, StyleSheet, Text, TextInput, View } from 'react-native'
import { Ionicons } from '@expo/vector-icons'
import { useMutation, useQuery } from '@tanstack/react-query'

import { askProcedures, getProceduresForIncident } from '@/api/sop'
import { canSee } from '@/lib/access'
import { NOT_AN_ANSWER, passageSource, procedureSource, worthAsking } from '@/lib/procedures'
import { useAuthStore } from '@/store/auth'
import { Card } from '@/components/Card'
import { colors, fontSize, radius, spacing } from '@/theme'

export function ProcedureCard({ incidentId }: { incidentId: string }) {
  const permissions = useAuthStore((s) => s.permissions)
  const roleId = useAuthStore((s) => s.user?.roleId)
  const allowed = canSee({ permission: 'sop:read' }, permissions, roleId)
  const [open, setOpen] = useState<string | null>(null)
  const [question, setQuestion] = useState('')
  const { data } = useQuery({
    queryKey: ['sop', 'for-incident', incidentId], queryFn: () => getProceduresForIncident(incidentId),
    enabled: allowed, retry: false })
  const ask = useMutation({ mutationFn: () => askProcedures(question.trim()) })
  if (!allowed || !data) return null
  return (
    <Card style={styles.card}>
      <View style={styles.head}>
        <Ionicons name="book-outline" size={18} color={colors.primary} />
        <Text style={styles.title}>The procedure</Text>
      </View>
      {!data.procedures.length && <Text style={styles.meta}>{data.why_none}</Text>}
      {data.procedures.map((p) => {
        const shown = open === p.id || data.procedures.length === 1
        return (
          <View key={p.id} style={styles.procedure}>
            <Pressable onPress={() => setOpen(shown ? null : p.id)} accessibilityRole="button"
                       accessibilityLabel={`${shown ? 'Close' : 'Read'} ${p.title}`} style={styles.row}>
              <Text style={styles.name}>{p.title}</Text>
              {data.procedures.length > 1 && (
                <Ionicons name={shown ? 'chevron-up' : 'chevron-down'} size={18} color={colors.textSecondary} />)}
            </Pressable>
            <Text style={styles.meta}>{procedureSource(p)}</Text>
            {shown && <Text style={styles.text} selectable>{p.text}</Text>}
          </View>
        )
      })}

      <View style={styles.ask}>
        <TextInput style={styles.input} value={question} onChangeText={setQuestion} maxLength={300}
                   placeholder="Look for something else in the procedures" placeholderTextColor={colors.textDisabled}
                   returnKeyType="search" onSubmitEditing={() => worthAsking(question) && ask.mutate()} />
        <Pressable style={[styles.find, (!worthAsking(question) || ask.isPending) && styles.disabled]}
                   disabled={!worthAsking(question) || ask.isPending} onPress={() => ask.mutate()}
                   accessibilityRole="button" accessibilityLabel="Find it in the procedures">
          {ask.isPending ? <ActivityIndicator color="#fff" size="small" /> : <Ionicons name="search" size={18} color="#fff" />}
        </Pressable>
      </View>
      {ask.isError && <Text style={styles.meta}>That could not be looked for. Check your connection and try again.</Text>}
      {ask.data && (
        <View style={styles.found}>
          <Text style={styles.meta}>{ask.data.nothing ?? NOT_AN_ANSWER}</Text>
          {ask.data.passages.map((p) => (
            <View key={p.id} style={styles.passage}>
              {!!p.heading && <Text style={styles.name}>{p.heading}</Text>}
              <Text style={styles.text} selectable>{p.text}</Text>
              <Text style={styles.meta}>{passageSource(p)}</Text>
            </View>))}
        </View>)}
    </Card>
  )
}

const styles = StyleSheet.create({
  card: { gap: spacing.sm },
  head: { flexDirection: 'row', alignItems: 'center', gap: spacing.xs },
  title: { color: colors.text, fontSize: fontSize.md, fontWeight: '700' },
  procedure: { gap: 4, paddingTop: spacing.sm, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.divider },
  row: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: spacing.sm },
  name: { color: colors.text, fontSize: fontSize.sm, fontWeight: '700', flex: 1 },
  meta: { color: colors.textSecondary, fontSize: fontSize.xs, lineHeight: 18 },
  text: { color: colors.text, fontSize: fontSize.sm, lineHeight: 21, marginTop: 2 },
  ask: { flexDirection: 'row', gap: spacing.sm, alignItems: 'center', marginTop: spacing.xs },
  input: { flex: 1, borderWidth: 1, borderColor: colors.cardBorder, borderRadius: radius.sm, backgroundColor: colors.surface,
           color: colors.text, fontSize: fontSize.sm, paddingHorizontal: spacing.md, paddingVertical: spacing.sm },
  find: { backgroundColor: colors.primary, borderRadius: radius.sm, padding: spacing.sm + 2 },
  disabled: { opacity: 0.5 },
  found: { gap: spacing.sm },
  passage: { gap: 2, padding: spacing.sm, borderRadius: radius.sm, borderWidth: 1, borderColor: colors.cardBorder },
})
