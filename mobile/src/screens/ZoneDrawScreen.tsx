import React, { useLayoutEffect, useState } from 'react'
import {
  ActivityIndicator, Alert, Pressable, StyleSheet, Text, TextInput, View,
} from 'react-native'
import { WebView } from 'react-native-webview'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigation } from '@react-navigation/native'
import { Ionicons } from '@expo/vector-icons'
import { apiClient } from '@/api/client'
import { useAuthStore } from '@/store/auth'
import { createZone, createCrowdZone, type ZoneSeverity } from '@/api/zones'
import { createPrivacyZone } from '@/api/privacyZones'
import { ZoneDrawOverlay, type ZonePoint } from '@/components/ZoneDrawOverlay'
import { Card } from '@/components/Card'
import { MASKED_CAMERAS_KEY } from '@/hooks/useMaskedCameras'
import { apiErrorText } from '@/lib/apiErrorText'
import { WHAT_A_ZONE_DOES, managesPrivacy } from '@/lib/privacyZoneWords'
import { colors, fontSize, radius, spacing } from '@/theme'

type Props = { route: { params: { cameraId: string; streamId: string; cameraName: string; kind?: 'privacy' } } }

const SEVERITIES: ZoneSeverity[] = ['low', 'medium', 'high', 'critical']
// A privacy zone is not a rule for the AI, as the other two are: it is painted
// out of the camera's picture by the server, for good in what is recorded
// after it. It is offered only to whoever is known to manage privacy.
type ZoneKind = 'restricted' | 'crowd' | 'privacy'
const KIND_LABEL: Record<ZoneKind, string> = { restricted: 'Restricted', crowd: 'Crowd', privacy: 'Privacy' }
/** The colour a privacy zone is drawn in while it is placed. It is applied in black. */
const PRIVACY_DRAWING_COLOUR = '#B0BEC5'

export function ZoneDrawScreen({ route }: Props) {
  const { cameraId, streamId, cameraName } = route.params
  const navigation = useNavigation<any>()
  const accessToken = useAuthStore((s) => s.accessToken)
  const mayMask = managesPrivacy(useAuthStore((s) => s.permissions))
  const qc = useQueryClient()

  const [points, setPoints] = useState<ZonePoint[]>([])
  const [closed, setClosed] = useState(false)
  const [name, setName] = useState('')
  const [severity, setSeverity] = useState<ZoneSeverity>('medium')
  const [kind, setKind] = useState<ZoneKind>(route.params.kind === 'privacy' && mayMask ? 'privacy' : 'restricted')
  const kinds: ZoneKind[] = mayMask ? ['restricted', 'crowd', 'privacy'] : ['restricted', 'crowd']

  useLayoutEffect(() => {
    navigation.setOptions({ title: `Draw Zone — ${cameraName}` })
  }, [navigation, cameraName])

  const { mutate: save, isPending } = useMutation({
    mutationFn: (): Promise<unknown> => {
      const polygon = points.map((p) => ({ x: p.x, y: p.y }))
      if (kind === 'privacy') return createPrivacyZone({ camera_id: cameraId, name, polygon })
      return kind === 'restricted'
        ? createZone({ camera_id: cameraId, name, polygon, severity })
        : createCrowdZone({ camera_id: cameraId, name, polygon, severity })
    },
    onSuccess: () => {
      if (kind === 'privacy') {
        // The list of this camera's zones, and the label on its live picture, are read again.
        void qc.invalidateQueries({ queryKey: ['privacy-zones', cameraId] })
        void qc.invalidateQueries({ queryKey: MASKED_CAMERAS_KEY })
        Alert.alert('Privacy zone drawn', `Within about ten seconds "${name.trim()}" is masked on this camera.`)
      } else {
        Alert.alert('Zone saved', `"${name}" has been created.`)
      }
      navigation.goBack()
    },
    onError: (err) => {
      // A privacy zone is refused for reasons worth reading - a drone's camera, twenty zones already - so the
      // server's own words are shown.
      Alert.alert('Save failed', kind === 'privacy' ? apiErrorText(err) : 'Could not save the zone. Please try again.')
    },
  })

  // A privacy zone cannot be undone for what is recorded after it: the phone asks once more, and says what it does.
  const onSave = () => {
    if (kind !== 'privacy') { save(); return }
    Alert.alert('Mask this part of the picture?', WHAT_A_ZONE_DOES, [
      { text: 'Not yet', style: 'cancel' },
      { text: 'Mask it', style: 'destructive', onPress: () => save() },
    ])
  }

  const liveUrl = accessToken
    ? `${(apiClient.defaults.baseURL ?? 'http://10.0.2.2:8000').replace(/\/$/, '')}/api/v1/cameras/${cameraId}/streams/${streamId}/live?token=${accessToken}`
    : null

  const html = liveUrl ? `<!DOCTYPE html>
<html>
<head>
  <meta name="viewport" content="width=device-width, initial-scale=1, user-scalable=no">
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body { background: #000; }
    img { width: 100vw; height: 100vh; object-fit: fill; display: block; }
  </style>
</head>
<body>
  <img src="${liveUrl}" />
</body>
</html>` : null

  const canClose = points.length >= 3 && !closed
  const canSave = closed && name.trim().length > 0

  return (
    <View style={styles.root}>
      <View style={styles.videoWrap}>
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
            <Text style={styles.hintText}>Authentication required.</Text>
          </View>
        )}
        <ZoneDrawOverlay
          points={points}
          closed={closed}
          onAddPoint={(p) => setPoints((prev) => [...prev, p])}
          onClose={() => setClosed(true)}
          color={kind === 'privacy' ? PRIVACY_DRAWING_COLOUR : undefined}
        />
      </View>

      <View style={styles.toolbar}>
        <Text style={styles.hintText}>
          {closed
            ? `${points.length} points — polygon closed`
            : points.length === 0
            ? 'Tap the video to place the first point'
            : `${points.length} points — tap the first point to close (min 3)`}
        </Text>
        <View style={styles.toolbarBtns}>
          <Pressable
            style={styles.toolBtn}
            disabled={closed || points.length === 0}
            onPress={() => setPoints((prev) => prev.slice(0, -1))}
          >
            <Ionicons name="arrow-undo" size={16} color={colors.text} />
            <Text style={styles.toolBtnText}>Undo</Text>
          </Pressable>
          <Pressable
            style={styles.toolBtn}
            disabled={points.length === 0}
            onPress={() => { setPoints([]); setClosed(false) }}
          >
            <Ionicons name="trash-outline" size={16} color={colors.text} />
            <Text style={styles.toolBtnText}>Clear</Text>
          </Pressable>
        </View>
      </View>

      {closed && (
        <Card style={styles.form}>
          <TextInput
            style={styles.input}
            placeholder="Zone name"
            placeholderTextColor={colors.textDisabled}
            value={name}
            onChangeText={setName}
          />
          <View style={styles.row}>
            <Text style={styles.label}>Type</Text>
            <View style={styles.pillRow}>
              {kinds.map((k) => (
                <Pressable
                  key={k}
                  style={[styles.pill, kind === k && styles.pillActive]}
                  onPress={() => setKind(k)}
                  accessibilityRole="button"
                  accessibilityState={{ selected: kind === k }}
                >
                  <Text style={[styles.pillText, kind === k && styles.pillTextActive]}>
                    {KIND_LABEL[k]}
                  </Text>
                </Pressable>
              ))}
            </View>
          </View>
          {kind === 'privacy' ? (
            // What it does is said before the button that does it. A privacy zone has no severity: it alerts nobody.
            <View style={styles.warning}>
              <Text style={styles.warningText} testID="what-a-zone-does">{WHAT_A_ZONE_DOES}</Text>
            </View>
          ) : (
          <View style={styles.row}>
            <Text style={styles.label}>Severity</Text>
            <View style={styles.pillRow}>
              {SEVERITIES.map((s) => (
                <Pressable
                  key={s}
                  style={[styles.pill, severity === s && styles.pillActive]}
                  onPress={() => setSeverity(s)}
                >
                  <Text style={[styles.pillText, severity === s && styles.pillTextActive]}>
                    {s}
                  </Text>
                </Pressable>
              ))}
            </View>
          </View>
          )}
          <Pressable
            style={[styles.saveBtn, !canSave && styles.saveBtnDisabled]}
            disabled={!canSave || isPending}
            onPress={onSave}
            accessibilityRole="button"
          >
            {isPending ? <ActivityIndicator color="#fff" size="small" /> : (
              <Text style={styles.saveBtnText}>{kind === 'privacy' ? 'Mask this part of the picture' : 'Save Zone'}</Text>
            )}
          </Pressable>
        </Card>
      )}
    </View>
  )
}

