/**
 * The AI assistant on the phone (AI_MODULE.md; backend/app/routers/ai_assistant.py).
 *
 * A guard types a question in their own language and the reply comes in the same language. It reads the app with
 * the person's own permissions; a change it prepares comes as a card showing exactly what will be sent, and nothing
 * changes until the person presses Confirm. Shown only to people it is switched on for: the licence, the admin's
 * switch and their role (the More menu hides the row otherwise; this screen says why if reached anyway).
 */
import React, { useRef, useState } from 'react'
import {
  ActivityIndicator, KeyboardAvoidingView, Platform, Pressable, ScrollView, StyleSheet, Text, TextInput, View,
} from 'react-native'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Ionicons } from '@expo/vector-icons'

import {
  askAssistant, assistantIsMine, cancelAction, confirmAction, getAiStatus, getAssistantUsage,
} from '@/api/aiAssistant'
import type { AssistantAction } from '@/api/aiAssistant'
import { Card } from '@/components/Card'
import { DetailSkeleton, ErrorState } from '@/components/states'
import { apiErrorText } from '@/lib/apiErrorText'
import { askErrorText, cardOutcome, replyLines, shownValue, type ChatLine } from '@/lib/assistant'
import { colors, fontSize, radius, spacing } from '@/theme'

const NOT_MINE = 'The AI assistant is not switched on for your role. Your administrator chooses who uses it in AI Settings.'

function ActionCard({ action, onDecided }: { action: AssistantAction; onDecided: (a: AssistantAction) => void }) {
  const [error, setError] = useState<string | null>(null)
  const decide = useMutation({
    mutationFn: (how: 'confirm' | 'cancel') => (how === 'confirm' ? confirmAction(action.id) : cancelAction(action.id)),
    onSuccess: (a) => { setError(null); onDecided(a) },
    onError: (err) => setError(apiErrorText(err)),
  })
  const outcome = cardOutcome(action)
  return (
    <Card style={[styles.card, !outcome && styles.cardOpen]} testID={`assistant-card-${action.id}`}>
      <Text style={styles.cardTitle}>{action.title}</Text>
      {action.fields.map((f) => (
        <View key={f.name} style={styles.fieldRow}>
          <Text style={styles.fieldName}>{f.name}</Text>
          <Text style={styles.fieldValue}>{shownValue(f.value)}</Text>
        </View>
      ))}
      {!!action.temporary_password && (
        <Text style={styles.password} selectable>
          Temporary password, shown this once: {action.temporary_password}. Give it to the person; it is not kept.
        </Text>
      )}
      {!!error && <Text style={styles.error}>{error}</Text>}
      {outcome ? (
        <Text style={styles.outcome}>{outcome}</Text>
      ) : (
        <View style={styles.cardButtons}>
          <Pressable accessibilityRole="button" disabled={decide.isPending} onPress={() => decide.mutate('confirm')}
                     style={[styles.confirm, decide.isPending && styles.dim]}>
            <Text style={styles.confirmText}>Confirm</Text>
          </Pressable>
          <Pressable accessibilityRole="button" disabled={decide.isPending} onPress={() => decide.mutate('cancel')}
                     style={styles.cancel}>
            <Text style={styles.cancelText}>Cancel</Text>
          </Pressable>
        </View>
      )}
    </Card>
  )
}

