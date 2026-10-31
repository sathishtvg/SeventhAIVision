/**
 * The AI corner on the phone: the assistant's button in the bottom-right corner of every screen, and above it the card
 * asking for what the AI is waiting on this person to decide.
 *
 * Design: docs/plans/2026-10-31-ai-corner-button-and-decision-popups-design.md (AI_MODULE.md, "Decisions that pop
 * up"). Placed once, at the app's root beside the man-down watcher, so it is on every screen without each screen
 * knowing. It reads the screen from navigation and stays off the screens in HIDDEN_ON, and while the keyboard is up.
 *
 * It asks GET /ai/waiting every minute while the app is open, and again when the app comes back to the front - which
 * is also how a tapped push shows its card. The answer is not kept in the offline cache: yesterday's card is not a
 * decision to take now. Deciding uses the chat's and AI Insights' own calls, so the server checks everything again.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { AppState, Keyboard, Pressable, StyleSheet, Text, View } from 'react-native'
import { Ionicons } from '@expo/vector-icons'
import { useNavigation } from '@react-navigation/native'
import AsyncStorage from '@react-native-async-storage/async-storage'
import { initialWindowMetrics } from 'react-native-safe-area-context'

import { cancelAction, confirmAction } from '@/api/aiAssistant'
import type { AssistantAction } from '@/api/aiAssistant'
import { decideWarning, getAiWaiting } from '@/api/aiWaiting'
import type { AiWaiting, AiWarning } from '@/api/aiWaiting'
import { apiErrorText } from '@/lib/apiErrorText'
import { cardOutcome, shownValue } from '@/lib/assistant'
import {
  HIDDEN_ON, LATER_KEY, NOTICE_MS, WAITING_EVERY_MS, decisionId, decisionsToShow, putOff, warningLines,
} from '@/lib/aiWaiting'
import { useAuthStore } from '@/store/auth'
import { colors, fontSize, radius, spacing } from '@/theme'

/** The bottom tabs' own height (React Navigation's default), above the phone's own bar: the corner sits over it. */
const TAB_BAR = 49
const BOTTOM = TAB_BAR + (initialWindowMetrics?.insets.bottom ?? 0) + spacing.sm

interface Notice { text: string; ok: boolean; password?: string }

/** Outside a screen, useNavigation() is the app's navigation container itself. */
interface RootNavigation {
  getCurrentRoute?: () => { name: string } | undefined
  addListener?: (event: 'state', listener: () => void) => () => void
  navigate: (name: string, params?: object) => void
}

