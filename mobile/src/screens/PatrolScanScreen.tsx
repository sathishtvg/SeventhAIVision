import React, { useState, useEffect, useRef } from 'react'
import {
  ActivityIndicator, Alert as RNAlert, FlatList, Pressable,
  ScrollView, StyleSheet, Text, View,
} from 'react-native'
import { CameraView, useCameraPermissions } from 'expo-camera'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useRoute, useNavigation, type RouteProp } from '@react-navigation/native'
import type { NativeStackNavigationProp } from '@react-navigation/native-stack'
import { Ionicons } from '@expo/vector-icons'
import { getRouteDetail, startSession, completeSession, scanCheckpoint, type PatrolCheckpoint } from '@/api/patrols'
import { enqueueRequest, isNetworkError } from '@/offline/outbox'
import { Card } from '@/components/Card'
import { colors, fontSize, radius, spacing } from '@/theme'
import type { PatrolStackParamList } from '@/navigation'

type RoutePropType = RouteProp<PatrolStackParamList, 'PatrolScan'>
type NavProp = NativeStackNavigationProp<PatrolStackParamList>

export function PatrolScanScreen() {
  const { params } = useRoute<RoutePropType>()
  const navigation = useNavigation<NavProp>()
  const qc = useQueryClient()

  const [permission, requestPermission] = useCameraPermissions()
  const [scanning, setScanning] = useState(false)
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [scannedIds, setScannedIds] = useState<Set<string>>(new Set())
  const [lastScanResult, setLastScanResult] = useState<{ verified: boolean; checkpoint: string } | null>(null)
  const scanCooldown = useRef(false)

  const { data: route, isLoading } = useQuery({
    queryKey: ['patrol-route', params.routeId],
    queryFn: () => getRouteDetail(params.routeId),
  })

  const startMut = useMutation({
    mutationFn: () => startSession(params.routeId, params.shiftId),
    onSuccess: (data) => setSessionId(data.id),
    onError: () => RNAlert.alert('Error', 'Failed to start patrol session.'),
  })

  const completeMut = useMutation({
    mutationFn: () => completeSession(sessionId!),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['patrol-route'] })
      RNAlert.alert('Patrol Complete', 'Session completed successfully.', [
        { text: 'OK', onPress: () => navigation.goBack() },
      ])
    },
    onError: () => RNAlert.alert('Error', 'Failed to complete session.'),
  })

  const scanMut = useMutation({
    mutationFn: ({ checkpointId, code }: { checkpointId: string; code?: string }) =>
      scanCheckpoint(sessionId!, checkpointId, code ? 'qr' : 'manual', code),
    onSuccess: (data, vars) => {
      setScannedIds((prev) => new Set([...prev, vars.checkpointId]))
      const cp = route?.checkpoints.find((c: PatrolCheckpoint) => c.id === vars.checkpointId)
      setLastScanResult({ verified: data.verified, checkpoint: cp?.name ?? 'Checkpoint' })
    },
    onError: (err, vars) => {
      // Offline (Gap 88): queue the scan with its ORIGINAL time; the outbox
      // replays it when signal returns. Mark scanned locally so the guard
      // can carry on with the patrol.
      if (isNetworkError(err) && sessionId) {
        const cp = route?.checkpoints.find((c: PatrolCheckpoint) => c.id === vars.checkpointId)
        void enqueueRequest(
          `/api/v1/patrols/sessions/${sessionId}/scan`,
          {
            checkpoint_id: vars.checkpointId,
            scan_method: vars.code ? 'qr' : 'manual',
            scanned_code: vars.code,
            scanned_at: new Date().toISOString(),
          },
          `Checkpoint scan — ${cp?.name ?? 'checkpoint'}`,
        )
        setScannedIds((prev) => new Set([...prev, vars.checkpointId]))
        setLastScanResult({ verified: false, checkpoint: `${cp?.name ?? 'Checkpoint'} (queued offline)` })
        RNAlert.alert('Saved Offline', 'No signal — the scan is queued and will sync automatically.')
        return
      }
      RNAlert.alert('Error', 'Failed to record scan.')
    },
  })

  useEffect(() => {
    if (!permission?.granted) requestPermission()
  }, [])

  const handleBarCodeScanned = ({ data }: { data: string }) => {
    if (scanCooldown.current || !sessionId) return
    scanCooldown.current = true
    setTimeout(() => { scanCooldown.current = false }, 2000)

    const checkpoint = route?.checkpoints.find(
      (c: PatrolCheckpoint) => c.qr_code === data || c.nfc_tag_id === data,
    )
    if (!checkpoint) {
      RNAlert.alert('Not Found', `QR code not matched to any checkpoint on this route.`)
      return
    }
    if (scannedIds.has(checkpoint.id)) {
      RNAlert.alert('Already Scanned', `${checkpoint.name} was already scanned this session.`)
      return
    }
    scanMut.mutate({ checkpointId: checkpoint.id, code: data })
  }

  if (isLoading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={colors.primary} />
      </View>
    )
  }

  const checkpoints = route?.checkpoints ?? []
  const allScanned = checkpoints.every((c: PatrolCheckpoint) => scannedIds.has(c.id))

  return (
    <View style={styles.root}>
      {/* Route header */}
      <View style={styles.header}>
        <Text style={styles.routeName}>{route?.name}</Text>
        <Text style={styles.progress}>
          {scannedIds.size} / {checkpoints.length} checkpoints
        </Text>
      </View>

      {/* QR Camera view */}
      {scanning && sessionId && (
        <View style={styles.cameraContainer}>
          {permission?.granted ? (
            <CameraView
              style={StyleSheet.absoluteFillObject}
              facing="back"
              barcodeScannerSettings={{ barcodeTypes: ['qr'] }}
              onBarcodeScanned={handleBarCodeScanned}
            />
          ) : (
            <View style={styles.center}>
              <Text style={styles.permText}>Camera permission required</Text>
              <Pressable style={styles.btn} onPress={requestPermission}>
                <Text style={styles.btnText}>Grant Permission</Text>
              </Pressable>
            </View>
          )}
          <View style={styles.scanOverlay}>
            <View style={styles.scanFrame} />
          </View>
          <Pressable style={styles.closeScan} onPress={() => setScanning(false)}>
            <Ionicons name="close-circle" size={36} color="#fff" />
          </Pressable>
          {lastScanResult && (
            <View style={[styles.scanFeedback, lastScanResult.verified ? styles.feedbackOk : styles.feedbackWarn]}>
              <Ionicons name={lastScanResult.verified ? 'checkmark-circle' : 'warning'} size={20} color="#fff" />
              <Text style={styles.feedbackText}>
                {lastScanResult.checkpoint}: {lastScanResult.verified ? 'Verified' : 'Unverified'}
              </Text>
            </View>
          )}
        </View>
      )}

      {/* Session controls */}
      {!sessionId ? (
        <Pressable
          style={[styles.btn, startMut.isPending && styles.btnDisabled]}
          onPress={() => startMut.mutate()}
          disabled={startMut.isPending}
        >
          {startMut.isPending
            ? <ActivityIndicator color="#fff" size="small" />
            : (
              <>
                <Ionicons name="play-circle" size={20} color="#fff" />
                <Text style={styles.btnText}>Start Patrol Session</Text>
              </>
            )
          }
        </Pressable>
      ) : !scanning && (
        <Pressable style={styles.btn} onPress={() => setScanning(true)}>
          <Ionicons name="qr-code" size={20} color="#fff" />
          <Text style={styles.btnText}>Scan QR Checkpoint</Text>
        </Pressable>
      )}

      {/* Checkpoint list */}
      <ScrollView style={styles.list}>
        {checkpoints.map((cp: PatrolCheckpoint) => {
          const done = scannedIds.has(cp.id)
          return (
            <Card key={cp.id} style={styles.cpCard}>
              <View style={styles.cpRow}>
                <View style={styles.cpInfo}>
                  <Text style={styles.cpSeq}>{cp.sequence}</Text>
                  <Text style={styles.cpName}>{cp.name}</Text>
                  {!cp.qr_code && !cp.nfc_tag_id && (
                    <Text style={styles.manualLabel}>Manual</Text>
                  )}
                </View>
                <View style={styles.cpStatus}>
                  {done ? (
                    <Ionicons name="checkmark-circle" size={22} color={colors.success} />
                  ) : sessionId ? (
                    <Pressable
                      style={styles.manualBtn}
                      onPress={() => scanMut.mutate({ checkpointId: cp.id })}
                      disabled={scanMut.isPending}
                    >
                      <Text style={styles.manualBtnText}>Manual</Text>
                    </Pressable>
                  ) : (
                    <Ionicons name="ellipse-outline" size={22} color={colors.textDisabled} />
                  )}
                </View>
              </View>
            </Card>
          )
        })}
      </ScrollView>

      {/* Complete session button */}
      {sessionId && allScanned && (
        <Pressable
          style={[styles.completeBtn, completeMut.isPending && styles.btnDisabled]}
          onPress={() => completeMut.mutate()}
          disabled={completeMut.isPending}
        >
          {completeMut.isPending
            ? <ActivityIndicator color="#fff" size="small" />
            : (
              <>
                <Ionicons name="flag" size={20} color="#fff" />
                <Text style={styles.btnText}>Complete Patrol</Text>
              </>
            )
          }
        </Pressable>
      )}
    </View>
  )
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.background },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  header: {
    flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center',
    padding: spacing.md, backgroundColor: colors.surface,
    borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.divider,
  },
  routeName: { fontSize: fontSize.md, fontWeight: '700', color: colors.text, flex: 1 },
  progress: { fontSize: fontSize.sm, color: colors.primary, fontWeight: '600' },
  cameraContainer: { height: 280, position: 'relative', backgroundColor: '#000' },
  scanOverlay: { ...StyleSheet.absoluteFillObject, alignItems: 'center', justifyContent: 'center' },
  scanFrame: {
    width: 200, height: 200, borderWidth: 2, borderColor: colors.primary,
    borderRadius: 8, backgroundColor: 'transparent',
  },
  closeScan: { position: 'absolute', top: 12, right: 12 },
  scanFeedback: {
    position: 'absolute', bottom: 12, left: 12, right: 12,
    flexDirection: 'row', alignItems: 'center', gap: spacing.sm,
    padding: spacing.sm, borderRadius: radius.sm,
  },
  feedbackOk: { backgroundColor: 'rgba(0,180,0,0.85)' },
  feedbackWarn: { backgroundColor: 'rgba(200,100,0,0.85)' },
  feedbackText: { color: '#fff', fontWeight: '600', flex: 1 },
  list: { flex: 1, padding: spacing.sm },
  cpCard: { marginBottom: spacing.xs },
  cpRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  cpInfo: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, flex: 1 },
  cpSeq: {
    width: 28, height: 28, borderRadius: 14, backgroundColor: colors.primary,
    textAlign: 'center', lineHeight: 28, color: '#fff', fontWeight: '700', fontSize: fontSize.xs,
  },
  cpName: { fontSize: fontSize.sm, color: colors.text, flex: 1 },
  manualLabel: { fontSize: fontSize.xs, color: colors.textDisabled },
  cpStatus: { marginLeft: spacing.sm },
  manualBtn: {
    paddingHorizontal: spacing.sm, paddingVertical: 4,
    borderRadius: radius.xs, borderWidth: 1, borderColor: colors.primary,
  },
  manualBtnText: { color: colors.primary, fontSize: fontSize.xs, fontWeight: '600' },
  btn: {
    backgroundColor: colors.primary, borderRadius: radius.sm,
    paddingVertical: spacing.md, marginHorizontal: spacing.md, marginVertical: spacing.sm,
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: spacing.sm,
  },
  completeBtn: {
    backgroundColor: colors.success, borderRadius: radius.sm,
    paddingVertical: spacing.md, marginHorizontal: spacing.md, marginBottom: spacing.md,
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: spacing.sm,
  },
  btnText: { color: '#fff', fontWeight: '700', fontSize: fontSize.md },
  btnDisabled: { opacity: 0.5 },
  permText: { color: colors.textSecondary, marginBottom: spacing.md },
})
