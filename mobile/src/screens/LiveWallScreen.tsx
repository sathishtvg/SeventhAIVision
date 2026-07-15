import React, { useEffect, useMemo, useState } from 'react'
import {
  ActivityIndicator, FlatList, Modal, Pressable, StyleSheet, Text, View,
} from 'react-native'
import { WebView } from 'react-native-webview'
import { useQuery } from '@tanstack/react-query'
import { Ionicons } from '@expo/vector-icons'
import { getCameras, getStreams, type Camera, type Stream } from '@/api/cameras'
import { apiClient } from '@/api/client'
import { useAuthStore } from '@/store/auth'
import { Card } from '@/components/Card'
import { colors, fontSize, radius, spacing } from '@/theme'
import { loadLiveWallCameras, saveLiveWallCameras, type LiveWallEntry } from '@/lib/liveWallStorage'

const MAX_TILES = 4

export function LiveWallScreen() {
  const accessToken = useAuthStore((s) => s.accessToken)
  const [entries, setEntries] = useState<LiveWallEntry[] | null>(null)
  const [pickerOpen, setPickerOpen] = useState(false)

  const { data: cameras = [] } = useQuery({ queryKey: ['cameras'], queryFn: () => getCameras() })
  const { data: streams = [] } = useQuery({ queryKey: ['streams'], queryFn: () => getStreams() })

  useEffect(() => {
    loadLiveWallCameras().then(setEntries)
  }, [])

  const streamMap = useMemo(
    () => Object.fromEntries(streams.map((s: Stream) => [s.camera_id, s])),
    [streams],
  )

  const watchable = useMemo(
    () => cameras.filter((c: Camera) => streamMap[c.id]),
    [cameras, streamMap],
  )

  const toggleCamera = (cameraId: string) => {
    if (!entries) return
    const stream = streamMap[cameraId]
    if (!stream) return
    const camera = cameras.find((c: Camera) => c.id === cameraId)
    const already = entries.some((e) => e.cameraId === cameraId)
    let next: LiveWallEntry[]
    if (already) {
      next = entries.filter((e) => e.cameraId !== cameraId)
    } else {
      if (entries.length >= MAX_TILES) return
      next = [...entries, { cameraId, streamId: stream.id, cameraName: camera?.name ?? 'Camera' }]
    }
    setEntries(next)
    saveLiveWallCameras(next)
  }

  const html = useMemo(() => {
    if (!entries || entries.length === 0 || !accessToken) return null
    const base = (apiClient.defaults.baseURL ?? 'http://10.0.2.2:8000').replace(/\/$/, '')
    const cols = entries.length <= 1 ? 1 : 2
    const tiles = entries.map((e) => {
      const url = `${base}/api/v1/cameras/${e.cameraId}/streams/${e.streamId}/live?token=${accessToken}`
      return `
        <div class="tile">
          <img src="${url}" onerror="this.style.display='none'" />
          <div class="label">${e.cameraName.replace(/</g, '&lt;')}</div>
        </div>`
    }).join('')

    return `<!DOCTYPE html>
<html>
<head>
  <meta name="viewport" content="width=device-width, initial-scale=1, user-scalable=no">
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body { background: #000; }
    .grid {
      display: grid;
      grid-template-columns: repeat(${cols}, 1fr);
      gap: 2px;
      width: 100vw;
      height: 100vh;
    }
    .tile { position: relative; background: #000; overflow: hidden; }
    .tile img { width: 100%; height: 100%; object-fit: contain; display: block; }
    .label {
      position: absolute; bottom: 0; left: 0; right: 0;
      background: rgba(0,0,0,0.55); color: #fff; font-family: sans-serif;
      font-size: 11px; padding: 3px 6px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    }
  </style>
</head>
<body>
  <div class="grid">${tiles}</div>
</body>
</html>`
  }, [entries, accessToken])

  return (
    <View style={styles.root}>
      {!entries || entries.length === 0 ? (
        <View style={styles.center}>
          <Ionicons name="grid-outline" size={40} color={colors.textDisabled} />
          <Text style={styles.emptyText}>No cameras on your wall yet</Text>
          <Pressable style={styles.addBtn} onPress={() => setPickerOpen(true)}>
            <Ionicons name="add" size={16} color="#fff" />
            <Text style={styles.addBtnText}>Add Cameras</Text>
          </Pressable>
        </View>
      ) : (
        <>
          {html ? (
            <WebView
              source={{ html }}
              style={styles.webview}
              startInLoadingState
              renderLoading={() => (
                <View style={styles.loadingOverlay}>
                  <ActivityIndicator color={colors.primary} size="large" />
                </View>
              )}
              allowsInlineMediaPlayback
              mediaPlaybackRequiresUserAction={false}
              originWhitelist={['*']}
              mixedContentMode="always"
              javaScriptEnabled
            />
          ) : (
            <View style={styles.center}>
              <ActivityIndicator color={colors.primary} />
            </View>
          )}
          <Pressable style={styles.fab} onPress={() => setPickerOpen(true)}>
            <Ionicons name="apps" size={20} color="#fff" />
          </Pressable>
        </>
      )}

      <Modal visible={pickerOpen} animationType="slide" onRequestClose={() => setPickerOpen(false)}>
        <View style={styles.modalRoot}>
          <View style={styles.modalHeader}>
            <Text style={styles.modalTitle}>Live Wall Cameras</Text>
            <Text style={styles.modalSubtitle}>{(entries ?? []).length}/{MAX_TILES} selected</Text>
            <Pressable onPress={() => setPickerOpen(false)}>
              <Ionicons name="close" size={24} color={colors.text} />
            </Pressable>
          </View>
          <FlatList
            data={watchable}
            keyExtractor={(item) => item.id}
            contentContainerStyle={styles.modalList}
            renderItem={({ item }) => {
              const selected = (entries ?? []).some((e) => e.cameraId === item.id)
              return (
                <Pressable onPress={() => toggleCamera(item.id)}>
                  <Card style={[styles.pickerCard, selected && styles.pickerCardActive]}>
                    <Ionicons
                      name={selected ? 'checkmark-circle' : 'ellipse-outline'}
                      size={22}
                      color={selected ? colors.primary : colors.textDisabled}
                    />
                    <View style={{ flex: 1 }}>
                      <Text style={styles.pickerName}>{item.name}</Text>
                      {item.site_name && <Text style={styles.pickerSite}>{item.site_name}</Text>}
                    </View>
                  </Card>
                </Pressable>
              )
            }}
            ItemSeparatorComponent={() => <View style={{ height: spacing.sm }} />}
          />
        </View>
      </Modal>
    </View>
  )
}