export function AiCorner() {
  const accessToken = useAuthStore((s) => s.accessToken)
  const nav = useNavigation() as unknown as RootNavigation
  const [route, setRoute] = useState<string | undefined>()
  const [keyboard, setKeyboard] = useState(false)
  const [waiting, setWaiting] = useState<AiWaiting | null>(null)
  const [later, setLater] = useState<Record<string, number>>({})
  const [notice, setNotice] = useState<Notice | null>(null)
  const [working, setWorking] = useState(false)

  useEffect(() => {
    const read = () => setRoute(nav.getCurrentRoute?.()?.name)
    read()
    return nav.addListener?.('state', read)
  }, [nav])

  useEffect(() => {
    const shown = Keyboard.addListener('keyboardDidShow', () => setKeyboard(true))
    const hidden = Keyboard.addListener('keyboardDidHide', () => setKeyboard(false))
    return () => { shown.remove(); hidden.remove() }
  }, [])

  useEffect(() => {
    AsyncStorage.getItem(LATER_KEY)
      .then((v) => { if (v) setLater(JSON.parse(v)) })
      .catch(() => { /* nothing put off, or an entry that cannot be read: ask again */ })
  }, [])

  const load = useCallback(async () => {
    try {
      setWaiting(await getAiWaiting())
    } catch {
      // Offline, or the server is down: say nothing, and ask again next minute.
    }
  }, [])

  useEffect(() => {
    if (!accessToken) {
      setWaiting(null)
      return undefined
    }
    void load()
    const every = setInterval(() => { if (AppState.currentState === 'active') void load() }, WAITING_EVERY_MS)
    const front = AppState.addEventListener('change', (state) => { if (state === 'active') void load() })
    return () => { clearInterval(every); front.remove() }
  }, [accessToken, load])

  useEffect(() => {
    // A temporary password stays until the person closes it: it is shown this once and kept nowhere.
    if (!notice || notice.password) return undefined
    const t = setTimeout(() => setNotice(null), NOTICE_MS)
    return () => clearTimeout(t)
  }, [notice])

  const { list, beyond } = useMemo(() => decisionsToShow(waiting, later, Date.now()), [waiting, later])
  const item = list[0] ?? null
  const total = list.length + beyond

  if (!accessToken || !waiting || keyboard || (route && HIDDEN_ON.has(route))) return null
  if (!waiting.assistant && !item && !notice) return null

  const putItOff = (id: string) => {
    const next = putOff(later, id, Date.now())
    setLater(next)
    AsyncStorage.setItem(LATER_KEY, JSON.stringify(next)).catch(() => { /* Later lasts until the app is closed */ })
  }

  const run = async (work: () => Promise<Notice>) => {
    setWorking(true)
    try {
      setNotice(await work())
    } catch (err) {
      // Someone else decided first, the card expired, or the person may no longer: the server says which.
      setNotice({ text: apiErrorText(err), ok: false })
    } finally {
      setWorking(false)
      void load()
    }
  }

  const confirm = (a: AssistantAction) => run(async () => {
    const out = await confirmAction(a.id)
    return { text: out.status === 'CONFIRMED' ? `Done: ${out.title}.` : cardOutcome(out) ?? '', ok: out.status === 'CONFIRMED',
             password: out.temporary_password }
  })
  const cancel = (a: AssistantAction) => run(async () => ({ text: cardOutcome(await cancelAction(a.id)) ?? '', ok: true }))
  const decide = (w: AiWarning, decision: 'ACCEPT' | 'DISMISS') => run(async () => {
    await decideWarning(w.id, decision)
    return { text: decision === 'ACCEPT' ? 'Accepted.' : 'Dismissed.', ok: true }
  })

  const more = list.length - 1 + beyond

  return (
    <View pointerEvents="box-none" style={[styles.corner, { bottom: BOTTOM }]} testID="ai-corner">
      {(notice || item) && (
        <View style={styles.card} testID="ai-decision-card" accessibilityLabel="The AI needs your decision">
          {notice ? (
            <>
              <Text style={[styles.notice, !notice.ok && styles.noticeBad]}>{notice.text}</Text>
              {!!notice.password && (
                <>
                  <Text style={styles.password} selectable>
                    Temporary password, shown this once: {notice.password}. Give it to the person; it is not kept.
                  </Text>
                  <Pressable accessibilityRole="button" onPress={() => setNotice(null)} style={styles.primary}>
                    <Text style={styles.primaryText}>Close</Text>
                  </Pressable>
                </>
              )}
            </>
          ) : item && (
            <>
              <Text style={styles.kind}>
                {item.kind === 'action' ? 'Waiting for your Confirm'
                  : [item.warning.feature_title, item.warning.site_name].filter(Boolean).join(' · ')}
              </Text>
              <Text style={styles.title}>{item.kind === 'action' ? item.action.title : item.warning.title}</Text>
              {item.kind === 'action'
                ? item.action.fields.slice(0, 4).map((f) => (
                  <Text key={f.name} style={styles.line} numberOfLines={2}>
                    <Text style={styles.fieldName}>{f.name} </Text>{shownValue(f.value)}
                  </Text>
                ))
                : warningLines(item.warning).map((words) => (
                  <Text key={words} style={styles.line} numberOfLines={2}>• {words}</Text>
                ))}
              <View style={styles.buttons}>
                {item.kind === 'action' ? (
                  <>
                    <Pressable accessibilityRole="button" disabled={working} onPress={() => void confirm(item.action)}
                               style={[styles.primary, working && styles.dim]}>
                      <Text style={styles.primaryText}>Confirm</Text>
                    </Pressable>
                    <Pressable accessibilityRole="button" disabled={working} onPress={() => void cancel(item.action)}
                               style={styles.plain}>
                      <Text style={styles.plainText}>Cancel</Text>
                    </Pressable>
                  </>
                ) : (
                  <>
                    <Pressable accessibilityRole="button" disabled={working} onPress={() => void decide(item.warning, 'ACCEPT')}
                               style={[styles.primary, working && styles.dim]}>
                      <Text style={styles.primaryText}>Accept</Text>
                    </Pressable>
                    <Pressable accessibilityRole="button" disabled={working} onPress={() => void decide(item.warning, 'DISMISS')}
                               style={styles.plain}>
                      <Text style={styles.plainText}>Dismiss</Text>
                    </Pressable>
                  </>
                )}
                <Pressable accessibilityRole="button" disabled={working} onPress={() => putItOff(decisionId(item))}
                           style={styles.plain}>
                  <Text style={styles.laterText}>Later</Text>
                </Pressable>
              </View>
              {more > 0 && <Text style={styles.more}>and {more} more waiting</Text>}
            </>
          )}
        </View>
      )}
      {waiting.assistant && (
        <Pressable accessibilityRole="button" accessibilityLabel="Ask the AI assistant" testID="ai-corner-button"
                   onPress={() => nav.navigate('Main', { screen: 'More', params: { screen: 'Assistant' } })}
                   style={styles.fab}>
          <Ionicons name="sparkles" size={24} color={colors.text} />
          {total > 0 && (
            <View style={styles.badge}>
              <Text style={styles.badgeText} testID="ai-waiting-count">{total > 99 ? '99+' : total}</Text>
            </View>
          )}
        </Pressable>
      )}
    </View>
  )
}

