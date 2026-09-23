/**
 * Picking a date, instead of typing one.
 *
 * WHAT IT REPLACES. Leave dates were two text boxes with the placeholder
 * "YYYY-MM-DD", validated by a regular expression. On a phone, at the start of
 * a shift, that asks a guard to know today's date, know the format, and type it
 * without a keyboard mistake — and the only feedback for getting it wrong was a
 * Submit button that stayed grey with no explanation.
 *
 * WHY A COMPONENT AND NOT AN INLINE PICKER. So the next date field in this app
 * behaves the same. It is the only shape of date entry the app has now, and the
 * ISO string it produces is what the API already expects.
 *
 * ANDROID AND iOS DIFFER and it matters here: Android's picker is a dialog that
 * fires once and dismisses itself, iOS's is inline and fires on every scroll.
 * The `display` and the dismiss handling below are what make one component
 * behave the same way on both.
 */
import { useState } from 'react'
import { Platform, Pressable, StyleSheet, Text, View } from 'react-native'
import DateTimePicker, { type DateTimePickerEvent } from '@react-native-community/datetimepicker'
import { Ionicons } from '@expo/vector-icons'

import { colors, fontSize, radius, spacing } from '@/theme'

/** YYYY-MM-DD in the device's own timezone. toISOString() would shift the date
 *  backwards for anyone east of UTC — Singapore included, where a date picked
 *  before 08:00 would be saved as the day before. */
export function toISODate(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

/** Parse YYYY-MM-DD as a local date, or null. Same reason: `new Date('2026-09-23')`
 *  is parsed as UTC midnight and lands on the 22nd in local time. */
export function fromISODate(s: string | undefined | null): Date | null {
  if (!s) return null
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s)
  if (!m) return null
  const [y, mo, day] = [Number(m[1]), Number(m[2]), Number(m[3])]
  const d = new Date(y, mo - 1, day)
  // JavaScript rolls impossible dates over rather than rejecting them: month 13
  // becomes January of the next year, and the 45th becomes two weeks later. A
  // stored "2026-13-45" would then silently show a date nobody chose, so check
  // the parts came back unchanged.
  if (d.getFullYear() !== y || d.getMonth() !== mo - 1 || d.getDate() !== day) return null
  return d
}

const HUMAN: Intl.DateTimeFormatOptions = {
  weekday: 'short', day: '2-digit', month: 'short', year: 'numeric',
}

interface Props {
  label: string
  value: string                       // ISO YYYY-MM-DD, '' when unset
  onChange: (iso: string) => void
  placeholder?: string
  minimumDate?: Date
  maximumDate?: Date
}

export function DateField({
  label, value, onChange, placeholder = 'Choose a date', minimumDate, maximumDate,
}: Props) {
  const [open, setOpen] = useState(false)
  const selected = fromISODate(value)

  const handle = (event: DateTimePickerEvent, picked?: Date) => {
    // Android: one event, then the dialog is gone either way. iOS: stays open.
    if (Platform.OS === 'android') setOpen(false)
    if (event.type === 'dismissed' || !picked) return
    onChange(toISODate(picked))
  }

  return (
    <View>
      <Text style={styles.label}>{label}</Text>
      <Pressable
        style={styles.field}
        onPress={() => setOpen(true)}
        accessibilityRole="button"
        accessibilityLabel={`${label}: ${selected ? selected.toLocaleDateString(undefined, HUMAN) : placeholder}`}
      >
        <Ionicons name="calendar-outline" size={18} color={colors.textSecondary} />
        <Text style={[styles.value, !selected && styles.placeholder]}>
          {selected ? selected.toLocaleDateString(undefined, HUMAN) : placeholder}
        </Text>
      </Pressable>

      {open && (
        <DateTimePicker
          value={selected ?? new Date()}
          mode="date"
          display={Platform.OS === 'ios' ? 'inline' : 'default'}
          minimumDate={minimumDate}
          maximumDate={maximumDate}
          onChange={handle}
        />
      )}

      {/* iOS keeps the picker on screen, so it needs a way to put it away. */}
      {open && Platform.OS === 'ios' && (
        <Pressable style={styles.done} onPress={() => setOpen(false)}>
          <Text style={styles.doneText}>Done</Text>
        </Pressable>
      )}
    </View>
  )
}

const styles = StyleSheet.create({
  label: {
    fontSize: fontSize.xs, color: colors.textSecondary, textTransform: 'uppercase',
    letterSpacing: 0.5, marginTop: spacing.md, marginBottom: spacing.xs,
  },
  field: {
    flexDirection: 'row', alignItems: 'center', gap: spacing.sm,
    backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.cardBorder,
    borderRadius: radius.sm, paddingHorizontal: spacing.sm, paddingVertical: spacing.md,
  },
  value: { color: colors.text, fontSize: fontSize.md },
  placeholder: { color: colors.textDisabled },
  done: { alignSelf: 'flex-end', padding: spacing.sm },
  doneText: { color: colors.primary, fontWeight: '700' },
})