const styles = StyleSheet.create({
  root:            { flex: 1, backgroundColor: colors.background },
  webview:         { flex: 1, backgroundColor: '#000' },
  center:          { flex: 1, alignItems: 'center', justifyContent: 'center', gap: spacing.sm, padding: spacing.lg },
  emptyText:       { color: colors.textSecondary, fontSize: fontSize.md },
  addBtn:          { flexDirection: 'row', alignItems: 'center', gap: 6, backgroundColor: colors.primary, borderRadius: radius.md, paddingHorizontal: spacing.md, paddingVertical: spacing.sm, marginTop: spacing.sm },
  addBtnText:      { color: '#fff', fontWeight: '700', fontSize: fontSize.sm },
  loadingOverlay:  { position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, alignItems: 'center', justifyContent: 'center', backgroundColor: '#000' },
  fab:             { position: 'absolute', right: spacing.md, bottom: spacing.md, width: 48, height: 48, borderRadius: 24, backgroundColor: colors.primary, alignItems: 'center', justifyContent: 'center', elevation: 4 },
  modalRoot:       { flex: 1, backgroundColor: colors.background },
  modalHeader:     { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, padding: spacing.md, borderBottomWidth: 1, borderBottomColor: colors.divider },
  modalTitle:      { flex: 1, fontSize: fontSize.lg, fontWeight: '700', color: colors.text },
  modalSubtitle:   { fontSize: fontSize.xs, color: colors.textSecondary },
  modalList:       { padding: spacing.md },
  pickerCard:      { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  pickerCardActive:{ borderColor: colors.primary },
  pickerName:      { fontSize: fontSize.md, fontWeight: '600', color: colors.text },
  pickerSite:      { fontSize: fontSize.xs, color: colors.textSecondary, marginTop: 2 },
})
