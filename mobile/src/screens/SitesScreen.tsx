import React, { useCallback, useState } from 'react'
import {
  ActivityIndicator, FlatList, Pressable, RefreshControl,
  StyleSheet, Text, View,
} from 'react-native'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigation } from '@react-navigation/native'
import type { NativeStackNavigationProp } from '@react-navigation/native-stack'
import { Ionicons } from '@expo/vector-icons'
import { getSites, type Site } from '@/api/sites'
import { getCameras, type Camera } from '@/api/cameras'
import { Card } from '@/components/Card'
import { colors, fontSize, radius, spacing } from '@/theme'
import type { CamerasStackParamList } from '@/navigation'

type NavProp = NativeStackNavigationProp<CamerasStackParamList>

interface SiteStats {
  total: number
  online: number
  degraded: number
  offline: number
}

function buildSiteStats(cameras: Camera[]): Record<string, SiteStats> {
  const map: Record<string, SiteStats> = {}
  for (const cam of cameras) {
    const key = cam.site_id ?? '__none__'
    if (!map[key]) map[key] = { total: 0, online: 0, degraded: 0, offline: 0 }
    map[key].total += 1
  }
  return map
}

function StatusBar({ stats }: { stats: SiteStats }) {
  if (stats.total === 0) return null
  const onlinePct  = (stats.online   / stats.total) * 100
  const degradedPct = (stats.degraded / stats.total) * 100
  return (
    <View style={bar.track}>
      <View style={[bar.seg, { width: `${onlinePct}%` as any,   backgroundColor: colors.success }]} />
      <View style={[bar.seg, { width: `${degradedPct}%` as any, backgroundColor: colors.warning }]} />
    </View>
  )
}
const bar = StyleSheet.create({
  track: { height: 4, borderRadius: 2, backgroundColor: colors.error + '60', flexDirection: 'row', overflow: 'hidden', marginTop: spacing.sm },
  seg:   { height: '100%' },
})

function SiteCard({ site, stats, onPress }: { site: Site; stats?: SiteStats; onPress: () => void }) {
  const s = stats ?? { total: 0, online: 0, degraded: 0, offline: 0 }
  const allOnline  = s.total > 0 && s.online === s.total
  const anyOffline = s.offline > 0
  const statusColor = allOnline ? colors.success : anyOffline ? colors.error : s.total > 0 ? colors.warning : colors.textDisabled

  return (
    <Pressable onPress={onPress}>
      <Card style={styles.card}>
        <View style={styles.cardTop}>
          <View style={[styles.siteIcon, { backgroundColor: statusColor + '28' }]}>
            <Ionicons name="business-outline" size={22} color={statusColor} />
          </View>
          <View style={styles.siteInfo}>
            <Text style={styles.siteName}>{site.name}</Text>
            {site.address && (
              <Text style={styles.siteAddress} numberOfLines={1}>{site.address}</Text>
            )}
          </View>
          <View style={styles.cameraCount}>
            <Text style={styles.cameraCountNum}>{s.total}</Text>
            <Text style={styles.cameraCountLabel}>cams</Text>
          </View>
          <Ionicons name="chevron-forward" size={18} color={colors.textDisabled} />
        </View>

        {s.total > 0 && (
          <>
            <StatusBar stats={s} />
            <View style={styles.statsRow}>
              <View style={styles.stat}>
                <View style={[styles.statDot, { backgroundColor: colors.success }]} />
                <Text style={styles.statText}>{s.online} online</Text>
              </View>
              {s.degraded > 0 && (
                <View style={styles.stat}>
                  <View style={[styles.statDot, { backgroundColor: colors.warning }]} />
                  <Text style={styles.statText}>{s.degraded} degraded</Text>
                </View>
              )}
              {s.offline > 0 && (
                <View style={styles.stat}>
                  <View style={[styles.statDot, { backgroundColor: colors.error }]} />
                  <Text style={styles.statText}>{s.offline} offline</Text>
                </View>
              )}
            </View>
          </>
        )}
        {s.total === 0 && (
          <Text style={styles.noCams}>No cameras assigned</Text>
        )}
      </Card>
    </Pressable>
  )
}

