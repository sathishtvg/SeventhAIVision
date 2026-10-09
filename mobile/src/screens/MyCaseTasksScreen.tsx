/**
 * My Case Tasks: the tasks given to whoever is signed in, on the cases they
 * are part of.
 *
 * Somebody given a task finishes that task and does nothing else to the case,
 * so this is all the phone does with a case: say what was done, or say why a
 * task is dropped. A task is shown only when the server says this person may
 * finish it. The case itself is worked at a desk.
 */
import React, { useCallback, useState } from 'react'
import {
  ActivityIndicator, FlatList, Pressable, RefreshControl, StyleSheet, Text, TextInput, View,
} from 'react-native'
import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query'
import { Ionicons } from '@expo/vector-icons'
import { finishTask, getCase, listMyCases } from '@/api/cases'
import { Card } from '@/components/Card'
import { apiErrorText } from '@/lib/apiErrorText'
import { myOpenTasks, noteNeeded, taskDue, type MyTask } from '@/lib/deskWork'
import { colors, fontSize, radius, spacing } from '@/theme'

/** The most cases whose tasks are read at once. */
const MOST_CASES = 20

function Task({ item, onChanged }: { item: MyTask; onChanged: () => Promise<unknown> }) {
  const [how, setHow] = useState<'done' | 'drop' | null>(null)
  const [note, setNote] = useState('')
  const finish = useMutation({
    mutationFn: () => finishTask(item.caseId, item.task.id, how ?? 'done', note).then(onChanged),
    onSuccess: () => { setHow(null); setNote('') },
  })
  return (
    <Card style={styles.row} testID="case-task">
      <Text style={styles.title}>{item.task.title}</Text>
      <Text style={styles.meta}>{item.caseNumber} · {item.caseTitle}</Text>
      <Text style={styles.meta}>
        {taskDue(item.task)}{item.task.created_by_name ? ` · given by ${item.task.created_by_name}` : ''}
      </Text>
      {item.task.detail && <Text style={styles.line}>{item.task.detail}</Text>}
      {!!finish.error && <Text style={styles.error}>{apiErrorText(finish.error)}</Text>}
      {how === null ? (
        <View style={styles.actions}>
          <Pressable style={styles.button} onPress={() => setHow('drop')} accessibilityRole="button">
            <Text style={styles.buttonText}>It will not be done</Text>
          </Pressable>
          <Pressable style={[styles.button, styles.primary]} onPress={() => setHow('done')} accessibilityRole="button">
            <Text style={[styles.buttonText, styles.primaryText]}>It is done</Text>
          </Pressable>
        </View>
      ) : (
        <View style={styles.form}>
          <TextInput style={styles.input} value={note} onChangeText={setNote} multiline maxLength={4000}
                     placeholder={how === 'done' ? 'What was done' : 'Why it is dropped'}
                     accessibilityLabel={how === 'done' ? 'What was done' : 'Why it is dropped'}
                     placeholderTextColor={colors.textDisabled} />
          <View style={styles.actions}>
            <Pressable style={styles.button} disabled={finish.isPending} accessibilityRole="button"
                       onPress={() => { setHow(null); finish.reset() }}>
              <Text style={styles.buttonText}>Not yet</Text>
            </Pressable>
            <Pressable style={[styles.button, styles.primary, (noteNeeded(how, note) || finish.isPending) && styles.off]}
                       disabled={noteNeeded(how, note) || finish.isPending} onPress={() => finish.mutate()}
                       accessibilityRole="button">
              <Text style={[styles.buttonText, styles.primaryText]}>{how === 'done' ? 'Record it as done' : 'Drop the task'}</Text>
            </Pressable>
          </View>
        </View>)}
    </Card>
  )
}

export function MyCaseTasksScreen() {
  const qc = useQueryClient()
  const [refreshing, setRefreshing] = useState(false)
  const mine = useQuery({ queryKey: ['my-cases'], queryFn: listMyCases })
  const open = (mine.data ?? []).filter((c) => c.status === 'OPEN' && c.tasks_open > 0).slice(0, MOST_CASES)
  const details = useQueries({
    queries: open.map((c) => ({ queryKey: ['my-case', c.id], queryFn: () => getCase(c.id) })),
  })
  const tasks = myOpenTasks(details.map((d) => d.data))
  const again = useCallback(async () => {
    await qc.invalidateQueries({ queryKey: ['my-cases'] })
    await qc.invalidateQueries({ queryKey: ['my-case'] })
  }, [qc])
  const onRefresh = useCallback(async () => { setRefreshing(true); await again(); setRefreshing(false) }, [again])
  const waiting = mine.isLoading || details.some((d) => d.isLoading)
  const error = mine.error ?? details.find((d) => d.error)?.error
  if (waiting) return <View style={styles.center}><ActivityIndicator color={colors.primary} /></View>
  return (
    <View style={styles.root}>
      <FlatList
        data={tasks}
        keyExtractor={(t) => t.task.id}
        contentContainerStyle={styles.list}
        keyboardShouldPersistTaps="handled"
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.primary} />}
        renderItem={({ item }) => <Task item={item} onChanged={again} />}
        ListEmptyComponent={
          <View style={styles.center}>
            <Ionicons name={error ? 'alert-circle-outline' : 'checkmark-done-outline'} size={48} color={colors.textDisabled} />
            <Text style={styles.empty}>{error ? apiErrorText(error) : 'No case task is waiting on you.'}</Text>
          </View>}
      />
    </View>
  )
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.background },
  list: { padding: spacing.md, gap: spacing.sm, flexGrow: 1 },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: spacing.xl, gap: spacing.sm },
  empty: { color: colors.textSecondary, fontSize: fontSize.md, textAlign: 'center' },
  row: { gap: 6 },
  title: { color: colors.text, fontSize: fontSize.md, fontWeight: '600' },
  meta: { color: colors.textSecondary, fontSize: fontSize.sm },
  line: { color: colors.text, fontSize: fontSize.sm },
  error: { color: colors.error, fontSize: fontSize.sm },
  actions: { flexDirection: 'row', gap: spacing.sm, flexWrap: 'wrap', marginTop: 4 },
  button: { borderWidth: 1, borderColor: colors.glassBorder, borderRadius: radius.md, paddingHorizontal: spacing.md, paddingVertical: 8 },
  primary: { backgroundColor: colors.primary, borderColor: colors.primary },
  buttonText: { color: colors.text, fontSize: fontSize.sm, fontWeight: '600' },
  primaryText: { color: colors.background },
  off: { opacity: 0.4 },
  form: { gap: spacing.sm, marginTop: 4 },
  input: {
    backgroundColor: colors.surface, borderRadius: radius.md, color: colors.text, paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm, fontSize: fontSize.md,
  },
})
