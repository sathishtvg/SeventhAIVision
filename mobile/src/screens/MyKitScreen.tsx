/**
 * My Kit — what this officer is signed out for.
 *
 * Read-only on purpose. A guard cannot issue themselves a radio, and the
 * screen exists so the answer to "do I still have the body camera" does not
 * require finding a supervisor. It is also the screen somebody opens on their
 * last day to see what they have to hand back.
 */
import React from 'react'
import {
  ActivityIndicator, FlatList, Pressable, StyleSheet, Text, View,
} from 'react-native'
import { useQuery } from '@tanstack/react-query'
import { Ionicons } from '@expo/vector-icons'

import { getHeldByUser, type HeldByUser } from '@/api/guardhouse'
import { Card } from '@/components/Card'
import { useAuthStore } from '@/store/auth'
import { colors, fontSize, radius, spacing } from '@/theme'

function fmtDate(ts: string) {
  return new Date(ts).toLocaleDateString(undefined, {
    day: '2-digit', month: 'short', year: 'numeric',
  })
}

function tidy(value: string) {
  return value.replace(/_/g, ' ')
}

type Row =
  | { kind: 'heading'; key: string; label: string }
  | { kind: 'equipment'; key: string; item: HeldByUser['equipment'][number] }
  | { kind: 'uniform'; key: string; item: HeldByUser['uniform'][number] }
  | { kind: 'empty'; key: string; label: string }