export function AssistantScreen() {
  const qc = useQueryClient()
  const scroll = useRef<ScrollView>(null)
  const [lines, setLines] = useState<ChatLine[]>([])
  const [conversationId, setConversationId] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const [error, setError] = useState<string | null>(null)
  // After a card is decided the assistant waits; one press tells it to go on to its next question.
  const [carryOn, setCarryOn] = useState(false)
  const turns = useRef(0)

  const status = useQuery({ queryKey: ['ai-status'], queryFn: getAiStatus, staleTime: 60_000 })
  const mine = assistantIsMine(status.data)
  const usage = useQuery({ queryKey: ['ai-assistant-usage'], queryFn: getAssistantUsage, enabled: mine, retry: false })

  const ask = useMutation({
    mutationFn: (message: string) => askAssistant(message, conversationId),
    onMutate: (message) => {
      turns.current += 1
      setError(null)
      setCarryOn(false)
      setLines((l) => [...l, { key: `${turns.current}-u`, kind: 'user', text: message }])
    },
    onSuccess: (reply) => {
      setConversationId(reply.conversation_id)
      setLines((l) => [...l, ...replyLines(reply, turns.current)])
    },
    onError: (err) => setError(askErrorText(err)),
    onSettled: () => { void qc.invalidateQueries({ queryKey: ['ai-assistant-usage'] }) },
  })

  const send = (text?: string) => {
    const message = (text ?? draft).trim()
    if (!message || ask.isPending) return
    if (text === undefined) setDraft('')
    ask.mutate(message)
  }

  const fresh = () => {
    setLines([]); setConversationId(null); setError(null); setCarryOn(false)
  }

  if (status.isLoading) return <DetailSkeleton lines={3} style={styles.content} />
  if (status.isError || !status.data) {
    return <ErrorState title="The assistant could not be reached" error={status.error} onRetry={status.refetch} />
  }
  if (!mine) {
    return (
      <View style={styles.root}>
        <Card style={styles.notMine}>
          <Text style={styles.line} testID="assistant-not-mine">{status.data.reason ?? NOT_MINE}</Text>
        </Card>
      </View>
    )
  }

  return (
    <KeyboardAvoidingView style={styles.root} behavior={Platform.OS === 'ios' ? 'padding' : undefined}
                          keyboardVerticalOffset={Platform.OS === 'ios' ? 90 : 0}>
      <ScrollView ref={scroll} style={styles.root} contentContainerStyle={styles.content}
                  onContentSizeChange={() => scroll.current?.scrollToEnd({ animated: true })}>
        {lines.length === 0 && (
          <Text style={styles.intro}>
            Ask about your work in your own language: your next shift, your patrols today, how to report something.
            It works with your own permissions. When you ask for a change it shows you a card, and nothing changes
            until you press Confirm.
          </Text>
        )}
        {lines.map((l) => {
          if (l.kind === 'card' && l.action) {
            return (
              <ActionCard key={l.key} action={l.action} onDecided={(a) => {
                setLines((all) => all.map((x) => (x.action?.id === a.id ? { ...x, action: a } : x)))
                setCarryOn(true)
              }} />
            )
          }
          if (l.kind === 'status') return <Text key={l.key} style={styles.status}>{l.text}</Text>
          return (
            <View key={l.key} style={[styles.bubble, l.kind === 'user' ? styles.mineBubble : styles.theirBubble]}>
              <Text style={l.kind === 'user' ? styles.mineText : styles.line} selectable>{l.text}</Text>
            </View>
          )
        })}
        {ask.isPending && (
          <View style={[styles.bubble, styles.theirBubble, styles.thinking]} testID="assistant-thinking">
            <ActivityIndicator size="small" color={colors.primary} />
            <Text style={styles.status}>Thinking…</Text>
          </View>
        )}
      </ScrollView>
      <View style={styles.footer}>
        {!!error && <Text style={styles.error}>{error}</Text>}
        <View style={styles.footerRow}>
          {carryOn && !ask.isPending && (
            <Pressable accessibilityRole="button" onPress={() => send('Carry on.')} style={styles.chip}>
              <Text style={styles.chipText}>Carry on</Text>
            </Pressable>
          )}
          {lines.length > 0 && !ask.isPending && (
            <Pressable accessibilityRole="button" onPress={fresh} style={styles.chip}>
              <Text style={styles.chipText}>New conversation</Text>
            </Pressable>
          )}
        </View>
        <View style={styles.inputRow}>
          <TextInput style={styles.input} value={draft} onChangeText={setDraft} multiline maxLength={4000}
                     placeholder="Ask anything about your work" placeholderTextColor={colors.textDisabled}
                     accessibilityLabel="Message to the assistant" />
          <Pressable accessibilityRole="button" accessibilityLabel="Send" onPress={() => send()}
                     disabled={ask.isPending || !draft.trim()}
                     style={[styles.send, (ask.isPending || !draft.trim()) && styles.dim]}>
            <Ionicons name="send" size={18} color={colors.text} />
          </Pressable>
        </View>
        {usage.data?.limit != null && (
          <Text style={styles.usage}>{usage.data.left} of {usage.data.limit} replies left this month for your organisation.</Text>
        )}
      </View>
    </KeyboardAvoidingView>
  )
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.background },
  content: { padding: spacing.md, gap: spacing.sm },
  intro: { color: colors.textSecondary, fontSize: fontSize.sm },
  notMine: { margin: spacing.md },
  bubble: { maxWidth: '85%', borderRadius: radius.md, paddingHorizontal: spacing.md, paddingVertical: spacing.sm },
  mineBubble: { alignSelf: 'flex-end', backgroundColor: colors.primary },
  theirBubble: { alignSelf: 'flex-start', backgroundColor: colors.glassBg },
  thinking: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  mineText: { color: colors.text, fontSize: fontSize.md },
  line: { color: colors.text, fontSize: fontSize.md },
  status: { color: colors.textSecondary, fontSize: fontSize.xs, fontStyle: 'italic' },
  card: { gap: 4 },
  cardOpen: { borderColor: colors.primary, borderWidth: 1 },
  cardTitle: { color: colors.text, fontSize: fontSize.md, fontWeight: '700', marginBottom: 4 },
  fieldRow: { flexDirection: 'row', gap: spacing.sm },
  fieldName: { color: colors.textSecondary, fontSize: fontSize.xs, width: 110 },
  fieldValue: { color: colors.text, fontSize: fontSize.xs, flex: 1 },
  password: { color: colors.info, fontSize: fontSize.sm, marginTop: spacing.sm },
  outcome: { color: colors.textSecondary, fontSize: fontSize.xs, marginTop: spacing.sm },
  cardButtons: { flexDirection: 'row', gap: spacing.sm, marginTop: spacing.sm },
  confirm: { backgroundColor: colors.primary, borderRadius: radius.md, paddingHorizontal: spacing.lg, paddingVertical: spacing.sm },
  confirmText: { color: colors.text, fontWeight: '700', fontSize: fontSize.sm },
  cancel: { borderColor: colors.glassBorder, borderWidth: 1, borderRadius: radius.md, paddingHorizontal: spacing.lg, paddingVertical: spacing.sm },
  cancelText: { color: colors.textSecondary, fontSize: fontSize.sm },
  dim: { opacity: 0.5 },
  error: { color: colors.error, fontSize: fontSize.sm, marginBottom: spacing.xs },
  footer: { borderTopWidth: 1, borderTopColor: colors.divider, padding: spacing.md, gap: spacing.xs },
  footerRow: { flexDirection: 'row', gap: spacing.sm },
  chip: { borderWidth: 1, borderColor: colors.glassBorder, borderRadius: radius.md, paddingHorizontal: spacing.md, paddingVertical: 6 },
  chipText: { color: colors.text, fontSize: fontSize.sm },
  inputRow: { flexDirection: 'row', alignItems: 'flex-end', gap: spacing.sm },
  input: { flex: 1, maxHeight: 120, color: colors.text, fontSize: fontSize.md, borderWidth: 1, borderColor: colors.glassBorder,
           borderRadius: radius.md, paddingHorizontal: spacing.md, paddingVertical: spacing.sm },
  send: { backgroundColor: colors.primary, borderRadius: radius.md, padding: spacing.md },
  usage: { color: colors.textSecondary, fontSize: fontSize.xs },
})
