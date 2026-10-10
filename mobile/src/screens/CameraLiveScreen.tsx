import React, { useLayoutEffect, useMemo } from 'react'
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native'
import { WebView } from 'react-native-webview'
import { useNavigation } from '@react-navigation/native'
import { Ionicons } from '@expo/vector-icons'
import { useAuthStore } from '@/store/auth'
import { apiClient } from '@/api/client'
import { useMaskedCameras } from '@/hooks/useMaskedCameras'
import { MASKED_LABEL, managesPrivacy } from '@/lib/privacyZoneWords'
import { colors, fontSize, spacing } from '@/theme'

type LiveParams = { cameraId: string; streamId: string; cameraName: string }
type Props = { route: { params: LiveParams } }

export function CameraLiveScreen({ route }: Props) {
  const { cameraId, streamId, cameraName } = route.params
  const navigation = useNavigation<any>()
  const accessToken = useAuthStore((s) => s.accessToken)

  // CameraLiveScreen is reused by both CamerasStack (which registers
  // ZoneDraw) and IncidentsStack (which doesn't, via IncidentCameraLive) —
  // only offer the "Draw Zone" action when it's actually reachable from
  // whichever stack this instance is currently mounted in.
  const canDrawZone = navigation.getState?.()?.routeNames?.includes('ZoneDraw')
  // The privacy zones of this camera, for whoever is known to manage privacy -
  // and, like Draw Zone, only where the stack this is mounted in has the screen.
  const mayMask = managesPrivacy(useAuthStore((s) => s.permissions))
  const canOpenPrivacy = mayMask && navigation.getState?.()?.routeNames?.includes('CameraPrivacyZones')
  // The picture is the server's masked one whatever this says. It is asked so
  // that a black block in it is labelled as meant, and not taken for a fault.
  const masked = useMaskedCameras().has(cameraId)

  useLayoutEffect(() => {
    navigation.setOptions({
      title: cameraName,
      headerRight: canDrawZone || canOpenPrivacy
        ? () => (
            <View style={styles.headerButtons}>
              {canOpenPrivacy && (
                <Pressable
                  onPress={() => navigation.navigate('CameraPrivacyZones', { cameraId, streamId, cameraName })}
                  style={{ paddingHorizontal: spacing.xs }}
                  accessibilityRole="button"
                  accessibilityLabel="Privacy zones of this camera"
                >
                  <Ionicons name="eye-off-outline" size={20} color={colors.primary} />
                </Pressable>
              )}
              {canDrawZone && (
                <Pressable
                  onPress={() => navigation.navigate('ZoneDraw', { cameraId, streamId, cameraName })}
                  style={{ paddingHorizontal: spacing.xs }}
                  accessibilityRole="button"
                  accessibilityLabel="Draw a zone"
                >
                  <Ionicons name="shapes-outline" size={20} color={colors.primary} />
                </Pressable>
              )}
            </View>
          )
        : undefined,
    })
  }, [navigation, cameraName, cameraId, streamId, canDrawZone, canOpenPrivacy])

  const liveUrl = useMemo(() => {
    const base = (apiClient.defaults.baseURL ?? 'http://10.0.2.2:8000').replace(/\/$/, '')
    return `${base}/api/v1/cameras/${cameraId}/streams/${streamId}/live?token=${accessToken ?? ''}`
  }, [cameraId, streamId, accessToken])

  const html = `<!DOCTYPE html>
<html>
<head>
  <meta name="viewport" content="width=device-width, initial-scale=1, user-scalable=no">
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body { background: #000; display: flex; align-items: center; justify-content: center; min-height: 100vh; }
    img { width: 100%; height: 100vh; object-fit: contain; display: block; }
    .error { color: #ccc; font-family: sans-serif; text-align: center; padding: 24px; line-height: 1.6; }
    .error strong { color: #fff; display: block; margin-bottom: 8px; font-size: 16px; }
  </style>
</head>
<body>
  <img
    src="${liveUrl}"
    onerror="this.style.display='none'; document.body.innerHTML='<div class=error><strong>Stream Unavailable</strong>Check camera connection and ensure the server is reachable.</div>'"
  />
</body>
</html>`

  if (!accessToken) {
    return (
      <View style={styles.center}>
        <Text style={styles.errorText}>Authentication required. Please log in again.</Text>
      </View>
    )
  }

  return (
    <View style={styles.root}>
      <WebView
        source={{ html }}
        style={styles.webview}
        startInLoadingState
        renderLoading={() => (
          <View style={styles.loadingOverlay}>
            <ActivityIndicator color={colors.primary} size="large" />
            <Text style={styles.loadingText}>Connecting to stream…</Text>
          </View>
        )}
        allowsInlineMediaPlayback
        mediaPlaybackRequiresUserAction={false}
        originWhitelist={['*']}
        mixedContentMode="always"
        javaScriptEnabled={true}
      />
      {masked && (
        <View style={styles.maskedLabel} pointerEvents="none">
          <Ionicons name="eye-off-outline" size={12} color="#fff" />
          <Text style={styles.maskedText}>{MASKED_LABEL}</Text>
        </View>
      )}
    </View>
  )
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: '#000',
  },
  webview: {
    flex: 1,
    backgroundColor: '#000',
  },
  loadingOverlay: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#000',
    gap: 12,
  },
  loadingText: {
    color: colors.textSecondary,
    fontSize: fontSize.sm,
  },
  center: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.background,
    padding: 24,
  },
  errorText: {
    color: colors.textSecondary,
    fontSize: fontSize.md,
    textAlign: 'center',
  },
  headerButtons: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  maskedLabel: {
    position: 'absolute',
    top: spacing.sm,
    left: spacing.sm,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    backgroundColor: 'rgba(0,0,0,0.6)',
    borderColor: 'rgba(255,255,255,0.5)',
    borderWidth: 1,
    borderRadius: 10,
    paddingHorizontal: 8,
    paddingVertical: 3,
  },
  maskedText: {
    color: '#fff',
    fontSize: fontSize.xs,
    fontWeight: '600',
  },
})
