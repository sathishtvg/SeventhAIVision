/**
 * Daily briefings: the days a person at a desk has reviewed and published,
 * newest first, and one of them read whole.
 *
 * Each line is a fixed sentence with a count in it. A line that is not simply
 * about the day says what it is true of — the moment the briefing was drafted,
 * or the weeks before. A section the reviewer left out is named as left out.
 * The phone reads; drafting and publishing are done at a desk.
 */
import React, { useCallback, useState } from 'react'
import { ActivityIndicator, FlatList, Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Ionicons } from '@expo/vector-icons'
import { getBriefing, listPublishedBriefings, type BriefingSummary } from '@/api/briefings'
import { Card } from '@/components/Card'
import { apiErrorText } from '@/lib/apiErrorText'
import { AS_AT_LABEL, briefingStanding, briefingWhere } from '@/lib/deskWork'
import { colors, fontSize, spacing } from '@/theme'

const dayOf = (iso: string) =>
  new Date(`${iso}T00:00:00`).toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' })

function OneBriefing({ id, onBack }: { id: string; onBack: () => void }) {
  const { data, isLoading, error } = useQuery({ queryKey: ['briefing', id], queryFn: () => getBriefing(id) })
  return (
    <ScrollView style={styles.root} contentContainerStyle={styles.content}>
      <Pressable onPress={onBack} accessibilityRole="button" style={styles.back}>
        <Ionicons name="chevron-back" size={18} color={colors.primary} />
        <Text style={styles.backText}>All briefings</Text>
      </Pressable>
      {isLoading && <ActivityIndicator color={colors.primary} />}
      {!!error && <Text style={styles.empty}>{apiErrorText(error)}</Text>}
      {data && (
        <>
          <Text style={styles.heading}>{dayOf(data.briefing_date)} · {briefingWhere(data)}</Text>
          {briefingStanding(data) && <Text style={styles.meta}>{briefingStanding(data)}</Text>}
          {data.published_by_name && <Text style={styles.meta}>Published by {data.published_by_name}</Text>}
          {data.note && (
            <Card style={styles.note}>
              <Text style={styles.meta}>The reviewer&apos;s note</Text>
              <Text style={styles.line}>{data.note}</Text>
            </Card>)}
          {data.sections.filter((s) => !s.left_out).map((s) => (
            <Card key={s.key} style={styles.section} testID={`briefing-${s.key}`}>
              <Text style={styles.title}>{s.title}</Text>
              {s.note && <Text style={styles.meta}>{s.note}</Text>}
              {s.lines.map((l) => (
                <Text key={l.text} style={styles.line}>
                  {l.text}{AS_AT_LABEL[l.as_at] ? <Text style={styles.meta}>  ({AS_AT_LABEL[l.as_at]})</Text> : null}
                </Text>))}
            </Card>))}
          {data.left_out.length > 0 && (
            <Text style={styles.meta} testID="left-out">Left out by the reviewer: {data.left_out.map((s) => s.title).join(', ')}.</Text>)}
        </>)}
    </ScrollView>
  )
}

function Row({ item, onOpen }: { item: BriefingSummary; onOpen: (id: string) => void }) {
  return (
    <Pressable onPress={() => onOpen(item.id)} accessibilityRole="button">
      <Card style={styles.section}>
        <Text style={styles.title}>{dayOf(item.briefing_date)}</Text>
        <Text style={styles.line}>{briefingWhere(item)}</Text>
        {briefingStanding(item) && <Text style={styles.meta}>{briefingStanding(item)}</Text>}
        {item.note && <Text style={styles.meta} numberOfLines={2}>{item.note}</Text>}
      </Card>
    </Pressable>
  )
}

export function BriefingsScreen() {
  const qc = useQueryClient()
  const [openId, setOpenId] = useState<string | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  const { data = [], isLoading, error } = useQuery({ queryKey: ['briefings'], queryFn: listPublishedBriefings })
  const onRefresh = useCallback(async () => {
    setRefreshing(true)
    await qc.invalidateQueries({ queryKey: ['briefings'] })
    setRefreshing(false)
  }, [qc])
  if (openId) return <OneBriefing id={openId} onBack={() => setOpenId(null)} />
  if (isLoading) return <View style={styles.center}><ActivityIndicator color={colors.primary} /></View>
  return (
    <View style={styles.root}>
      <FlatList
        data={data}
        keyExtractor={(b) => b.id}
        contentContainerStyle={styles.list}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.primary} />}
        renderItem={({ item }) => <Row item={item} onOpen={setOpenId} />}
        ListEmptyComponent={
          <View style={styles.center}>
            <Ionicons name={error ? 'alert-circle-outline' : 'newspaper-outline'} size={48} color={colors.textDisabled} />
            <Text style={styles.empty}>{error ? apiErrorText(error) : 'No briefing has been published yet.'}</Text>
          </View>}
      />
    </View>
  )
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.background },
  content: { padding: spacing.md, gap: spacing.sm },
  list: { padding: spacing.md, gap: spacing.sm, flexGrow: 1 },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: spacing.xl, gap: spacing.sm },
  empty: { color: colors.textSecondary, fontSize: fontSize.md, textAlign: 'center' },
  back: { flexDirection: 'row', alignItems: 'center', gap: 4, paddingVertical: 4 },
  backText: { color: colors.primary, fontSize: fontSize.sm, fontWeight: '600' },
  heading: { color: colors.text, fontSize: fontSize.lg, fontWeight: '700' },
  note: { borderColor: colors.info, borderWidth: 1, gap: 4 },
  section: { gap: 4 },
  title: { color: colors.text, fontSize: fontSize.md, fontWeight: '700' },
  line: { color: colors.text, fontSize: fontSize.sm },
  meta: { color: colors.textSecondary, fontSize: fontSize.xs },
})
