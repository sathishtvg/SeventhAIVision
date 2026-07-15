import React, { useRef, useState } from 'react'
import {
  ActivityIndicator, Alert as RNAlert, Image, Modal, Pressable,
  StyleSheet, Text, View,
} from 'react-native'
import { CameraView, useCameraPermissions } from 'expo-camera'
import { Ionicons } from '@expo/vector-icons'
import { colors, fontSize, radius, spacing } from '@/theme'

interface CheckInPhotoModalProps {
  visible: boolean
  title: string
  onClose: () => void
  onConfirm: (photoUri: string) => void
  confirming?: boolean
  errorMessage?: string | null
}

/** Front-camera selfie capture for check-in/out — mirrors the QR-scanner
 * screens' CameraView usage, but takes a still photo (takePictureAsync)
 * instead of scanning a barcode. Capture → preview → confirm/retake. */
export function CheckInPhotoModal({
  visible, title, onClose, onConfirm, confirming, errorMessage,
}: CheckInPhotoModalProps) {
  const [permission, requestPermission] = useCameraPermissions()
  const cameraRef = useRef<CameraView>(null)
  const [capturedUri, setCapturedUri] = useState<string | null>(null)
  const [capturing, setCapturing] = useState(false)

  const handleCapture = async () => {
    if (!cameraRef.current || capturing) return
    setCapturing(true)
    try {
      const photo = await cameraRef.current.takePictureAsync({ quality: 0.7 })
      if (photo?.uri) setCapturedUri(photo.uri)
    } catch {
      RNAlert.alert('Error', 'Failed to capture photo. Please try again.')
    } finally {
      setCapturing(false)
    }
  }

  const handleClose = () => {
    setCapturedUri(null)
    onClose()
  }

  return (
    <Modal visible={visible} animationType="slide" onRequestClose={handleClose}>
      <View style={styles.root}>
        <View style={styles.header}>
          <Text style={styles.title}>{title}</Text>
          <Pressable onPress={handleClose}>
            <Ionicons name="close-circle" size={28} color={colors.textSecondary} />
          </Pressable>
        </View>

        {errorMessage && (
          <View style={styles.errorBanner}>
            <Ionicons name="warning" size={16} color="#fff" />
            <Text style={styles.errorText}>{errorMessage}</Text>
          </View>
        )}

        <View style={styles.cameraContainer}>
          {capturedUri ? (
            <Image source={{ uri: capturedUri }} style={StyleSheet.absoluteFillObject} resizeMode="cover" />
          ) : permission?.granted ? (
            <CameraView ref={cameraRef} style={StyleSheet.absoluteFillObject} facing="front" />
          ) : (
            <View style={styles.center}>
              <Text style={styles.permText}>Camera permission required for check-in selfie</Text>
              <Pressable style={styles.btn} onPress={requestPermission}>
                <Text style={styles.btnText}>Grant Permission</Text>
              </Pressable>
            </View>
          )}
        </View>

        <View style={styles.footer}>
          {capturedUri ? (
            <View style={styles.actionsRow}>
              <Pressable
                style={[styles.btn, styles.secondaryBtn]}
                onPress={() => setCapturedUri(null)}
                disabled={confirming}
              >
                <Ionicons name="camera-reverse" size={18} color={colors.text} />
                <Text style={styles.btnText}>Retake</Text>
              </Pressable>
              <Pressable
                style={[styles.btn, confirming && styles.btnDisabled]}
                onPress={() => onConfirm(capturedUri)}
                disabled={confirming}
              >
                {confirming
                  ? <ActivityIndicator color="#fff" size="small" />
                  : (
                    <>
                      <Ionicons name="checkmark-circle" size={18} color="#fff" />
                      <Text style={styles.btnText}>Use Photo</Text>
                    </>
                  )
                }
              </Pressable>
            </View>
          ) : permission?.granted ? (
            <Pressable style={styles.captureBtn} onPress={handleCapture} disabled={capturing}>
              {capturing ? <ActivityIndicator color={colors.primary} /> : <View style={styles.captureBtnInner} />}
            </Pressable>
          ) : null}
        </View>
      </View>
    </Modal>
  )
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.background },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: spacing.lg },
  header: {
    flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center',
    padding: spacing.md, backgroundColor: colors.surface,
    borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.divider,
  },
  title: { fontSize: fontSize.md, fontWeight: '700', color: colors.text },
  errorBanner: {
    flexDirection: 'row', alignItems: 'center', gap: spacing.sm,
    backgroundColor: colors.error, padding: spacing.sm,
  },
  errorText: { color: '#fff', fontSize: fontSize.sm, flex: 1, fontWeight: '600' },
  cameraContainer: { flex: 1, position: 'relative', backgroundColor: '#000' },
  permText: { color: colors.textSecondary, marginBottom: spacing.md, textAlign: 'center' },
  footer: { padding: spacing.lg, alignItems: 'center' },
  actionsRow: { flexDirection: 'row', gap: spacing.md, width: '100%' },
  captureBtn: {
    width: 72, height: 72, borderRadius: 36, borderWidth: 4, borderColor: '#fff',
    alignItems: 'center', justifyContent: 'center',
  },
  captureBtnInner: { width: 56, height: 56, borderRadius: 28, backgroundColor: '#fff' },
  btn: {
    flex: 1, backgroundColor: colors.primary, borderRadius: radius.sm,
    paddingVertical: spacing.md, flexDirection: 'row', alignItems: 'center',
    justifyContent: 'center', gap: spacing.sm,
  },
  secondaryBtn: { backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.cardBorder },
  btnText: { color: '#fff', fontWeight: '700', fontSize: fontSize.md },
  btnDisabled: { opacity: 0.5 },
})
