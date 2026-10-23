/**
 * My Face: register your face for check-in, once.
 *
 * Design: docs/plans/2026-10-23-staff-face-registration-design.md. The person is
 * told what the face is used for and agrees before the camera opens; the selfie
 * must pass the liveness check. Once a face is on file this screen only shows it:
 * changing it is for a supervisor or an admin, on the web (the owner's rule).
 */
import React, { useState } from 'react'
import { Image, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Ionicons } from '@expo/vector-icons'

import { getMyFace, myFacePhotoUrl, registerMyFace } from '@/api/faces'
import { Card } from '@/components/Card'
import { CheckInPhotoModal } from '@/components/CheckInPhotoModal'
import { DetailSkeleton, ErrorState } from '@/components/states'
import { apiErrorText } from '@/lib/apiErrorText'
import { useAuthStore } from '@/store/auth'
import { colors, fontSize, radius, spacing } from '@/theme'

export const FACE_CONSENT_TEXT =
  'Your face is used only to confirm that it is you when you check in and check out. ' +
  'A photo and a face code are kept while your account is active and deleted when it is closed. ' +
  'Only you, your supervisors and the admins can see the photo.'

export function MyFaceScreen() {
  const qc = useQueryClient()
  const userId = useAuthStore((s) => s.user?.id)
  const token = useAuthStore((s) => s.accessToken)
  const [agreed, setAgreed] = useState(false)
  const [camera, setCamera] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [photoFailed, setPhotoFailed] = useState(false)

  const { data, isLoading, isLoadingError, error: loadError, refetch } = useQuery({
    queryKey: ['my-face'],
    queryFn: getMyFace,
  })

  const register = useMutation({
    mutationFn: (photoUri: string) => registerMyFace(photoUri, agreed),
    onSuccess: (face) => {
      qc.setQueryData(['my-face'], face)
      setCamera(false)
      setError(null)
    },
    onError: (err) => {
      setError((err as { code?: string })?.code === 'ECONNABORTED'
        ? 'The server took too long to check the photo. Please try again.'
        : apiErrorText(err))
    },
  })

  if (isLoading) {
    return <DetailSkeleton lines={3} block={160} style={styles.content} />
  }
  if (isLoadingError || !data) {
    return <ErrorState error={loadError} onRetry={refetch} />
  }

  const photo = data.registered && userId ? myFacePhotoUrl(userId, token) : null

  return (
    <ScrollView style={styles.root} contentContainerStyle={styles.content}>
      {data.registered ? (
        <Card>
          <View style={styles.row}>
            <Ionicons name="checkmark-circle" size={22} color={colors.success} />
            <Text style={styles.heading}>Your face is registered</Text>
          </View>
          {photo && !photoFailed ? (
            <Image source={{ uri: photo }} style={styles.photo} resizeMode="cover" onError={() => setPhotoFailed(true)} />
          ) : null}
          <Text style={styles.muted}>
            {data.source === 'UPLOAD' ? 'Registered from a photo' : 'Registered with a selfie'}
            {data.registered_by_name && data.source === 'UPLOAD' ? ` by ${data.registered_by_name}` : ''}
            {data.updated_at ? ` on ${new Date(data.updated_at).toLocaleDateString()}` : ''}.
          </Text>
          <Text style={styles.body}>
            It is compared with your selfie each time you check in and check out. To change it, ask {data.who_may_change}.
          </Text>
        </Card>
      ) : data.may_register ? (
        <Card>
          <Text style={styles.heading}>Register your face</Text>
          <Text style={styles.body}>{FACE_CONSENT_TEXT}</Text>
          <Pressable
            style={styles.agreeRow}
            onPress={() => setAgreed((a) => !a)}
            accessibilityRole="checkbox"
            accessibilityState={{ checked: agreed }}
          >
            <Ionicons name={agreed ? 'checkbox' : 'square-outline'} size={22} color={agreed ? colors.primary : colors.textSecondary} />
            <Text style={styles.agreeText}>I agree to my face being registered for check-in</Text>
          </Pressable>
          <Pressable
            style={[styles.button, !agreed && styles.buttonOff]}
            disabled={!agreed}
            onPress={() => { setError(null); setCamera(true) }}
            accessibilityRole="button"
            accessibilityLabel="Take a selfie to register your face"
          >
            <Ionicons name="camera-outline" size={20} color="#fff" />
            <Text style={styles.buttonText}>Take a selfie</Text>
          </Pressable>
          <Text style={styles.note}>Face the camera in good light, alone. You can register yourself only once.</Text>
        </Card>
      ) : (
        <Card>
          <Text style={styles.heading}>No face on file</Text>
          <Text style={styles.body}>Your role does not register a face for check-in.</Text>
        </Card>
      )}

      <CheckInPhotoModal
        visible={camera}
        title="Face Registration Selfie"
        onClose={() => { setCamera(false); setError(null) }}
        onConfirm={(uri) => register.mutate(uri)}
        confirming={register.isPending}
        errorMessage={error}
      />
    </ScrollView>
  )
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.background },
  content: { padding: spacing.md, paddingBottom: spacing.xxl },
  row: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  heading: { fontSize: fontSize.lg, fontWeight: '700', color: colors.text },
  body: { fontSize: fontSize.sm, color: colors.text, marginTop: spacing.sm, lineHeight: 20 },
  muted: { fontSize: fontSize.sm, color: colors.textSecondary, marginTop: spacing.sm },
  note: { fontSize: fontSize.xs, color: colors.textSecondary, marginTop: spacing.sm },
  photo: { width: 160, height: 160, borderRadius: radius.md, marginTop: spacing.md, alignSelf: 'center' },
  agreeRow: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, marginTop: spacing.md },
  agreeText: { flex: 1, fontSize: fontSize.sm, color: colors.text },
  button: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: spacing.xs,
    backgroundColor: colors.primary, borderRadius: radius.sm, paddingVertical: spacing.md, marginTop: spacing.md,
  },
  buttonOff: { opacity: 0.4 },
  buttonText: { color: '#fff', fontWeight: '700', fontSize: fontSize.md },
})
