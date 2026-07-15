import React, { useState } from 'react'
import {
  ActivityIndicator, FlatList, Image, Modal, RefreshControl,
  StyleSheet, Text, TouchableOpacity, View,
} from 'react-native'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Ionicons } from '@expo/vector-icons'
import { getEvidence, evidenceFileUrl, getEvidenceFileHeaders } from '@/api/evidence'
import { Card } from '@/components/Card'
import { colors, fontSize, spacing, radius } from '@/theme'

export function EvidenceScreen() {
  const qc = useQueryClient()
  const [preview, setPreview] = useState<string | null>(null)

  const { data, isLoading, isFetching } = useQuery({
    queryKey: ['evidence'],
    queryFn: () => getEvidence({ limit: 40 }),
  })

  const authHeaders = getEvidenceFileHeaders()

  return (
    <View style={styles.root}>
      {isLoading ? (
        <View style={styles.center}>
          <ActivityIndicator color={colors.primary} />
        </View>
      ) : (
        <FlatList
          data={data ?? []}
          keyExtractor={(item) => item.id}
          numColumns={2}
          contentContainerStyle={styles.grid}
          columnWrapperStyle={styles.row}
          refreshControl={
            <RefreshControl
              refreshing={isFetching}
              onRefresh={() => qc.invalidateQueries({ queryKey: ['evidence'] })}
              tintColor={colors.primary}
            />
          }
          ListEmptyComponent={
            <View style={styles.empty}>
              <Ionicons name="images-outline" size={48} color={colors.textDisabled} />
              <Text style={styles.emptyText}>No evidence files yet</Text>
            </View>
          }
          renderItem={({ item }) => (
            <TouchableOpacity
              style={styles.tile}
              activeOpacity={0.8}
              onPress={() => setPreview(item.id)}
            >
              <Image
                source={{ uri: evidenceFileUrl(item.id), headers: authHeaders }}
                style={styles.thumb}
                resizeMode="cover"
              />
              <View style={styles.tileFooter}>
                <Ionicons
                  name={item.media_type === 'video' ? 'videocam' : 'image'}
                  size={12}
                  color={colors.textSecondary}
                />
                <Text style={styles.tileDate} numberOfLines={1}>
                  {new Date(item.captured_at).toLocaleDateString()}
                </Text>
              </View>
            </TouchableOpacity>
          )}
        />
      )}

      {/* Full-screen preview */}
      <Modal visible={!!preview} transparent animationType="fade" onRequestClose={() => setPreview(null)}>
        <View style={styles.modalBg}>
          <TouchableOpacity style={styles.modalClose} onPress={() => setPreview(null)}>
            <Ionicons name="close-circle" size={32} color={colors.text} />
          </TouchableOpacity>
          {preview && (
            <Image
              source={{ uri: evidenceFileUrl(preview), headers: authHeaders }}
              style={styles.modalImg}
              resizeMode="contain"
            />
          )}
        </View>
      </Modal>
    </View>
  )
}

const TILE_SIZE = 168

const styles = StyleSheet.create({
  root:       { flex: 1, backgroundColor: colors.background },
  center:     { flex: 1, alignItems: 'center', justifyContent: 'center' },
  grid:       { padding: spacing.sm, paddingBottom: spacing.xl },
  row:        { justifyContent: 'space-between', marginBottom: spacing.sm },
  tile: {
    width: TILE_SIZE,
    borderRadius: radius.md,
    overflow: 'hidden',
    backgroundColor: colors.card,
    borderWidth: 1,
    borderColor: colors.cardBorder,
  },
  thumb:      { width: TILE_SIZE, height: TILE_SIZE },
  tileFooter: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    paddingHorizontal: spacing.sm,
    paddingVertical: 5,
    backgroundColor: colors.surface,
  },
  tileDate:   { fontSize: fontSize.xs, color: colors.textSecondary, flex: 1 },
  empty:      { alignItems: 'center', justifyContent: 'center', paddingTop: spacing.xl * 2 },
  emptyText:  { color: colors.textSecondary, fontSize: fontSize.md, marginTop: spacing.md },
  modalBg:    { flex: 1, backgroundColor: 'rgba(0,0,0,0.92)', justifyContent: 'center', alignItems: 'center' },
  modalClose: { position: 'absolute', top: 52, right: spacing.md, zIndex: 10 },
  modalImg:   { width: '100%', height: '80%' },
})
