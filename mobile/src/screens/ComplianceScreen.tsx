import React, { useState, useCallback } from 'react'
import {
  ActivityIndicator, FlatList, RefreshControl,
  StyleSheet, Text, View, Pressable,
} from 'react-native'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Ionicons } from '@expo/vector-icons'
import { getComplianceSummary, getPolicies, getComplianceChecks, type CompliancePolicy, type ComplianceCheck } from '@/api/compliance'
import { Card } from '@/components/Card'
import { colors, fontSize, radius, spacing } from '@/theme'

const CHECK_COLOR: Record<string, string> = {
  passed:  colors.success,
  failed:  colors.error,
  partial: colors.warning,
  pending: colors.info,
  waived:  colors.textDisabled,
}

function StatusPill({ status }: { status: string }) {
  const color = CHECK_COLOR[status] ?? colors.textDisabled
  return (
    <View style={[styles.pill, { backgroundColor: color + '28', borderColor: color }]}>
      <Text style={[styles.pillText, { color }]}>{status}</Text>
    </View>
  )
}

function PolicyRow({ item }: { item: CompliancePolicy }) {
  const isOverdue = item.next_due_at != null && new Date(item.next_due_at) < new Date()
  return (
    <Card style={styles.row}>
      <View style={styles.rowTop}>
        <View style={[styles.iconWrap, { backgroundColor: (isOverdue ? colors.error : colors.info) + '20' }]}>
          <Ionicons name="clipboard-outline" size={18} color={isOverdue ? colors.error : colors.info} />
        </View>
        <View style={styles.rowInfo}>
          <Text style={styles.name}>{item.title}</Text>
          <Text style={styles.sub}>{item.category} · {item.frequency}</Text>
        </View>
        {isOverdue && (
          <View style={[styles.pill, { backgroundColor: colors.error + '28', borderColor: colors.error }]}>
            <Text style={[styles.pillText, { color: colors.error }]}>OVERDUE</Text>
          </View>
        )}
      </View>
      {item.next_due_at && (
        <View style={styles.metaRow}>
          <Ionicons name="calendar-outline" size={12} color={colors.textSecondary} />
          <Text style={styles.metaText}>Due {new Date(item.next_due_at).toLocaleDateString()}</Text>
        </View>
      )}
      {item.last_completed_at && (
        <View style={styles.metaRow}>
          <Ionicons name="checkmark-circle-outline" size={12} color={colors.success} />
          <Text style={styles.metaText}>Last done {new Date(item.last_completed_at).toLocaleDateString()}</Text>
        </View>
      )}
    </Card>
  )
}

function CheckRow({ item }: { item: ComplianceCheck }) {
  return (
    <Card style={styles.row}>
      <View style={styles.rowTop}>
        <View style={styles.rowInfo}>
          <Text style={styles.name}>{item.policy_title ?? 'Compliance Check'}</Text>
          {item.checker_name && <Text style={styles.sub}>By: {item.checker_name}</Text>}
          {item.notes && <Text style={styles.sub}>{item.notes}</Text>}
        </View>
        <StatusPill status={item.status} />
      </View>
      {(item.score != null && item.max_score != null) && (
        <View style={styles.metaRow}>
          <Ionicons name="stats-chart-outline" size={12} color={colors.textSecondary} />
          <Text style={styles.metaText}>Score {item.score}/{item.max_score}</Text>
        </View>
      )}
      {item.completed_at && (
        <View style={styles.metaRow}>
          <Ionicons name="time-outline" size={12} color={colors.textSecondary} />
          <Text style={styles.metaText}>{new Date(item.completed_at).toLocaleString()}</Text>
        </View>
      )}
    </Card>
  )
}

