import React, { useCallback, useLayoutEffect, useMemo, useState } from 'react'
import {
  ActivityIndicator, FlatList, Pressable, RefreshControl,
  ScrollView, StyleSheet, Text, View,
} from 'react-native'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigation, useRoute } from '@react-navigation/native'
import type { RouteProp } from '@react-navigation/native'
import type { NativeStackNavigationProp } from '@react-navigation/native-stack'
import { Ionicons } from '@expo/vector-icons'
import { getCameras, getStreams, type Camera, type Stream } from '@/api/cameras'
import { getSites, type Site } from '@/api/sites'
import { Card } from '@/components/Card'
import { StatusBadge } from '@/components/StatusBadge'
import { colors, fontSize, radius, spacing } from '@/theme'
import type { CamerasStackParamList } from '@/navigation'

type NavProp  = NativeStackNavigationProp<CamerasStackParamList>
type RouteType = RouteProp<CamerasStackParamList, 'CamerasList'>

function CameraCard({
  camera,
  stream,
  onWatchLive,
}: {
  camera: Camera
  stream?: Stream
  onWatchLive?: () => void
}) {
  const statusIcon: Record<string, React.ComponentProps<typeof Ionicons>['name']> = {
    online:   'checkmark-circle',
    degraded: 'warning',
    offline:  'close-circle',
  }
  const status     = stream?.status ?? 'offline'
  const iconName   = statusIcon[status] ?? 'help-circle'
  const iconColor  =
    status === 'online' ? colors.success : status === 'degraded' ? colors.warning : colors.error

  return (
    <Card style={styles.card}>
      <View style={styles.cardHeader}>
        <View style={styles.iconWrap}>
          <Ionicons name="videocam" size={24} color={colors.primary} />
        </View>
        <View style={styles.cardMeta}>
          <Text style={styles.cameraName} numberOfLines={1}>{camera.name}</Text>
          {camera.site_name ? (
            <View style={styles.siteTag}>
              <Ionicons name="business-outline" size={11} color={colors.secondary} />
              <Text style={styles.siteTagText}>{camera.site_name}</Text>
            </View>
          ) : camera.location ? (
            <Text style={styles.location} numberOfLines={1}>{camera.location}</Text>
          ) : null}
        </View>
        <Ionicons name={iconName} size={20} color={iconColor} />
      </View>

      <View style={styles.modulesRow}>
        {(camera.ai_modules_enabled as string[]).map((mod) => (
          <View key={mod} style={styles.modChip}>
            <Text style={styles.modText}>{mod.toUpperCase()}</Text>
          </View>
        ))}
      </View>

      <View style={styles.footer}>
        <StatusBadge value={status} />
        {stream?.last_frame_at && (
          <Text style={styles.lastSeen}>
            {new Date(stream.last_frame_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
          </Text>
        )}
        {stream && status !== 'offline' && (
          <Pressable style={styles.liveBtn} onPress={onWatchLive}>
            <Ionicons name="play-circle" size={14} color="#fff" />
            <Text style={styles.liveBtnText}>Watch Live</Text>
          </Pressable>
        )}
      </View>
    </Card>
  )
}

export function CamerasScreen() {
  const qc  = useQueryClient()
  const nav = useNavigation<NavProp>()
  const route = useRoute<RouteType>()

  // Honour a pre-selected siteId from SitesScreen navigation param
  const initialSiteId = (route.params as any)?.siteId as string | undefined
  const [activeSiteId, setActiveSiteId] = useState<string | undefined>(
    initialSiteId === '__none__' ? '__none__' : initialSiteId,
  )
  const [refreshing, setRefreshing] = useState(false)

  const { data: sites = [] } = useQuery({
    queryKey: ['sites'],
    queryFn: getSites,
    refetchInterval: 120_000,
  })

  // Pass site_id to backend when a real site is selected
  const backendSiteId = activeSiteId === '__none__' ? undefined : activeSiteId

  const { data: cameras = [], isLoading: loadCam } = useQuery({
    queryKey: ['cameras', activeSiteId],
    queryFn: () => getCameras(backendSiteId ? { site_id: backendSiteId } : undefined),
    refetchInterval: 60_000,
  })

  const { data: streams = [], isLoading: loadStr } = useQuery({
    queryKey: ['streams'],
    queryFn: () => getStreams(),
    refetchInterval: 30_000,
  })

  useLayoutEffect(() => {
    nav.setOptions({
      headerRight: () => (
        <Pressable onPress={() => nav.navigate('LiveWall')} style={{ paddingHorizontal: spacing.xs }}>
          <Ionicons name="grid-outline" size={22} color={colors.primary} />
        </Pressable>
      ),
    })
  }, [nav])

  const onRefresh = useCallback(async () => {
    setRefreshing(true)
    await Promise.all([
      qc.invalidateQueries({ queryKey: ['cameras'] }),
      qc.invalidateQueries({ queryKey: ['streams'] }),
      qc.invalidateQueries({ queryKey: ['sites'] }),
    ])
    setRefreshing(false)
  }, [qc])

  const streamMap = useMemo(
    () => Object.fromEntries(streams.map((s: Stream) => [s.camera_id, s])),
    [streams],
  )

  // Client-side filter for unassigned (when __none__ is active)
  const displayedCameras = useMemo(() => {
    if (activeSiteId === '__none__') return cameras.filter((c: Camera) => !c.site_id)
    return cameras
  }, [cameras, activeSiteId])

  const isLoading = loadCam || loadStr

  return (
    <View style={styles.root}>
      {/* Site filter chips */}
      {sites.length > 0 && (
        <View style={styles.filterBar}>
          <ScrollView
            horizontal
            showsHorizontalScrollIndicator={false}
            contentContainerStyle={styles.chipScroll}
          >
            <Pressable
              style={[styles.chip, activeSiteId === undefined && styles.chipActive]}
              onPress={() => setActiveSiteId(undefined)}
            >
              <Text style={[styles.chipText, activeSiteId === undefined && styles.chipTextActive]}>
                All
              </Text>
            </Pressable>
            {sites.filter((s: Site) => s.is_active).map((site: Site) => (
              <Pressable
                key={site.id}
                style={[styles.chip, activeSiteId === site.id && styles.chipActive]}
                onPress={() => setActiveSiteId(site.id)}
              >
                <Text style={[styles.chipText, activeSiteId === site.id && styles.chipTextActive]}>
                  {site.name}
                </Text>
              </Pressable>
            ))}
          </ScrollView>
          <Pressable
            style={styles.sitesBtn}
            onPress={() => nav.navigate('Sites')}
          >
            <Ionicons name="business-outline" size={18} color={colors.secondary} />
          </Pressable>
        </View>
      )}

      {isLoading ? (
        <View style={styles.center}>
          <ActivityIndicator color={colors.primary} />
        </View>
      ) : displayedCameras.length === 0 ? (
        <View style={styles.center}>
          <Ionicons name="videocam-off-outline" size={48} color={colors.textDisabled} />
          <Text style={styles.emptyText}>
            {activeSiteId ? 'No cameras for this site' : 'No cameras configured'}
          </Text>
        </View>
      ) : (
        <FlatList
          data={displayedCameras}
          keyExtractor={(item) => item.id}
          renderItem={({ item }) => {
            const stream = streamMap[item.id]
            return (
              <CameraCard
                camera={item}
                stream={stream}
                onWatchLive={() => {
                  if (stream) {
                    nav.navigate('CameraLive', {
                      cameraId: item.id,
                      streamId: stream.id,
                      cameraName: item.name,
                    })
                  }
                }}
              />
            )
          }}
          ItemSeparatorComponent={() => <View style={{ height: spacing.sm }} />}
          contentContainerStyle={styles.list}
          refreshControl={
            <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.primary} />
          }
        />
      )}
    </View>
  )
}

const styles = StyleSheet.create({
  root:          { flex: 1, backgroundColor: colors.background },
  filterBar:     { flexDirection: 'row', alignItems: 'center', paddingTop: spacing.sm, paddingBottom: 0, borderBottomWidth: 1, borderBottomColor: colors.divider },
  chipScroll:    { flexDirection: 'row', gap: spacing.xs, paddingHorizontal: spacing.md, paddingBottom: spacing.sm },
  chip:          { borderRadius: radius.full, borderWidth: 1, borderColor: colors.cardBorder, paddingHorizontal: spacing.md, paddingVertical: 6 },
  chipActive:    { backgroundColor: colors.primary, borderColor: colors.primary },
  chipText:      { fontSize: fontSize.sm, color: colors.textSecondary },
  chipTextActive:{ color: '#fff', fontWeight: '600' },
  sitesBtn:      { paddingHorizontal: spacing.md, paddingBottom: spacing.sm },
  list:          { padding: spacing.md, paddingBottom: spacing.xl },
  center:        { flex: 1, alignItems: 'center', justifyContent: 'center', gap: spacing.sm },
  card:          { gap: spacing.sm },
  cardHeader:    { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  iconWrap:      { width: 40, height: 40, borderRadius: 20, backgroundColor: colors.primary + '22', alignItems: 'center', justifyContent: 'center' },
  cardMeta:      { flex: 1 },
  cameraName:    { fontSize: fontSize.md, fontWeight: '600', color: colors.text },
  siteTag:       { flexDirection: 'row', alignItems: 'center', gap: 4, marginTop: 2 },
  siteTagText:   { fontSize: fontSize.xs, color: colors.secondary },
  location:      { fontSize: fontSize.xs, color: colors.textSecondary },
  modulesRow:    { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.xs },
  modChip:       { borderRadius: 4, backgroundColor: colors.primary + '22', paddingHorizontal: 6, paddingVertical: 2 },
  modText:       { fontSize: fontSize.xs, color: colors.primary, fontWeight: '600' },
  footer:        { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: spacing.xs },
  lastSeen:      { fontSize: fontSize.xs, color: colors.textSecondary },
  liveBtn:       { flexDirection: 'row', alignItems: 'center', gap: 4, backgroundColor: colors.primary, borderRadius: 4, paddingHorizontal: 8, paddingVertical: 4 },
  liveBtnText:   { color: '#fff', fontSize: fontSize.xs, fontWeight: '600' },
  emptyText:     { color: colors.textSecondary, fontSize: fontSize.md },
})
