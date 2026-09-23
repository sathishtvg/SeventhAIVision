/**
 * Virtual patrol, on the first page.
 *
 * The feature ran on the server and in the web app and was invisible on the
 * phone, so an officer holding vpatrol:execute had no idea it was theirs to do.
 * This card is the promotion: it appears for anyone who can execute a patrol,
 * says how many are waiting, and opens the list.
 *
 * It is shown even when nothing is due — with that stated plainly — because a
 * card that only exists on the days work is assigned teaches nobody that the
 * feature is there. It stays one line in that case.
 */
import { Pressable, StyleSheet, Text, View } from 'react-native'
import { Ionicons } from '@expo/vector-icons'
import { useNavigation } from '@react-navigation/native'
import { useQuery } from '@tanstack/react-query'

import { getMyPatrols, isRunning } from '@/api/virtualPatrol'
import { canSee } from '@/lib/access'
import { useAuthStore } from '@/store/auth'
import { colors, fontSize, radius, spacing } from '@/theme'

export function VirtualPatrolCard() {
  const nav = useNavigation<any>()
  const permissions = useAuthStore((s) => s.permissions)
  const roleId = useAuthStore((s) => s.user?.roleId)

  // vpatrol:execute is the permission the run endpoints demand, so this is the
  // objective test of whether the feature belongs on this person's phone.
  const allowed = canSee({ permission: 'vpatrol:execute' }, permissions, roleId)

  const { data: patrols = [] } = useQuery({
    queryKey: ['my-virtual-patrols'],
    queryFn: getMyPatrols,
    enabled: allowed,
  })

  if (!allowed) return null

  // Everything my-patrols returns is outstanding — the endpoint filters to
  // SCHEDULED/STARTED/IN_PROGRESS — so there is nothing to filter again here.
  const due = patrols
  const running = due.find((p) => isRunning(p.status))

  return (
    <Pressable
      style={styles.card}
      onPress={() => nav.navigate('Patrol', { screen: 'VirtualPatrols' })}
      accessibilityRole="button"
      accessibilityLabel="Open virtual patrols"
    >
      <View style={styles.icon}>
        <Ionicons name="desktop-outline" size={20} color={colors.primary} />
      </View>
      <View style={styles.body}>
        <Text style={styles.title}>Virtual Patrol</Text>
        <Text style={styles.meta} numberOfLines={1}>
          {running
            ? `In progress — ${running.completed_camera_count} of ${running.camera_count} cameras`
            : due.length > 0
              ? `${due.length} patrol${due.length === 1 ? '' : 's'} waiting`
              : 'Nothing assigned right now'}
        </Text>
      </View>
      {due.length > 0 && (
        <View style={styles.count}>
          <Text style={styles.countText}>{due.length}</Text>
        </View>
      )}
      <Ionicons name="chevron-forward" size={18} color={colors.textSecondary} />
    </Pressable>
  )
}

const styles = StyleSheet.create({
  card: {
    flexDirection: 'row', alignItems: 'center', gap: spacing.sm,
    backgroundColor: colors.surface, borderRadius: radius.md, borderWidth: 1,
    borderColor: colors.cardBorder, padding: spacing.md, marginBottom: spacing.md,
  },
  icon: {
    width: 38, height: 38, borderRadius: radius.sm, alignItems: 'center',
    justifyContent: 'center', backgroundColor: 'rgba(99,102,241,0.15)',
  },
  body: { flex: 1 },
  title: { fontSize: fontSize.md, fontWeight: '700', color: colors.text },
  meta: { fontSize: fontSize.sm, color: colors.textSecondary, marginTop: 2 },
  count: {
    minWidth: 24, height: 24, borderRadius: 12, paddingHorizontal: 6,
    alignItems: 'center', justifyContent: 'center', backgroundColor: colors.warning,
  },
  countText: { fontSize: fontSize.xs, fontWeight: '700', color: '#1a1205' },
})