export function ComplianceScreen() {
  const qc = useQueryClient()
  const [tab, setTab] = useState<'summary' | 'policies' | 'checks'>('summary')
  const [refreshing, setRefreshing] = useState(false)

  const { data: summary, isLoading: loadingSummary } = useQuery({
    queryKey: ['compliance-summary'],
    queryFn: getComplianceSummary,
  })

  const { data: policies = [], isLoading: loadingPolicies } = useQuery({
    queryKey: ['compliance-policies'],
    queryFn: () => getPolicies(),
    enabled: tab === 'policies',
  })

  const { data: checks = [], isLoading: loadingChecks } = useQuery({
    queryKey: ['compliance-checks'],
    queryFn: () => getComplianceChecks({ limit: 50 }),
    enabled: tab === 'checks',
  })

  const onRefresh = useCallback(async () => {
    setRefreshing(true)
    await qc.invalidateQueries({ queryKey: ['compliance'] })
    setRefreshing(false)
  }, [qc])

  const isLoading = tab === 'summary' ? loadingSummary : tab === 'policies' ? loadingPolicies : loadingChecks

  return (
    <View style={styles.root}>
      <View style={styles.tabRow}>
        {(['summary', 'policies', 'checks'] as const).map((t) => (
          <Pressable key={t} style={[styles.tabBtn, tab === t && styles.tabActive]} onPress={() => setTab(t)}>
            <Text style={[styles.tabText, tab === t && styles.tabTextActive]}>
              {t.charAt(0).toUpperCase() + t.slice(1)}
            </Text>
          </Pressable>
        ))}
      </View>

      {isLoading ? (
        <View style={styles.center}><ActivityIndicator color={colors.primary} /></View>
      ) : tab === 'summary' && summary ? (
        <FlatList
          data={summary.recent_checks}
          keyExtractor={(item) => item.id}
          ListHeaderComponent={() => (
            <View style={styles.kpiGrid}>
              {[
                { label: 'Active Policies', value: summary.active_policies, color: colors.text },
                { label: 'Overdue', value: summary.overdue, color: summary.overdue > 0 ? colors.error : colors.success },
                { label: 'Due This Week', value: summary.due_this_week, color: colors.warning },
                { label: 'Pass Rate', value: `${summary.passed_rate_pct}%`, color: colors.success },
              ].map((k) => (
                <Card key={k.label} style={styles.kpiCard}>
                  <Text style={[styles.kpiValue, { color: k.color }]}>{k.value}</Text>
                  <Text style={styles.kpiLabel}>{k.label}</Text>
                </Card>
              ))}
              <Text style={[styles.name, { paddingHorizontal: spacing.md, paddingTop: spacing.sm }]}>Recent Checks</Text>
            </View>
          )}
          renderItem={({ item }) => <CheckRow item={item} />}
          ItemSeparatorComponent={() => <View style={{ height: spacing.xs }} />}
          contentContainerStyle={styles.list}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.primary} />}
        />
      ) : tab === 'policies' ? (
        policies.length === 0 ? (
          <View style={styles.center}>
            <Ionicons name="clipboard-outline" size={48} color={colors.textDisabled} />
            <Text style={styles.emptyText}>No policies</Text>
          </View>
        ) : (
          <FlatList
            data={policies}
            keyExtractor={(item) => item.id}
            renderItem={({ item }) => <PolicyRow item={item} />}
            ItemSeparatorComponent={() => <View style={{ height: spacing.xs }} />}
            contentContainerStyle={styles.list}
            refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.primary} />}
          />
        )
      ) : (
        checks.length === 0 ? (
          <View style={styles.center}>
            <Ionicons name="checkmark-done-outline" size={48} color={colors.textDisabled} />
            <Text style={styles.emptyText}>No checks recorded</Text>
          </View>
        ) : (
          <FlatList
            data={checks}
            keyExtractor={(item) => item.id}
            renderItem={({ item }) => <CheckRow item={item} />}
            ItemSeparatorComponent={() => <View style={{ height: spacing.xs }} />}
            contentContainerStyle={styles.list}
            refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.primary} />}
          />
        )
      )}
    </View>
  )
}

const styles = StyleSheet.create({
  root:          { flex: 1, backgroundColor: colors.background },
  center:        { flex: 1, alignItems: 'center', justifyContent: 'center', gap: spacing.sm },
  emptyText:     { color: colors.textSecondary, fontSize: fontSize.md },
  tabRow:        { flexDirection: 'row', padding: spacing.md, gap: spacing.sm },
  tabBtn:        { flex: 1, paddingVertical: 8, borderRadius: radius.sm, borderWidth: 1, borderColor: colors.cardBorder, alignItems: 'center' },
  tabActive:     { backgroundColor: colors.primary, borderColor: colors.primary },
  tabText:       { fontSize: fontSize.sm, color: colors.textSecondary, fontWeight: '600' },
  tabTextActive: { color: '#fff' },
  kpiGrid:       { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.xs, paddingHorizontal: spacing.md, paddingBottom: spacing.sm },
  kpiCard:       { width: '47%', alignItems: 'center', paddingVertical: spacing.sm },
  kpiValue:      { fontSize: fontSize.xl, fontWeight: '800' },
  kpiLabel:      { fontSize: 10, color: colors.textSecondary, marginTop: 2, textAlign: 'center' },
  list:          { padding: spacing.md, paddingTop: 0, paddingBottom: spacing.xl },
  row:           { gap: 6 },
  rowTop:        { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  iconWrap:      { width: 36, height: 36, borderRadius: 18, alignItems: 'center', justifyContent: 'center' },
  rowInfo:       { flex: 1 },
  name:          { fontSize: fontSize.md, fontWeight: '700', color: colors.text },
  sub:           { fontSize: fontSize.xs, color: colors.textSecondary, marginTop: 1 },
  metaRow:       { flexDirection: 'row', alignItems: 'center', gap: 4 },
  metaText:      { fontSize: fontSize.xs, color: colors.textSecondary },
  pill:          { paddingHorizontal: spacing.sm, paddingVertical: 3, borderRadius: radius.full, borderWidth: 1 },
  pillText:      { fontSize: 10, fontWeight: '700', textTransform: 'uppercase', letterSpacing: 0.5 },
})
