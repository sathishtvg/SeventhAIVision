/**
 * The privacy zones of one camera: what each covers, who drew it and when.
 *
 * A zone is applied by the server, and cannot be undone for what is recorded
 * after it. So this says what a zone does above the list, a zone is deleted
 * only after the phone has asked and said what that changes, and there is no
 * editing: a zone is deleted and drawn again.
 *
 * Reached from a camera's live view by whoever manages privacy. Drawing opens
 * the Draw Zone screen with Privacy chosen.
 */
import React, { useCallback, useLayoutEffect, useState } from 'react'
import { ActivityIndicator, Alert, FlatList, Pressable, RefreshControl, StyleSheet, Text, View } from 'react-native'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigation } from '@react-navigation/native'
import { Ionicons } from '@expo/vector-icons'
import { deletePrivacyZone, listCameraPrivacyZones, type PrivacyZone } from '@/api/privacyZones'
import { Card } from '@/components/Card'
import { MASKED_CAMERAS_KEY } from '@/hooks/useMaskedCameras'
import { apiErrorText } from '@/lib/apiErrorText'
import { WHAT_A_ZONE_DOES, WHAT_DELETING_DOES, drawnLine } from '@/lib/privacyZoneWords'
import { colors, fontSize, radius, spacing } from '@/theme'

type Props = { route: { params: { cameraId: string; streamId: string; cameraName: string } } }

export function CameraPrivacyZonesScreen({ route }: Props) {
  const { cameraId, streamId, cameraName } = route.params
  const navigation = useNavigation<any>()
  const qc = useQueryClient()
  const [refreshing, setRefreshing] = useState(false)
  const { data: zones = [], isLoading, error } = useQuery({
    queryKey: ['privacy-zones', cameraId], queryFn: () => listCameraPrivacyZones(cameraId) })

  useLayoutEffect(() => {
    navigation.setOptions({ title: `Privacy zones — ${cameraName}` })
  }, [navigation, cameraName])

  const again = useCallback(async () => {
    await Promise.all([
      qc.invalidateQueries({ queryKey: ['privacy-zones', cameraId] }),
      qc.invalidateQueries({ queryKey: MASKED_CAMERAS_KEY }),
    ])
  }, [qc, cameraId])
  const onRefresh = useCallback(async () => { setRefreshing(true); await again(); setRefreshing(false) }, [again])

  const remove = useMutation({ mutationFn: (zoneId: string) => deletePrivacyZone(zoneId).then(again) })

  // Deleting asks first, and says what it changes: the camera is shown and recorded without the mask from then on.
  const askToDelete = (zone: PrivacyZone) =>
    Alert.alert('Delete this privacy zone?', `“${zone.name}” on ${cameraName}.\n\n${WHAT_DELETING_DOES}`, [
      { text: 'Keep it', style: 'cancel' },
      { text: 'Delete the zone', style: 'destructive', onPress: () => remove.mutate(zone.id) },
    ])

  if (isLoading) return <View style={styles.center}><ActivityIndicator color={colors.primary} /></View>
  return (
    <View style={styles.root}>
      <FlatList
        data={zones}
        keyExtractor={(z) => z.id}
        contentContainerStyle={styles.list}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.primary} />}
        ListHeaderComponent={
          <View style={styles.header}>
            <Card style={styles.note}>
              <Text style={styles.noteText} testID="what-a-zone-does">{WHAT_A_ZONE_DOES}</Text>
            </Card>
            <Pressable style={styles.draw} accessibilityRole="button"
                       onPress={() => navigation.navigate('ZoneDraw', { cameraId, streamId, cameraName, kind: 'privacy' })}>
              <Ionicons name="add" size={18} color={colors.background} />
              <Text style={styles.drawText}>Draw a privacy zone</Text>
            </Pressable>
            {!!remove.error && <Text style={styles.error}>{apiErrorText(remove.error)}</Text>}
          </View>}
        renderItem={({ item }) => (
          <Card style={styles.row} testID="privacy-zone">
            <View style={styles.words}>
              <Text style={styles.name}>{item.name}</Text>
              <Text style={styles.meta}>{drawnLine(item)}</Text>
              {!item.is_active && <Text style={styles.meta}>Switched off: it masks nothing.</Text>}
            </View>
            <Pressable onPress={() => askToDelete(item)} disabled={remove.isPending} hitSlop={10}
                       accessibilityRole="button" accessibilityLabel={`Delete the zone ${item.name}`}
                       style={remove.isPending && styles.off}>
              <Ionicons name="trash-outline" size={20} color={colors.error} />
            </Pressable>
          </Card>)}
        ListEmptyComponent={
          <View style={styles.empty}>
            <Ionicons name={error ? 'alert-circle-outline' : 'eye-outline'} size={44} color={colors.textDisabled} />
            <Text style={styles.emptyText}>
              {error ? apiErrorText(error)
                : 'No privacy zone is drawn on this camera. It is shown, analysed and recorded whole.'}
            </Text>
          </View>}
      />
    </View>
  )
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.background },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', backgroundColor: colors.background },
  list: { padding: spacing.md, gap: spacing.sm, flexGrow: 1 },
  header: { gap: spacing.sm, marginBottom: spacing.xs },
  note: { borderColor: colors.warning, borderWidth: 1 },
  noteText: { color: colors.text, fontSize: fontSize.sm },
  draw: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 6, backgroundColor: colors.primary,
    borderRadius: radius.md, paddingVertical: spacing.sm,
  },
  drawText: { color: colors.background, fontSize: fontSize.md, fontWeight: '700' },
  row: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  words: { flex: 1, gap: 4 },
  name: { color: colors.text, fontSize: fontSize.md, fontWeight: '600' },
  meta: { color: colors.textSecondary, fontSize: fontSize.sm },
  error: { color: colors.error, fontSize: fontSize.sm },
  off: { opacity: 0.4 },
  empty: { alignItems: 'center', padding: spacing.xl, gap: spacing.sm },
  emptyText: { color: colors.textSecondary, fontSize: fontSize.md, textAlign: 'center' },
})
