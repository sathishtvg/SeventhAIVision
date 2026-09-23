/**
 * The patrols assigned to me.
 *
 * Lists what the server says is mine (my-patrols is scoped to the token, not to
 * a parameter this screen passes), newest first, with the ones still to do
 * clearly ahead of the ones already done.
 */
import { useCallback, useState } from 'react'
import {
  ActivityIndicator, FlatList, Pressable, RefreshControl, StyleSheet, Text, View,
} from 'react-native'
import { Ionicons } from '@expo/vector-icons'
import { useNavigation } from '@react-navigation/native'
import type { NativeStackNavigationProp } from '@react-navigation/native-stack'
import { useQuery } from '@tanstack/react-query'

import { getMyPatrols, type MyPatrol } from '@/api/virtualPatrol'
import type { PatrolStackParamList } from '@/navigation'
import { colors, fontSize, radius, spacing } from '@/theme'

type NavProp = NativeStackNavigationProp<PatrolStackParamList>

const STATUS_COLOR: Record<string, string> = {
  SCHEDULED: colors.info,
  STARTED: colors.warning,
  IN_PROGRESS: colors.warning,
  COMPLETED: colors.success,
  PARTIALLY_COMPLETED: colors.warning,
  MISSED: colors.error,
  CANCELLED: colors.textDisabled,
  FAILED: colors.error,
}

const when = (iso: string) =>
  new Date(iso).toLocaleString([], {
    day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
  })

function PatrolRow({ patrol, onPress }: { patrol: MyPatrol; onPress: () => void }) {
  const done = patrol.status === 'COMPLETED'
  return (
    <Pressable style={styles.row} onPress={onPress} accessibilityRole="button">
      <View style={styles.rowHeader}>
        <Text style={styles.scheduleName} numberOfLines={1}>{patrol.schedule_name}</Text>
        <View style={[styles.badge, { backgroundColor: STATUS_COLOR[patrol.status] ?? colors.info }]}>
          <Text style={styles.badgeText}>{patrol.status.replace(/_/g, ' ').toLowerCase()}</Text>
        </View>
      </View>
      <Text style={styles.meta}>
        {patrol.patrol_number} · {when(patrol.scheduled_for)}
      </Text>
      <View style={styles.progressRow}>
        <Ionicons name="videocam-outline" size={14} color={colors.textSecondary} />
        <Text style={styles.meta}>
          {patrol.completed_camera_count} of {patrol.camera_count} cameras
        </Text>
        {!done && <Ionicons name="chevron-forward" size={18} color={colors.textSecondary} style={styles.chevron} />}
      </View>
    </Pressable>
  )
}

export function VirtualPatrolScreen() {
  const nav = useNavigation<NavProp>()
  const [refreshing, setRefreshing] = useState(false)

  const { data: patrols = [], isLoading, refetch } = useQuery({
    queryKey: ['my-virtual-patrols'],
    queryFn: getMyPatrols,
  })

  const onRefresh = useCallback(async () => {
    setRefreshing(true)
    await refetch()
    setRefreshing(false)
  }, [refetch])

  if (isLoading) {
    return (
      <View style={styles.centre}>
        <ActivityIndicator color={colors.primary} />
      </View>
    )
  }

  return (
    <FlatList
      style={styles.root}
      contentContainerStyle={styles.content}
      data={patrols}
      keyExtractor={(p) => p.id}
      refreshControl={
        <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.primary} />
      }
      ListEmptyComponent={
        <View style={styles.centre}>
          <Ionicons name="desktop-outline" size={44} color={colors.textDisabled} />
          <Text style={styles.empty}>No virtual patrols assigned to you.</Text>
        </View>
      }
      renderItem={({ item }) => (
        <PatrolRow
          patrol={item}
          onPress={() => nav.navigate('VirtualPatrolRun', { sessionId: item.id })}
        />
      )}
    />
  )
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.background },
  content: { padding: spacing.md },
  centre: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: spacing.xl, gap: spacing.sm },
  empty: { color: colors.textSecondary, fontSize: fontSize.sm, textAlign: 'center' },
  row: {
    backgroundColor: colors.surface, borderRadius: radius.md, borderWidth: 1,
    borderColor: colors.cardBorder, padding: spacing.md, marginBottom: spacing.sm,
  },
  rowHeader: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: spacing.sm },
  scheduleName: { flex: 1, fontSize: fontSize.md, fontWeight: '700', color: colors.text },
  badge: { borderRadius: radius.sm, paddingHorizontal: spacing.xs, paddingVertical: 2 },
  badgeText: { fontSize: 10, fontWeight: '700', color: '#04120a', textTransform: 'uppercase' },
  meta: { fontSize: fontSize.sm, color: colors.textSecondary, marginTop: 2 },
  progressRow: { flexDirection: 'row', alignItems: 'center', gap: 4, marginTop: spacing.xs },
  chevron: { marginLeft: 'auto' },
})
