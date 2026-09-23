/**
 * Attaching a medical certificate: a photo of it, or a file.
 *
 * A guard on a phone has the certificate one of two ways — in their hand, or
 * already in their downloads — so both are offered rather than guessing.
 *
 * WHETHER IT IS REQUIRED IS THE TENANT'S DECISION, NOT A NAME MATCH. Leave
 * types carry requires_document, and in the Demo tenant Medical Leave has it
 * set while the other three do not. Keying off the word "medical" would break
 * for an agency that calls it "Sick Leave" or requires a document for
 * compassionate leave too.
 */
import { useState } from 'react'
import { ActivityIndicator, Alert, Pressable, StyleSheet, Text, View } from 'react-native'
import { Ionicons } from '@expo/vector-icons'
import * as DocumentPicker from 'expo-document-picker'
import * as ImagePicker from 'expo-image-picker'

import { colors, fontSize, radius, spacing } from '@/theme'

export interface PickedFile {
  uri: string
  name: string
  mimeType: string
}

/** A sensible filename when the picker gives none — the server takes the
 *  extension from it to store the document. */
export function fileNameFor(uri: string, fallbackExt = 'jpg'): string {
  const tail = uri.split('/').pop() ?? ''
  if (tail.includes('.')) return tail
  return `attachment-${Date.now()}.${fallbackExt}`
}

interface Props {
  label: string
  hint?: string
  value: PickedFile | null
  onChange: (file: PickedFile | null) => void
}

export function AttachmentField({ label, hint, value, onChange }: Props) {
  const [busy, setBusy] = useState(false)

  const takePhoto = async () => {
    const perm = await ImagePicker.requestCameraPermissionsAsync()
    if (!perm.granted) {
      Alert.alert('Camera needed', 'Allow camera access to photograph the certificate, or choose a file instead.')
      return
    }
    setBusy(true)
    try {
      const shot = await ImagePicker.launchCameraAsync({ quality: 0.7 })
      const asset = shot.canceled ? null : shot.assets[0]
      if (asset) {
        onChange({
          uri: asset.uri,
          name: asset.fileName ?? fileNameFor(asset.uri),
          mimeType: asset.mimeType ?? 'image/jpeg',
        })
      }
    } finally {
      setBusy(false)
    }
  }

  const chooseFile = async () => {
    setBusy(true)
    try {
      // Images and PDFs: what a medical certificate actually arrives as.
      const res = await DocumentPicker.getDocumentAsync({
        type: ['image/*', 'application/pdf'],
        copyToCacheDirectory: true,
      })
      const asset = res.canceled ? null : res.assets[0]
      if (asset) {
        onChange({
          uri: asset.uri,
          name: asset.name ?? fileNameFor(asset.uri, 'pdf'),
          mimeType: asset.mimeType ?? 'application/octet-stream',
        })
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <View>
      <Text style={styles.label}>{label}</Text>
      {!!hint && <Text style={styles.hint}>{hint}</Text>}

      {value ? (
        <View style={styles.attached}>
          <Ionicons name="document-attach-outline" size={18} color={colors.success} />
          <Text style={styles.fileName} numberOfLines={1}>{value.name}</Text>
          <Pressable onPress={() => onChange(null)} accessibilityLabel="Remove attachment">
            <Ionicons name="close-circle" size={20} color={colors.textSecondary} />
          </Pressable>
        </View>
      ) : (
        <View style={styles.row}>
          <Pressable style={styles.btn} onPress={takePhoto} disabled={busy}>
            {busy ? <ActivityIndicator color={colors.primary} size="small" /> : (
              <>
                <Ionicons name="camera-outline" size={18} color={colors.primary} />
                <Text style={styles.btnText}>Take Photo</Text>
              </>
            )}
          </Pressable>
          <Pressable style={styles.btn} onPress={chooseFile} disabled={busy}>
            <Ionicons name="folder-open-outline" size={18} color={colors.primary} />
            <Text style={styles.btnText}>Choose File</Text>
          </Pressable>
        </View>
      )}
    </View>
  )
}

const styles = StyleSheet.create({
  label: {
    fontSize: fontSize.xs, color: colors.textSecondary, textTransform: 'uppercase',
    letterSpacing: 0.5, marginTop: spacing.md, marginBottom: spacing.xs,
  },
  hint: { fontSize: fontSize.xs, color: colors.textSecondary, marginBottom: spacing.xs },
  row: { flexDirection: 'row', gap: spacing.sm },
  btn: {
    flex: 1, flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
    gap: spacing.xs, borderWidth: 1, borderColor: colors.primary,
    borderRadius: radius.sm, paddingVertical: spacing.md,
  },
  btnText: { color: colors.primary, fontWeight: '700', fontSize: fontSize.sm },
  attached: {
    flexDirection: 'row', alignItems: 'center', gap: spacing.sm,
    backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.success,
    borderRadius: radius.sm, padding: spacing.sm,
  },
  fileName: { flex: 1, color: colors.text, fontSize: fontSize.sm },
})