const styles = StyleSheet.create({
  root:            { flex: 1, backgroundColor: colors.background },
  videoWrap:       { flex: 1, backgroundColor: '#000', position: 'relative' },
  webview:         { flex: 1, backgroundColor: '#000' },
  loadingOverlay:  { position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, alignItems: 'center', justifyContent: 'center', backgroundColor: '#000' },
  center:          { flex: 1, alignItems: 'center', justifyContent: 'center' },
  toolbar:         { padding: spacing.md, gap: spacing.sm },
  hintText:        { color: colors.textSecondary, fontSize: fontSize.sm },
  toolbarBtns:     { flexDirection: 'row', gap: spacing.sm },
  toolBtn:         { flexDirection: 'row', alignItems: 'center', gap: 6, borderRadius: radius.md, borderWidth: 1, borderColor: colors.cardBorder, paddingHorizontal: spacing.md, paddingVertical: spacing.sm },
  toolBtnText:     { color: colors.text, fontSize: fontSize.sm, fontWeight: '600' },
  form:            { margin: spacing.md, marginTop: 0, gap: spacing.sm },
  input:           { borderWidth: 1, borderColor: colors.cardBorder, borderRadius: radius.md, paddingHorizontal: spacing.md, paddingVertical: spacing.sm, color: colors.text, fontSize: fontSize.md },
  row:             { gap: 6 },
  label:           { fontSize: fontSize.xs, color: colors.textSecondary, textTransform: 'uppercase', letterSpacing: 0.5 },
  pillRow:         { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.xs },
  pill:            { borderRadius: radius.full, borderWidth: 1, borderColor: colors.cardBorder, paddingHorizontal: spacing.md, paddingVertical: 6 },
  pillActive:      { backgroundColor: colors.primary, borderColor: colors.primary },
  pillText:        { fontSize: fontSize.sm, color: colors.textSecondary, textTransform: 'capitalize' },
  pillTextActive:  { color: '#fff', fontWeight: '700' },
  saveBtn:         { backgroundColor: colors.primary, borderRadius: radius.md, alignItems: 'center', paddingVertical: spacing.sm, marginTop: spacing.xs },
  saveBtnDisabled: { opacity: 0.4 },
  warning:         { borderWidth: 1, borderColor: colors.warning, borderRadius: radius.md, padding: spacing.sm },
  warningText:     { color: colors.text, fontSize: fontSize.sm },
  saveBtnText:     { color: '#fff', fontWeight: '700', fontSize: fontSize.md },
})
