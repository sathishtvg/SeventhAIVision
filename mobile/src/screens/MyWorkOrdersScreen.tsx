/**
 * My Work Orders: the maintenance work given to whoever is signed in.
 *
 * Two things are done from the phone, by the person doing the work: saying it
 * has been started, and saying what was done. A button is there only when the
 * server offers the act. Raising, accepting, assigning and cancelling work are
 * done at a desk.
 */
import React, { useCallback, useState } from 'react'
import {
  ActivityIndicator, FlatList, Pressable, RefreshControl, StyleSheet, Text, TextInput, View,
} from 'react-native'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Ionicons } from '@expo/vector-icons'
import { completeOrder, listMyOrders, startOrder, type WorkOrder } from '@/api/maintenance'
import { Card } from '@/components/Card'
import { apiErrorText } from '@/lib/apiErrorText'
import { ORDER_STATE_LABEL, inOrder, orderDue, orderWhere } from '@/lib/deskWork'
import { colors, fontSize, radius, spacing } from '@/theme'

function Order({ item, onChanged }: { item: WorkOrder; onChanged: () => Promise<unknown> }) {
  const [finishing, setFinishing] = useState(false)
  const [note, setNote] = useState('')
  const [parts, setParts] = useState('')
  const start = useMutation({ mutationFn: () => startOrder(item.id).then(onChanged) })
  const done = useMutation({
    mutationFn: () => completeOrder(item.id, note, parts).then(onChanged),
    onSuccess: () => { setFinishing(false); setNote(''); setParts('') },
  })
  const busy = start.isPending || done.isPending
  const failed = start.error ?? done.error
  return (
    <Card style={[styles.row, item.overdue && styles.late]} testID="work-order">
      <View style={styles.top}>
        <Text style={styles.title} numberOfLines={2}>{item.number} · {item.title}</Text>
        <Text style={styles.state}>{ORDER_STATE_LABEL[item.state]}</Text>
      </View>
      <Text style={styles.meta}>{orderWhere(item)}</Text>
      <Text style={[styles.meta, item.overdue && styles.lateText]}>{orderDue(item)}</Text>
      {item.description && <Text style={styles.line}>{item.description}</Text>}
      {!!failed && <Text style={styles.error}>{apiErrorText(failed)}</Text>}
      {!finishing && (
        <View style={styles.actions}>
          {item.may.start && (
            <Pressable style={[styles.button, busy && styles.off]} disabled={busy} onPress={() => start.mutate()}
                       accessibilityRole="button">
              <Text style={styles.buttonText}>I have started</Text>
            </Pressable>)}
          {item.may.complete && (
            <Pressable style={[styles.button, styles.primary, busy && styles.off]} disabled={busy}
                       onPress={() => setFinishing(true)} accessibilityRole="button">
              <Text style={[styles.buttonText, styles.primaryText]}>It is done</Text>
            </Pressable>)}
        </View>)}
      {finishing && (
        <View style={styles.form}>
          <TextInput style={styles.input} value={note} onChangeText={setNote} multiline placeholder="What was done"
                     placeholderTextColor={colors.textDisabled} accessibilityLabel="What was done" maxLength={5000} />
          <TextInput style={styles.input} value={parts} onChangeText={setParts} placeholder="Parts used, if any"
                     placeholderTextColor={colors.textDisabled} accessibilityLabel="Parts used, if any" maxLength={2000} />
          <View style={styles.actions}>
            <Pressable style={styles.button} disabled={busy} onPress={() => { setFinishing(false); done.reset() }}
                       accessibilityRole="button">
              <Text style={styles.buttonText}>Not yet</Text>
            </Pressable>
            <Pressable style={[styles.button, styles.primary, (!note.trim() || busy) && styles.off]}
                       disabled={!note.trim() || busy} onPress={() => done.mutate()} accessibilityRole="button">
              <Text style={[styles.buttonText, styles.primaryText]}>Record it as done</Text>
            </Pressable>
          </View>
        </View>)}
    </Card>
  )
}

export function MyWorkOrdersScreen() {
  const qc = useQueryClient()
  const [refreshing, setRefreshing] = useState(false)
  const { data = [], isLoading, error } = useQuery({ queryKey: ['my-work-orders'], queryFn: listMyOrders })
  const again = useCallback(() => qc.invalidateQueries({ queryKey: ['my-work-orders'] }), [qc])
  const onRefresh = useCallback(async () => { setRefreshing(true); await again(); setRefreshing(false) }, [again])
  if (isLoading) return <View style={styles.center}><ActivityIndicator color={colors.primary} /></View>
  return (
    <View style={styles.root}>
      <FlatList
        data={inOrder(data)}
        keyExtractor={(o) => o.id}
        contentContainerStyle={styles.list}
        keyboardShouldPersistTaps="handled"
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.primary} />}
        renderItem={({ item }) => <Order item={item} onChanged={again} />}
        ListEmptyComponent={
          <View style={styles.center}>
            <Ionicons name={error ? 'alert-circle-outline' : 'build-outline'} size={48} color={colors.textDisabled} />
            <Text style={styles.empty}>{error ? apiErrorText(error) : 'No work is given to you.'}</Text>
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
  late: { borderColor: colors.error, borderWidth: 1 },
  lateText: { color: colors.error },
  top: { flexDirection: 'row', justifyContent: 'space-between', gap: spacing.sm },
  title: { flex: 1, color: colors.text, fontSize: fontSize.md, fontWeight: '600' },
  state: { color: colors.textSecondary, fontSize: fontSize.xs },
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
