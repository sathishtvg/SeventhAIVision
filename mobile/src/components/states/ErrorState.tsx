import React from 'react'
import { Pressable, StyleSheet, Text, View, type StyleProp, type ViewStyle } from 'react-native'
import { Ionicons } from '@expo/vector-icons'
import { colors, fontSize, radius, spacing } from '@/theme'
import { errorText } from './errorText'

export interface ErrorStateProps {
  /** What the request failed with. The words shown are `errorText(error)`, unless `words` are given. */
  error?: unknown
  /** The screen's own words for this failure, where it has them (a module's own reading of the server's answer). */
  words?: string
  /** Asks again. Left out, there is no button - for something that cannot be asked again from here. */
  onRetry?: () => unknown
  title?: string
  /** For a slot inside a card or a list, where the full size would crowd. */
  compact?: boolean
  style?: StyleProp<ViewStyle>
}

/**
 * A request failed, and the screen says so.
 *
 * Before this a screen that was not loading and had nothing to show said "No
 * alerts" or "No incidents" - which is also what it said when the server had
 * not answered, or when the phone had no signal: a guard could not tell a quiet
 * site from a broken connection. This is the third answer, beside "loading" and
 * "nothing here". The phone's copy of the web's `ErrorState`, with the same
 * words.
 *
 * A screen reader is told at once. The button is a real button, large enough
 * for a thumb.
 */
export function ErrorState({ error, words, onRetry, title = 'Could not load this', compact = false, style }: ErrorStateProps) {
  return (
    <View
      style={[styles.box, compact && styles.compact, style]}
      accessibilityRole="alert"
      accessibilityLiveRegion="polite"
      testID="error-state"
    >
      <Ionicons name="alert-circle-outline" size={compact ? 26 : 40} color={colors.error} />
      <Text style={[styles.title, compact && styles.titleCompact]}>{title}</Text>
      <Text style={styles.why}>{words ?? errorText(error)}</Text>
      {onRetry && (
        <Pressable
          onPress={() => { void onRetry() }}
          accessibilityRole="button"
          accessibilityLabel="Try again"
          style={({ pressed }) => [styles.button, pressed && styles.pressed]}
          hitSlop={8}
        >
          <Ionicons name="refresh" size={16} color={colors.primary} />
          <Text style={styles.buttonText}>Try again</Text>
        </Pressable>
      )}
    </View>
  )
}

const styles = StyleSheet.create({
  box: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: spacing.xl, gap: spacing.sm },
  compact: { flex: 0, paddingVertical: spacing.lg },
  title: { color: colors.text, fontSize: fontSize.lg, fontWeight: '700', textAlign: 'center' },
  titleCompact: { fontSize: fontSize.md },
  why: { color: colors.textSecondary, fontSize: fontSize.sm, textAlign: 'center', maxWidth: 320, lineHeight: 19 },
  button: {
    flexDirection: 'row', alignItems: 'center', gap: 6, marginTop: spacing.xs, minHeight: 44,
    paddingHorizontal: spacing.md, borderRadius: radius.md, borderWidth: 1, borderColor: colors.primary,
  },
  pressed: { opacity: 0.7 },
  buttonText: { color: colors.primary, fontSize: fontSize.sm, fontWeight: '700' },
})
