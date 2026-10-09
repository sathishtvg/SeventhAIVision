/**
 * My Reading: what is recorded of this person's own work — shifts, patrols,
 * responses, violations, training and handovers — over a period they choose.
 *
 * It is counts, each beside how much there was to do, and the server's note
 * that it is not an appraisal is shown above them. It is theirs to read; this
 * screen reads nobody else's. What is recommended for them is shown as advice
 * for their manager, with what it says to consider.
 */
import React, { useCallback, useState } from 'react'
import { ActivityIndicator, Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Ionicons } from '@expo/vector-icons'
import { READING_DAYS, getMyReading } from '@/api/workforce'
import { apiErrorText } from '@/lib/apiErrorText'
import { Card } from '@/components/Card'
import { periodLabel, readingLines } from '@/lib/myReading'
import { colors, fontSize, radius, spacing } from '@/theme'

export function MyReadingScreen() {
  const qc = useQueryClient()
  const [days, setDays] = useState<number>(28)
  const [refreshing, setRefreshing] = useState(false)
  const { data, isLoading, error } = useQuery({ queryKey: ['my-reading', days], queryFn: () => getMyReading(days) })
  const onRefresh = useCallback(async () => {
    setRefreshing(true)
    await qc.invalidateQueries({ queryKey: ['my-reading'] })
    setRefreshing(false)
  }, [qc])

  return (
    <ScrollView style={styles.root} contentContainerStyle={styles.content}
                refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.primary} />}>
      <View style={styles.chips}>
        {READING_DAYS.map((d) => (
          <Pressable key={d} onPress={() => setDays(d)} accessibilityRole="button" accessibilityState={{ selected: d === days }}
                     style={[styles.chip, d === days && styles.chipOn]}>
            <Text style={[styles.chipText, d === days && styles.chipTextOn]}>{periodLabel(d)}</Text>
          </Pressable>))}
      </View>
      {isLoading && <ActivityIndicator color={colors.primary} style={styles.wait} />}
      {!!error && (
        <View style={styles.center}>
          <Ionicons name="alert-circle-outline" size={40} color={colors.textDisabled} />
          <Text style={styles.empty}>{apiErrorText(error)}</Text>
        </View>)}
      {data && (
        <>
          <Card style={styles.note}>
            <Text style={styles.noteText} testID="reading-note">{data.note}</Text>
          </Card>
          {data.sections.map((s) => {
            const lines = readingLines(s.key, data.figures)
            if (!lines.length) return null
            return (
              <Card key={s.key} style={styles.section} testID={`section-${s.key}`}>
                <Text style={styles.title}>{s.title}</Text>
                {lines.map((line) => <Text key={line} style={styles.line}>{line}</Text>)}
                <Text style={styles.from}>{s.counted_from}</Text>
              </Card>)
          })}
          {data.not_read.length > 0 && (
            <Text style={styles.from}>
              Not shown to you: {data.not_read.map((n) => n.title).join(', ')}.
            </Text>)}
          <Text style={styles.heading}>Recommended for you</Text>
          <Text style={styles.from}>{data.recommendations_note}</Text>
          {data.recommendations.length === 0 && <Text style={styles.line}>Nothing is recommended for you.</Text>}
          {data.recommendations.map((r) => (
            <Card key={r.key} style={styles.section} testID="recommendation">
              <Text style={styles.line}>{r.statement}</Text>
              <Text style={styles.from}>To consider: {r.consider}</Text>
              {r.answer && (
                <Text style={styles.from}>
                  Your manager {r.answer.answer === 'ACCEPTED' ? 'accepted this' : `did not accept this: ${r.answer.reason ?? ''}`}
                </Text>)}
            </Card>))}
        </>)}
    </ScrollView>
  )
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.background },
  content: { padding: spacing.md, gap: spacing.sm },
  chips: { flexDirection: 'row', gap: spacing.sm, flexWrap: 'wrap' },
  chip: { borderWidth: 1, borderColor: colors.glassBorder, borderRadius: radius.md, paddingHorizontal: spacing.md, paddingVertical: 6 },
  chipOn: { backgroundColor: colors.primary, borderColor: colors.primary },
  chipText: { color: colors.textSecondary, fontSize: fontSize.sm },
  chipTextOn: { color: colors.background, fontWeight: '700' },
  wait: { marginTop: spacing.xl },
  center: { alignItems: 'center', padding: spacing.xl, gap: spacing.sm },
  empty: { color: colors.textSecondary, fontSize: fontSize.md, textAlign: 'center' },
  note: { borderColor: colors.info, borderWidth: 1 },
  noteText: { color: colors.text, fontSize: fontSize.sm },
  section: { gap: 4 },
  title: { color: colors.text, fontSize: fontSize.md, fontWeight: '700' },
  heading: { color: colors.text, fontSize: fontSize.md, fontWeight: '700', marginTop: spacing.md },
  line: { color: colors.text, fontSize: fontSize.sm },
  from: { color: colors.textSecondary, fontSize: fontSize.xs },
})