export function SitesScreen() {
  const qc = useQueryClient()
  const nav = useNavigation<NavProp>()
  const [refreshing, setRefreshing] = useState(false)

  const { data: sites = [], isLoading: loadSites } = useQuery({
    queryKey: ['sites'],
    queryFn: getSites,
    refetchInterval: 120_000,
  })

  const { data: cameras = [], isLoading: loadCams } = useQuery({
    queryKey: ['cameras'],
    queryFn: () => getCameras(),
    refetchInterval: 60_000,
  })

  const onRefresh = useCallback(async () => {
    setRefreshing(true)
    await Promise.all([
      qc.invalidateQueries({ queryKey: ['sites'] }),
      qc.invalidateQueries({ queryKey: ['cameras'] }),
    ])
    setRefreshing(false)
  }, [qc])

  const siteStats = buildSiteStats(cameras)

  const unassigned = cameras.filter((c: Camera) => !c.site_id)

  if (loadSites || loadCams) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={colors.primary} size="large" />
      </View>
    )
  }

  if (sites.length === 0) {
    return (
      <View style={styles.center}>
        <Ionicons name="business-outline" size={48} color={colors.textDisabled} />
        <Text style={styles.emptyText}>No sites configured</Text>
        <Text style={styles.emptyHint}>Sites are managed from the web dashboard.</Text>
      </View>
    )
  }

  const activeSites = sites.filter((s: Site) => s.is_active)

  return (
    <FlatList
      data={activeSites}
      keyExtractor={(item) => item.id}
      renderItem={({ item }) => (
        <SiteCard
          site={item}
          stats={siteStats[item.id]}
          onPress={() => nav.navigate('CamerasList', { siteId: item.id })}
        />
      )}
      ItemSeparatorComponent={() => <View style={{ height: spacing.sm }} />}
      contentContainerStyle={styles.list}
      style={styles.root}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.primary} />}
      ListFooterComponent={
        unassigned.length > 0 ? (
          <Pressable onPress={() => nav.navigate('CamerasList', { siteId: '__none__' })}>
            <Card style={[styles.card, styles.unassignedCard]}>
              <View style={styles.cardTop}>
                <View style={[styles.siteIcon, { backgroundColor: colors.textDisabled + '28' }]}>
                  <Ionicons name="help-circle-outline" size={22} color={colors.textDisabled} />
                </View>
                <View style={styles.siteInfo}>
                  <Text style={styles.siteName}>Unassigned</Text>
                  <Text style={styles.siteAddress}>Cameras with no site</Text>
                </View>
                <View style={styles.cameraCount}>
                  <Text style={styles.cameraCountNum}>{unassigned.length}</Text>
                  <Text style={styles.cameraCountLabel}>cams</Text>
                </View>
                <Ionicons name="chevron-forward" size={18} color={colors.textDisabled} />
              </View>
            </Card>
          </Pressable>
        ) : null
      }
    />
  )
}

const styles = StyleSheet.create({
  root:             { flex: 1, backgroundColor: colors.background },
  center:           { flex: 1, alignItems: 'center', justifyContent: 'center', gap: spacing.sm, backgroundColor: colors.background, padding: spacing.xl },
  emptyText:        { color: colors.text, fontSize: fontSize.lg, fontWeight: '700' },
  emptyHint:        { color: colors.textSecondary, fontSize: fontSize.sm, textAlign: 'center' },
  list:             { padding: spacing.md, paddingBottom: spacing.xl },
  card:             { gap: 0 },
  unassignedCard:   { marginTop: spacing.sm, opacity: 0.7 },
  cardTop:          { flexDirection: 'row', alignItems: 'center', gap: spacing.md },
  siteIcon:         { width: 44, height: 44, borderRadius: radius.md, alignItems: 'center', justifyContent: 'center', flexShrink: 0 },
  siteInfo:         { flex: 1 },
  siteName:         { fontSize: fontSize.md, fontWeight: '700', color: colors.text },
  siteAddress:      { fontSize: fontSize.xs, color: colors.textSecondary, marginTop: 2 },
  cameraCount:      { alignItems: 'center', minWidth: 36 },
  cameraCountNum:   { fontSize: fontSize.lg, fontWeight: '800', color: colors.text },
  cameraCountLabel: { fontSize: fontSize.xs, color: colors.textSecondary },
  statsRow:         { flexDirection: 'row', gap: spacing.md, marginTop: 6 },
  stat:             { flexDirection: 'row', alignItems: 'center', gap: 5 },
  statDot:          { width: 8, height: 8, borderRadius: 4 },
  statText:         { fontSize: fontSize.xs, color: colors.textSecondary },
  noCams:           { fontSize: fontSize.xs, color: colors.textDisabled, marginTop: 6, fontStyle: 'italic' },
})
