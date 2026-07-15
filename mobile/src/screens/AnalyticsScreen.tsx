import React, { useCallback, useState } from 'react'
import {
  ActivityIndicator, RefreshControl, ScrollView,
  StyleSheet, Text, View,
} from 'react-native'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Ionicons } from '@expo/vector-icons'
import { getSummary } from '@/api/dashboard'
import { getAlertsBySeverity, getAlertsByModule, getTopCameras, type TopCamera } from '@/api/analytics'
import { Card } from '@/components/Card'
import { colors, fontSize, spacing } from '@/theme'

// Inline mini bar chart (no external dep)
function BarChart({
  data,
  colorFn,
  labelKey,
  valueKey,
}: {
  data: Record<string, any>[]
  colorFn: (item: any) => string
  labelKey: string
  valueKey: string
}) {
  const max = Math.max(...data.map((d) => d[valueKey] as number), 1)
  return (
    <View style={bc.wrap}>
      {data.map((item, i) => {
        const pct = ((item[valueKey] as number) / max) * 100
        const color = colorFn(item)
        return (
          <View key={i} style={bc.row}>
            <Text style={bc.label} numberOfLines={1}>{String(item[labelKey])}</Text>
            <View style={bc.track}>
              <View style={[bc.bar, { width: `${pct}%` as any, backgroundColor: color }]} />
            </View>
            <Text style={[bc.val, { color }]}>{item[valueKey]}</Text>
          </View>
        )
      })}
    </View>
  )
}
const bc = StyleSheet.create({
  wrap:  { gap: 6 },
  row:   { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  label: { width: 72, fontSize: fontSize.xs, color: colors.textSecondary },
  track: { flex: 1, height: 8, borderRadius: 4, backgroundColor: colors.glassBg, overflow: 'hidden' },
  bar:   { height: '100%', borderRadius: 4 },
  val:   { width: 32, fontSize: fontSize.xs, fontWeight: '700', textAlign: 'right' },
})

const SEVERITY_COLOR: Record<string, string> = {
  info:     colors.info,
  low:      colors.success,
  medium:   colors.warning,
  high:     '#FF6B35',
  critical: colors.error,
}

const MODULE_COLOR: Record<string, string> = {
  lpr: colors.secondary, face: colors.primary, intrusion: colors.error,
  ppe: colors.warning, crowd: colors.info, fire_smoke: '#FF6B35',
  weapon: colors.error, behavior: colors.textSecondary,
}

function KpiCard({ label, value, icon, color }: { label: string; value: number | string; icon: React.ComponentProps<typeof Ionicons>['name']; color: string }) {
  return (
    <Card style={kpi.card}>
      <View style={[kpi.iconBg, { backgroundColor: color + '28' }]}>
        <Ionicons name={icon} size={20} color={color} />
      </View>
      <Text style={kpi.val}>{value}</Text>
      <Text style={kpi.label}>{label}</Text>
    </Card>
  )
}
const kpi = StyleSheet.create({
  card:   { flex: 1, minWidth: '45%', alignItems: 'center', paddingVertical: spacing.md },
  iconBg: { width: 44, height: 44, borderRadius: 13, alignItems: 'center', justifyContent: 'center', marginBottom: spacing.sm },
  val:    { fontSize: fontSize.xxl, fontWeight: '800', color: colors.text },
  label:  { fontSize: fontSize.xs, color: colors.textSecondary, marginTop: 2, textAlign: 'center' },
})

export function AnalyticsScreen() {
  const qc = useQueryClient()
  const [refreshing, setRefreshing] = useState(false)

  const { data: summary, isLoading: sumLoading } = useQuery({
    queryKey: ['analytics-summary'],
    queryFn: getSummary,
    refetchInterval: 60_000,
  })

  const { data: bySeverity = [] } = useQuery({
    queryKey: ['analytics-by-severity'],
    queryFn: () => getAlertsBySeverity({ days: 7 }),
  })

  const { data: byModule = [] } = useQuery({
    queryKey: ['analytics-by-module'],
    queryFn: () => getAlertsByModule({ days: 7 }),
  })

  const { data: topCameras = [] } = useQuery({
    queryKey: ['analytics-top-cameras'],
    queryFn: () => getTopCameras({ limit: 5, days: 7 }),
  })

  const onRefresh = useCallback(async () => {
    setRefreshing(true)
    await Promise.all([
      qc.invalidateQueries({ queryKey: ['analytics-summary'] }),
      qc.invalidateQueries({ queryKey: ['analytics-by-severity'] }),
      qc.invalidateQueries({ queryKey: ['analytics-by-module'] }),
      qc.invalidateQueries({ queryKey: ['analytics-top-cameras'] }),
    ])
    setRefreshing(false)
  }, [qc])

  if (sumLoading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={colors.primary} size="large" />
      </View>
    )
  }

  return (
    <ScrollView
      style={styles.root}
      contentContainerStyle={styles.content}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.primary} />}
    >
      {/* KPI row */}
      <Text style={styles.sectionTitle}>Overview</Text>
      <View style={styles.kpiGrid}>
        <KpiCard icon="alert-circle"     label="Open Alerts"    value={summary?.open_alerts ?? 0}     color={colors.error} />
        <KpiCard icon="warning"          label="Open Incidents" value={summary?.open_incidents ?? 0}  color={colors.warning} />
        <KpiCard icon="videocam"         label="Cameras Online" value={summary?.active_cameras ?? 0}  color={colors.success} />
        <KpiCard icon="scan"             label="Today"          value={summary?.detections_today ?? 0} color={colors.primary} />
        <KpiCard icon="alert-circle-outline" label="Alerts 7d" value={summary?.alerts_7d ?? 0}       color={colors.secondary} />
        <KpiCard icon="scan-outline"     label="Detections 7d"  value={summary?.detections_7d ?? 0}   color={colors.info} />
      </View>

      {/* By severity */}
      {bySeverity.length > 0 && (
        <>
          <Text style={styles.sectionTitle}>Alerts by Severity (7d)</Text>
          <Card>
            <BarChart
              data={bySeverity}
              labelKey="severity"
              valueKey="count"
              colorFn={(d) => SEVERITY_COLOR[d.severity] ?? colors.textDisabled}
            />
          </Card>
        </>
      )}

      {/* By module */}
      {byModule.length > 0 && (
        <>
          <Text style={styles.sectionTitle}>Alerts by Module (7d)</Text>
          <Card>
            <BarChart
              data={byModule}
              labelKey="module_type"
              valueKey="count"
              colorFn={(d) => MODULE_COLOR[d.module_type] ?? colors.primary}
            />
          </Card>
        </>
      )}

      {/* Top cameras */}
      {topCameras.length > 0 && (
        <>
          <Text style={styles.sectionTitle}>Top Cameras by Alerts (7d)</Text>
          <Card>
            {topCameras.map((cam: TopCamera, i: number) => (
              <View key={cam.camera_id} style={styles.camRow}>
                <Text style={styles.camRank}>#{i + 1}</Text>
                <Text style={styles.camName} numberOfLines={1}>{cam.camera_name}</Text>
                <Text style={styles.camCount}>{cam.alert_count}</Text>
              </View>
            ))}
          </Card>
        </>
      )}
    </ScrollView>
  )
}

const styles = StyleSheet.create({
  root:         { flex: 1, backgroundColor: colors.background },
  center:       { flex: 1, alignItems: 'center', justifyContent: 'center', backgroundColor: colors.background },
  content:      { padding: spacing.md, paddingBottom: spacing.xxl },
  sectionTitle: { fontSize: fontSize.lg, fontWeight: '700', color: colors.text, marginBottom: spacing.sm, marginTop: spacing.md },
  kpiGrid:      { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm, marginBottom: spacing.xs },
  camRow:       { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, paddingVertical: 6, borderBottomWidth: 1, borderBottomColor: colors.divider },
  camRank:      { fontSize: fontSize.sm, color: colors.textDisabled, width: 24 },
  camName:      { flex: 1, fontSize: fontSize.sm, color: colors.text, fontWeight: '600' },
  camCount:     { fontSize: fontSize.sm, fontWeight: '800', color: colors.error },
})