export function MyKitScreen() {
  const userId = useAuthStore((s) => s.user?.id)

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['my-kit', userId],
    queryFn: () => getHeldByUser(userId!),
    enabled: Boolean(userId),
  })
  // useQuery is `any` in this app's typings; see KeyRegisterScreen.
  const held: HeldByUser | null = data ?? null

  if (isLoading || !userId) {
    return <View style={styles.center}><ActivityIndicator color={colors.primary} /></View>
  }
  if (isError || !held) {
    return (
      <View style={styles.center}>
        <Ionicons name="cloud-offline-outline" size={40} color={colors.textSecondary} />
        <Text style={styles.errorText}>Couldn’t load your kit</Text>
        <Pressable style={styles.retryBtn} onPress={() => refetch()}>
          <Text style={styles.retryText}>Retry</Text>
        </Pressable>
      </View>
    )
  }

  const rows: Row[] = [
    { kind: 'heading', key: 'h-equipment', label: 'EQUIPMENT' },
    ...(held.equipment.length
      ? held.equipment.map((item) => ({ kind: 'equipment' as const, key: item.id, item }))
      : [{ kind: 'empty' as const, key: 'e-empty', label: 'Nothing signed out to you.' }]),
    { kind: 'heading', key: 'h-uniform', label: 'UNIFORM' },
    ...(held.uniform.length
      ? held.uniform.map((item) => ({ kind: 'uniform' as const, key: item.id, item }))
      : [{ kind: 'empty' as const, key: 'u-empty', label: 'Nothing outstanding.' }]),
  ]

  return (
    <View style={styles.container}>
      {held.summary.equipment_overdue > 0 && (
        <View style={styles.banner}>
          <Ionicons name="alert-circle" size={18} color="#fff" />
          <Text style={styles.bannerText}>
            {held.summary.equipment_overdue}{' '}
            {held.summary.equipment_overdue === 1 ? 'item is' : 'items are'} past their return date
          </Text>
        </View>
      )}

      <View style={styles.summaryRow}>
        <View style={styles.summaryTile}>
          <Text style={styles.summaryValue}>{held.summary.equipment_out}</Text>
          <Text style={styles.summaryLabel}>items</Text>
        </View>
        <View style={styles.summaryTile}>
          <Text style={styles.summaryValue}>{held.summary.uniform_pieces_out}</Text>
          <Text style={styles.summaryLabel}>uniform pieces</Text>
        </View>
        {Number(held.summary.deposit_held) > 0 && (
          <View style={styles.summaryTile}>
            <Text style={styles.summaryValue}>
              ${Number(held.summary.deposit_held).toFixed(0)}
            </Text>
            <Text style={styles.summaryLabel}>deposit held</Text>
          </View>
        )}
      </View>

      <FlatList
        data={rows}
        keyExtractor={(r) => r.key}
        contentContainerStyle={styles.list}
        renderItem={({ item: row }) => {
          if (row.kind === 'heading') {
            return <Text style={styles.sectionTitle}>{row.label}</Text>
          }
          if (row.kind === 'empty') {
            return <Card><Text style={styles.muted}>{row.label}</Text></Card>
          }
          if (row.kind === 'equipment') {
            const e = row.item
            return (
              <Card>
                <View style={styles.rowLine}>
                  <View style={styles.flex}>
                    <Text style={styles.title}>{e.asset_code} — {e.name}</Text>
                    <Text style={styles.meta}>
                      {tidy(e.category)} · since {fmtDate(e.issued_at)}
                      {e.serial_number ? ` · ${e.serial_number}` : ''}
                    </Text>
                    {!!e.expected_return_at && (
                      <Text style={styles.meta}>Due back {fmtDate(e.expected_return_at)}</Text>
                    )}
                  </View>
                  {e.is_overdue && (
                    <View style={[styles.badge, { backgroundColor: colors.error }]}>
                      <Text style={styles.badgeText}>Overdue</Text>
                    </View>
                  )}
                </View>
              </Card>
            )
          }
          const u = row.item
          return (
            <Card>
              <Text style={styles.title}>
                {u.outstanding_quantity} × {tidy(u.item_type)}
                {u.size ? ` (${u.size})` : ''}
              </Text>
              <Text style={styles.meta}>
                {u.quantity} issued {fmtDate(u.issued_at)}
                {u.returned_quantity > 0 ? ` · ${u.returned_quantity} returned` : ''}
                {u.deposit_amount ? ` · $${Number(u.deposit_amount).toFixed(2)} deposit` : ''}
              </Text>
            </Card>
          )
        }}
      />
    </View>
  )
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: spacing.sm },
  flex: { flex: 1 },
  banner: {
    flexDirection: 'row', alignItems: 'center', gap: spacing.xs,
    backgroundColor: colors.error, paddingVertical: spacing.xs, paddingHorizontal: spacing.md,
  },
  bannerText: { color: '#fff', fontWeight: '700', fontSize: fontSize.sm },
  summaryRow: { flexDirection: 'row', gap: spacing.sm, padding: spacing.md, paddingBottom: 0 },
  summaryTile: {
    flex: 1, backgroundColor: colors.surface, borderRadius: radius.md,
    paddingVertical: spacing.sm, alignItems: 'center',
  },
  summaryValue: { color: colors.text, fontSize: fontSize.lg, fontWeight: '800' },
  summaryLabel: { color: colors.textSecondary, fontSize: fontSize.xs },
  list: { padding: spacing.md, gap: spacing.sm },
  sectionTitle: {
    color: colors.text, fontSize: fontSize.sm, fontWeight: '700',
    marginTop: spacing.sm,
  },
  rowLine: { flexDirection: 'row', alignItems: 'flex-start', justifyContent: 'space-between', gap: spacing.sm },
  title: { color: colors.text, fontSize: fontSize.md, fontWeight: '600' },
  meta: { color: colors.textSecondary, fontSize: fontSize.xs, marginTop: 2 },
  muted: { color: colors.textSecondary, fontSize: fontSize.sm },
  badge: { paddingHorizontal: spacing.sm, paddingVertical: 2, borderRadius: radius.sm },
  badgeText: { color: '#fff', fontSize: fontSize.xs, fontWeight: '700' },
  errorText: { color: colors.text, fontSize: fontSize.md },
  retryBtn: {
    borderColor: colors.primary, borderWidth: 1, borderRadius: radius.md,
    paddingHorizontal: spacing.lg, paddingVertical: spacing.xs,
  },
  retryText: { color: colors.primary, fontWeight: '600' },
})