const styles = StyleSheet.create({
  corner:      { position: 'absolute', right: spacing.md, alignItems: 'flex-end', gap: spacing.sm, zIndex: 10, elevation: 10 },
  // Solid, as every pop-up on the phone: a glass tint lets the screen behind show through its words.
  card:        { width: 300, maxWidth: '92%', backgroundColor: colors.sheet, borderColor: colors.primary, borderWidth: 1,
                 borderRadius: radius.lg, padding: spacing.md, elevation: 12 },
  kind:        { color: colors.textSecondary, fontSize: fontSize.xs, marginBottom: spacing.xs },
  title:       { color: colors.text, fontSize: fontSize.md, fontWeight: '600', marginBottom: spacing.xs },
  line:        { color: colors.text, fontSize: fontSize.sm, marginTop: 2 },
  fieldName:   { color: colors.textSecondary },
  buttons:     { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm, marginTop: spacing.md },
  primary:     { backgroundColor: colors.primary, borderRadius: radius.sm, paddingVertical: spacing.sm, paddingHorizontal: spacing.md },
  primaryText: { color: colors.text, fontSize: fontSize.sm, fontWeight: '600' },
  plain:       { borderRadius: radius.sm, paddingVertical: spacing.sm, paddingHorizontal: spacing.sm },
  plainText:   { color: colors.primary, fontSize: fontSize.sm, fontWeight: '600' },
  laterText:   { color: colors.textSecondary, fontSize: fontSize.sm },
  dim:         { opacity: 0.5 },
  more:        { color: colors.textSecondary, fontSize: fontSize.xs, marginTop: spacing.sm },
  notice:      { color: colors.success, fontSize: fontSize.sm },
  noticeBad:   { color: colors.warning },
  password:    { color: colors.text, fontSize: fontSize.sm, marginVertical: spacing.sm },
  fab:         { width: 56, height: 56, borderRadius: radius.full, backgroundColor: colors.primary, alignItems: 'center',
                 justifyContent: 'center', elevation: 6 },
  badge:       { position: 'absolute', top: -4, right: -4, minWidth: 20, height: 20, borderRadius: 10, paddingHorizontal: 4,
                 backgroundColor: colors.error, alignItems: 'center', justifyContent: 'center' },
  badgeText:   { color: colors.text, fontSize: fontSize.xs, fontWeight: '700' },
})
