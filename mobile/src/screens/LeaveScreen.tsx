/**
 * Leave: apply, and see where the application got to.
 *
 * WHY IT IS A SCREEN. All of this already worked — balances, the request form,
 * cancellation, a status on every application — behind the second tab of "My
 * Record", a menu row described as "Your violations and leave requests". A
 * guard wondering whether their leave was approved had to open a screen about
 * their violations to find out. It was reported as missing, which is what
 * unfindable means in practice.
 *
 * THE BODY IS THE SAME COMPONENT the My Record tab renders, imported rather
 * than copied: two implementations of "where did my leave application get to"
 * would drift, and the drifting one shows a guard the wrong answer.
 *
 * WHAT IS NEW HERE is the summary at the top. The list already carried a status
 * badge per row, but the question a guard actually has — "is my leave approved
 * yet?" — was answerable only by reading every row. One line answers it.
 */
import { StyleSheet, Text, View } from 'react-native'
import { useQuery } from '@tanstack/react-query'

import { getMyLeaveRequests } from '@/api/leave'
import { LeaveTab } from '@/screens/MyRecordScreen'
import { colors, fontSize, radius, spacing } from '@/theme'

/**
 * How many applications sit at each status.
 *
 * Pure and exported because the summary is a claim about the guard's own leave:
 * saying nothing is pending when something is would stop them chasing it.
 * Takes a plain string, not the app's status union: the union is this client's
 * idea of the statuses, and a server that grows one must not make a guard's
 * pending application disappear from the count.
 */
export function leaveSummary(requests: { status: string }[]): Record<string, number> {
  const counts: Record<string, number> = {}
  for (const r of requests) {
    const key = (r.status ?? 'unknown').toLowerCase()
    counts[key] = (counts[key] ?? 0) + 1
  }
  return counts
}

/** Order the ones a guard cares about first; anything unexpected after. */
const PREFERRED = ['pending', 'approved', 'rejected', 'cancelled']

export function orderedStatuses(counts: Record<string, number>): string[] {
  const known = PREFERRED.filter((s) => counts[s])
  const rest = Object.keys(counts).filter((s) => !PREFERRED.includes(s)).sort()
  return [...known, ...rest]
}

const COLOR: Record<string, string> = {
  pending: colors.warning,
  approved: colors.success,
  rejected: colors.error,
  cancelled: colors.textDisabled,
}

export function LeaveScreen() {
  const { data: requests = [] } = useQuery({
    queryKey: ['my-leave-requests'],
    queryFn: getMyLeaveRequests,
  })

  const counts = leaveSummary(requests)
  const statuses = orderedStatuses(counts)

  return (
    <View style={styles.root}>
      {statuses.length > 0 && (
        <View style={styles.summary}>
          {statuses.map((s) => (
            <View key={s} style={styles.chip}>
              <Text style={[styles.chipCount, { color: COLOR[s] ?? colors.info }]}>{counts[s]}</Text>
              <Text style={styles.chipLabel}>{s}</Text>
            </View>
          ))}
        </View>
      )}
      <LeaveTab />
    </View>
  )
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.background },
  summary: {
    flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm,
    paddingHorizontal: spacing.md, paddingTop: spacing.md,
  },
  chip: {
    flexDirection: 'row', alignItems: 'baseline', gap: 6,
    backgroundColor: colors.surface, borderRadius: radius.sm, borderWidth: 1,
    borderColor: colors.cardBorder, paddingHorizontal: spacing.sm, paddingVertical: spacing.xs,
  },
  chipCount: { fontSize: fontSize.md, fontWeight: '700' },
  chipLabel: { fontSize: fontSize.xs, color: colors.textSecondary, textTransform: 'capitalize' },
})
