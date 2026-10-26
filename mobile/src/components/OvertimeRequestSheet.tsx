/**
 * "Working overtime": the guard says how many extra hours and why. Used by the check-in card and My Shifts.
 * Design: docs/plans/2026-10-25-roster-autoclose-overtime-design.md, section 3.
 */
import React, { useState } from 'react'
import { ActivityIndicator, Modal, Pressable, StyleSheet, Text, TextInput, View } from 'react-native'
import { Ionicons } from '@expo/vector-icons'
import { useMutation, useQueryClient } from '@tanstack/react-query'

import { askOvertime, stepHours } from '@/api/overtime'
import { apiErrorText } from '@/lib/apiErrorText'
import { colors, fontSize, radius, spacing } from '@/theme'

export function OvertimeRequestSheet({ shiftId, onClose }: { shiftId: string | null; onClose: () => void }) {
  const qc = useQueryClient()
  const [hours, setHours] = useState(1)
  const [reason, setReason] = useState('')
  const [error, setError] = useState<string | null>(null)
  const ask = useMutation({
    mutationFn: () => askOvertime(shiftId!, hours, reason.trim()),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['overtime', shiftId] })
      qc.invalidateQueries({ queryKey: ['my-shifts'] })
      setReason(''); setHours(1); setError(null)
      onClose()
    },
    onError: (err) => setError(apiErrorText(err)),
  })

  return (
    <Modal visible={shiftId !== null} transparent animationType="slide" onRequestClose={onClose}>
      <View style={styles.backdrop}>
        <View style={styles.sheet}>
          <Text style={styles.title}>Working overtime</Text>
          <Text style={styles.muted}>
            Your supervisor will approve or reject it. While it is waiting or approved, your shift is not closed
            automatically until these hours have passed. Only approved overtime is paid.
          </Text>
          <View style={styles.stepper}>
            <Pressable style={styles.stepBtn} onPress={() => setHours((h) => stepHours(h, -0.5))}
                       accessibilityRole="button" accessibilityLabel="Fewer hours">
              <Ionicons name="remove" size={20} color={colors.text} />
            </Pressable>
            <Text style={styles.hours}>{hours} h</Text>
            <Pressable style={styles.stepBtn} onPress={() => setHours((h) => stepHours(h, 0.5))}
                       accessibilityRole="button" accessibilityLabel="More hours">
              <Ionicons name="add" size={20} color={colors.text} />
            </Pressable>
          </View>
          <TextInput
            style={styles.input}
            placeholder="Why (for example: relief guard late, incident on site)"
            placeholderTextColor={colors.textSecondary}
            value={reason}
            onChangeText={setReason}
            multiline
            maxLength={500}
          />
          {error && <Text style={styles.error}>{error}</Text>}
          <View style={styles.row}>
            <Pressable style={[styles.button, styles.cancel]} onPress={onClose} accessibilityRole="button">
              <Text style={styles.buttonText}>Cancel</Text>
            </Pressable>
            <Pressable
              style={[styles.button, styles.send, (!reason.trim() || ask.isPending) && styles.off]}
              disabled={!reason.trim() || ask.isPending}
              onPress={() => ask.mutate()}
              accessibilityRole="button"
              accessibilityLabel="Send the overtime request"
            >
              {ask.isPending ? <ActivityIndicator color="#fff" /> : <Text style={styles.buttonText}>Send</Text>}
            </Pressable>
          </View>
        </View>
      </View>
    </Modal>
  )
}

const styles = StyleSheet.create({
  backdrop: { flex: 1, justifyContent: 'flex-end', backgroundColor: 'rgba(0,0,0,0.5)' },
  sheet: {
    backgroundColor: colors.sheet, padding: spacing.lg, borderTopLeftRadius: radius.lg,
    borderTopRightRadius: radius.lg, gap: spacing.sm,
  },
  title: { fontSize: fontSize.lg, fontWeight: '700', color: colors.text },
  muted: { fontSize: fontSize.sm, color: colors.textSecondary },
  stepper: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: spacing.lg, marginVertical: spacing.sm },
  stepBtn: {
    width: 44, height: 44, borderRadius: 22, alignItems: 'center', justifyContent: 'center',
    borderWidth: 1, borderColor: colors.cardBorder,
  },
  hours: { fontSize: 28, fontWeight: '700', color: colors.text, minWidth: 90, textAlign: 'center' },
  input: {
    minHeight: 72, borderWidth: 1, borderColor: colors.cardBorder, borderRadius: radius.sm, padding: spacing.sm,
    color: colors.text, textAlignVertical: 'top',
  },
  error: { fontSize: fontSize.sm, color: colors.error },
  row: { flexDirection: 'row', gap: spacing.sm, marginTop: spacing.sm },
  button: { flex: 1, alignItems: 'center', justifyContent: 'center', paddingVertical: spacing.md, borderRadius: radius.sm },
  cancel: { backgroundColor: colors.card },
  send: { backgroundColor: colors.primary },
  off: { opacity: 0.4 },
  buttonText: { color: '#fff', fontWeight: '700' },
})
